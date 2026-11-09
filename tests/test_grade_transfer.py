"""Creative grade transfer is isolated, transactional, and LUT-content checked."""
from copy import deepcopy
from dataclasses import asdict, FrozenInstanceError, replace
import ast
import json
import os
from pathlib import Path

import pytest

from lumen.clip_transform import ClipTransform
from lumen.grade_transfer import (CopiedGrade, copy_grade, grade_for_paste,
                                  is_picture_clip, paste_grade)
from lumen.model import Clip, ClipEffect, Editor, Grade, Media, Project
from lumen.restoration_settings import RestorationSettings
from lumen.stabilization_settings import StabilizationSettings


CUBE = 'LUT_3D_SIZE 2\n0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n'


def project_pair(tmp_path):
    lut = tmp_path / 'Look.cube'
    lut.write_text(CUBE)
    grade = Grade(exposure=.125, contrast=1.23, saturation=.987654321,
                  temperature=.17, tint=-.2, shadows=.3, highlights=-.4,
                  enabled=False, lut_path=str(lut), linear_exposure=.918273645,
                  lift=[-.12, .13, .14], gamma=[.88, .99, 1.12], gain=[1.11, 1.22, 1.33],
                  master_curve=[[0., -.1], [.22, .28], [1., 1.1]],
                  red_curve=[[0., 0.], [.31, .42], [1., 1.]],
                  green_curve=[[0., 0.], [.43, .37], [1., 1.]],
                  blue_curve=[[0., 0.], [.52, .63], [1., 1.]],
                  input_encoding='linear_rec709', lut_strength=.321987654)
    source = Clip('source', 'video', 0, 0, 0, 60, grade=grade)
    target = Clip('target', 'video', 0, 90, 80, 40, grade=Grade(input_encoding='srgb'),
                  gain_db=-6, muted=True, fade_in=3, fade_out=5,
                  effects=[ClipEffect('blur', 'blur', 1.3)],
                  restoration=RestorationSettings(True, .1, .2, .3),
                  stabilization=StabilizationSettings(enabled=True, strength=.7),
                  transform=ClipTransform(x=12, y=-7, scale=1.2, rotation=7, mirror_x=True))
    project = Project(media=[Media('video', str(tmp_path / 'movie.mp4'), 'Movie', 10,
                                   640, 360, True, True),
                             Media('audio', str(tmp_path / 'audio.wav'), 'Sound', 10,
                                   has_video=False, has_audio=True)],
                      clips=[source, target, Clip('audio', 'audio', 2, 0, 0, 30)])
    project.validate()
    return project, lut


@pytest.fixture
def pair(tmp_path):
    return project_pair(tmp_path)


def test_copy_contains_every_creative_field_and_no_input_assignment(pair):
    project, _ = pair
    before = deepcopy(project)
    copied = copy_grade(project, 'source')
    expected = asdict(project.clips[0].grade)
    del expected['input_encoding']
    assert json.loads(copied.creative_json) == expected
    assert 'input_encoding' not in copied.creative_json
    assert project == before
    with pytest.raises(FrozenInstanceError):
        copied.creative_json = '{}'


def test_paste_preserves_every_other_target_property_and_is_one_undo_step(pair, monkeypatch):
    project, _ = pair
    editor = Editor(project)
    before = deepcopy(editor.project)
    copied = copy_grade(project, 'source')
    calls = []
    update = editor.update_clip
    monkeypatch.setattr(editor, 'update_clip', lambda *args, **kwargs: (calls.append((args, kwargs)), update(*args, **kwargs))[1])
    assert paste_grade(editor, 'target', copied)
    after = deepcopy(editor.project)
    expected = deepcopy(before)
    expected.clips[1].grade = deepcopy(expected.clips[0].grade)
    expected.clips[1].grade.input_encoding = 'srgb'
    assert after == expected
    assert len(calls) == len(editor._undo) == editor.revision == 1
    assert list(calls[0][1]) == ['grade']
    assert editor.undo() and editor.project == before
    assert editor.redo() and editor.project == after


def test_no_shared_references_and_reusable_snapshot(pair):
    project, _ = pair
    copied = copy_grade(project, 'source')
    original = copied.grade_for_input('rec709')
    project.clips[0].grade.lift[0] = .99
    project.clips[0].grade.red_curve[1][1] = .81
    editor = Editor(project)
    paste_grade(editor, 'target', copied)
    assert editor.project.clips[1].grade.red_curve == original.red_curve
    editor.project.clips[1].grade.lift[0] = -.99
    candidate = grade_for_paste(editor.project, 'source', copied)
    assert candidate.lift == original.lift
    candidate.green_curve[1][1] = .999
    assert copied.grade_for_input('srgb').green_curve == original.green_curve


@pytest.mark.parametrize('encoding', ['rec709', 'srgb', 'linear_rec709'])
def test_target_source_interpretation_is_never_overwritten(pair, encoding):
    project, _ = pair
    project.clips[1].grade.input_encoding = encoding
    copied = copy_grade(project, 'source')
    editor = Editor(project)
    paste_grade(editor, 'target', copied)
    assert editor.project.clips[1].grade.input_encoding == encoding
    assert editor.project.clips[1].grade.enabled is False
    assert editor.project.clips[1].grade.lut_strength == .321987654


def test_identical_grade_is_noop_even_with_different_input_assignment(pair):
    project, _ = pair
    copied = copy_grade(project, 'source')
    project.clips[1].grade = copied.grade_for_input('srgb')
    editor = Editor(project)
    before = deepcopy(editor.project)
    assert not paste_grade(editor, 'target', copied)
    assert editor.project == before and not editor.can_undo and editor.revision == 0


@pytest.mark.parametrize('clip_id', ['', 'missing', 'audio'])
def test_nonpicture_selection_cannot_copy_or_paste(pair, clip_id):
    project, _ = pair
    copied = copy_grade(project, 'source')
    editor = Editor(project)
    before = deepcopy(editor.project)
    assert not is_picture_clip(project, clip_id)
    with pytest.raises(ValueError, match='Select a picture clip'):
        copy_grade(project, clip_id)
    with pytest.raises(ValueError, match='Select a picture clip'):
        paste_grade(editor, clip_id, copied)
    assert editor.project == before and not editor.can_undo


def test_video_media_on_audio_track_cannot_copy(pair):
    project, _ = pair
    project.clips.append(Clip('video-audio', 'video', 3, 0, 0, 30))
    with pytest.raises(ValueError, match='Select a picture clip'):
        copy_grade(project, 'video-audio')


def test_empty_clipboard_does_not_edit(pair):
    editor = Editor(pair[0])
    before = deepcopy(editor.project)
    with pytest.raises(ValueError, match='No grade has been copied'):
        paste_grade(editor, 'target', None)
    assert editor.project == before and not editor.can_undo


@pytest.mark.parametrize('mode', ['missing', 'invalid', 'changed_same_size', 'directory'])
def test_stale_or_invalid_lut_aborts_atomically_including_disabled_grade(pair, mode):
    project, lut = pair
    copied = copy_grade(project, 'source')
    editor = Editor(project)
    before = deepcopy(editor.project)
    previous = lut.stat()
    if mode == 'missing':
        lut.unlink()
    elif mode == 'invalid':
        lut.write_text('not a cube')
    elif mode == 'changed_same_size':
        lut.write_text(CUBE.replace('0 0 0', '1 0 0', 1))
        os.utime(lut, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    else:
        lut.unlink(); lut.mkdir()
    with pytest.raises(ValueError, match='LUT'):
        paste_grade(editor, 'target', copied)
    assert editor.project == before and not editor.can_undo and editor.revision == 0


def test_touch_same_content_remains_valid(pair):
    project, lut = pair
    copied = copy_grade(project, 'source')
    previous = lut.stat()
    os.utime(lut, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000))
    assert paste_grade(Editor(project), 'target', copied)


def test_copy_invalid_or_unreadable_lut_fails_without_mutation(pair):
    project, lut = pair
    before = deepcopy(project)
    lut.write_bytes(b'\xff\xfe')
    with pytest.raises(ValueError, match='Grade LUT is invalid'):
        copy_grade(project, 'source')
    assert project == before
    lut.unlink()
    with pytest.raises(ValueError, match='Cannot read the grade LUT'):
        copy_grade(project, 'source')
    assert project == before


def test_symlink_retarget_to_identical_bytes_is_not_same_file(pair, tmp_path):
    project, lut = pair
    link = tmp_path / 'link.cube'
    alternate = tmp_path / 'alternate.cube'
    alternate.write_text(CUBE)
    try:
        link.symlink_to(lut)
    except (OSError, NotImplementedError):
        pytest.skip('Symlinks are unavailable on this host')
    project.clips[0].grade.lut_path = str(link)
    copied = copy_grade(project, 'source')
    link.unlink(); link.symlink_to(alternate)
    with pytest.raises(ValueError, match='changed or moved'):
        paste_grade(Editor(project), 'target', copied)


def test_missing_identity_and_corrupted_snapshot_rejected(pair):
    project, _ = pair
    copied = copy_grade(project, 'source')
    for malformed in [replace(copied, lut_identity=None), CopiedGrade('{}'), CopiedGrade('null'),
                      CopiedGrade('not json'), object()]:
        editor = Editor(deepcopy(project))
        before = deepcopy(editor.project)
        with pytest.raises(ValueError):
            paste_grade(editor, 'target', malformed)
        assert editor.project == before and not editor.can_undo


def test_new_project_same_window_snapshot_and_save_reload(pair, tmp_path):
    project, _ = pair
    copied = copy_grade(project, 'source')
    other = Project(media=deepcopy(project.media), clips=[deepcopy(project.clips[1])])
    editor = Editor(other)
    assert paste_grade(editor, 'target', copied)
    path = tmp_path / 'saved.lumen.json'
    editor.project.save(path)
    restored = Project.load(path)
    assert restored == editor.project
    assert restored.clips[0].grade.input_encoding == 'srgb'
    assert restored.clips[0].grade.lut_strength == .321987654


def test_no_lut_has_no_filesystem_dependency(pair, monkeypatch):
    project, _ = pair
    project.clips[0].grade.lut_path = ''
    copied = copy_grade(project, 'source')
    assert copied.lut_identity is None
    monkeypatch.setattr(Path, 'open', lambda *_args, **_kwargs: pytest.fail('Unexpected filesystem read'))
    assert paste_grade(Editor(project), 'target', copied)


def test_lut_read_has_hard_size_bound_and_rejects_special_files(pair, monkeypatch):
    from lumen import grade_transfer
    project, lut = pair
    monkeypatch.setattr(grade_transfer, 'MAX_LUT_BYTES', len(CUBE) - 1)
    with pytest.raises(ValueError, match='exceeds 64 MiB'):
        copy_grade(project, 'source')
    if hasattr(os, 'mkfifo'):
        lut.unlink(); os.mkfifo(lut)
        with pytest.raises(ValueError, match='regular .cube'):
            copy_grade(project, 'source')


def test_all_grade_transfer_errors_have_explicit_chinese_catalog_entries():
    from lumen.i18n_catalog_grade_transfer import ERROR_ZH_CN
    root = Path(__file__).resolve().parents[1] / 'lumen'
    for module in ['grade_transfer.py', 'ui_grade_transfer.py']:
        for node in ast.walk(ast.parse((root / module).read_text())):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
                message = node.exc.args[0]
                if isinstance(message, ast.Constant) and isinstance(message.value, str):
                    assert message.value in ERROR_ZH_CN


def test_read_budget_is_enforced_even_if_stream_grows_after_stat(pair, monkeypatch):
    from lumen import grade_transfer
    project, _ = pair
    calls = []
    fdopen = grade_transfer.os.fdopen
    monkeypatch.setattr(grade_transfer, 'MAX_LUT_BYTES', 128)

    class GrowingStream:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *_): self.stream.close()
        def fileno(self): return self.stream.fileno()
        def read(self, size):
            calls.append(size)
            return b'0' * size

    monkeypatch.setattr(grade_transfer.os, 'fdopen', lambda *args, **kwargs: GrowingStream(fdopen(*args, **kwargs)))
    with pytest.raises(ValueError, match='exceeds 64 MiB'):
        copy_grade(project, 'source')
    assert calls == [129]


def test_regular_file_replaced_by_fifo_before_open_never_blocks(pair, monkeypatch):
    from lumen import grade_transfer
    if not hasattr(os, 'mkfifo') or not hasattr(os, 'O_NONBLOCK'):
        pytest.skip('Host does not support POSIX nonblocking named pipes')
    project, lut = pair
    original_open = grade_transfer.os.open

    def replaced_open(path, flags, *args, **kwargs):
        assert flags & os.O_NONBLOCK
        lut.unlink(); os.mkfifo(lut)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(grade_transfer.os, 'open', replaced_open)
    with pytest.raises(ValueError, match='changed while it was being read'):
        copy_grade(project, 'source')


def test_content_change_during_read_aborts_and_closes_file(pair, monkeypatch):
    from lumen import grade_transfer
    project, lut = pair
    fdopen = grade_transfer.os.fdopen
    opened = []

    class ChangedStream:
        def __init__(self, stream): self.stream = stream; opened.append(stream)
        def __enter__(self): return self
        def __exit__(self, *_): self.stream.close()
        def fileno(self): return self.stream.fileno()
        def read(self, size):
            result = self.stream.read(size)
            lut.write_text(CUBE.replace('0 0 0', '1 0 0', 1))
            return result

    monkeypatch.setattr(grade_transfer.os, 'fdopen', lambda *args, **kwargs: ChangedStream(fdopen(*args, **kwargs)))
    with pytest.raises(ValueError, match='changed while it was being read'):
        copy_grade(project, 'source')
    assert opened and all(stream.closed for stream in opened)


def test_read_permission_denial_is_a_readable_atomic_error(pair, monkeypatch):
    from lumen import grade_transfer
    project, _ = pair
    copied = copy_grade(project, 'source')
    editor = Editor(project); before = deepcopy(editor.project)
    monkeypatch.setattr(grade_transfer.os, 'open', lambda *_a, **_k: (_ for _ in ()).throw(PermissionError('denied')))
    with pytest.raises(ValueError, match='Cannot read the grade LUT'):
        paste_grade(editor, 'target', copied)
    assert editor.project == before and not editor.can_undo


@pytest.mark.parametrize('mutation', [None, 'st_dev', 'st_ino', 'st_size',
                                      'st_mtime_ns', 'st_ctime_ns', 'path_ctime'])
def test_windows_split_ctime_keeps_lut_snapshot_and_race_checks(pair, monkeypatch, mutation):
    """Creation time differs from descriptor change time; neither API may drift."""
    from types import SimpleNamespace
    from lumen import file_identity, grade_transfer
    import hashlib

    project, lut = pair
    before = deepcopy(project)
    payload = lut.read_bytes()
    original_fstat = os.fstat
    original_stat = Path.stat
    original_fdopen = os.fdopen
    state = {'read': False, 'streams': []}
    fields = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns')

    def snapshot(info, **changes):
        values = {key: getattr(info, key) for key in fields}
        values['st_birthtime_ns'] = getattr(info, 'st_birthtime_ns', None)
        return SimpleNamespace(**(values | changes))

    def by_fd(fd):
        info = original_fstat(fd)
        changes = {'st_ctime_ns': info.st_ctime_ns + 1000000}
        if state['read'] and mutation and mutation != 'path_ctime':
            changes[mutation] = changes.get(mutation, getattr(info, mutation)) + 1
        return snapshot(info, **changes)

    def by_path(path, *args, **kwargs):
        info = original_stat(path, *args, **kwargs)
        if path == lut and state['read'] and mutation == 'path_ctime':
            return snapshot(info, st_ctime_ns=info.st_ctime_ns + 1)
        return info

    class TrackedStream:
        def __init__(self, stream): self.stream = stream; state['streams'].append(stream)
        def __enter__(self): return self
        def __exit__(self, *_): self.stream.close()
        def fileno(self): return self.stream.fileno()
        def read(self, size):
            result = self.stream.read(size)
            state['read'] = True
            return result

    monkeypatch.setattr(file_identity, '_WINDOWS', True)
    monkeypatch.setattr(os, 'fstat', by_fd)
    monkeypatch.setattr(Path, 'stat', by_path)
    monkeypatch.setattr(os, 'fdopen', lambda *a, **kw: TrackedStream(original_fdopen(*a, **kw)))
    if mutation is None:
        copied = copy_grade(project, 'source')
        assert copied.lut_identity.sha256 == hashlib.sha256(payload).hexdigest()
        assert copied.lut_identity.resolved_path == str(lut.resolve())
    else:
        with pytest.raises(ValueError, match='changed while it was being read'):
            copy_grade(project, 'source')
        assert state['read'], 'Must reach the read before injecting the race'
    assert project == before
    assert state['streams'] and all(stream.closed for stream in state['streams'])


def test_lut_race_diagnostic_is_bounded_and_contains_no_path_or_file_id():
    from types import SimpleNamespace
    from lumen.grade_transfer import _changed_lut
    values = dict(st_dev=123456789, st_ino=987654321, st_mode=0o100644,
                  st_size=64, st_mtime_ns=100, st_ctime_ns=200,
                  st_birthtime_ns=50)
    before = SimpleNamespace(**values)
    after = SimpleNamespace(**(values | {'st_ctime_ns': 201}))
    error = _changed_lut(before, after, stage='descriptor_after_read')
    assert str(error) == 'Grade LUT changed while it was being read. Try copying the grade again.'
    assert len(error.__notes__) == 1
    assert json.loads(error.__notes__[0]) == {
        'kind': 'grade_lut_snapshot', 'stage': 'descriptor_after_read',
        'different_fields': ['st_ctime_ns'], 'mtime_delta_ns': 0, 'ctime_delta_ns': 1,
    }
    assert '123456789' not in error.__notes__[0]
    assert '987654321' not in error.__notes__[0]


def test_same_size_mtime_replacement_before_open_is_rejected(pair, monkeypatch):
    from lumen import grade_transfer
    project, lut = pair
    replacement = lut.with_name('Replacement.cube')
    replacement.write_bytes(lut.read_bytes())
    original = lut.stat()
    os.utime(replacement, ns=(original.st_atime_ns, original.st_mtime_ns))
    assert replacement.stat().st_size == original.st_size
    assert replacement.stat().st_mtime_ns == original.st_mtime_ns
    original_open = os.open
    opened = []

    def replaced_open(path, flags, *args, **kwargs):
        fd = original_open(replacement if Path(path) == lut else path, flags, *args, **kwargs)
        opened.append(fd)
        return fd

    monkeypatch.setattr(os, 'open', replaced_open)
    with pytest.raises(ValueError, match='changed while it was being read') as caught:
        copy_grade(project, 'source')
    note = json.loads(caught.value.__notes__[0])
    assert note['stage'] == 'path_to_open_descriptor'
    assert 'st_ino' in note['different_fields']
    assert opened
    for fd in opened:
        with pytest.raises(OSError):
            os.fstat(fd)


def test_fdopen_failure_closes_unowned_descriptor_and_keeps_project(pair, monkeypatch):
    project, _ = pair
    before = deepcopy(project)
    original_open = os.open
    descriptors = []

    def tracked_open(*args, **kwargs):
        fd = original_open(*args, **kwargs)
        descriptors.append(fd)
        return fd

    def denied_fdopen(*args, **kwargs):
        raise OSError('synthetic stream construction failure')

    monkeypatch.setattr(os, 'open', tracked_open)
    monkeypatch.setattr(os, 'fdopen', denied_fdopen)
    with pytest.raises(ValueError, match='Cannot read the grade LUT'):
        copy_grade(project, 'source')
    assert project == before
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])
