"""Independent lifecycle and bounded-helper review regressions."""
from copy import deepcopy
from types import SimpleNamespace
import os
import io
from pathlib import Path
import struct
import subprocess

import pytest

# Reuse only actual Qt/window harness; assertions/flows below are independent.
from test_final_frame_preview_ui import app, window, loaded
from test_highbit_caption_reference import latin_font


@pytest.mark.parametrize('operation', ['new', 'open', 'recover'])
def test_actual_project_replacement_paths_reject_old_success_and_error(window, app, monkeypatch, operation):
    from PySide6.QtWidgets import QFileDialog, QInputDialog
    from lumen.model import Project
    w = window
    dialog = loaded(w, app)
    generation, context, snapshot = dialog._generation, dialog.context, dialog.snapshot
    before = w.project
    replacement = deepcopy(before)
    monkeypatch.setattr(w, 'confirm_discard', lambda: True)
    if operation == 'new':
        monkeypatch.setattr(QInputDialog, 'getItem', lambda *a, **k: ('1280 × 720 · 30 fps', True))
        w.new_project()
    elif operation == 'open':
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: ('/tmp/review-project.lumen', ''))
        monkeypatch.setattr(Project, 'load', lambda *a, **k: replacement)
        w.open_project()
    else:
        entry = SimpleNamespace(identity='independent-final-frame-review-recovery', original_path=None)
        monkeypatch.setattr(w.recovery_store, 'load', lambda *a, **k: replacement)
        w.restore_recovery(entry)
    assert w.project is not before
    assert dialog._generation != generation
    dialog._accept_result('verify', generation, context, snapshot, (True, None))
    dialog._accept_result('render', generation, context, None, (None, 'old project error'))
    assert dialog.snapshot is None and dialog.canvas.image.isNull()
    assert dialog.state == 'stale' and not w.test_warnings


def test_new_and_open_cannot_replace_project_while_registered_still_is_running(window, app, monkeypatch):
    """Existing app work gate remains effective for the new registered worker."""
    import threading
    import lumen.ui_final_frame_preview as module
    from test_final_frame_preview_ui import opened, until, fake_snapshot
    from PySide6.QtWidgets import QInputDialog, QFileDialog
    w = window
    entered, release = threading.Event(), threading.Event()
    def capture(project, frame, **kwargs):
        entered.set()
        assert release.wait(5)
        return fake_snapshot(project, frame)
    monkeypatch.setattr(module, 'capture_final_frame_preview', capture)
    monkeypatch.setattr(QInputDialog, 'getItem', lambda *a, **k: pytest.fail('New dialog opened during work'))
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **k: pytest.fail('Open dialog opened during work'))
    d = opened(w)
    before = w.project
    d.refresh_frame()
    try:
        assert entered.wait(2)
        w.new_project()
        w.open_project()
        assert w.project is before and len(w.jobs) == 1
    finally:
        d.cancel_refresh()
        release.set()
        until(app, lambda: not w.jobs)


def test_caption_guarded_and_default_paths_keep_identical_filter_bytes(tmp_path, latin_font):
    from lumen import caption_render as captions
    from lumen.model import Project, Caption, CaptionStyle
    p = Project(width=320, height=180,
                captions=[Caption('c', 3, 5, 'A line, with punctuation.\nA second line.')],
                caption_style=CaptionStyle(font_family=latin_font, font_size=18,
                                           margin_bottom=12, outline_width=1))
    calls = []
    default_dir, guarded_dir = tmp_path / 'default', tmp_path / 'guarded'
    default_dir.mkdir(); guarded_dir.mkdir()
    default = captions.caption_filters(p, default_dir)
    guarded = captions.caption_filters(p, guarded_dir, check=lambda: calls.append(1))
    # Separate empty caches force both paths to parse, prepare and save their
    # own selected font; reuse could hide a guarded-font preparation failure.
    normalized = [value.replace(str(guarded_dir), str(default_dir)) for value in guarded]
    assert calls and default == normalized
    assert [path.name for path in default_dir.iterdir()] == [path.name for path in guarded_dir.iterdir()]
    assert captions.font_identity(p.caption_style) == captions.font_identity(p.caption_style, check=lambda: None)


def test_font_discovery_cancellation_is_not_swallowed(tmp_path, monkeypatch):
    from lumen import caption_render as captions
    from lumen.final_frame_preview import FinalFramePreviewCancelled
    from lumen.model import CaptionStyle
    monkeypatch.setattr(captions, '_roots', lambda directory: [tmp_path])
    def cancel():
        raise FinalFramePreviewCancelled('review cancellation')
    with pytest.raises(FinalFramePreviewCancelled, match='review cancellation'):
        captions.font_identity(CaptionStyle(font_directory=str(tmp_path)), check=cancel)


@pytest.mark.parametrize('field,limit,label', [('dirs', 2048, 'directories'),
                                             ('entries', 20000, 'entries'),
                                             ('fonts', 4096, 'fonts')])
def test_guarded_font_discovery_has_explicit_independent_budgets(tmp_path, field, limit, label):
    from lumen import caption_render as captions
    (tmp_path / 'one.ttf').write_bytes(b'not parsed for this budget test')
    budget = dict(dirs=0, entries=0, fonts=0)
    budget[field] = limit
    with pytest.raises(captions.CaptionRenderError, match=label):
        captions._guarded_font_paths(tmp_path, lambda: None, budget)


def test_guarded_font_discovery_avoids_eager_unbounded_os_walk(tmp_path, monkeypatch):
    from lumen import caption_render as captions
    (tmp_path / 'regular.ttf').write_bytes(b'bounded discovery only')
    monkeypatch.setattr(captions.os, 'walk', lambda *a, **k: pytest.fail('os.walk eagerly materializes complete directories'))
    paths = captions._guarded_font_paths(tmp_path, lambda: None, dict(dirs=0, entries=0, fonts=0))
    assert paths == [tmp_path / 'regular.ttf']


def test_guarded_font_file_size_and_collection_face_count_reject_before_parse(tmp_path):
    from lumen import caption_render as captions
    path = tmp_path / 'large.ttc'
    path.write_bytes(b'ttcf' + struct.pack('>II', 0x00010000, 65))
    with pytest.raises(captions.CaptionRenderError, match='64 faces'):
        captions._guarded_font_file(path, lambda: None)
    with pytest.raises(captions.CaptionRenderError, match='64 MiB'):
        captions._guarded_font_size(path, 64 * 1024 * 1024 + 1, lambda: None)


@pytest.mark.skipif(not hasattr(os, 'mkfifo'), reason='POSIX special-file boundary')
def test_guarded_font_rejects_fifo_before_attempting_a_blocking_open(tmp_path, monkeypatch):
    from lumen import caption_render as captions
    from lumen.model import CaptionStyle
    special = tmp_path / 'regular.ttf'
    os.mkfifo(special)
    monkeypatch.setattr(captions, '_roots', lambda directory: [tmp_path])
    original = Path.open
    def checked_open(path, *args, **kwargs):
        if path == special:
            pytest.fail('Must reject FIFO before a blocking open')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', checked_open)
    with pytest.raises(captions.CaptionRenderError, match='regular|plain|special|local'):
        captions.font_identity(CaptionStyle(font_directory=str(tmp_path)), check=lambda: None)


def test_font_face_parse_propagates_guard_and_closes_selected_face(tmp_path, monkeypatch):
    from lumen import caption_render as captions
    from lumen.final_frame_preview import FinalFramePreviewCancelled
    import fontTools.ttLib
    closed = []
    class Face:
        def close(self):
            closed.append(True)
    monkeypatch.setattr(fontTools.ttLib, 'TTFont', lambda *a, **k: Face())
    monkeypatch.setattr(captions, '_guarded_font_file', lambda *a, **k: io.BytesIO())
    def cancel():
        raise FinalFramePreviewCancelled('cancel during bounded parse')
    with pytest.raises(FinalFramePreviewCancelled, match='bounded parse'):
        captions._read_faces(str(tmp_path / 'a.ttf'), 1, 1, 1, check=cancel)
    assert closed == [True]


@pytest.mark.parametrize('magic', [b'wOFF', b'wOF2'])
def test_guarded_font_rejects_compressed_container_even_with_ttf_suffix(tmp_path, magic):
    from lumen import caption_render as captions
    path = tmp_path / 'compressed.ttf'
    path.write_bytes(magic + bytes(48))
    with pytest.raises(captions.CaptionRenderError, match='uncompressed SFNT/TTC'):
        captions._guarded_font_file(path, lambda: None)


@pytest.mark.parametrize('tables,length,message', [(257, 0, '256 tables'), (1, 100, 'table exceeds')])
def test_guarded_sfnt_directory_counts_and_ranges_are_checked_before_parse(tmp_path, tables, length, message):
    from lumen import caption_render as captions
    path = tmp_path / 'untrusted.ttf'
    data = b'\x00\x01\x00\x00' + struct.pack('>HHHH', tables, 0, 0, 0)
    data += b'name' + struct.pack('>III', 0, 28, length) + bytes(4)
    path.write_bytes(data)
    with pytest.raises(captions.CaptionRenderError, match=message):
        captions._guarded_font_file(path, lambda: None)


def _cmap_groups(groups, fmt=12):
    return (struct.pack('>HHIII', fmt, 0, 16 + 12 * len(groups), 0, len(groups))
            + b''.join(struct.pack('>III', start, end, 0) for start, end in groups))


def _cmap_table(*subtables):
    offset = 4 + 8 * len(subtables)
    records = []
    for index, subtable in enumerate(subtables):
        records.append(struct.pack('>HHI', 3 if index == 0 else 0, 10 if index == 0 else 4, offset))
        offset += len(subtable)
    return struct.pack('>HH', 0, len(subtables)) + b''.join(records) + b''.join(subtables)


@pytest.mark.parametrize('fmt', [12, 13])
@pytest.mark.parametrize('groups', [[(0, 0xFFFFFFFF)], [(10, 30), (20, 40)], [(20, 10)]])
def test_character_map_expansion_rejects_tiny_unbounded_or_overlapping_groups(fmt, groups):
    from lumen import caption_render as captions
    data = _cmap_table(_cmap_groups(groups, fmt))
    assert len(data) < 64  # Safe tiny malicious metadata; never allocate its ranges.
    with pytest.raises(captions.CaptionRenderError, match='character map'):
        captions._guarded_cmap(data, lambda: None)


def test_character_map_expansion_budget_is_across_unique_subtables():
    from lumen import caption_render as captions
    # Each table fits Unicode individually; their aggregate expansion does not.
    data = _cmap_table(_cmap_groups([(0, 0x8FFFF)]), _cmap_groups([(0, 0x8FFFF)]))
    with pytest.raises(captions.CaptionRenderError, match='character map'):
        captions._guarded_cmap(data, lambda: None)


def test_character_map_small_valid_bmp_full_unicode_and_variation_ranges():
    from lumen import caption_render as captions
    bmp = struct.pack('>12H', 4, 24, 0, 2, 2, 0, 0, 65535, 0, 65535, 1, 0)
    for subtable in (bmp, _cmap_groups([(65, 90)]), _cmap_groups([(0x20000, 0x20005)], 13)):
        captions._guarded_cmap(_cmap_table(subtable), lambda: None)


def test_character_map_variation_range_cannot_expand_beyond_unicode():
    from lumen import caption_render as captions
    sub = struct.pack('>HII', 14, 29, 1)
    sub += (0xFE00).to_bytes(3, 'big') + struct.pack('>II', 21, 0)
    sub += struct.pack('>I', 1) + (0x10FFFF).to_bytes(3, 'big') + bytes([255])
    with pytest.raises(captions.CaptionRenderError, match='character map'):
        captions._guarded_cmap(_cmap_table(sub), lambda: None)


def _font_with_names(path, count, string_size, payload):
    strings = 6 + count * 12
    names = struct.pack('>HHH', 0, count, strings)
    names += struct.pack('>6H', 3, 1, 0x409, 1, string_size, 0) * count
    names += payload
    header = b'\x00\x01\x00\x00' + struct.pack('>4H', 1, 16, 0, 0)
    path.write_bytes(header + b'name' + struct.pack('>III', 0, 28, len(names)) + names)


@pytest.mark.parametrize('count,string_size,payload,message', [
    (1025, 1, b'A', 'name table'),
    (65, 65535, bytes(65535), 'decoded font names exceed 4 MiB'),
    (1, 2, b'A', 'verified table'),
], ids=['too-many-names', 'aliased-expansion', 'out-of-table-string'])
def test_font_name_alias_expansion_and_ranges_are_bounded_before_parse(tmp_path, count, string_size, payload, message):
    from lumen import caption_render as captions
    path = tmp_path / 'names.ttf'
    _font_with_names(path, count, string_size, payload)
    assert path.stat().st_size < 70 * 1024
    with pytest.raises(captions.CaptionRenderError, match=message):
        captions._guarded_font_file(path, lambda: None)


def test_font_name_small_valid_table_is_kept_as_verified_bytes(tmp_path):
    from lumen import caption_render as captions
    path = tmp_path / 'names.ttf'
    _font_with_names(path, 1, 2, b'\x00A')
    with captions._guarded_font_file(path, lambda: None) as data:
        assert data.read() == path.read_bytes()
        assert data.name == str(path)


@pytest.fixture
def indirect_picture_project(tmp_path):
    from lumen.media import ffmpeg_available, ffmpeg_binary, probe
    from lumen.model import Project, Clip
    if not ffmpeg_available():
        pytest.skip('FFmpeg/ffprobe required for tiny indirect-dependency fixture')
    child = tmp_path / 'referenced-picture.mkv'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-y', '-threads', '1',
                    '-f', 'lavfi', '-i', 'color=c=red:s=64x48:r=24', '-frames:v', '3',
                    '-an', '-c:v', 'ffv1', '-threads', '1', str(child)],
                   check=True, capture_output=True, timeout=10)
    manifest = tmp_path / 'indirect.ffconcat'
    manifest.write_text("ffconcat version 1.0\nfile 'referenced-picture.mkv'\nduration 0.125\n")
    media = probe(manifest)
    return Project(width=64, height=48, fps_num=24, media=[media],
                   clips=[Clip('indirect', media.id, 0, 0, 0, 3)])


def test_indirect_picture_manifest_rejects_before_grade_ingress(indirect_picture_project, monkeypatch):
    from lumen import final_frame_preview as module
    monkeypatch.setattr(module, 'capture_grade_preview_snapshot',
                        lambda *a, **k: pytest.fail('Indirect media reached grade ingress'))
    with pytest.raises(module.FinalFramePreviewError, match='Build preview'):
        module.capture_final_frame_preview(indirect_picture_project, 0, timeout=10)


def test_dependency_revalidation_rejects_legacy_manifest_snapshot(indirect_picture_project):
    from lumen import final_frame_preview as module
    p = indirect_picture_project
    # This is the identity shape previously accepted by the preflight-free
    # implementation. Its manifest stat cannot prove its child-file pixels.
    dependencies = (('source', 'indirect', module._source_identity(p.media[0].path)),
                    ('lut', 'indirect', None))
    snapshot = module.FinalFrameSnapshot(64, 48, bytes(64 * 48 * 3), bytes(64 * 48 * 3 // 2),
        module.FinalFrameIdentity(module.project_signature(p), 0, dependencies),
        module.FinalFrameWork(('indirect',), (0,), 1, 0, 1, 0))
    assert not module.verify_final_frame_preview(snapshot, p, 0, timeout=10)


def test_source_allowlist_is_active_only_deduplicated_and_applied_on_revalidation(indirect_picture_project, monkeypatch):
    from lumen import final_frame_preview as module
    from lumen.media import probe
    from lumen.model import Project, Clip, Media
    from lumen.source_viewer import SAFE_INPUT_FORMATS
    indirect = indirect_picture_project.media[0]
    root = Path(indirect.path).parent
    active = probe(root / 'referenced-picture.mkv')
    missing_audio = Media('missing-audio', str(root / 'never-read.wav'), 'unused by still', 1,
                          has_video=False, has_audio=True)
    p = Project(width=64, height=48, fps_num=24, media=[active, indirect, missing_audio],
                clips=[Clip('bottom', active.id, 0, 0, 0, 3),
                       Clip('top', active.id, 1, 0, 0, 3),
                       Clip('unrelated-tail', indirect.id, 0, 20, 0, 3),
                       Clip('audio', missing_audio.id, 2, 0, 0, 3)])
    commands = []
    original = module._run
    def record(args, *rest):
        commands.append(args)
        return original(args, *rest)
    monkeypatch.setattr(module, '_run', record)
    snapshot = module.capture_final_frame_preview(p, 0, timeout=10)
    assert module.verify_final_frame_preview(snapshot, p, 0, timeout=10)
    probes = [cmd for cmd in commands if '-format_whitelist' in cmd]
    # One unique active source in each capture-before, capture-after and later
    # verification pass, even though both picture tracks use the same source.
    assert len(probes) == 3
    for cmd in probes:
        assert cmd[cmd.index('-format_whitelist') + 1] == SAFE_INPUT_FORMATS
        assert cmd[cmd.index('-protocol_whitelist') + 1] == 'file,pipe'
        assert cmd[cmd.index('-show_entries') + 1] == 'format=format_name'
        assert cmd[-1] == str(Path(active.path).resolve())
    assert all(indirect.path not in cmd and missing_audio.path not in cmd for cmd in commands)
    assert snapshot.work.active_clip_ids == ('bottom', 'top')
