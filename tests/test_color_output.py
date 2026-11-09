"""Delivery policy and fail-closed publication, independent of encoder availability."""
import json
from types import SimpleNamespace

import pytest

from lumen.color_output import validate_export_profile
from lumen.model import Project
from lumen.render import RenderEngine, RenderError


def test_default_and_explicit_profiles_do_not_mutate_project():
    project = Project()
    before = project.to_dict()
    assert validate_export_profile(project).pixel_format == 'yuv420p'
    high = validate_export_profile(project, 'prores_422_hq_10bit')
    assert high.pixel_format == 'yuv422p10le' and high.suffixes == ('.mov',)
    assert project.to_dict() == before


@pytest.mark.parametrize('value', [None, False, [], {}, 10, 'prores', 'hdr'])
def test_unknown_profiles_are_not_silently_downgraded(value):
    with pytest.raises(ValueError, match='Unknown export profile'):
        validate_export_profile(Project(), value)


def metadata():
    return {'streams': [{'codec_name': 'prores', 'profile': 'HQ', 'pix_fmt': 'yuv422p10le',
                         'color_space': 'bt709', 'color_transfer': 'bt709', 'color_primaries': 'bt709'}],
            'frames': [{'pix_fmt': 'yuv422p10le', 'color_range': 'tv'}]}


@pytest.mark.parametrize('section,key,value', [
    ('streams', 'codec_name', 'h264'), ('streams', 'profile', 'Standard'),
    ('streams', 'pix_fmt', 'yuv420p'), ('streams', 'color_space', 'bt2020nc'),
    ('streams', 'color_transfer', 'smpte2084'), ('streams', 'color_primaries', 'bt2020'),
    ('frames', 'color_range', 'pc'), ('frames', 'pix_fmt', 'yuv420p')])
def test_external_encoder_contract_mismatch_rejected(monkeypatch, section, key, value):
    data = metadata(); data[section][0][key] = value
    monkeypatch.setattr('lumen.render.subprocess.run', lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(data)))
    engine = RenderEngine()
    try:
        with pytest.raises(RenderError, match='no export was published'):
            engine._verify_highbit_output('unused.mov')
    finally: engine.close()


def test_mov_range_is_verified_on_decoded_frame_not_invented_stream_tag(monkeypatch):
    data = metadata()
    assert 'color_range' not in data['streams'][0]
    def run(command, **kwargs):
        assert command[command.index('-read_intervals') + 1] == '%+#1'
        assert '-show_frames' in command
        assert kwargs['timeout'] <= 30
        return SimpleNamespace(returncode=0, stdout=json.dumps(data))
    monkeypatch.setattr('lumen.render.subprocess.run', run)
    engine = RenderEngine()
    try: engine._verify_highbit_output('unused.mov')
    finally: engine.close()


@pytest.mark.parametrize('data', [{}, {'streams': []}, {'streams': [None]},
                                 {'streams': [{}], 'frames': []}])
def test_malformed_verification_cannot_publish(monkeypatch, data):
    monkeypatch.setattr('lumen.render.subprocess.run', lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(data)))
    engine = RenderEngine()
    try:
        with pytest.raises(RenderError, match='verification failed'):
            engine._verify_highbit_output('unused.mov')
    finally: engine.close()


@pytest.mark.parametrize('stdout,returncode', [('', 0), ('Unknown encoder prores_ks', 0),
                                             ('Encoder prores_ks yuv422p10le', 1)])
def test_missing_encoder_never_falls_back(monkeypatch, stdout, returncode):
    monkeypatch.setattr('lumen.render.subprocess.run', lambda *a, **k: SimpleNamespace(returncode=returncode, stdout=stdout))
    engine = RenderEngine()
    try:
        with pytest.raises(RenderError, match='lacks the required'):
            engine._ensure_highbit_encoder()
    finally: engine.close()


def test_encoder_contract_supported(monkeypatch):
    monkeypatch.setattr('lumen.render.subprocess.run', lambda *a, **k: SimpleNamespace(returncode=0, stdout='Encoder prores_ks\nSupported pixel formats: yuv422p10le'))
    engine = RenderEngine()
    try: engine._ensure_highbit_encoder()
    finally: engine.close()


def test_cancel_during_highbit_verification_never_publishes(tmp_path, monkeypatch):
    import threading
    import numpy as np
    from lumen.media import ffmpeg_available
    from lumen.render import RenderCancelled
    from test_color_source_render import rgb_source, project
    if not ffmpeg_available(): pytest.skip('FFmpeg/ffprobe required')
    media = rgb_source(tmp_path, np.full((48, 64, 3), 30000, dtype=np.uint16))
    p = project(media)
    cancel = threading.Event()
    engine = RenderEngine(timeout=30)
    original = engine._verify_highbit_output
    def verify(path):
        original(path)
        cancel.set()
    monkeypatch.setattr(engine, '_verify_highbit_output', verify)
    target = tmp_path / 'cancelled.mov'
    try:
        with pytest.raises(RenderCancelled):
            engine.render(p, target, cancel=cancel, export_profile='prores_422_hq_10bit')
        assert not target.exists()
        assert not list(tmp_path.glob('.lumen-render-*'))
    finally: engine.close()
