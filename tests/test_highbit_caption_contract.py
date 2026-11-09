"""Fail-closed capabilities and unchanged caption validation/publication rules."""
from copy import deepcopy
from pathlib import Path
import threading
import struct

import pytest

from lumen import highbit_captions as high
from lumen.color_output import validate_export_profile
from lumen.model import Caption, CaptionStyle, Project
from lumen.render import RenderCancelled, RenderEngine, RenderError


@pytest.fixture
def project():
    return Project(width=320, height=180, captions=[Caption('cue', 1, 2, 'Literal caption')],
                   caption_style=CaptionStyle(font_size=24, margin_bottom=20))


@pytest.fixture
def stub_layout(monkeypatch, tmp_path):
    monkeypatch.setattr(high, 'caption_filters', lambda *args: ['drawtext=text=literal'])
    monkeypatch.setattr(high, 'prepare_font', lambda *args: tmp_path / 'font.ttf')


class Process:
    def __init__(self, command, *, stdin, stdout, stderr, code=0, size=16384, running=False):
        self.command = command
        self.returncode = code
        self.running = running
        self.killed = False
        self.waited = False
        data = list(struct.unpack('<8192H', stdin.read()))
        for y in range(16, 32):
            for x in range(16, 32):
                data[y * 64 + x] = 250
                data[4096 + y * 32 + x // 2] = 411
                data[6144 + y * 32 + x // 2] = 961
        payload = struct.pack('<8192H', *data)
        stdout.write((payload + b'\0' * max(0, size - len(payload)))[:size])
        stdout.flush()

    def poll(self):
        return None if self.running else self.returncode

    def kill(self):
        self.killed = True
        self.running = False

    def wait(self):
        self.waited = True
        return self.returncode


def compile_filters(p, tmp_path, cancel=None, timeout=30):
    return high.highbit_caption_filters(p, tmp_path, timeout, cancel, RenderError, RenderCancelled)


def test_capability_requires_native_format_no_implicit_conversion(project, stub_layout, monkeypatch, tmp_path):
    calls = []
    def start(command, **kwargs):
        calls.append(Process(command, **kwargs))
        return calls[-1]
    monkeypatch.setattr(high.subprocess, 'Popen', start)
    before = deepcopy(project.to_dict())
    result = compile_filters(project, tmp_path)
    assert result[0] == result[-1] == 'format=yuv422p10le'
    assert 'colorspace=bt709' in result[1] and 'range=limited' in result[1]
    assert result[2] == 'drawtext=text=literal'
    command = calls[0].command
    assert '-noauto_conversion_filters' in command
    assert command[command.index('-pix_fmt') + 1] == 'yuv422p10le'
    assert command[command.index('-frames:v') + 1] == '1'
    assert calls[0].waited and not calls[0].killed
    assert project.to_dict() == before
    # No stale global capability cache survives a binary replacement/export.
    compile_filters(project, tmp_path)
    assert len(calls) == 2


@pytest.mark.parametrize('code,size', [(1, 0), (0, 8192), (0, 16383), (0, 16385)])
def test_failed_or_wrong_typed_probe_rejected(project, stub_layout, monkeypatch, tmp_path, code, size):
    processes = []
    def start(command, **kwargs):
        processes.append(Process(command, **kwargs, code=code, size=size))
        return processes[-1]
    monkeypatch.setattr(high.subprocess, 'Popen', start)
    with pytest.raises(RenderError, match='cannot burn captions directly'):
        compile_filters(project, tmp_path)
    assert processes[0].waited


def test_missing_binary_rejected(project, stub_layout, monkeypatch, tmp_path):
    def start(*args, **kwargs):
        raise FileNotFoundError('not installed')
    monkeypatch.setattr(high.subprocess, 'Popen', start)
    with pytest.raises(RenderError, match='compatible FFmpeg'):
        compile_filters(project, tmp_path)


@pytest.mark.parametrize('reason', ['cancel', 'timeout', 'budget'])
def test_probe_cleans_up_on_cancel_timeout_budget(project, stub_layout, monkeypatch, tmp_path, reason):
    processes = []
    cancel = threading.Event()
    def start(command, **kwargs):
        processes.append(Process(command, **kwargs, running=True,
                                 size=65537 if reason == 'budget' else 16384))
        if reason == 'cancel': cancel.set()
        return processes[-1]
    monkeypatch.setattr(high.subprocess, 'Popen', start)
    expected = RenderCancelled if reason == 'cancel' else RenderError
    with pytest.raises(expected):
        compile_filters(project, tmp_path, cancel=cancel, timeout=0 if reason == 'timeout' else 30)
    assert processes[0].killed and processes[0].waited


def test_pre_cancel_does_not_start_font_or_subprocess(project, monkeypatch, tmp_path):
    cancel = threading.Event(); cancel.set()
    def forbidden(*args, **kwargs):
        pytest.fail('Pre-cancel must not start preparation')
    monkeypatch.setattr(high, 'caption_filters', forbidden)
    monkeypatch.setattr(high.subprocess, 'Popen', forbidden)
    with pytest.raises(RenderCancelled):
        compile_filters(project, tmp_path, cancel=cancel)


def test_highbit_caption_policy_keeps_8bit_default_and_project_unchanged(project):
    before = deepcopy(project.to_dict())
    assert validate_export_profile(project).key == 'h264_8bit'
    assert validate_export_profile(project, 'prores_422_hq_10bit').high_bit
    assert project.to_dict() == before


@pytest.mark.parametrize('invalid', ['negative_start', 'overlap', 'bad_color', 'style_type', 'bad_fontsize'])
def test_existing_caption_validation_remains_fail_closed(project, tmp_path, invalid):
    if invalid == 'negative_start': project.captions[0].start = -1
    elif invalid == 'overlap': project.captions.append(Caption('overlap', 2, 2, 'other'))
    elif invalid == 'bad_color': project.caption_style.color = '#FFFFFF80'
    elif invalid == 'style_type': project.caption_style = {'unknown': True}
    else: project.caption_style.font_size = 0
    engine = RenderEngine(timeout=10)
    try:
        with pytest.raises((ValueError, RenderError)):
            engine.render(project, tmp_path / 'invalid.mov', export_profile='prores_422_hq_10bit')
        assert not (tmp_path / 'invalid.mov').exists()
        assert not list(tmp_path.glob('.lumen-render-*'))
    finally: engine.close()


def test_capability_failure_never_publishes_or_grades(project, tmp_path, monkeypatch):
    engine = RenderEngine(timeout=10)
    def fail(*args, **kwargs):
        raise RenderError(high.HIGHBIT_CAPTION_ERROR)
    def forbidden(*args, **kwargs):
        pytest.fail('Capability rejection must occur before grading')
    monkeypatch.setattr(high, 'highbit_caption_filters', fail)
    monkeypatch.setattr(engine, '_graded_segment', forbidden)
    try:
        with pytest.raises(RenderError, match='cannot burn captions'):
            engine.render(project, tmp_path / 'failed.mov', export_profile='prores_422_hq_10bit')
        assert not (tmp_path / 'failed.mov').exists()
        assert not list(tmp_path.glob('.lumen-render-*'))
    finally: engine.close()


def test_legacy_preview_does_not_require_highbit_capability(project, monkeypatch):
    engine = RenderEngine(timeout=10)
    def forbidden(*args, **kwargs):
        pytest.fail('8-bit compilation must not run high-bit probe')
    monkeypatch.setattr(high, 'highbit_caption_filters', forbidden)
    try:
        _, graph, _ = engine._compile(project)
        assert 'drawtext' in graph
        assert 'format=yuv422p10le' not in graph
    finally: engine.close()


@pytest.mark.parametrize('bad', ['noop', 'matrix601', 'eightbit', 'wrong_chroma', 'out_of_range'])
def test_probe_validates_real_drawing_matrix_and_uncovered_precision(bad):
    original = high._probe_frame()
    source = list(struct.unpack('<8192H', original))
    output = source.copy()
    for y in range(16, 32):
        for x in range(16, 32):
            output[y * 64 + x] = 250
            output[4096 + y * 32 + x // 2] = 411
            output[6144 + y * 32 + x // 2] = 961
    assert high._valid_probe(struct.pack('<8192H', *output), original)
    if bad == 'noop': output = source
    elif bad == 'matrix601': output[20 * 64 + 20] = 325
    elif bad == 'eightbit': output[1] = output[1] // 4 * 4
    elif bad == 'wrong_chroma': output[4096 + 20 * 32 + 10] = 362
    else: output[63 * 64] = 2048
    assert not high._valid_probe(struct.pack('<8192H', *output), original)


def test_cancel_during_layout_does_not_start_subprocess(project, monkeypatch, tmp_path):
    cancel = threading.Event()
    def layout(*args):
        cancel.set()
        return ['drawtext=text=literal']
    def forbidden(*args, **kwargs):
        pytest.fail('Cancel after preparation must not start capability process')
    monkeypatch.setattr(high, 'caption_filters', layout)
    monkeypatch.setattr(high, 'prepare_font', lambda *args: tmp_path / 'font.ttf')
    monkeypatch.setattr(high.subprocess, 'Popen', forbidden)
    with pytest.raises(RenderCancelled):
        compile_filters(project, tmp_path, cancel=cancel)
