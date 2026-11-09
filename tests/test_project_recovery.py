"""Data integrity, safe retention, process ownership and crash recovery."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from lumen.clip_transform import ClipTransform
from lumen.model import (Caption, CaptionStyle, Clip, ClipEffect, Grade, Media, Overlay,
                         OverlayKeyframe, Project, SoundProvenance, SpeechProvenance)
from lumen.project_recovery import (RecoveryBusyError, RecoveryCapacityError, RecoveryError,
                                    RecoveryStore)
from lumen.restoration_settings import RestorationSettings
from lumen.stabilization_settings import StabilizationSettings
from lumen.timing import ClipTimeMap


def full_project(root):
    speech = SpeechProvenance(text='Hello 世界', engine='local-test', engine_version='1',
        model_sha256='a'*64, voice_sha256='b'*64, assets_sha256='c'*64, voice_id='voice',
        speed=1.1, sample_rate=24000, created_utc='2026-10-22T00:00:00Z',
        source_caption_id='caption', source_start=12, source_duration=60,
        output_sha256='d'*64, silence_scale=.4, sample_count=48000)
    sound = SoundProvenance('sound', 'user-imported', 'user-supplied', 'e'*64, 'f'*64,
                            48000, 48000)
    mapping = ClipTimeMap(Fraction(1, 2), Fraction(181, 2), Fraction(3, 2))
    project = Project(name='项目 Hello', fps_num=30000, fps_den=1001,
        media=[Media('v', str(root/'video.mp4'), 'video', 12, 1920, 1080, True, True,
                     color_space='bt709', color_transfer='bt709', color_primaries='bt709',
                     color_range='tv', decoded_video_end_num=12, decoded_video_end_den=1),
               Media('speech', str(root/'voice.wav'), 'voice', 2, has_video=False,
                     has_audio=True, speech_provenance=speech),
               Media('sound', str(root/'sound.wav'), 'sound', 1, has_video=False,
                     has_audio=True, sound_provenance=sound)],
        clips=[Clip('video', 'v', 0, 3, 0, 60,
                    grade=Grade(lut_path=str(root/'grade.cube'), linear_exposure=.7,
                                gain=[1.1, 1, .9], lut_strength=.5),
                    gain_db=-3, muted=True, fade_in=4, fade_out=5,
                    effects=[ClipEffect('blur', 'blur', 1.5), ClipEffect('vignette','vignette',.3)],
                    time_map=mapping, transform=ClipTransform(23,-14,1.4,45,True,False),
                    stabilization=StabilizationSettings(True,.8,21,1.2,'black'),
                    restoration=RestorationSettings(True,.2,.3,.4)),
               Clip('voice-clip','speech',2,12,0,30), Clip('sound-clip','sound',3,8,0,20)],
        captions=[Caption('caption',12,60,'Hello 世界\nSecond line',language='zh')],
        caption_style=CaptionStyle(font_directory=str(root/'fonts'), max_lines=3),
        overlays=[Overlay('overlay',str(root/'sticker.png'),'a'*64,1,60,z_order=4,
                          keyframes=[OverlayKeyframe(0,12,34,.7,.8), OverlayKeyframe(40,56,78,1.2,.5)])])
    project.validate()
    return project


@pytest.fixture
def store(tmp_path):
    value = RecoveryStore(tmp_path/'recovery')
    yield value
    value.close()


def test_complete_schema_roundtrip_and_no_input_mutation_or_copy(store, tmp_path, monkeypatch):
    project = full_project(tmp_path)
    original_state = deepcopy(project.to_dict())
    original_file = tmp_path/'project.lumen'
    original_file.write_text('original project remains untouched', encoding='utf-8')
    for media in project.media:
        Path(media.path).write_bytes(b'original media remains untouched')
    monkeypatch.setattr(Project, 'save', lambda *_: pytest.fail('Recovery must never call Project.save'))
    identity = str(uuid4())
    entry = store.write(project, identity, original_file)
    assert store.load(entry).to_dict() == original_state
    assert project.to_dict() == original_state
    assert original_file.read_text() == 'original project remains untouched'
    assert all(Path(media.path).read_bytes() == b'original media remains untouched' for media in project.media)
    assert not list(store.root.glob('*.assets'))
    assert not list(store.root.glob('*.wav'))
    envelope = json.loads(entry.path.read_text())
    assert set(envelope) == {'format','version','identity','generation','created_utc','original_path','project'}
    assert envelope['project'] == original_state
    assert store.list_entries() == ([], [])  # A live session is not a crash.
    store.release(identity)
    assert store.list_entries() == ([entry], [])


def test_relative_inputs_use_original_project_folder_not_recovery_folder(store, tmp_path):
    project = full_project(tmp_path)
    for media in project.media:
        media.path = Path(media.path).name
    project.clips[0].grade.lut_path = 'looks/look.cube'
    project.overlays[0].asset_path = 'art/sticker.png'
    project.caption_style.font_directory = 'fonts'
    before = deepcopy(project)
    original = tmp_path/'originals'/'source.lumen'
    recovered = store.load(store.write(project, uuid4(), original))
    assert recovered.media[0].path == str(original.parent/'video.mp4')
    assert recovered.clips[0].grade.lut_path == str(original.parent/'looks'/'look.cube')
    assert recovered.overlays[0].asset_path == str(original.parent/'art'/'sticker.png')
    assert recovered.caption_style.font_directory == str(original.parent/'fonts')
    assert project == before


def test_unsaved_relative_inputs_use_cwd(store, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    project = Project(media=[Media('v','missing.mp4','missing',1)])
    entry = store.write(project, uuid4())
    assert entry.original_path is None
    assert store.load(entry).media[0].path == str(tmp_path/'missing.mp4')


@pytest.mark.parametrize('generations', [2, 3])
def test_retention_multiple_ids_and_no_unresolved_session_eviction(tmp_path, generations):
    with RecoveryStore(tmp_path/'r', generations=generations, max_sessions=2) as store:
        first, second, third = [str(uuid4()) for _ in range(3)]
        for index in range(9):
            latest = store.write(Project(name=f'edit-{index}'), first)
        assert len(list(store.root.glob(f'cutcat-{first}.*.json'))) == generations
        other = store.write(Project(name='other session'), second)
        with pytest.raises(RecoveryCapacityError):
            store.write(Project(name='too many'), third)
        assert other.path.exists() and latest.path.exists()
        store.write(Project(name='existing may continue'), first)
        store.release(first)
        store.release(second)
        entries, warnings = store.list_entries()
        assert not warnings and {e.identity for e in entries} == {first, second}
        store.discard(second)
        assert not other.path.exists()
        assert store.write(Project(name='now fits'), third).path.exists()


def test_corrupt_newest_falls_back_and_interrupted_temp_is_not_a_project(store):
    identity = str(uuid4())
    first = store.write(Project(name='safe'), identity)
    second = store.write(Project(name='interrupted'), identity)
    second.path.write_text('{"partial":', encoding='utf-8')
    (store.root/f'.cutcat-{identity}.{uuid4().hex}.tmp').write_text('partial')
    unrelated = store.root/'personal-note.json'
    unrelated.write_text('preserve me')
    store.release(identity)
    entries, warnings = store.list_entries()
    assert entries == [first] and len(warnings) == 1
    assert store.load(entries[0]).name == 'safe'
    store.discard(identity)
    assert unrelated.read_text() == 'preserve me'
    assert not list(store.root.glob(f'cutcat-{identity}.*.json'))
    assert not list(store.root.glob(f'.cutcat-{identity}.*.tmp'))


def test_all_corrupt_sessions_are_explicitly_identified_and_discardable(store):
    identity = str(uuid4())
    entry = store.write(Project(), identity)
    entry.path.write_text('invalid JSON')
    store.release(identity)
    entries, warnings = store.list_entries()
    assert entries == [] and warnings
    assert store.unrecoverable_identities == (identity,)
    store.discard(identity)
    assert store.list_entries() == ([], [])
    assert store.unrecoverable_identities == ()


@pytest.mark.parametrize('bad', ['../outside', '/absolute', '', 'not-a-uuid', '1'*32, 1, None])
def test_identity_traversal_and_noncanonical_identity_rejected(store, bad):
    with pytest.raises(RecoveryError):
        store.write(Project(), bad)
    with pytest.raises(RecoveryError):
        store.discard(bad)
    assert not store.root.exists()


def test_forged_entry_cannot_read_or_delete_outside_store(store, tmp_path):
    entry = store.write(Project(), uuid4())
    outside = tmp_path/'outside.json'
    outside.write_text('private data')
    with pytest.raises(RecoveryError, match='outside'):
        store.load(replace(entry, path=outside))
    assert outside.read_text() == 'private data'


def symlink_or_skip(source, target, *, directory=False):
    try:
        source.symlink_to(target, target_is_directory=directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f'Symlink creation unavailable: {exc}')


def test_root_symlink_is_rejected_lazily(tmp_path):
    outside = tmp_path/'outside'
    outside.mkdir()
    link = tmp_path/'link'
    symlink_or_skip(link, outside, directory=True)
    with RecoveryStore(link) as store:
        with pytest.raises(RecoveryError):
            store.write(Project(), uuid4())
        with pytest.raises(RecoveryError):
            store.list_entries()
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize('kind', ['symlink', 'hardlink'])
def test_snapshot_links_never_read_or_discard_targets(store, tmp_path, kind):
    identity = str(uuid4())
    entry = store.write(Project(), identity)
    entry.path.unlink()
    outside = tmp_path/'private.json'
    outside.write_text('private non-project content')
    if kind == 'symlink':
        symlink_or_skip(entry.path, outside)
    else:
        try:
            os.link(outside, entry.path)
        except OSError as exc:
            pytest.skip(f'Hardlink unavailable: {exc}')
    store.release(identity)
    entries, warnings = store.list_entries()
    assert not entries and warnings
    with pytest.raises(RecoveryError):
        store.load(entry)
    with pytest.raises(RecoveryError):
        store.discard(identity)
    assert outside.read_text() == 'private non-project content'
    assert entry.path.exists()


def test_lock_symlink_never_writes_target(store, tmp_path):
    store.root.mkdir()
    identity = str(uuid4())
    outside = tmp_path/'private.txt'
    outside.write_text('secret remains')
    symlink_or_skip(store.root/f'.cutcat-{identity}.lock', outside)
    with pytest.raises(RecoveryError):
        store.acquire(identity)
    assert outside.read_text() == 'secret remains'


def test_managed_directory_substitution_is_detected(store, tmp_path):
    entry = store.write(Project(), uuid4())
    previous = tmp_path/'previous'
    try:
        store.root.rename(previous)
    except OSError as exc:
        pytest.skip(f'Open directory rename unavailable: {exc}')
    store.root.mkdir()
    with pytest.raises(RecoveryError, match='changed'):
        store.write(Project(), uuid4())
    assert list(store.root.iterdir()) == []
    assert (previous/entry.path.name).exists()


@pytest.mark.parametrize('mutation', ['unknown', 'relative', 'identity', 'version', 'duplicate', 'nan', 'deep'])
def test_strict_schema_rejects_corrupt_or_untrusted_envelopes(store, mutation):
    identity = str(uuid4())
    entry = store.write(Project(media=[Media('v',str(store.root.parent/'missing.mp4'),'v',1)]), identity)
    data = json.loads(entry.path.read_text())
    if mutation == 'unknown': data['api_key'] = 'must-not-be-accepted'
    if mutation == 'relative': data['project']['media'][0]['path'] = '../outside.mp4'
    if mutation == 'identity': data['identity'] = str(uuid4())
    if mutation == 'version': data['version'] = True
    payload = json.dumps(data)
    if mutation == 'duplicate': payload = payload.replace('"version": 1,', '"version": 1, "version": 1,', 1)
    if mutation == 'nan': payload = payload.replace('"width": 1280', '"width": NaN')
    if mutation == 'deep': payload = '['*1100 + '0' + ']'*1100
    entry.path.write_text(payload)
    store.release(identity)
    assert store.list_entries()[0] == []
    with pytest.raises(RecoveryError):
        store.load(entry)
    with RecoveryStore(store.root) as other:
        assert other.acquire(identity)  # A failed load must release its new lease.


def test_snapshot_size_is_bounded_before_read(store, monkeypatch):
    entry = store.write(Project(), uuid4())
    import lumen.project_recovery as recovery
    monkeypatch.setattr(recovery, 'MAX_SNAPSHOT_BYTES', 10)
    with pytest.raises(RecoveryError, match='32 MiB'):
        store.load(entry)
    with pytest.raises(RecoveryError, match='32 MiB'):
        store.write(Project(), uuid4())


@pytest.mark.parametrize('failed_step', ['replace', 'fsync'])
def test_failed_write_preserves_previous_generation_and_original(store, tmp_path, monkeypatch, failed_step):
    identity = str(uuid4())
    original = tmp_path/'original.lumen'
    original.write_text('original')
    first = store.write(Project(name='safe'), identity, original)
    real = getattr(os, failed_step)
    def failure(*args, **kwargs):
        raise OSError('simulated full disk')
    monkeypatch.setattr(os, failed_step, failure)
    with pytest.raises(OSError, match='full disk'):
        store.write(Project(name='not committed'), identity, original)
    monkeypatch.setattr(os, failed_step, real)
    assert store.load(first).name == 'safe'
    assert original.read_text() == 'original'
    assert not list(store.root.glob('*.tmp'))
    assert len(list(store.root.glob('*.json'))) == 1


def test_active_session_claim_load_and_discard_are_exclusive(store):
    identity = str(uuid4())
    entry = store.write(Project(name='active'), identity)
    with RecoveryStore(store.root) as other:
        assert not other.claim(identity)
        assert other.list_entries() == ([], [])
        assert other.list_entries(include_active=True)[0] == [entry]
        with pytest.raises(RecoveryBusyError): other.load(entry)
        with pytest.raises(RecoveryBusyError): other.discard(identity)
        with pytest.raises(RecoveryBusyError): other.write(Project(), identity)
        store.release(identity)
        assert other.load(entry).name == 'active'
        assert not store.acquire(identity)
        other.release(identity)
        assert store.acquire(identity)


def test_empty_store_read_and_discard_do_not_create_directory_or_locks(store):
    assert store.list_entries() == ([], [])
    store.discard(uuid4())
    assert not store.root.exists()
    store.root.mkdir()
    store.discard(uuid4())
    assert list(store.root.iterdir()) == []


def test_live_child_hidden_then_abnormal_exit_releases_lock_and_preserves_snapshot(tmp_path):
    root = tmp_path/'process-recovery'
    identity = str(uuid4())
    code = '''
import os, sys
from lumen.model import Project
from lumen.project_recovery import RecoveryStore
store = RecoveryStore(sys.argv[1])
entry = store.write(Project(name="crash survivor"), sys.argv[2])
print("ready", flush=True)
sys.stdin.readline()
os._exit(23)
'''
    process = subprocess.Popen([sys.executable,'-c',code,str(root),identity],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'ready'
        with RecoveryStore(root) as parent:
            assert parent.list_entries() == ([], [])
            assert not parent.claim(identity)
            process.communicate('\n', timeout=15)
            assert process.returncode == 23
            entries, warnings = parent.list_entries()
            assert len(entries) == 1 and not warnings
            assert parent.load(entries[0]).name == 'crash survivor'
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=15)


def test_process_exit_mid_replace_leaves_prior_generation_recoverable(tmp_path):
    root = tmp_path/'crash'
    identity = str(uuid4())
    with RecoveryStore(root) as store:
        first = store.write(Project(name='last complete'), identity)
    code = '''
import os, sys
from lumen.model import Project
from lumen.project_recovery import RecoveryStore
def interrupted(*args, **kwargs):
    os._exit(31)
os.replace = interrupted
store = RecoveryStore(sys.argv[1])
store.write(Project(name="interrupted"), sys.argv[2])
'''
    process = subprocess.run([sys.executable,'-c',code,str(root),identity], timeout=15)
    assert process.returncode == 31
    with RecoveryStore(root) as restarted:
        entries, warnings = restarted.list_entries()
        assert entries == [first] and not warnings
        assert restarted.load(entries[0]).name == 'last complete'
        assert list(root.glob('*.tmp'))
        restarted.discard(identity)
        assert not list(root.glob('*.tmp'))


@pytest.mark.parametrize('kind', ['original', 'media', 'lut', 'overlay', 'font', 'font_parent'])
def test_inputs_cannot_become_retention_victims(store, tmp_path, kind):
    identity = str(uuid4())
    store.root.mkdir()
    protected = store.root/f'cutcat-{identity}.00000000000000000001.json'
    protected.write_text('irreplaceable input')
    project = full_project(tmp_path)
    original = None
    if kind == 'original': original = protected
    if kind == 'media': project.media[0].path = str(protected)
    if kind == 'lut': project.clips[0].grade.lut_path = str(protected)
    if kind == 'overlay': project.overlays[0].asset_path = str(protected)
    if kind == 'font': project.caption_style.font_directory = str(store.root/'fonts')
    if kind == 'font_parent': project.caption_style.font_directory = str(tmp_path)
    with pytest.raises(RecoveryError, match='recovery directory|font directory'):
        store.write(project, identity, original)
    assert protected.read_text() == 'irreplaceable input'
    assert list(store.root.iterdir()) == [protected]
