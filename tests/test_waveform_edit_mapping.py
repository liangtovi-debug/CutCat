"""Source waveform follows edit history, never changing edit or render semantics."""
from fractions import Fraction
import copy
import numpy as np
import pytest
from lumen.audio_waveform import peaks_from_samples, map_peaks
from lumen.model import Project, Media, Clip, Editor
from lumen.timing import ClipTimeMap, duration_frames


def setup_editor():
    media = Media('m', '/synthetic.wav', 'Synthetic', 12, has_video=False, has_audio=True)
    clip = Clip('c', 'm', 2, 0, 0, 240)
    project = Project(media=[media], clips=[clip])
    return Editor(project)


def envelope():
    samples = np.zeros(12 * 48000, dtype=np.float32)
    samples[48000:96000] = .25
    samples[3 * 48000:4 * 48000] = .5
    samples[6 * 48000:7 * 48000] = .9
    return peaks_from_samples(samples, block_size=1)


def values(peaks, project, clip=None):
    return map_peaks(peaks, clip or project.clips[0], project.fps, pixels=240)


def test_trim_slip_move_undo_redo_and_split_reaggregate_one_source():
    editor = setup_editor(); peaks = envelope()
    original = values(peaks, editor.project)
    before = copy.deepcopy(editor.project)
    assert np.count_nonzero(original)
    assert editor.project == before and not editor.can_undo
    editor.trim_clip('c', 30, 180)
    trimmed = values(peaks, editor.project)
    assert not np.array_equal(original, trimmed)
    editor.slip_clip('c', 60)
    slipped = values(peaks, editor.project)
    assert not np.array_equal(trimmed, slipped)
    editor.move_clip('c', 30)
    assert np.array_equal(values(peaks, editor.project), slipped)
    assert editor.undo() and np.array_equal(values(peaks, editor.project), slipped)
    assert editor.undo() and np.array_equal(values(peaks, editor.project), trimmed)
    assert editor.redo() and np.array_equal(values(peaks, editor.project), slipped)
    clip = editor.project.clips[0]
    full = map_peaks(peaks, clip, editor.project.fps, pixels=180)
    left, right = editor.split_clip('c', clip.start + 90)
    joined = np.concatenate([map_peaks(peaks, c, editor.project.fps, pixels=90) for c in (left, right)])
    np.testing.assert_array_equal(joined, full)
    assert editor.undo()
    np.testing.assert_array_equal(map_peaks(peaks, editor.project.clips[0], editor.project.fps, pixels=180), full)


@pytest.mark.parametrize('speed', [Fraction(1,4), Fraction(1,2), Fraction(1), Fraction(3,2), Fraction(4)])
def test_gain_mute_fades_and_timeline_move_do_not_change_source_envelope(speed):
    peaks = envelope(); mapping = ClipTimeMap(0, 240, speed)
    clip = Clip('c', 'm', 2, 0, 0, duration_frames(mapping), time_map=mapping)
    first = map_peaks(peaks, clip, Fraction(30), pixels=240)
    clip.gain_db = -12; clip.muted = True; clip.fade_in = 10; clip.fade_out = 12
    clip.start = 120; clip.track = 3
    np.testing.assert_array_equal(map_peaks(peaks, clip, Fraction(30), pixels=240), first)
