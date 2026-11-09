"""Numerical and fail-closed tests independent of export/UI implementation."""
from dataclasses import replace
from fractions import Fraction
import hashlib
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import wave

import numpy as np
import pytest

from lumen import audio_analysis as aa
from lumen.model import Clip, Media, Project, Caption
from lumen.timing import ClipTimeMap, duration_frames


@pytest.fixture(autouse=True)
def local_tools():
    if not shutil.which(aa.ffmpeg_binary()) or not shutil.which(aa.ffprobe_binary()):
        pytest.skip('FFmpeg / ffprobe unavailable')


def wav(path, values, rate=48000):
    values = np.asarray(values)
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(values.shape[1] if values.ndim == 2 else 1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(np.rint(values * 32767).astype('<i2').tobytes())
    return path


def project(path, seconds=1, *, fps=Fraction(30), channels=2):
    frames = math.ceil(seconds * fps)
    media = Media('media', str(path), 'tone', float(seconds), has_video=False, has_audio=True)
    return Project(fps_num=fps.numerator, fps_den=fps.denominator, media=[media],
                   clips=[Clip('clip', 'media', 2, 0, 0, frames)])


def tone(seconds=2, amplitude=.1, hz=1000, stereo=True):
    mono = amplitude * np.sin(2 * np.pi * hz * np.arange(round(seconds * 48000)) / 48000)
    return np.column_stack((mono, mono)) if stereo else mono


def analytical_sine_lufs(amplitude, hz, channel_energy=2):
    # Published BS.1770 48 kHz K-weight biquads, evaluated analytically at the
    # sine frequency. This does not invoke the production meter or its output.
    z = np.exp(-2j * np.pi * hz / 48000)
    b1 = (1.53512485958697, -2.69169618940638, 1.19839281085285)
    a1 = (1, -1.69065929318241, .73248077421585)
    b2 = (1, -2, 1)
    a2 = (1, -1.99004745483398, .99007225036621)
    h = ((sum(b * z**i for i, b in enumerate(b1)) / sum(a * z**i for i, a in enumerate(a1)))
         * (sum(b * z**i for i, b in enumerate(b2)) / sum(a * z**i for i, a in enumerate(a2))))
    return -.691 + 10 * math.log10(channel_energy * amplitude**2 / 2 * abs(h)**2)


@pytest.mark.parametrize('hz', [100, 1000, 8000])
def test_known_sine_lufs_against_independent_filter_response(tmp_path, hz):
    source = wav(tmp_path / 'sine.wav', tone(hz=hz))
    result = aa.analyze_audio(project(source, 2))
    assert result.loudness_status == 'measured'
    assert result.integrated_lufs == pytest.approx(analytical_sine_lufs(.1, hz), abs=.13)
    assert result.sample_peaks == pytest.approx((float(np.max(np.abs(tone(hz=hz)))),) * 2, abs=4e-5)
    assert result.overload_count == 0
    assert result.first_overload_seconds is None
    assert result.sample_count == 96000
    assert result.source_hashes == (('media', hashlib.sha256(source.read_bytes()).hexdigest()),)
    assert result.decoder_version.startswith('ffmpeg version')
    assert aa.result_is_current(project(source, 2), result)


def test_mono_downmix_preserves_energy_and_antiphase_never_cancels(tmp_path):
    mono = wav(tmp_path / 'mono.wav', tone(stereo=False))
    result = aa.analyze_audio(project(mono, 2))
    assert result.integrated_lufs == pytest.approx(analytical_sine_lufs(.1, 1000, 1), abs=.13)
    assert result.sample_peaks == pytest.approx((.1 / math.sqrt(2),) * 2, abs=4e-5)
    values = tone()
    values[:, 1] *= -1
    stereo = wav(tmp_path / 'phase.wav', values)
    antiphase = aa.analyze_audio(project(stereo, 2))
    assert antiphase.integrated_lufs == pytest.approx(analytical_sine_lufs(.1, 1000), abs=.13)
    assert antiphase.sample_peaks == pytest.approx((.1, .1), abs=4e-5)


def test_streamed_float_peaks_counts_first_time_and_split_bytes():
    values = np.array([[0, 0], [1, -1], [1.25, -.5], [2, -3], [.5, 1.01]], dtype='<f4')
    reducer = aa._PCMReduction(5)
    raw = values.tobytes()
    for start in range(0, len(raw), 3):
        reducer.feed(raw[start:start+3])
    reducer.finish()
    assert tuple(reducer.peaks) == (2, 3)
    assert tuple(reducer.counts) == (2, 2)
    assert reducer.first == 2


def test_actual_overlap_overload_preserves_floats_and_sample_counts(tmp_path):
    # Both channels above full scale only after mixing; sample==1 is not overload.
    values = np.zeros((48000, 2))
    values[12000:24000] = (.7, -.8)
    source = wav(tmp_path / 'steps.wav', values)
    p = project(source)
    p.clips.append(Clip('overlap', 'media', 3, 0, 0, 30))
    result = aa.analyze_audio(p)
    assert result.sample_peaks == pytest.approx((1.4, 1.6), abs=1e-4)
    assert result.overload_counts == (12000, 12000)
    assert result.overload_count == 24000
    assert result.first_overload_seconds == .25
    assert result.sample_peak_dbfs[0] > 0


def test_silence_short_and_below_gate_are_not_fabricated(tmp_path):
    silence = wav(tmp_path / 'silence.wav', np.zeros((48000, 2)))
    result = aa.analyze_audio(project(silence))
    assert result.integrated_lufs is None and result.loudness_status == 'silence'
    assert result.sample_peak_dbfs == (None, None)
    short = wav(tmp_path / 'short.wav', tone(.2))
    result = aa.analyze_audio(project(short, .2))
    assert result.integrated_lufs is None and result.loudness_status == 'too_short'
    quiet = wav(tmp_path / 'quiet.wav', tone(2, amplitude=.0001))
    result = aa.analyze_audio(project(quiet, 2))
    assert result.integrated_lufs is None and result.loudness_status == 'below_gate'


def test_gate_sentinel_differs_from_real_rounded_minus70():
    reducer = aa._PCMReduction(48000)
    reducer.feed(np.full((48000, 2), .001, dtype='<f4').tobytes())
    template = 'Summary:\n\n  Integrated loudness:\n    I: -70.0 LUFS\n    Threshold: {} LUFS\n'
    assert aa._loudness(template.format('0.0').encode(), reducer) == (None, 'below_gate')
    assert aa._loudness(template.format('-80.0').encode(), reducer) == (-70.0, 'measured')


def _reference_filter(pcm):
    # Deliberately simple, scalar direct form, only used by independent tests.
    out = np.asarray(pcm, dtype=np.float64)
    for b, a in [((1.53512485958697, -2.69169618940638, 1.19839281085285),
                   (1, -1.69065929318241, .73248077421585)),
                  ((1, -2, 1), (1, -1.99004745483398, .99007225036621))]:
        filtered = np.empty_like(out)
        x1 = x2 = y1 = y2 = np.zeros(2)
        for index, x in enumerate(out):
            y = b[0]*x + b[1]*x1 + b[2]*x2 - a[1]*y1 - a[2]*y2
            filtered[index] = y
            x2, x1, y2, y1 = x1, x, y1, y
        out = filtered
    return out


def reference_integrated_lufs(pcm):
    filtered = _reference_filter(pcm)
    energies = np.array([np.square(filtered[i:i+19200]).mean(axis=0).sum()
                         for i in range(0, len(filtered)-19200+1, 4800)])
    absolute = energies[energies > 10**((-70+.691)/10)]
    preliminary = -.691 + 10 * np.log10(absolute.mean())
    gate = max(-70, preliminary - 10)
    gated = energies[energies > 10**((gate+.691)/10)]
    return -.691 + 10 * np.log10(gated.mean())


def test_relative_gating_against_independent_block_filter(tmp_path):
    values = np.concatenate([tone(1.5, .15), tone(1.5, .002), np.zeros((48000, 2))])
    source = wav(tmp_path / 'gated.wav', values)
    with wave.open(str(source), 'rb') as stream:
        pcm = np.frombuffer(stream.readframes(stream.getnframes()), dtype='<i2').reshape(-1, 2) / 32768
    expected = reference_integrated_lufs(pcm)
    result = aa.analyze_audio(project(source, 4))
    assert result.integrated_lufs == pytest.approx(expected, abs=.15)
    # An ungated whole-file RMS would be materially lower.
    assert result.integrated_lufs > analytical_sine_lufs(.15, 1000) - 2


def test_context_is_audio_relevant_and_rejects_source_changes(tmp_path):
    source = wav(tmp_path / 'tone.wav', tone())
    p = project(source, 2)
    result = aa.analyze_audio(p)
    p.width = 1920
    p.clips[0].grade.exposure = 2
    p.clips[0].transform.rotation = 30
    assert aa.result_is_current(p, result)
    p.clips[0].gain_db = 1
    assert not aa.result_is_current(p, result)
    p.clips[0].gain_db = 0
    assert aa.result_is_current(p, result)
    p.captions.append(Caption('tail', 60, 30, 'tail'))
    assert not aa.result_is_current(p, result)
    p.captions.clear()
    before = source.stat()
    with source.open('r+b') as stream:
        stream.seek(-2, 2); stream.write(b'\x01\x00')
    os.utime(source, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert not aa.result_is_current(p, result)


def test_mutation_during_decode_fails_closed_and_owned_snapshot_removed(tmp_path, monkeypatch):
    source = wav(tmp_path / 'tone.wav', tone())
    p = project(source, 2)
    snapshots = []
    original = aa._run
    def run(command, *args, **kwargs):
        if '-/filter_complex' in command:
            graph = Path(command[command.index('-/filter_complex')+1])
            snapshots.append(graph.parent)
            source.write_bytes(source.read_bytes()[:-4] + b'\0\0\0\0')
        return original(command, *args, **kwargs)
    monkeypatch.setattr(aa, '_run', run)
    with pytest.raises(aa.AudioAnalysisError, match='changed'):
        aa.analyze_audio(p)
    assert snapshots and all(not path.exists() for path in snapshots)


def test_nonfinite_truncated_missing_meter_fail_closed():
    for value in (np.nan, np.inf, -np.inf):
        reducer = aa._PCMReduction(1)
        with pytest.raises(aa.AudioAnalysisError, match='non-finite'):
            reducer.feed(np.array([[value, 0]], dtype='<f4').tobytes())
    reducer = aa._PCMReduction(2)
    reducer.feed(b'\0' * 9)
    with pytest.raises(aa.AudioAnalysisError, match='truncated'):
        reducer.finish()
    reducer = aa._PCMReduction(0)
    with pytest.raises(aa.AudioAnalysisError, match='missing'):
        aa._loudness(b'', reducer)
    with pytest.raises(aa.AudioAnalysisError, match='without errors'):
        aa._loudness(b'[error] decoder failed', reducer)


@pytest.mark.parametrize('kind', ['individual', 'total', 'duration', 'pcm', 'disk'])
def test_resource_limits_fail_closed(tmp_path, monkeypatch, kind):
    source = wav(tmp_path / 'tone.wav', tone())
    p = project(source, 2)
    limits = aa.AnalysisLimits()
    if kind == 'individual': limits = replace(limits, max_source_bytes=8)
    if kind == 'total': limits = replace(limits, max_total_source_bytes=8)
    if kind == 'duration': limits = replace(limits, max_duration_seconds=.1)
    if kind == 'pcm': limits = replace(limits, max_pcm_bytes=8)
    if kind == 'disk':
        monkeypatch.setattr(aa.shutil, 'disk_usage', lambda path: type('Usage', (), {'free': 0})())
    with pytest.raises(aa.AudioAnalysisError):
        aa.analyze_audio(p, limits=limits)


def test_multichannel_playlist_link_network_rejected(tmp_path):
    surround = wav(tmp_path / 'multi.wav', np.zeros((48000, 6)))
    with pytest.raises(aa.AudioAnalysisError, match='mono/stereo'):
        aa.analyze_audio(project(surround))
    playlist = tmp_path / 'media.m3u8'
    playlist.write_text('#EXTM3U\nhttp://127.0.0.1:9/a.ts\n')
    with pytest.raises(aa.AudioAnalysisError):
        aa.analyze_audio(project(playlist))
    for path in ('https://example.com/a.wav', '//host/share/a.wav'):
        with pytest.raises(aa.AudioAnalysisError, match='plain local'):
            aa.analyze_audio(project(path))
    link = tmp_path / 'link.wav'
    try: link.symlink_to(surround)
    except OSError: return
    with pytest.raises(aa.AudioAnalysisError, match='plain local'):
        aa.analyze_audio(project(link))


def test_process_deadline_cancel_output_and_log_caps():
    script = [sys.executable, '-c']
    with pytest.raises(aa.AudioAnalysisError, match='timed out'):
        aa._run(script+['import time;time.sleep(20)'], aa._Budget(aa.AnalysisLimits(timeout=.15), None), 1024, 1024)
    cancel = threading.Event()
    timer = threading.Timer(.1, cancel.set)
    timer.start()
    started = time.monotonic()
    with pytest.raises(aa.AudioAnalysisCancelled):
        aa._run(script+['import time;time.sleep(20)'], aa._Budget(aa.AnalysisLimits(), cancel), 1024, 1024)
    timer.join()
    assert time.monotonic() - started < 3
    for command in ('import sys;sys.stdout.buffer.write(b"x"*4096)',
                    'import sys;sys.stderr.buffer.write(b"x"*4096)'):
        with pytest.raises(aa.AudioAnalysisError, match='output limit'):
            aa._run(script+[command], aa._Budget(aa.AnalysisLimits(), None), 1024, 1024)


def test_cancel_during_snapshot_and_decode_cleans_tempfiles(tmp_path, monkeypatch):
    source = wav(tmp_path / 'tone.wav', tone())
    p = project(source, 2)
    for phase in ('snapshot', 'decode'):
        cancel = threading.Event()
        directories = []
        original = aa.tempfile.TemporaryDirectory
        def directory(*args, **kwargs):
            value = original(*args, **kwargs)
            directories.append(Path(value.name))
            return value
        with monkeypatch.context() as patch:
            patch.setattr(aa.tempfile, 'TemporaryDirectory', directory)
            def progress(value):
                if value == phase: cancel.set()
            with pytest.raises(aa.AudioAnalysisCancelled):
                aa.analyze_audio(p, cancel, progress=progress)
        assert all(not path.exists() for path in directories)


def with_sound_provenance(p):
    from lumen.model import SoundProvenance
    item = p.media[0]
    digest = hashlib.sha256(Path(item.path).read_bytes()).hexdigest()
    with wave.open(item.path, 'rb') as source:
        count, rate = source.getnframes(), source.getframerate()
    item.sound_provenance = SoundProvenance('test', 'procedural', 'CutCat original / CC0-1.0',
                                             digest, digest, rate, count)
    return p


def test_mono_sound_provenance_and_forged_safe_peak(tmp_path):
    source = wav(tmp_path / 'sound.wav', tone(1, .1, stereo=False))
    p = with_sound_provenance(project(source))
    assert aa.analyze_audio(p).integrated_lufs == pytest.approx(-23.0, abs=.1)
    loud = wav(tmp_path / 'loud.wav', tone(1, .9, stereo=False))
    p = with_sound_provenance(project(loud))
    with pytest.raises(aa.AudioAnalysisError, match='safe peak'):
        aa.analyze_audio(p)
    # Generated sources still verify when muted, matching the export contract.
    p.clips[0].muted = True
    with pytest.raises(aa.AudioAnalysisError, match='safe peak'):
        aa.analyze_audio(p)


def test_generated_speech_exact_count_and_hash(tmp_path):
    from lumen.model import SpeechProvenance
    source = wav(tmp_path / 'speech.wav', tone(1, stereo=False))
    p = project(source)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    provenance = SpeechProvenance('Test', 'fixture', '1', 'a'*64, 'b'*64, 'c'*64,
                                  'test', 1., 48000, '2026-10-25T00:00:00Z', 'caption',
                                  0, 30, digest, sample_count=48000)
    p.media[0].speech_provenance = provenance
    assert aa.analyze_audio(p).integrated_lufs is not None
    p.media[0].speech_provenance = replace(provenance, sample_count=47999)
    p.media[0].duration = 47999 / 48000
    with pytest.raises(aa.AudioAnalysisError, match='metadata'):
        aa.analyze_audio(p)
    p.media[0].speech_provenance = replace(provenance, output_sha256='0'*64)
    p.media[0].duration = 1
    with pytest.raises(aa.AudioAnalysisError, match='changed'):
        aa.analyze_audio(p)


def test_recorded_no_audio_but_source_has_audio_rejected(tmp_path):
    source = wav(tmp_path / 'replacement.wav', tone(1))
    p = project(source)
    p.media[0].has_audio = False
    p.media[0].has_video = True
    p.clips[0].track = 0
    with pytest.raises(aa.AudioAnalysisError, match='mono/stereo'):
        aa.analyze_audio(p)


def test_truncated_source_fails_instead_of_trusting_output_padding(tmp_path):
    source = wav(tmp_path / 'truncated.wav', tone(2))
    source.write_bytes(source.read_bytes()[:48000])
    with pytest.raises(aa.AudioAnalysisError):
        aa.analyze_audio(project(source, 2))


def test_retime_uses_cancellable_numerical_authority_in_frozen_app(tmp_path, monkeypatch):
    source = wav(tmp_path / 'speed.wav', tone(2))
    p = project(source, 2)
    timing = ClipTimeMap(Fraction(0), Fraction(60), Fraction(3, 2))
    p.clips[0] = Clip('speed', 'media', 2, 0, 0, duration_frames(timing), time_map=timing)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', '/not-a-python-interpreter/CutCat')
    result = aa.analyze_audio(p)
    assert result.sample_count == 64000
    assert result.loudness_status == 'measured'
    assert result.integrated_lufs == pytest.approx(-20, abs=.5)


def test_missing_required_meter_filter_fails_closed(tmp_path, monkeypatch):
    source = wav(tmp_path / 'tone.wav', tone())
    real_run = aa._run
    def run(command, *args, **kwargs):
        if '-/filter_complex' in command:
            graph = Path(command[command.index('-/filter_complex')+1])
            graph.write_text(graph.read_text().replace('ebur128=', 'missing_ebur128_filter='))
        return real_run(command, *args, **kwargs)
    monkeypatch.setattr(aa, '_run', run)
    with pytest.raises(aa.AudioAnalysisError, match='filter failed'):
        aa.analyze_audio(project(source, 2))


def test_copy_cancel_is_observed_between_bounded_reads(tmp_path):
    source = tmp_path / 'large.bin'
    source.write_bytes(b'\0' * (3 * 1024**2))
    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        return calls >= 3
    with pytest.raises(aa.AudioAnalysisCancelled):
        aa._copy_hash(source, None, aa._Budget(aa.AnalysisLimits(), cancel), 4 * 1024**2)
    assert calls == 3


def test_clean_shorter_source_replacement_is_not_silently_padded(tmp_path):
    source = wav(tmp_path / 'replacement.wav', tone(2))
    p = project(source, 2)
    wav(source, tone(.5))
    with pytest.raises(aa.AudioAnalysisError, match='duration changed'):
        aa.analyze_audio(p)


def test_caption_only_timeline_is_exact_silence_without_source_decode():
    p = Project(captions=[Caption('caption', 0, 30, 'No audio')])
    result = aa.analyze_audio(p)
    assert result.sample_count == 48000
    assert result.loudness_status == 'silence'
    assert result.source_hashes == ()
    assert result.sample_peaks == (0, 0)


def test_windows_context_includes_descriptor_change_time(tmp_path, monkeypatch):
    from types import SimpleNamespace
    source = wav(tmp_path / 'tone.wav', tone())
    path_info = source.stat()
    monkeypatch.setattr(aa, '_WINDOWS', True)
    original = aa.os.fstat
    state = {'change': 1}
    def fstat(fd):
        real = original(fd)
        fields = {key: getattr(real, key) for key in dir(real) if key.startswith('st_')}
        fields['st_ctime_ns'] = state['change']
        return SimpleNamespace(**fields)
    # The portability helper deliberately handles path/fd ctime differences.
    monkeypatch.setattr(aa, 'path_matches_descriptor', lambda a, b: a.st_ino == b.st_ino)
    monkeypatch.setattr(aa.os, 'fstat', fstat)
    first = aa._state(source)
    state['change'] = 2
    second = aa._state(source)
    assert first != second
    assert source.stat().st_mtime_ns == path_info.st_mtime_ns


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux process-state cleanup verification')
def test_cancel_reaps_child_when_group_leader_has_already_exited(tmp_path):
    pidfile = tmp_path / 'child.pid'
    script = ('import subprocess,sys;from pathlib import Path;'
              'p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(60)"]);'
              f'Path({str(pidfile)!r}).write_text(str(p.pid))')
    first_seen = None
    def cancelled():
        nonlocal first_seen
        if pidfile.exists() and first_seen is None:
            first_seen = time.monotonic()
        return first_seen is not None and time.monotonic() - first_seen > .05
    with pytest.raises(aa.AudioAnalysisCancelled):
        aa._run([sys.executable, '-c', script], aa._Budget(aa.AnalysisLimits(timeout=5), cancelled), 1024, 1024)
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        status = Path(f'/proc/{pid}/stat')
        if not status.exists() or status.read_text().split()[2] == 'Z':
            break
        time.sleep(.02)
    else:
        pytest.fail('Decoder descendant survived cancellation')


def test_settings_fingerprint_is_pure_and_tracks_audio_edits(monkeypatch):
    p = project('/unavailable/source.wav')
    def no_io(*args, **kwargs):
        pytest.fail('Pure audio settings fingerprint attempted filesystem/tool I/O')
    with monkeypatch.context() as patch:
        for target, name in ((aa, '_local_path'), (aa, '_state'), (aa, 'ffmpeg_binary'),
                             (aa.shutil, 'which'), (aa.os, 'open'), (Path, 'stat'),
                             (Path, 'lstat'), (Path, 'resolve')):
            patch.setattr(target, name, no_io)
        original = aa.audio_settings_fingerprint(p)
        p.clips[0].grade.exposure = 2
        p.width = 1920
        assert aa.audio_settings_fingerprint(p) == original
        p.clips[0].gain_db = 3
        assert aa.audio_settings_fingerprint(p) != original
        p.clips[0].gain_db = 0
        assert aa.audio_settings_fingerprint(p) == original


@pytest.mark.parametrize('outcome', ['success', 'cancel', 'filter_failure'])
def test_file_graph_argument_handles_special_path_and_always_cleans(tmp_path, monkeypatch, outcome):
    # Valid on Windows too: no quote escaping, shell expansion, or inline graph.
    parent = tmp_path / "音频 graph [x] & spaced 'folder'"
    parent.mkdir()
    source = wav(tmp_path / 'tone.wav', tone())
    original_directory, original_run = aa.tempfile.TemporaryDirectory, aa._run
    directories, graphs = [], []
    cancel = threading.Event()
    def directory(*args, **kwargs):
        value = original_directory(*args, dir=parent, **kwargs)
        directories.append(Path(value.name))
        return value
    def run(command, *args, **kwargs):
        if '-/filter_complex' in command:
            assert '-filter_complex_script' not in command and '-filter_complex' not in command
            graph = Path(command[command.index('-/filter_complex') + 1])
            assert graph.parent.parent == parent
            contents = graph.read_text(encoding='utf-8')
            assert 'ebur128=' in contents and contents not in command
            graphs.append(graph)
            if outcome == 'cancel':
                cancel.set()
            elif outcome == 'filter_failure':
                graph.write_text(contents.replace('ebur128=', 'missing_ebur128_filter='), encoding='utf-8')
        return original_run(command, *args, **kwargs)
    monkeypatch.setattr(aa.tempfile, 'TemporaryDirectory', directory)
    monkeypatch.setattr(aa, '_run', run)
    if outcome == 'success':
        result = aa.analyze_audio(project(source, 2), cancel)
        assert result.sample_count == 96000 and result.loudness_status == 'measured'
    else:
        error = aa.AudioAnalysisCancelled if outcome == 'cancel' else aa.AudioAnalysisError
        with pytest.raises(error):
            aa.analyze_audio(project(source, 2), cancel)
    assert graphs and directories
    assert all(not path.exists() for path in [*graphs, *directories])
    assert not aa._SNAPSHOT_LOCK.locked()
