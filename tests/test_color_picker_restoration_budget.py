"""Bounded transfer temporaries and cancellation before spatial restoration."""
import json
import threading

import numpy as np
import pytest

from lumen import color_picker_source as sampler
from lumen.color import normalize_input
from lumen.model import Clip, Media, Project
from lumen.restoration_settings import RestorationSettings


@pytest.fixture
def decoded_picture(tmp_path, monkeypatch):
    # Three processing blocks, but no real decoder or large media fixture.
    width, height = 512, 257
    source = tmp_path / 'bounded-picture.mkv'
    source.write_bytes(b'local source identity')
    pixels = np.arange(width * height * 3, dtype=np.uint32).reshape(height, width, 3)
    rgb16 = ((pixels * 13 + 1025) % 65536).astype('<u2')
    payload = rgb16.tobytes()
    probe = json.dumps({'streams': [{'width': width, 'height': height,
                                    'pix_fmt': 'rgb48le'}]}).encode()
    monkeypatch.setattr(sampler, 'ffmpeg_binary', lambda: 'test-ffmpeg')
    monkeypatch.setattr(sampler, 'ffprobe_binary', lambda: 'test-ffprobe')
    monkeypatch.setattr(sampler, '_run',
                        lambda args, *rest: probe if args[0] == 'test-ffprobe' else payload)
    monkeypatch.setattr(sampler, 'active_picture_size', lambda *args, **kwargs: (width, height))
    clip = Clip('c', 'm', 0, 0, 0, 1)
    clip.restoration = RestorationSettings(enabled=True, denoise=.7)
    clip.grade.enabled = False
    project = Project(width=width, height=height, fps_num=6,
                      media=[Media('m', str(source), 'bounded', 1., width, height, fps_num=6)],
                      clips=[clip])
    return project, rgb16


@pytest.mark.parametrize('encoding', ['srgb', 'linear_rec709'])
def test_input_transfer_and_creative_grade_are_bounded_blocks(decoded_picture, monkeypatch, encoding):
    project, rgb16 = decoded_picture
    project.clips[0].grade.input_encoding = encoding
    transfer_blocks, grade_blocks, restored_inputs = [], [], []
    original_grade = sampler.apply_grade

    def normalize(block, assigned_encoding):
        transfer_blocks.append((len(block), assigned_encoding))
        assert block.dtype == np.float32 and block.shape[1:] == (3,)
        assert len(block) <= 65536
        return normalize_input(block, assigned_encoding)

    def make_restore(settings, active_size, width, height, cancel, cancelled_type):
        def restore(rgb):
            assert rgb.shape == (height, width, 3)
            assert not cancel.is_set()
            restored_inputs.append(rgb.copy())
            return rgb
        return restore

    def grade(block, settings):
        grade_blocks.append((len(block), settings.input_encoding))
        assert len(block) <= 65536
        return original_grade(block, settings)

    monkeypatch.setattr(sampler, 'normalize_input', normalize)
    monkeypatch.setattr(sampler, 'normalized_restoration', make_restore)
    monkeypatch.setattr(sampler, 'apply_grade', grade)
    result = sampler.capture_picker_snapshot(project, 'c', 0)
    assert transfer_blocks == [(65536, encoding), (65536, encoding), (512, encoding)]
    assert grade_blocks == [(65536, 'rec709'), (65536, 'rec709'), (512, 'rec709')]
    assert len(restored_inputs) == 1
    expected = normalize_input(rgb16.astype(np.float32) / np.float32(65535), encoding)
    np.testing.assert_array_equal(restored_inputs[0], expected)
    np.testing.assert_array_equal(result.frame, expected)
    assert project.clips[0].grade.input_encoding == encoding
    assert not result.frame.flags.writeable


@pytest.mark.parametrize('stop', ['cancel', 'deadline'])
def test_stop_after_first_normalization_block_never_runs_restoration(decoded_picture, monkeypatch, stop):
    project, _ = decoded_picture
    project.clips[0].grade.input_encoding = 'srgb'
    cancel = threading.Event()
    now = [0.]
    blocks = []
    monkeypatch.setattr(sampler.time, 'monotonic', lambda: now[0])

    def normalize(block, encoding):
        blocks.append(len(block))
        result = normalize_input(block, encoding)
        if stop == 'cancel':
            cancel.set()
        else:
            now[0] = 31.
        return result

    def unexpected(*args, **kwargs):
        pytest.fail('Stopped input normalization must not reach restoration or creative grading')

    monkeypatch.setattr(sampler, 'normalize_input', normalize)
    monkeypatch.setattr(sampler, 'normalized_restoration', unexpected)
    monkeypatch.setattr(sampler, 'apply_grade', unexpected)
    error = sampler.PickerSourceCancelled if stop == 'cancel' else sampler.PickerSourceError
    message = 'cancelled' if stop == 'cancel' else 'timed out'
    with pytest.raises(error, match=message):
        sampler.capture_picker_snapshot(project, 'c', 0, cancel=cancel, timeout=30)
    assert blocks == [65536]
