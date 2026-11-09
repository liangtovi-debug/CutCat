"""Independent frozen-full-renderer final-preencoding exact-byte acceptance.

These are real FFmpeg/QPainter inputs, not mocked stages. Two separate children
compare immutable full-render code with current still code on the same verified
FFmpeg 7/caption runtime. Production 9 integration has separate coverage. Every
yuv420p code must match; no H.264 tolerance or cross-runtime equality claim.
"""
from fractions import Fraction
import hashlib

import numpy as np
import pytest

from lumen.media import ffmpeg_available
from scripts.benchmark_final_frame_preview import frozen_baseline, make_scene, make_source
from scripts.final_frame_code_oracle import frozen_full_pixels, candidate_snapshots

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg required')
RATES = [Fraction(24), Fraction(25), Fraction(24000, 1001), Fraction(30000, 1001)]


@pytest.fixture(scope='module', params=RATES, ids=['24', '25', '23.976', '29.97'])
def fixture_source(request, tmp_path_factory):
    root = tmp_path_factory.mktemp('final-frame-exact')
    return root, request.param, make_source(root, request.param)


@pytest.mark.parametrize('variant', ['legacy', 'static', 'complex', 'fractional', 'fractional_slow', 'fractional_fast'])
def test_exact_full_composite_preencoding_bytes(fixture_source, variant):
    root, rate, source = fixture_source
    p = make_scene(root / variant, rate, source=source, variant=variant)
    expected = frozen_full_pixels(p, root / variant / 'full-oracle')
    size = p.width * p.height * 3 // 2
    # All frames include both gap sides, every start/end half-open boundary,
    # hidden motion handles, hold endpoints, alpha=0 and sticker frame changes.
    frames = range(p.duration_frames)
    snapshots = candidate_snapshots(p, frames, root / variant / 'still-oracle',
                                    reference_directory=root / variant / 'full-oracle')
    for frame, result in zip(frames, snapshots):
        want = expected[frame*size:(frame+1)*size]
        actual = result.yuv420p
        a, b = np.frombuffer(actual, np.uint8), np.frombuffer(want, np.uint8)
        assert len(actual) == size
        assert actual == want, (
            f'{variant} {rate} frame={frame}: mismatches={np.count_nonzero(a != b)}, '
            f'max_code_error={np.max(np.abs(a.astype(int)-b.astype(int)))}, '
            f'candidate={hashlib.sha256(actual).hexdigest()}, oracle={hashlib.sha256(want).hexdigest()}')
        assert len(result.rgb_bytes) == p.width*p.height*3
        assert result.width == p.width and result.height == p.height


def test_frozen_renderer_is_not_candidate_frame_code():
    root = frozen_baseline()
    assert not (root/'lumen/render.py').read_text().find('capture_final_frame') >= 0


def test_composite_fixture_exposes_every_feature_omission(tmp_path):
    """A degenerate fixture must not let omitted visual stages pass unnoticed."""
    from copy import deepcopy
    from lumen.clip_composite import ClipComposite
    from lumen.clip_transform import ClipTransform
    from lumen.model import Grade
    p = make_scene(tmp_path/'fixture', Fraction(24))
    size = p.width*p.height*3//2
    expected = frozen_full_pixels(p, tmp_path/'reference')[5*size:6*size]
    for omit in ('grade', 'effects', 'motion_alpha', 'stickers', 'captions'):
        changed = deepcopy(p)
        if omit == 'grade': changed.clips[1].grade = Grade()
        elif omit == 'effects': changed.clips[1].effects = []
        elif omit == 'motion_alpha':
            changed.clips[1].motion_keyframes = []
            changed.clips[1].composite = ClipComposite()
            changed.clips[1].transform = ClipTransform()
        elif omit == 'stickers': changed.overlays = [o for o in changed.overlays if not o.enabled]
        elif omit == 'captions': changed.captions = []
        wrong = frozen_full_pixels(changed, tmp_path/omit)[5*size:6*size]
        assert wrong != expected, f'Fixture does not expose omitted {omit}'


@pytest.mark.parametrize('mode', ['legacy_blur', 'composite_default_vignette'])
def test_default_effect_precision_paths(fixture_source, mode):
    from lumen.model import ClipEffect
    root, rate, source = fixture_source
    p = make_scene(root/mode, rate, source=source,
                   variant='legacy' if mode == 'legacy_blur' else 'complex')
    if mode == 'legacy_blur':
        p.clips[0].effects.insert(0, ClipEffect('blur', 'blur', .8))
    else:
        p.clips[1].effects[-1].radius = p.clips[1].effects[-1].feather = 1.
    raw = frozen_full_pixels(p, root/mode/'full-oracle')
    size = p.width*p.height*3//2
    result, = candidate_snapshots(p, [5], root/mode/'still-oracle',
                                  reference_directory=root/mode/'full-oracle')
    assert result.yuv420p == raw[5*size:6*size]


@pytest.mark.parametrize('transfer,encoding', [('bt709','rec709'),
    ('iec61966-2-1','srgb'), ('linear','linear_rec709')])
def test_restoration_spatial_grade_and_sdr_input_transfer_are_exact(tmp_path, transfer, encoding):
    from lumen.restoration_settings import RestorationSettings
    rate = Fraction(24000,1001)
    source = make_source(tmp_path/'source', rate, transfer=transfer)
    p = make_scene(tmp_path/'scene', rate, source=source, variant='fractional_slow')
    p.height = 64  # Actual fit/pad geometry: restoration must exclude black bars.
    for clip in p.clips: clip.grade.input_encoding = encoding
    p.clips[1].restoration = RestorationSettings(enabled=True, deblock=.6, denoise=.7, sharpen=.4)
    p.clips[1].grade.qualifier.matte_smoothing = .7
    p.clips[1].grade.qualifier.matte_morphology = .45
    raw = frozen_full_pixels(p, tmp_path/'full-oracle')
    size = p.width*p.height*3//2
    frames = (5, 7)
    snapshots = candidate_snapshots(p, frames, tmp_path/'still-oracle',
                                    reference_directory=tmp_path/'full-oracle')
    for frame, result in zip(frames, snapshots):
        assert result.yuv420p == raw[frame*size:(frame+1)*size], (transfer, frame)


def test_fresh_extraction_frozen_caption_fallback_matches_baseline(tmp_path):
    from scripts.benchmark_final_frame_preview import ROOT
    p = make_scene(tmp_path/'scene', Fraction(25))
    reference = frozen_full_pixels(p, tmp_path/'reference')
    standalone = frozen_full_pixels(p, tmp_path/'standalone', baseline=ROOT)
    assert standalone == reference


def test_cancel_during_second_fullhd_grade_reaps_children_and_cleans(tmp_path, monkeypatch):
    """Cancellation after actual 1080p project work starts, not startup only."""
    import json
    import subprocess
    import tempfile
    import threading
    import time
    import lumen.color_picker_source as ingress
    from lumen.final_frame_preview import capture_final_frame_snapshot, FinalFramePreviewCancelled
    source = make_source(tmp_path/'source', Fraction(24), width=1920, height=1080, count=12)
    p = make_scene(tmp_path/'scene', Fraction(24), width=1920, height=1080, length=10, source=source)
    requests = tmp_path/'requests'; requests.mkdir()
    monkeypatch.setattr(tempfile, 'tempdir', str(requests))
    cancel = threading.Event(); children=[]; calls=[]; canceled=[]; timers=[]
    original_popen, original_grade = subprocess.Popen, ingress.apply_grade
    def start(command, *args, **kwargs):
        child = original_popen(command, *args, **kwargs); children.append(child); return child
    def stop(): canceled.append(time.perf_counter()); cancel.set()
    def grade(pixels, *args, **kwargs):
        calls.append(list(pixels.shape))
        if len(calls) == 2:
            timer=threading.Timer(.05, stop); timers.append(timer); timer.start()
        return original_grade(pixels, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', start)
    monkeypatch.setattr(ingress, 'apply_grade', grade)
    began=time.perf_counter()
    try:
        with pytest.raises(FinalFramePreviewCancelled):
            capture_final_frame_snapshot(p, 5, cancel=cancel)
        finished=time.perf_counter()
    finally:
        for timer in timers: timer.cancel(); timer.join()
    assert calls == [[1080,1920,3], [1080,1920,3]]
    assert canceled and finished-canceled[0] < 2
    assert not list(requests.iterdir())
    assert all(child.poll() is not None for child in children)
    report=dict(stage='second actual 1920x1080 project grade', grade_shapes=calls,
                cancel_to_return_seconds=finished-canceled[0], elapsed_seconds=finished-began,
                children=len(children), all_reaped=True, request_directory_empty=True)
    (tmp_path/'active-grade-cancel.json').write_text(json.dumps(report, indent=2))


def test_pure_motion_matches_frozen_actual_production_thread_selector(tmp_path):
    """Pure motion render uses 2/4 threads; candidate's scalar still must match."""
    import json
    import os
    p = make_scene(tmp_path/'scene', Fraction(30000,1001))
    p.overlays = []; p.captions = []
    for clip in p.clips:
        clip.effects = []
        clip.grade.qualifier.window_keyframes = []
    p.validate()
    raw = frozen_full_pixels(p, tmp_path/'full-oracle', production_threads=True)
    receipt = json.loads((tmp_path/'full-oracle/reference.json').read_text())
    threads = receipt['filter_complex_threads']
    assert threads == (4 if (os.cpu_count() or 1)>=4 else 2 if (os.cpu_count() or 1)>=2 else 1)
    size = p.width*p.height*3//2
    assert len(raw)==p.duration_frames*size
    frames = (3, 5, 7, 9)
    snapshots = candidate_snapshots(p, frames, tmp_path/'still-oracle',
                                    reference_directory=tmp_path/'full-oracle')
    for frame, result in zip(frames, snapshots):
        assert result.yuv420p == raw[frame*size:(frame+1)*size]


def test_chinese_caption_ttc_extraction_matches_frozen_pixels(tmp_path):
    """Real CJK collection face extraction, not merely Chinese UI strings."""
    from pathlib import Path
    from lumen.caption_render import CaptionRenderError, font_identity
    from lumen.model import CaptionStyle
    style=CaptionStyle(font_family='Noto Sans CJK SC',font_size=10,margin_bottom=3,outline_width=0)
    try:
        identity=font_identity(style)
    except CaptionRenderError:
        pytest.skip('Installed Noto Sans CJK SC TTC fixture unavailable')
    if Path(identity[0]).suffix.lower() not in ('.ttc','.otc'):
        pytest.skip('Noto Sans CJK SC resolved to a non-collection font')
    p=make_scene(tmp_path/'scene',Fraction(25));p.caption_style=style
    p.captions[1].text='剪辑猫'
    raw=frozen_full_pixels(p,tmp_path/'full-oracle');size=p.width*p.height*3//2
    result, = candidate_snapshots(p, [5], tmp_path/'still-oracle',
                                  reference_directory=tmp_path/'full-oracle')
    assert result.yuv420p == raw[5*size:6*size]
