"""Independent numerical proof of the finite-signal 4x true-peak estimator.

The reference table below was transcribed directly from ITU-R BS.1770-5,
Annex 2, printed pp. 18-19 (PDF pp. 19-20, zero-based):
https://www.itu.int/dms_pubrec/itu-r/rec/bs/R-REC-BS.1770-5-202311-I!!PDF-E.pdf
No production coefficients or filtering helpers are used by the scalar oracle.
This proves the specified finite FIR algorithm, not certified meter compliance.
"""
import math
import re
import shutil
import subprocess

import numpy as np
import pytest

from lumen import audio_analysis as aa
from lumen.audio_true_peak import TruePeakEstimator


# Each row is a tap; the four columns are the published phases 0 through 3.
# Retain the published decimals, independently of the production representation.
_REFERENCE_ROWS = (
    (0.0017089843750, -0.0291748046875, -0.0189208984375, -0.0083007812500),
    (0.0109863281250, 0.0292968750000, 0.0330810546875, 0.0148925781250),
    (-0.0196533203125, -0.0517578125000, -0.0582275390625, -0.0266113281250),
    (0.0332031250000, 0.0891113281250, 0.1015625000000, 0.0476074218750),
    (-0.0594482421875, -0.1665039062500, -0.2003173828125, -0.1022949218750),
    (0.1373291015625, 0.4650878906250, 0.7797851562500, 0.9721679687500),
    (0.9721679687500, 0.7797851562500, 0.4650878906250, 0.1373291015625),
    (-0.1022949218750, -0.2003173828125, -0.1665039062500, -0.0594482421875),
    (0.0476074218750, 0.1015625000000, 0.0891113281250, 0.0332031250000),
    (-0.0266113281250, -0.0582275390625, -0.0517578125000, -0.0196533203125),
    (0.0148925781250, 0.0330810546875, 0.0292968750000, 0.0109863281250),
    (-0.0083007812500, -0.0189208984375, -0.0291748046875, 0.0017089843750),
)


def scalar_reference(pcm, *, flush=True):
    """Literal causal y[p,n] = sum_k h[p,k] x[n-k], zero outside input."""
    frames = [[float(left), float(right)] for left, right in pcm]
    raw = [0.0, 0.0]
    samples = [0.0, 0.0]
    for frame in frames:
        for channel in range(2):
            samples[channel] = max(samples[channel], abs(frame[channel]))
    for n in range(len(frames) + (11 if flush else 0)):
        for channel in range(2):
            for phase in range(4):
                value = math.fsum(
                    row[phase] * frames[n - k][channel]
                    for k, row in enumerate(_REFERENCE_ROWS)
                    if 0 <= n - k < len(frames)
                )
                raw[channel] = max(raw[channel], abs(value))
    return tuple(raw), tuple(samples), tuple(max(a, b) for a, b in zip(raw, samples))


def assert_reference(meter, expected, *, atol=0.0):
    raw, samples, guarded = expected
    np.testing.assert_allclose(meter.raw_peaks, raw, rtol=2e-14, atol=atol)
    np.testing.assert_allclose(meter.sample_peaks, samples, rtol=0, atol=0)
    np.testing.assert_allclose(meter.finish(), guarded, rtol=2e-14, atol=atol)


def measure(pcm, widths=(17, 1, 12, 3, 29)):
    meter = TruePeakEstimator()
    offset = 0
    index = 0
    while offset < len(pcm):
        width = widths[index % len(widths)]
        meter.feed(pcm[offset:offset + width])
        offset += width
        index += 1
    meter.finish()
    return meter


@pytest.mark.parametrize('length', [1, 2, 11, 12, 13, 31])
def test_impulse_first_middle_last_at_every_possible_feed_boundary(length):
    # Check raw peaks separately: the sample safeguard otherwise conceals an
    # omitted tail, since the interpolated impulse peak is below its sample.
    for position in sorted({0, length // 2, length - 1}):
        pcm = np.zeros((length, 2), dtype=np.float64)
        pcm[position] = (1.0, -0.375)
        expected = scalar_reference(pcm)
        assert expected[0] == (0.97216796875, 0.36456298828125)
        assert expected[2] == (1.0, 0.375)
        for boundary in range(length + 1):
            meter = TruePeakEstimator()
            meter.feed(pcm[:boundary])
            meter.feed(pcm[boundary:])
            meter.finish()
            assert_reference(meter, expected)


def test_last_sample_impulse_requires_eof_tail_and_finish_is_idempotent():
    pcm = np.zeros((19, 2), dtype=np.float64)
    pcm[-1] = (-2.0, 0.125)
    meter = TruePeakEstimator()
    meter.feed(pcm)
    np.testing.assert_allclose(meter.raw_peaks, scalar_reference(pcm, flush=False)[0],
                               rtol=0, atol=0)
    before = meter.raw_peaks.copy()
    first = meter.finish()
    assert np.all(meter.raw_peaks > before * 30)
    assert_reference(meter, scalar_reference(pcm))
    raw = meter.raw_peaks.copy()
    samples = meter.sample_peaks.copy()
    for _ in range(3):
        assert meter.finish() == first
        np.testing.assert_array_equal(meter.raw_peaks, raw)
        np.testing.assert_array_equal(meter.sample_peaks, samples)
    for more in (np.empty((0, 2)), np.zeros((1, 2))):
        with pytest.raises(ValueError, match='final'):
            meter.feed(more)


def test_causal_prefix_peaks_match_scalar_oracle_before_finalization():
    pcm = np.random.default_rng(17705).uniform(-1.4, 1.2, (47, 2))
    meter = TruePeakEstimator()
    for stop in range(1, len(pcm) + 1):
        meter.feed(pcm[stop - 1:stop])
        raw, samples, _ = scalar_reference(pcm[:stop], flush=False)
        np.testing.assert_allclose(meter.raw_peaks, raw, rtol=2e-14, atol=0)
        np.testing.assert_array_equal(meter.sample_peaks, samples)
    meter.finish()
    assert_reference(meter, scalar_reference(pcm))


@pytest.mark.parametrize('frequency', [0.25, 0.375, 0.45])
@pytest.mark.parametrize('phase', [0.0, math.pi / 8, math.pi / 4, 3 * math.pi / 8])
def test_phase_varied_windowed_high_frequency_matches_scalar_fir(frequency, phase):
    n = np.arange(513)
    window = np.hanning(len(n))
    left = 1.2 * window * np.sin(2 * np.pi * frequency * n + phase)
    right = 0.57 * window * np.cos(2 * np.pi * frequency * n + phase / 3)
    pcm = np.column_stack((left, right))
    expected = scalar_reference(pcm)
    for widths in ((len(pcm),), (1,), (3, 11, 2, 13, 127)):
        assert_reference(measure(pcm, widths), expected)


def test_intersample_overload_is_visible_without_any_sample_overload():
    n = np.arange(1025)
    left = 1.2 * np.hanning(len(n)) * np.sin(np.pi * n / 2 + np.pi / 4)
    pcm = np.column_stack((left, -0.5 * left))
    expected = scalar_reference(pcm)
    assert max(expected[1]) < 0.85
    assert expected[0][0] > 1.16
    meter = measure(pcm)
    assert_reference(meter, expected)
    assert meter.finish()[0] > 1.0
    assert meter.finish()[1] < 1.0


@pytest.mark.parametrize('layout', ['left_only', 'right_only', 'asymmetric', 'antiphase'])
def test_channels_are_independent_and_never_averaged(layout):
    mono = np.random.default_rng(90).normal(0, 0.7, 173)
    if layout == 'left_only':
        pcm = np.column_stack((mono, np.zeros_like(mono)))
    elif layout == 'right_only':
        pcm = np.column_stack((np.zeros_like(mono), mono))
    elif layout == 'antiphase':
        pcm = np.column_stack((mono, -mono))
    else:
        pcm = np.column_stack((mono, 0.125 * np.roll(mono, 9)))
    meter = measure(pcm)
    assert_reference(meter, scalar_reference(pcm))
    if layout == 'antiphase':
        assert meter.finish()[0] == meter.finish()[1] > 1
    elif layout == 'left_only':
        assert meter.finish()[0] > 1 and meter.finish()[1] == 0
    elif layout == 'right_only':
        assert meter.finish()[1] > 1 and meter.finish()[0] == 0


@pytest.mark.parametrize('length', [0, 1, 11, 97])
def test_empty_and_silent_programs_are_exact_zero(length):
    pcm = np.zeros((length, 2), dtype=np.float64)
    meter = measure(pcm)
    assert meter.finish() == (0.0, 0.0)
    assert_reference(meter, scalar_reference(pcm))


@pytest.mark.parametrize('amplitude', [1e-300, 1e-310, np.nextafter(0.0, 1.0)])
def test_float64_tiny_and_subnormal_input_is_not_dropped(amplitude):
    pcm = np.array([[amplitude, -amplitude], [-amplitude, amplitude],
                    [amplitude, 0.0]], dtype=np.float64)
    meter = measure(pcm, (1,))
    # Subnormal products may differ by a few representable quanta depending on
    # summation order; zero absolute tolerance would overstate this precision.
    assert_reference(meter, scalar_reference(pcm), atol=8 * np.nextafter(0.0, 1.0))
    assert min(meter.finish()) >= amplitude > 0


def test_input_and_accumulation_retain_float64_precision():
    amplitude = 1.0 + 2.0 ** -40
    pcm = np.array([[amplitude, -2.0 * amplitude]], dtype=np.float64)
    meter = measure(pcm)
    assert meter.finish() == (amplitude, 2 * amplitude)
    assert meter.raw_peaks.dtype == np.float64
    assert meter.sample_peaks.dtype == np.float64
    assert_reference(meter, scalar_reference(pcm))


@pytest.mark.parametrize('scale', [-7.25, -1.0, 0.0, 0.125, 2.0, 16.0])
def test_linear_scaling_no_clipping_or_gain_normalization(scale):
    pcm = np.random.default_rng(48).uniform(-0.9, 0.9, (139, 2))
    baseline = measure(pcm)
    scaled = measure(scale * pcm, (7, 1, 32))
    assert_reference(scaled, scalar_reference(scale * pcm))
    np.testing.assert_allclose(scaled.raw_peaks, abs(scale) * baseline.raw_peaks,
                               rtol=2e-14, atol=0)
    np.testing.assert_allclose(scaled.finish(), abs(scale) * np.asarray(baseline.finish()),
                               rtol=2e-14, atol=0)


@pytest.mark.parametrize('bad_shape', [(), (2,), (4, 1), (4, 3), (2, 2, 1), (0, 1)])
def test_wrong_input_dimensions_are_rejected(bad_shape):
    with pytest.raises(ValueError, match='stereo'):
        TruePeakEstimator().feed(np.zeros(bad_shape))


@pytest.mark.parametrize('bad_value', [np.nan, np.inf, -np.inf])
@pytest.mark.parametrize('channel', [0, 1])
def test_nonfinite_values_are_rejected_in_either_channel(bad_value, channel):
    pcm = np.zeros((17, 2))
    pcm[-1, channel] = bad_value
    with pytest.raises(ValueError, match='non-finite'):
        TruePeakEstimator().feed(pcm)


def test_large_feed_and_internal_block_edges_match_arbitrary_streaming():
    pcm = np.random.default_rng(8192).normal(0, 0.15, (25007, 2))
    for position in (0, 8191, 8192, 8193, 16383, 16384, 25006):
        pcm[position] = (1.3, -1.7)
    expected = scalar_reference(pcm)
    for widths in ((len(pcm),), (8191, 1, 8192, 3), (113, 7, 1024)):
        assert_reference(measure(pcm, widths), expected)


def test_noncontiguous_input_and_empty_chunks_do_not_change_results():
    backing = np.random.default_rng(123).normal(0, 0.4, (202, 4))
    pcm = backing[::2, ::2]
    assert not pcm.flags.c_contiguous
    meter = TruePeakEstimator()
    for start in range(0, len(pcm), 3):
        meter.feed(np.empty((0, 2)))
        meter.feed(pcm[start:start + 3])
    meter.feed(np.empty((0, 2)))
    meter.finish()
    assert_reference(meter, scalar_reference(pcm))


@pytest.mark.parametrize('width', [1, 2, 3, 4, 7, 8, 9, 13, 64, 65536])
def test_pcm_reduction_byte_chunks_preserve_unclipped_float32_true_peak(width):
    pcm = np.random.default_rng(144).uniform(-1.7, 1.3, (151, 2)).astype('<f4')
    pcm[0] = (1.0, -1.0)
    pcm[-1] = (3.25, -2.5)
    raw = pcm.tobytes()
    reducer = aa._PCMReduction(len(pcm))
    for start in range(0, len(raw), width):
        reducer.feed(raw[start:start + width])
    reducer.finish()
    expected = scalar_reference(pcm)
    assert_reference(reducer.true_peak, expected)
    np.testing.assert_allclose(reducer.estimated_true_peaks, expected[2], rtol=2e-14, atol=0)
    np.testing.assert_array_equal(reducer.peaks, np.max(np.abs(pcm), axis=0))
    np.testing.assert_array_equal(reducer.counts, np.sum(np.abs(pcm) > 1.0, axis=0))
    assert reducer.first == int(np.flatnonzero((np.abs(pcm) > 1.0).any(axis=1))[0])
    assert reducer.sample_count == len(pcm)  # EOF padding is not program audio.
    assert reducer.pending == b''
    assert min(reducer.estimated_true_peaks) > 2.0


def test_every_byte_split_is_equivalent_including_partial_stereo_frames():
    pcm = np.array([[0.2, -0.1], [1.0, -1.0], [1.3, -0.4], [-0.7, 0.9],
                    [0.0, 0.2], [0.4, -1.5], [2.0, -2.5]], dtype='<f4')
    raw = pcm.tobytes()
    expected = scalar_reference(pcm)
    for split in range(len(raw) + 1):
        reducer = aa._PCMReduction(len(pcm))
        reducer.feed(raw[:split])
        reducer.feed(raw[split:])
        reducer.finish()
        assert_reference(reducer.true_peak, expected)
        np.testing.assert_allclose(reducer.estimated_true_peaks, expected[2],
                                   rtol=2e-14, atol=0)


@pytest.mark.parametrize('kind', ['incomplete_frame', 'missing_frame', 'extra_frame'])
def test_reduction_rejects_invalid_sample_count_before_tail_finalization(kind):
    reducer = aa._PCMReduction(2)
    if kind == 'extra_frame':
        with pytest.raises(aa.AudioAnalysisError, match='sample count'):
            reducer.feed(np.ones((3, 2), dtype='<f4').tobytes())
    else:
        data = np.ones((1, 2), dtype='<f4').tobytes()
        reducer.feed(data + (b'\x00' if kind == 'incomplete_frame' else b''))
        with pytest.raises(aa.AudioAnalysisError, match='truncated'):
            reducer.finish()
    assert reducer.estimated_true_peaks is None
    # Its causal FIR has not yet seen the tail's large impulse coefficient.
    assert np.max(reducer.true_peak.raw_peaks) < 0.03


def test_reduction_intersample_overload_does_not_fabricate_sample_events():
    n = np.arange(513)
    left = 1.2 * np.hanning(len(n)) * np.sin(np.pi * n / 2 + np.pi / 4)
    pcm = np.column_stack((left, -left)).astype('<f4')
    reducer = aa._PCMReduction(len(pcm))
    raw = pcm.tobytes()
    for start in range(0, len(raw), 37):
        reducer.feed(raw[start:start + 37])
    reducer.finish()
    assert_reference(reducer.true_peak, scalar_reference(pcm))
    assert max(reducer.peaks) < 0.85
    assert min(reducer.estimated_true_peaks) > 1.16
    assert tuple(reducer.counts) == (0, 0)
    assert reducer.first is None
    assert reducer.sample_count == len(pcm)


def ffmpeg_secondary_peak_db(pcm, *, padding=32):
    """Secondary implementation only; do not replace the scalar FIR oracle.

    FFmpeg's ebur128 true-peak resampler can leave its delayed signal tail
    unreported at EOF. Supply explicit zeros and verify the chosen padding
    against a longer pad below; never use an unpadded EOF impulse as an oracle.
    Its interpolation filter is not required to equal the Annex 2 example FIR,
    and its summary is rounded to 0.1 dB.
    """
    binary = aa.ffmpeg_binary()
    if not shutil.which(binary):
        pytest.skip('Secondary FFmpeg cross-check unavailable; scalar tests still run')
    padded = np.concatenate((np.asarray(pcm, dtype='<f4'),
                             np.zeros((padding, 2), dtype='<f4')))
    completed = subprocess.run(
        [binary, '-hide_banner', '-nostats', '-loglevel', 'info',
         '-f', 'f32le', '-ar', '48000', '-ac', '2', '-i', 'pipe:0',
         '-af', 'ebur128=peak=true', '-f', 'null', '-'],
        input=padded.tobytes(), capture_output=True, timeout=15,
    )
    log = completed.stderr.decode('utf-8', errors='replace')
    assert completed.returncode == 0, log
    peaks = re.findall(r'True peak:\s*Peak:\s*([-+\d.]+) dBFS', log)
    assert peaks, log
    value = float(peaks[-1])
    assert math.isfinite(value)
    return value


@pytest.mark.parametrize('length, position', [(1, 0), (4800, 0), (4800, 100), (4800, 4799)])
def test_ffmpeg_secondary_impulse_crosscheck_verifies_32_frame_eof_padding(length, position):
    pcm = np.zeros((length, 2), dtype='<f4')
    pcm[position] = (-0.625, 0.3125)
    meter = measure(pcm)
    assert_reference(meter, scalar_reference(pcm))
    padded_32 = ffmpeg_secondary_peak_db(pcm, padding=32)
    padded_64 = ffmpeg_secondary_peak_db(pcm, padding=64)
    # This must hold for the singleton/last sample as well as interior/start:
    # 32 zeros fully expose FFmpeg's delayed peak for these probe signals.
    assert padded_32 == padded_64
    assert padded_32 == pytest.approx(20 * math.log10(0.625), abs=0.051)
    assert 20 * math.log10(max(meter.finish())) == pytest.approx(padded_32, abs=0.051)


@pytest.mark.parametrize('hz', [12000, 18000, 21000])
@pytest.mark.parametrize('phase', [0.0, math.pi / 8, math.pi / 4])
def test_ffmpeg_secondary_windowed_tones_agree_with_documented_filter_tolerance(hz, phase):
    n = np.arange(4800)
    left = 1.2 * np.hanning(len(n)) * np.sin(2 * np.pi * hz * n / 48000 + phase)
    pcm = np.column_stack((left, -0.5 * left)).astype('<f4')
    meter = measure(pcm, (13, 7, 1024))
    # Exact example-FIR agreement is required first, independently of FFmpeg.
    assert_reference(meter, scalar_reference(pcm))
    ffmpeg_db = ffmpeg_secondary_peak_db(pcm)
    raw_db = 20 * math.log10(max(meter.raw_peaks))
    guarded_db = 20 * math.log10(max(meter.finish()))
    # Different interpolation filters and FFmpeg's 0.1 dB summary rounding
    # justify a looser cross-implementation bound, not a conformance claim.
    assert raw_db == pytest.approx(ffmpeg_db, abs=0.4)
    assert guarded_db == pytest.approx(ffmpeg_db, abs=0.15)
