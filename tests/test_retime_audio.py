"""Real PCM speed tests: exact edges/pitch, with explicit time-stretcher limitations.

Independent Rubber Band instances are not a phase-continuity or lossless-PCM
oracle. The compensated short-window pipeline is tested against measured
landmarks; these fixtures are not a universal guarantee for all transients. AAC priming is tested separately from PCM.
"""
from fractions import Fraction
import math
import subprocess
from types import SimpleNamespace

import numpy as np
import pytest

from lumen.audio_timing import compile_audio_timing, SAMPLE_RATE
from lumen.media import ffmpeg_available, ffmpeg_binary
from lumen.model import Clip, Editor, Media, Project
from lumen.timing import ClipTimeMap, duration_frames, round_half_up, sample_boundary


FPS = (Fraction(24), Fraction(30), Fraction(30000, 1001), Fraction(24000, 1001))
SPEEDS = (Fraction(1, 4), Fraction(1, 2), Fraction(2, 3), Fraction(1),
          Fraction(3, 2), Fraction(2), Fraction(4))
real = pytest.mark.skipif(not ffmpeg_available(), reason='FFmpeg/ffprobe required')


def clip_for(speed=Fraction(1), *, start=7, origin=Fraction(0), endpoint=Fraction(60)):
    mapping = ClipTimeMap(origin, endpoint, speed)
    return SimpleNamespace(timing=mapping, start=start, duration=duration_frames(mapping))


def run_pcm(source, plan, *, extra=(), input_rate=SAMPLE_RATE):
    assert not plan.silent
    result = subprocess.run(
        [ffmpeg_binary(), '-hide_banner', '-v', 'error', '-nostdin',
         '-f', 'f32le', '-ar', str(input_rate), '-ac', '1', '-i', 'pipe:0',
         '-af', ','.join([*plan.filters, *extra]), '-ar', str(SAMPLE_RATE),
         '-ac', '1', '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1'],
        input=np.asarray(source, dtype='<f4').tobytes(), capture_output=True,
        timeout=20, check=True)
    return np.frombuffer(result.stdout, dtype='<f4')


def tone(frequency, count, rate=SAMPLE_RATE):
    return (0.5 * np.sin(2 * np.pi * frequency * np.arange(count) / rate)).astype('<f4')


def dominant_hz(samples):
    spectrum = np.abs(np.fft.rfft(samples * np.hanning(len(samples))))
    return np.argmax(spectrum) * SAMPLE_RATE / len(samples)


@pytest.mark.parametrize('fps', FPS)
@pytest.mark.parametrize('speed', SPEEDS)
def test_plan_has_global_sample_edges_and_distinct_source_endpoint(fps, speed):
    clip = clip_for(speed, origin=Fraction(5, 3), endpoint=Fraction(65, 2))
    plan = compile_audio_timing(clip, fps)
    assert plan.start_sample == sample_boundary(7, fps)
    assert plan.sample_count == sample_boundary(7 + clip.duration, fps) - plan.start_sample
    assert plan.source_start_sample == round_half_up(clip.timing.source_in / fps * SAMPLE_RATE)
    assert plan.source_end_sample == round_half_up(clip.timing.source_out / fps * SAMPLE_RATE)
    assert f'atrim=end_sample={plan.sample_count}' in plan.filters
    factors = [Fraction(item.split(':')[0].split('=')[-1])
               for item in plan.filters if item.startswith('rubberband=tempo=')]
    assert all(Fraction(1, 2) <= factor <= 2 for factor in factors)
    assert math.prod(factors) == pytest.approx(float(speed), abs=1e-15)
    assert bool(factors) == (speed != 1)
    assert not any(item.startswith('atempo=') for item in plan.filters)
    assert plan.filters.count('adelay=512S:all=1') == len(factors)
    assert plan.filters.count('atrim=start_sample=512') == len(factors)


@pytest.mark.parametrize('fps', FPS)
def test_global_adjacent_budgets_telescope_without_rounding_drift(fps):
    # Odd cuts across nearly an hour of timeline. The endpoint of every span is
    # precisely the beginning of the next, including at 30000/1001 fps.
    edges = [0, 7, 14, 31, 133, 997, 8101, 44003, 100001]
    budgets = [compile_audio_timing(clip_for(start=a, endpoint=Fraction(b-a)), fps)
               for a, b in zip(edges, edges[1:])]
    assert sum(p.sample_count for p in budgets) == sample_boundary(edges[-1], fps)
    assert all(a.start_sample + a.sample_count == b.start_sample
               for a, b in zip(budgets, budgets[1:]))


@pytest.mark.parametrize('speed', SPEEDS)
def test_late_audio_is_scaled_by_speed_and_quantized_globally(speed):
    fps = Fraction(30000, 1001)
    clip = clip_for(speed, origin=Fraction(5))
    offset = Fraction(1, 2)
    plan = compile_audio_timing(clip, fps, offset)
    onset = Fraction(clip.start) / fps + (offset - clip.timing.source_in / fps) / speed
    assert plan.leading_samples == round_half_up(onset * SAMPLE_RATE) - plan.start_sample
    assert plan.source_start_sample == 0
    negative = compile_audio_timing(clip, fps, Fraction(-1, 2))
    assert negative.leading_samples == 0
    assert negative.source_start_sample == round_half_up((Fraction(5) / fps + Fraction(1, 2)) * SAMPLE_RATE)


@pytest.mark.parametrize('offset', [3, 30, Fraction(1000001, 2)])
def test_no_intersecting_audio_returns_silence_plan(offset):
    result = compile_audio_timing(clip_for(), Fraction(30), offset)
    assert result.silent and result.filters == ()


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), float('-inf'), True, '1/2'])
def test_invalid_offsets_are_rejected(bad):
    with pytest.raises(ValueError, match='finite'):
        compile_audio_timing(clip_for(), Fraction(30), bad)


@real
@pytest.mark.parametrize('fps', FPS)
@pytest.mark.parametrize('speed', SPEEDS)
@pytest.mark.parametrize('frequency', [440, 1000])
def test_real_tones_keep_pitch_and_exact_global_pcm_duration(fps, speed, frequency):
    clip = clip_for(speed)
    plan = compile_audio_timing(clip, fps)
    samples = run_pcm(tone(frequency, SAMPLE_RATE * 3), plan)
    assert len(samples) == plan.sample_count
    # The first 1/5 through 3/5 is active at every tested rate and frame budget.
    body = samples[len(samples) // 5:3 * len(samples) // 5]
    assert np.sqrt(np.mean(body ** 2)) > .15
    assert dominant_hz(body) == pytest.approx(frequency, abs=6)


@real
@pytest.mark.parametrize('speed', SPEEDS)
def test_real_late_stream_onset_and_exact_timeline_placement(speed):
    fps = Fraction(30000, 1001)
    clip = clip_for(speed, start=19, origin=Fraction(5), endpoint=Fraction(125, 2))
    plan = compile_audio_timing(clip, fps, Fraction(1, 2))
    samples = run_pcm(tone(1000, SAMPLE_RATE * 3), plan,
                      extra=(f'adelay={plan.start_sample}S:all=1',))
    expected = plan.start_sample + plan.leading_samples
    assert len(samples) == plan.start_sample + plan.sample_count
    assert np.max(np.abs(samples[:expected]), initial=0) == 0
    first_active = np.flatnonzero(np.abs(samples) > .01)[0]
    assert expected <= first_active <= expected + 2


@real
def test_real_one_x_preserves_selected_pcm_and_pads_partial_picture_frame():
    fps = Fraction(30000, 1001)
    clip = clip_for(origin=Fraction(5, 3), endpoint=Fraction(95, 3))
    plan = compile_audio_timing(clip, fps)
    source = tone(440, 2 * SAMPLE_RATE)
    samples = run_pcm(source, plan)
    selected = source[plan.source_start_sample:plan.source_end_sample]
    assert len(samples) == plan.sample_count
    np.testing.assert_allclose(samples[:min(len(selected), len(samples))],
                               selected[:len(samples)], rtol=2e-7, atol=2e-7)
    assert np.max(np.abs(samples[len(selected):]), initial=0) == 0


@real
@pytest.mark.parametrize('fps', [Fraction(24000, 1001), Fraction(30000, 1001)],
                         ids=['23.976', '29.97'])
@pytest.mark.parametrize('speed', [Fraction(1, 2), Fraction(1), Fraction(3, 2), Fraction(4)],
                         ids=['half', 'one', 'one-and-half', 'four'])
def test_exact_slip_changes_selected_pcm_without_extending_partial_source_tail(fps, speed):
    origin, endpoint, new_origin = Fraction(5, 4), Fraction(259, 8), Fraction(181, 4)
    mapping = ClipTimeMap(origin, endpoint, speed)
    clip = Clip('audio', 'source', 2, 7, 0, math.ceil((endpoint - origin) / speed),
                time_map=mapping)
    media = Media('source', '/tmp/slip.wav', 'Synthetic PCM', 4,
                  has_video=False, has_audio=True)
    editor = Editor(Project(fps_num=fps.numerator, fps_den=fps.denominator,
                            media=[media], clips=[clip]))
    before = compile_audio_timing(clip, fps)
    editor.slip_clip('audio', new_origin)
    shifted = editor.project.clips[0]
    after = compile_audio_timing(shifted, fps)
    shifted_endpoint = endpoint + new_origin - origin
    # Independent exact rounding of absolute source boundaries; keep the
    # timeline sample budget unchanged, including its fractional final frame.
    first = math.floor(new_origin / fps * SAMPLE_RATE + Fraction(1, 2))
    last = math.floor(shifted_endpoint / fps * SAMPLE_RATE + Fraction(1, 2))
    assert (after.source_start_sample, after.source_end_sample) == (first, last)
    assert (after.start_sample, after.sample_count) == (before.start_sample, before.sample_count)
    assert shifted.source_out - shifted.source_in == endpoint - origin
    assert shifted.source_out - shifted.source_in < shifted.duration * speed
    source = np.zeros(SAMPLE_RATE * 4, dtype='<f4')
    source[before.source_start_sample:before.source_end_sample] = tone(
        440, before.source_end_sample - before.source_start_sample)
    source[first:last] = tone(1000, last - first)
    source[last:] = tone(4000, len(source) - last)
    original_pcm, slipped_pcm = run_pcm(source, before), run_pcm(source, after)
    assert len(original_pcm) == len(slipped_pcm) == after.sample_count
    for samples, frequency in ((original_pcm, 440), (slipped_pcm, 1000)):
        body = samples[len(samples) // 5:3 * len(samples) // 5]
        assert dominant_hz(body) == pytest.approx(frequency, abs=6)
        assert np.sqrt(np.mean(body ** 2)) > .15
    spectrum = np.abs(np.fft.rfft(slipped_pcm)) ** 2
    bins = np.fft.rfftfreq(len(slipped_pcm), d=1 / SAMPLE_RATE)
    wanted = spectrum[(bins > 800) & (bins < 1200)].sum()
    excluded = spectrum[(bins > 3800) & (bins < 4200)].sum()
    assert wanted > 100 * excluded, 'Slip read source audio past its exact exclusive endpoint'


@real
@pytest.mark.parametrize('speed', SPEEDS)
def test_real_selected_tail_survives_and_excluded_source_is_never_read(speed):
    # E is 31 project frames, not D * speed. The last 10ms before E contains a
    # 1kHz marker. Everything beyond E is loud 4kHz and must be excluded.
    fps = Fraction(30)
    clip = clip_for(speed, endpoint=Fraction(31))
    plan = compile_audio_timing(clip, fps)
    source = np.zeros(3 * SAMPLE_RATE, dtype='<f4')
    source[plan.source_end_sample-480:plan.source_end_sample] = tone(1000, 480)
    source[plan.source_end_sample:] = tone(4000, len(source)-plan.source_end_sample)
    samples = run_pcm(source, plan)
    assert len(samples) == plan.sample_count
    assert np.max(np.abs(samples)) > .1
    energy = np.abs(np.fft.rfft(samples)) ** 2
    bins = np.fft.rfftfreq(len(samples), d=1/SAMPLE_RATE)
    wanted = energy[(bins > 800) & (bins < 1200)].sum()
    excluded = energy[(bins > 3800) & (bins < 4200)].sum()
    assert wanted > excluded * 100
    # A 10ms transient has roughly 100Hz Fourier resolution and is reshaped by
    # time stretching. Steady-tone pitch has the separate strict 6Hz FFT gate.


@real
@pytest.mark.parametrize('input_rate', [24000, 44100, 48000])
@pytest.mark.parametrize('speed', [Fraction(1, 4), Fraction(3, 2), Fraction(4)])
def test_real_non_frame_aligned_generated_audio_tail_survives(input_rate, speed):
    # Generated speech can have its last actual sample between project frames.
    source = np.zeros(round(1.013 * input_rate), dtype='<f4')
    start = round(1.003 * input_rate)
    source[start:] = tone(1000, len(source)-start, input_rate)
    plan = compile_audio_timing(clip_for(speed, endpoint=Fraction(31)), Fraction(30))
    samples = run_pcm(source, plan, input_rate=input_rate)
    assert len(samples) == plan.sample_count
    assert np.max(np.abs(samples)) > .05
    assert 800 < dominant_hz(samples) < 1200  # 10ms transient, not a steady tone


@real
@pytest.mark.parametrize('speed,tolerance', [(Fraction(1, 4), .0334), (Fraction(1, 2), .0334),
                                            (Fraction(3, 2), .035), (Fraction(2), .035),
                                            (Fraction(4), .035)])
def test_sparse_impulse_bursts_have_bounded_compensated_timing(speed, tolerance):
    # These three sparse bursts test position and lack of *accumulating* drift.
    # Tolerance is one 29.97fps frame on these measured landmarks. This does not
    # make independently processed splits phase-continuous or PCM-identical.
    fps = Fraction(30000, 1001)
    clip = clip_for(speed, endpoint=Fraction(601))
    plan = compile_audio_timing(clip, fps)
    source = np.zeros(21 * SAMPLE_RATE, dtype='<f4')
    centers = [.4, 10.0, 19.4]
    for center in centers:
        first = round((center - .005) * SAMPLE_RATE)
        source[first:first+480] = tone(1000, 480)
    samples = run_pcm(source, plan)
    errors = []
    for center in centers:
        expected = center / float(speed)
        lo = max(0, round((expected - .25) * SAMPLE_RATE))
        hi = min(len(samples), round((expected + .25) * SAMPLE_RATE))
        energy = samples[lo:hi].astype(np.float64) ** 2
        assert energy.sum() > .1
        centroid = (np.arange(lo, hi) * energy).sum() / energy.sum() / SAMPLE_RATE
        errors.append(centroid - expected)
    assert max(map(abs, errors)) < tolerance, errors
    assert max(errors) - min(errors) < tolerance, errors


@real
def test_pcm_budget_is_separate_from_aac_packet_priming(tmp_path):
    fps = Fraction(30000, 1001)
    plan = compile_audio_timing(clip_for(Fraction(3, 2), endpoint=Fraction(61)), fps)
    source = tone(440, SAMPLE_RATE * 3)
    pcm = run_pcm(source, plan)
    assert len(pcm) == plan.sample_count
    target = tmp_path / 'retimed.m4a'
    subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-y', '-f', 'f32le',
                    '-ar', str(SAMPLE_RATE), '-ac', '1', '-i', 'pipe:0', '-c:a', 'aac', str(target)],
                   input=pcm.astype('<f4').tobytes(), capture_output=True, timeout=20, check=True)
    decoded = subprocess.run([ffmpeg_binary(), '-v', 'error', '-nostdin', '-i', str(target),
                              '-ac', '1', '-ar', str(SAMPLE_RATE), '-f', 'f32le', 'pipe:1'],
                             capture_output=True, timeout=20, check=True)
    decoded_samples = np.frombuffer(decoded.stdout, dtype='<f4')
    # AAC's 1024-sample coding frame can leave trailing decoded padding; this is
    # not a change to the pre-encoder sample budget or a sample-exact codec.
    assert abs(len(decoded_samples) - plan.sample_count) <= 1024
    body = decoded_samples[len(pcm)//5:len(pcm)*3//5]
    assert dominant_hz(body) == pytest.approx(440, abs=6)


@real
@pytest.mark.parametrize('speed', [Fraction(1, 4), Fraction(2, 3), Fraction(3, 2), Fraction(4)])
def test_real_fractional_split_keeps_exact_adjacent_budgets_and_pitch(speed):
    fps = Fraction(30000, 1001)
    whole = clip_for(speed, start=7, origin=Fraction(5, 3), endpoint=Fraction(244, 3))
    cut = 7
    boundary = whole.timing.source_in + cut * speed
    left = clip_for(speed, start=whole.start, origin=whole.timing.source_in, endpoint=boundary)
    right = clip_for(speed, start=whole.start + cut, origin=boundary, endpoint=whole.timing.source_out)
    plans = [compile_audio_timing(item, fps) for item in [whole, left, right]]
    source = tone(440, SAMPLE_RATE * 4)
    rendered = [run_pcm(source, plan) for plan in plans]
    assert len(rendered[1]) + len(rendered[2]) == len(rendered[0])
    assert plans[1].start_sample + len(rendered[1]) == plans[2].start_sample
    assert left.timing.source_out == right.timing.source_in
    # Each independently started processor can reshape the splice. Test exact
    # placement and pitch; deliberately do not assert byte-identical audio.
    joined = np.concatenate(rendered[1:])
    body = joined[len(joined)//3:len(joined)*2//3]
    assert dominant_hz(body) == pytest.approx(440, abs=6)


@real
@pytest.mark.parametrize('speed', [Fraction(1, 4), Fraction(1, 2), Fraction(2), Fraction(4)])
def test_real_short_clip_preserves_signal_and_sample_budget(speed):
    clip = clip_for(speed, endpoint=Fraction(1))
    plan = compile_audio_timing(clip, Fraction(30))
    samples = run_pcm(tone(1000, SAMPLE_RATE), plan)
    assert len(samples) == plan.sample_count
    assert np.max(np.abs(samples), initial=0) > .05
    assert np.isfinite(samples).all()


@real
@pytest.mark.parametrize('speed', [Fraction(1, 4), Fraction(3, 2), Fraction(4)])
@pytest.mark.parametrize('input_channels', [2, 6])
def test_real_multichannel_resample_and_stereo_coherence(speed, input_channels):
    fps = Fraction(24000, 1001)
    rate = 44100
    source = np.zeros((rate*3, input_channels), dtype='<f4')
    source[:, 0] = tone(440, len(source), rate)
    source[:, 1] = -source[:, 0]
    plan = compile_audio_timing(clip_for(speed), fps, Fraction(-1, 10))
    result = subprocess.run(
        [ffmpeg_binary(), '-hide_banner', '-v', 'error', '-nostdin', '-f', 'f32le',
         '-ar', str(rate), '-ac', str(input_channels), '-i', 'pipe:0',
         '-af', ','.join(plan.filters), '-ar', str(SAMPLE_RATE), '-ac', '2',
         '-c:a', 'pcm_f32le', '-f', 'f32le', 'pipe:1'],
        input=source.tobytes(), capture_output=True, timeout=20, check=True)
    samples = np.frombuffer(result.stdout, dtype='<f4').reshape(-1, 2)
    assert len(samples) == plan.sample_count
    body = samples[len(samples)//5:len(samples)*3//5]
    assert dominant_hz(body[:, 0]) == pytest.approx(440, abs=6)
    assert np.sqrt(np.mean(body[:, 0]**2)) > .05
    np.testing.assert_allclose(body[:, 0], -body[:, 1], rtol=1e-6, atol=1e-6)


@real
def test_actual_runtime_gate_and_cached_report_are_independent():
    from lumen.audio_timing import AUDIO_TIMING_ALGORITHM, ensure_retime_audio_capability
    first = ensure_retime_audio_capability(ffmpeg_binary(), 30)
    assert first['algorithm'] == AUDIO_TIMING_ALGORITHM
    assert len(first['ffmpeg_sha256']) == 64
    assert len(first['measurements']) == 6
    assert max(abs(error) for row in first['measurements'] for error in row['marker_errors_ms']) < 1000/30
    first['measurements'].clear()
    assert len(ensure_retime_audio_capability(ffmpeg_binary(), 30)['measurements']) == 6


def test_capability_missing_filter_fails_closed(monkeypatch):
    from lumen import audio_timing
    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=b"Unknown filter 'rubberband'.\n", stderr=b'')
    monkeypatch.setattr(audio_timing.subprocess, 'run', fake_run)
    with pytest.raises(ValueError, match='no lower-quality fallback'):
        audio_timing._probe_audio_compensation('ffmpeg', 10)


def test_capability_does_not_accept_a_comment_mentioning_rubberband(monkeypatch):
    from lumen import audio_timing
    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=b'Filter atempo\n mentions rubberband\n', stderr=b'')
    monkeypatch.setattr(audio_timing.subprocess, 'run', fake_run)
    with pytest.raises(ValueError, match='does not provide'):
        audio_timing._probe_audio_compensation('ffmpeg', 10)


def test_capability_timeout_is_clear_and_not_a_fallback(monkeypatch):
    from lumen import audio_timing
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired('ffmpeg', 1)
    monkeypatch.setattr(audio_timing.subprocess, 'run', fake_run)
    with pytest.raises(ValueError, match='Cannot verify required Rubber Band'):
        audio_timing._probe_audio_compensation('ffmpeg', 1)


@real
@pytest.mark.parametrize('speed', [Fraction(5, 4), Fraction(7, 4), Fraction(37, 10)])
@pytest.mark.parametrize('frequency', [440, 1000])
def test_non_preset_fractional_speeds_keep_pitch_endpoints_and_late_audio(speed, frequency):
    fps = Fraction(30000, 1001)
    clip = clip_for(speed, start=31, origin=Fraction(5, 3), endpoint=Fraction(605, 3))
    plan = compile_audio_timing(clip, fps, Fraction(1, 10))
    samples = run_pcm(tone(frequency, SAMPLE_RATE * 8), plan)
    assert len(samples) == sample_boundary(clip.start + clip.duration, fps) - sample_boundary(clip.start, fps)
    assert np.max(np.abs(samples[:plan.leading_samples]), initial=0) == 0
    body = samples[len(samples)//4:len(samples)*3//4]
    assert dominant_hz(body) == pytest.approx(frequency, abs=6)


@real
@pytest.mark.parametrize('speed', [Fraction(5, 4), Fraction(7, 4), Fraction(37, 10)])
def test_non_preset_fractional_speed_tail_and_transient_gate(speed):
    plan = compile_audio_timing(clip_for(speed, endpoint=Fraction(61)), Fraction(30))
    source = np.zeros(SAMPLE_RATE * 3, dtype='<f4')
    centers = [.4, 1.2, plan.source_end_sample / SAMPLE_RATE - .005]
    for center in centers:
        first = round((center-.005) * SAMPLE_RATE)
        source[first:first+480] = tone(1000, 480)
    samples = run_pcm(source, plan)
    for center in centers:
        expected = center / float(speed) * SAMPLE_RATE
        lo, hi = max(0, round(expected-.06*SAMPLE_RATE)), min(len(samples), round(expected+.06*SAMPLE_RATE))
        energy = samples[lo:hi].astype(np.float64)**2
        assert energy.sum() > .05
        centroid = np.sum(np.arange(lo, hi)*energy) / np.sum(energy)
        assert abs(centroid-expected) / SAMPLE_RATE < 1/30


def test_runtime_fft_gate_rejects_pitch_change_even_with_exact_duration(monkeypatch):
    from lumen import audio_timing
    import re
    def fake_run(command, **kwargs):
        if 'filter=rubberband' in command:
            return SimpleNamespace(returncode=0, stdout=b'Filter rubberband\n', stderr=b'')
        graph = command[command.index('-af')+1]
        count = int(re.findall(r'atrim=end_sample=(\d+)', graph)[-1])
        return SimpleNamespace(returncode=0, stdout=tone(880, count).tobytes(), stderr=b'')
    monkeypatch.setattr(audio_timing.subprocess, 'run', fake_run)
    with pytest.raises(ValueError, match='failed pitch preservation'):
        audio_timing._probe_audio_compensation('ffmpeg', 10)
