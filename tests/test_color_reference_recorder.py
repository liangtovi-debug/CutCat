"""Deterministic observer-order regression, separate from actual Qt decoder proof."""
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QImage, QColor

import test_color_reference_render as render


LEGACY_LOAD_ACTUAL_PREVIEW = "def load_actual_preview(window, app, path):\n    window.preview_paths[(window.revision, 'graded')] = path\n    window.activate_preview(path, window.revision, 'graded')\n    decoded = {}\n    def receive(frame):\n        if frame.isValid():\n            format_ = frame.surfaceFormat()\n            decoded[round(Fraction(frame.startTime(), 1_000_000) * window.project.fps)] = {\n                'image': preview_frame_image(frame),\n                'uncorrected_qt_image': frame.toImage().copy(),\n                'start_us': frame.startTime(), 'end_us': frame.endTime(),\n                'pixel_format': str(format_.pixelFormat()),\n                'color_space': str(format_.colorSpace()),\n                'color_range': str(format_.colorRange()),\n                'color_transfer': str(format_.colorTransfer()),\n            }\n    window.video.videoSink().videoFrameChanged.connect(receive)\n    # Qt's multimedia backend primes its decoder when playback first starts.\n    window.player.play()\n    wait_until(app, lambda: window.video.frame_provenance is not None,\n               diagnostic=lambda: window.player.errorString())\n    window.player.pause()\n    return decoded\n"


class Signal:
    def __init__(self): self.receivers = []
    def connect(self, receiver): self.receivers.append(receiver)
    def emit(self, value):
        for receiver in tuple(self.receivers): receiver(value)


class Frame:
    def __init__(self):
        self.image = QImage(2, 2, QImage.Format.Format_RGBA8888)
        self.image.fill(QColor('#123456'))
    def isValid(self): return True
    def startTime(self): return 366667
    def endTime(self): return 400000
    def toImage(self): return self.image.copy()
    def surfaceFormat(self):
        return SimpleNamespace(pixelFormat=lambda: 'synthetic', colorSpace=lambda: 'BT709',
                               colorRange=lambda: 'limited', colorTransfer=lambda: 'BT709')


class EarlyWindow:
    """One target emission inside activation; same-frame seek emits nothing."""
    def __init__(self):
        self.revision = 7
        self.project = SimpleNamespace(fps=Fraction(30))
        self.preview_paths = {}
        self._preview_generation = 0
        self._reference_test_decoded = {}
        self.video = SimpleNamespace(frame_provenance=None, image=QImage())
        self._sink = SimpleNamespace(videoFrameChanged=Signal())
        self.video.videoSink = lambda: self._sink
        self.player = SimpleNamespace(play=lambda: None, pause=lambda: None,
                                      errorString=lambda: '', position=lambda: 367)
        self.requests = []
        self.frame = Frame()
        self.fail_activation = False
    def replace_preview_player(self):
        self._preview_generation += 1
        self._sink = SimpleNamespace(videoFrameChanged=Signal())
        def production(frame):
            self.video.image = frame.toImage()
            self.video.frame_provenance = SimpleNamespace(
                timeline_frame=11, start_us=frame.startTime(), end_us=frame.endTime(),
                generation=self._preview_generation, revision=self.revision,
                preview_path=self._preview_path)
        self._sink.videoFrameChanged.connect(production)
    def activate_preview(self, path, revision, mode):
        self.replace_preview_player()
        if self.fail_activation: raise RuntimeError('synthetic activation error')
        self._preview_path = str(Path(path).expanduser().absolute())
        self._sink.videoFrameChanged.emit(self.frame)
        return True
    def seek_frame(self, target):
        if self.video.frame_provenance.timeline_frame == target: return
        self.requests.append(target)


@pytest.fixture
def immediate_observer(monkeypatch):
    monkeypatch.setattr(render, 'preview_frame_image', lambda frame: frame.toImage())
    def wait(app, predicate, *, timeout=15, diagnostic=lambda: ''):
        assert timeout == 15  # No timeout inflation or hidden retries.
        assert predicate(), 'target observer missing or mismatched'
    monkeypatch.setattr(render, 'wait_until', wait)


def test_early_emission_reproduces_original_failure_and_fixed_helper_passes(immediate_observer):
    namespace = dict(vars(render))
    exec(LEGACY_LOAD_ACTUAL_PREVIEW, namespace)
    legacy_window = EarlyWindow()
    legacy = namespace['load_actual_preview'](legacy_window, None, '/synthetic-preview.mp4')
    assert legacy_window.video.frame_provenance.timeline_frame == 11
    assert legacy == {}  # Production already accepted11; late observer saw none.
    legacy_window.seek_frame(11)
    assert legacy_window.requests == []
    with pytest.raises(KeyError): legacy[11]
    with pytest.raises(AssertionError, match='target observer missing'):
        render.seek_decoded(legacy_window, None, 11)

    fixed_window = EarlyWindow()
    old_sink = fixed_window.video.videoSink()
    fixed = render.load_actual_preview(fixed_window, None, '/synthetic-preview.mp4')
    provenance = render.seek_decoded(fixed_window, None, 11)
    assert fixed_window.requests == []
    assert old_sink.videoFrameChanged.receivers == []
    assert len(fixed_window.video.videoSink().videoFrameChanged.receivers) == 2
    assert fixed[11]['image'] == fixed_window.video.image == fixed_window.frame.toImage()
    assert fixed[11]['start_us'] == provenance.start_us == 366667
    assert fixed[11]['end_us'] == provenance.end_us == 400000
    assert fixed[11]['generation'] == provenance.generation
    assert fixed[11]['revision'] == provenance.revision
    assert fixed[11]['preview_path'] == provenance.preview_path
    assert 'replace_preview_player' not in fixed_window.__dict__


@pytest.mark.parametrize('field', ['start_us', 'end_us', 'generation', 'revision', 'preview_path'])
def test_recorder_requires_matching_provenance(immediate_observer, field):
    window = EarlyWindow()
    decoded = render.load_actual_preview(window, None, '/synthetic-preview.mp4')
    decoded[11][field] = 'wrong' if field == 'preview_path' else -1
    with pytest.raises(AssertionError, match='target observer missing or mismatched'):
        render.seek_decoded(window, None, 11)


def test_scoped_recorder_hook_restores_method_when_activation_raises(immediate_observer):
    window = EarlyWindow()
    window.fail_activation = True
    with pytest.raises(RuntimeError, match='synthetic activation error'):
        render.load_actual_preview(window, None, '/synthetic-preview.mp4')
    assert 'replace_preview_player' not in window.__dict__
    assert window.replace_preview_player.__func__ is EarlyWindow.replace_preview_player
