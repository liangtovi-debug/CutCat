"""Fail-closed sticker capability and unchanged legacy graph contracts."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import struct
import threading

import pytest

from lumen import highbit_stickers as high
from lumen.color_output import validate_export_profile
from lumen.model import Clip, ClipEffect, Overlay, Project
from lumen.render import RenderCancelled, RenderEngine, RenderError
from lumen.sticker_assets import builtin_stickers, load_sticker


@pytest.fixture
def scene():
    path = builtin_stickers()[0][1]
    asset = load_sticker(path)
    project = Project(width=64, height=64,
                      overlays=[Overlay('art', str(path), asset.digest, 0, 2)])
    return project, {'art': asset}


def oracle(original):
    """Independent two-frame oracle for the known synthetic probe geometry."""
    source = struct.unpack('<8192H', original)
    output = list(source)
    alpha = [0] * 4096
    for y in range(16, 32):
        for x in range(16, 48):
            alpha[y * 64 + x] = 255 if x < 32 else 128
    for y in range(40, 48):
        for x, value in ((1, 255), (20, 255), (41, 128), (62, 255)):
            alpha[y * 64 + x] = value
    for offset, width, red in ((0, 64, 250), (4096, 32, 409), (6144, 32, 960)):
        for y in range(64):
            for x in range(width):
                a = (alpha[y * 64 + x] / 255 if width == 64 else
                     (alpha[y * 64 + x * 2] + alpha[y * 64 + x * 2 + 1]) / 510)
                index = offset + y * width + x
                output[index] = round(source[index] * (1 - a) + red * a)
    return struct.pack('<8192H', *output) + original


class Process:
    def __init__(self, command, *, stdin, stdout, stderr, code=0, size=32768,
                 running=False, bad=None, log_size=0):
        self.command = command
        self.returncode = code
        self.running = running
        self.killed = self.waited = False
        source = stdin.read()
        assert len(source) == 32768 and source[:16384] == source[16384:]
        payload = oracle(source[:16384])
        if bad == 'noop':
            payload = source
        elif bad == 'eightbit':
            samples = struct.unpack('<16384H', payload)
            payload = struct.pack('<16384H', *(value // 4 * 4 for value in samples))
        stdout.write((payload + b'\0' * max(0, size - len(payload)))[:size])
        stdout.flush()
        stderr.write(b'x' * log_size)
        stderr.flush()

    def poll(self):
        return None if self.running else self.returncode

    def kill(self):
        self.killed = True
        self.running = False

    def wait(self):
        self.waited = True
        return self.returncode


def filters(scene, *, cancel=None, timeout=30):
    project, assets = scene
    return high.highbit_sticker_filters(project, assets, timeout, cancel,
                                        RenderError, RenderCancelled)


def test_probe_requires_typed_native_behavior_and_is_repeated_each_export(scene, monkeypatch):
    calls = []
    paths = []
    def start(command, **kwargs):
        paths.append(Path(command[command.index('-filter_complex') - 1]))
        assert paths[-1].is_file()
        calls.append(Process(command, **kwargs))
        return calls[-1]
    monkeypatch.setattr(high.subprocess, 'Popen', start)
    before = deepcopy(scene[0].to_dict())
    main, layer, composite = filters(scene)
    assert 'flags=neighbor' in main and 'format=yuv444p10le' in main
    assert 'in_range=full:out_range=tv' in layer
    assert 'out_color_matrix=bt709' in layer and 'format=yuva444p10le' in layer
    assert 'overlay=format=yuv444p10' in composite and 'alpha=straight' in composite
    assert 'flags=area' in composite and 'format=yuv422p10le' in composite
    assert 'pad=iw+2:ih:0:0:color=black' in composite and 'crop=iw-2:ih:0:0' in composite
    command = calls[0].command
    assert '-noauto_conversion_filters' in command
    assert command[command.index('-pix_fmt') + 1] == 'yuv422p10le'
    assert command[command.index('-pix_fmt', command.index('-pix_fmt') + 1) + 1] == 'bgra'
    assert command[command.index('-frames:v') + 1] == '2'
    assert calls[0].waited and not calls[0].killed
    assert not paths[0].exists()
    assert scene[0].to_dict() == before
    filters(scene)
    assert len(calls) == 2 and all(not path.exists() for path in paths)


@pytest.mark.parametrize('code,size,bad', [
    (1, 0, None), (0, 0, None), (0, 16384, None), (0, 32767, None),
    (0, 32769, None), (0, 32768, 'noop'), (0, 32768, 'eightbit')])
def test_unknown_failed_or_false_precision_probe_fails_closed(scene, monkeypatch, code, size, bad):
    processes = []
    def start(command, **kwargs):
        processes.append(Process(command, **kwargs, code=code, size=size, bad=bad))
        return processes[-1]
    monkeypatch.setattr(high.subprocess, 'Popen', start)
    with pytest.raises(RenderError, match='cannot composite stickers directly'):
        filters(scene)
    assert processes[0].waited


@pytest.mark.parametrize('reason', ['cancel', 'timeout', 'output_budget', 'log_budget'])
def test_probe_reaps_process_and_cleans_input_on_interrupt(scene, monkeypatch, reason):
    processes, paths = [], []
    cancel = threading.Event()
    def start(command, **kwargs):
        paths.append(Path(command[command.index('-filter_complex') - 1]))
        processes.append(Process(command, **kwargs, running=True,
                                 size=65537 if reason == 'output_budget' else 32768,
                                 log_size=65537 if reason == 'log_budget' else 0))
        if reason == 'cancel':
            cancel.set()
        return processes[-1]
    monkeypatch.setattr(high.subprocess, 'Popen', start)
    with pytest.raises(RenderCancelled if reason == 'cancel' else RenderError):
        filters(scene, cancel=cancel, timeout=0 if reason == 'timeout' else 30)
    assert processes[0].killed and processes[0].waited
    assert all(not path.exists() for path in paths)


def test_missing_binary_fails_closed_and_deletes_probe_input(scene, monkeypatch):
    paths = []
    def start(command, **kwargs):
        paths.append(Path(command[command.index('-filter_complex') - 1]))
        raise FileNotFoundError('Missing FFmpeg')
    monkeypatch.setattr(high.subprocess, 'Popen', start)
    with pytest.raises(RenderError, match='compatible FFmpeg'):
        filters(scene)
    assert not paths[0].exists()


def test_pre_cancel_and_empty_scene_never_start_probe(scene, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('No capability process should start')
    monkeypatch.setattr(high.subprocess, 'Popen', forbidden)
    cancel = threading.Event(); cancel.set()
    with pytest.raises(RenderCancelled):
        filters(scene, cancel=cancel)
    scene[0].overlays[0].enabled = False
    assert filters(scene) == ()


def test_cancel_after_fixture_preparation_never_starts_process(scene, monkeypatch):
    cancel = threading.Event()
    prepare = high._probe_layer
    def cancelled_layer():
        cancel.set()
        return prepare()
    def forbidden(*args, **kwargs):
        pytest.fail('Cancellation after preparation must prevent process start')
    monkeypatch.setattr(high, '_probe_layer', cancelled_layer)
    monkeypatch.setattr(high.subprocess, 'Popen', forbidden)
    with pytest.raises(RenderCancelled):
        filters(scene, cancel=cancel)


@pytest.mark.parametrize('bad', ['missing', 'untyped', 'frames', 'hashes', 'digest', 'duration', 'loop'])
def test_only_current_verified_static_asset_records_are_accepted(scene, monkeypatch, bad):
    project, assets = scene
    asset = assets['art']
    if bad == 'missing':
        assets.clear()
    elif bad == 'untyped':
        assets['art'] = object()
    else:
        changes = {'frames': {'frames': asset.frames * 2},
                   'hashes': {'frame_sha256': ()}, 'digest': {'digest': '0' * 64},
                   'duration': {'frame_duration': 2}, 'loop': {'loop': False}}
        assets['art'] = replace(asset, **changes[bad])
    def forbidden(*args, **kwargs):
        pytest.fail('Unknown asset must fail before process start')
    monkeypatch.setattr(high.subprocess, 'Popen', forbidden)
    with pytest.raises(RenderError, match='source identity is missing or unsupported'):
        filters(scene)


@pytest.mark.parametrize('suffix', ['.json', '.JSON', '.gif', '.svg'])
def test_manifest_and_unsupported_source_block_before_verification(scene, suffix):
    scene[0].overlays[0].asset_path = 'unused' + suffix
    with pytest.raises(ValueError, match='only verified static PNG'):
        validate_export_profile(scene[0], 'prores_422_hq_10bit')
    with pytest.raises(RenderError, match='only verified static PNG'):
        filters(scene)
    assert not validate_export_profile(scene[0]).high_bit
    scene[0].overlays[0].enabled = False
    assert validate_export_profile(scene[0], 'prores_422_hq_10bit').high_bit


def test_probe_failure_precedes_art_cache_grading_and_output_publication(scene, monkeypatch, tmp_path):
    renderer = RenderEngine(timeout=10)
    def failed(*args, **kwargs):
        raise RenderError(high.HIGHBIT_STICKER_ERROR)
    def forbidden(*args, **kwargs):
        pytest.fail('Unsupported capability must fail before cache preparation')
    monkeypatch.setattr(high, 'highbit_sticker_filters', failed)
    monkeypatch.setattr(renderer, '_ensure_highbit_encoder', lambda: None)
    monkeypatch.setattr(renderer, '_overlay_segment', forbidden)
    monkeypatch.setattr(renderer, '_graded_segment', forbidden)
    try:
        with pytest.raises(RenderError, match='cannot composite stickers'):
            renderer.render(scene[0], tmp_path / 'failed.mov', export_profile='prores_422_hq_10bit')
        assert not (tmp_path / 'failed.mov').exists()
        assert not list(tmp_path.glob('.lumen-render-*'))
    finally:
        renderer.close()


@pytest.mark.parametrize('enabled,amount,blocked', [(True, 1, False), (False, 1, False), (True, 0, False)])
def test_verified_vignette_coexists_with_static_sticker_profile(scene, enabled, amount, blocked):
    scene[0].clips = [Clip('clip', 'media', 0, 0, 0, 2,
                           effects=[ClipEffect('effect', 'vignette', amount, enabled=enabled)])]
    if blocked:
        with pytest.raises(ValueError, match='Other unverified'):
            validate_export_profile(scene[0], 'prores_422_hq_10bit')
    else:
        assert validate_export_profile(scene[0], 'prores_422_hq_10bit').high_bit


def test_default_eight_bit_graph_is_byte_for_byte_legacy(scene, monkeypatch, tmp_path):
    renderer = RenderEngine(timeout=10)
    def forbidden(*args, **kwargs):
        pytest.fail('Legacy graph must not require high-bit capability')
    monkeypatch.setattr(high, 'highbit_sticker_filters', forbidden)
    monkeypatch.setattr(renderer, '_overlay_segment', lambda *args: tmp_path / 'legacy.mkv')
    before = deepcopy(scene[0].to_dict())
    try:
        inputs, graph, duration = renderer._compile(scene[0])
        assert graph == (
            'color=c=black:s=64x64:r=30/1:d=0.0666666666667,setsar=1,format=yuv420p[base];'
            'anullsrc=r=48000:cl=stereo,atrim=end_sample=3200[silence];'
            '[0:V:0]setpts=PTS-STARTPTS,fps=fps=30/1:start_time=0,'
            'format=rgba,scale=in_color_matrix=bt709:out_color_matrix=bt709:in_range=full:out_range=tv,'
            'format=yuva420p[stickers];'
            '[base][stickers]overlay=eof_action=pass:repeatlast=0:shortest=0:alpha=straight[stickered];'
            '[stickered]trim=duration=0.0666666666667,setpts=PTS-STARTPTS,format=yuv420p,'
            'setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709[vout];'
            '[silence]amix=inputs=1:normalize=0:duration=first,atrim=end_sample=3200[aout]')
        assert inputs == ['-protocol_whitelist', 'file,pipe', '-i', str(tmp_path / 'legacy.mkv')]
        assert duration == 2 / 30 and scene[0].to_dict() == before
    finally:
        renderer.close()


@pytest.mark.parametrize('bad', ['noop', 'bt601', 'eightbit', 'transparent', 'edge', 'final_pair', 'range'])
def test_probe_checks_matrix_full_precision_bypass_and_odd_edges(bad):
    original = high._probe_frame()
    output = oracle(original)
    assert high._valid_probe(output, original)
    samples = list(struct.unpack('<16384H', output))
    if bad == 'noop':
        output = original * 2
    else:
        index, value = {'bt601': (20 * 64 + 20, 325),
                        'eightbit': (1, 0),
                        'transparent': (8192 + 1, 0),
                        'edge': (6144 + 40 * 32, 847),
                        'final_pair': (6144 + 40 * 32 + 31, 960),
                        'range': (0, 2048)}[bad]
        samples[index] = value
        output = struct.pack('<16384H', *samples)
    assert not high._valid_probe(output, original)
