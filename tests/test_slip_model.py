"""Non-ripple exact-span source slip, including partial retimed tails."""
from copy import deepcopy
from fractions import Fraction

import pytest
from lumen.model import Caption, Clip, Editor, Media, Project
from lumen.timing import ClipTimeMap, duration_frames


def make_editor(speed=Fraction(3, 2)):
    mapping = ClipTimeMap(Fraction(1, 2), 31, speed)
    clip = Clip('c', 'm', 0, 10, 0, duration_frames(mapping), time_map=mapping,
                fade_in=2, fade_out=3)
    media = Media('m', '/tmp/source.mp4', 'Source', 10, 64, 48, True, True)
    return Editor(Project(media=[media], clips=[clip], captions=[Caption('t', 12, 4, 'Fixed')]))


@pytest.mark.parametrize('speed', [Fraction(1, 4), Fraction(2, 3), Fraction(1), Fraction(3, 2), Fraction(4)])
@pytest.mark.parametrize('origin', [0, '1/3', '6.5', Fraction(21, 2)])
def test_slip_preserves_exact_span_timing_fades_anchors_and_history(speed, origin):
    e = make_editor(speed); before = deepcopy(e.project); c = before.clips[0]
    e.project.clips.append(Clip('next', 'm', 0, c.end, 40, 10))
    before = deepcopy(e.project)
    moved = e.slip_clip('c', origin)
    delta = Fraction(origin) - c.source_in
    expected = deepcopy(c)
    expected.time_map = ClipTimeMap(Fraction(origin), c.source_out + delta, speed)
    expected.source_in = Fraction(origin)
    expected._normalize_legacy_timing()
    assert moved == expected
    assert e.project.clips[1] == before.clips[1]
    assert e.project.captions == before.captions
    after = deepcopy(e.project)
    assert Project.from_dict(after.to_dict()) == after
    assert e.undo() and e.project == before
    assert e.redo() and e.project == after


@pytest.mark.parametrize('origin', [-1, '-0.1', 280, '1/1000001', True, .5, 'NaN', '1/0'])
def test_invalid_slip_is_atomic(origin):
    e = make_editor(); before = deepcopy(e.project); undo = deepcopy(e._undo)
    with pytest.raises(ValueError):
        e.slip_clip('c', origin)
    assert e.project == before and e._undo == undo and not e.can_redo


def test_noop_keeps_redo_and_history():
    e = make_editor(); e.slip_clip('c', 7); e.undo()
    before = deepcopy(e.project); undo = deepcopy(e._undo); redo = deepcopy(e._redo)
    e.slip_clip('c', '0.5')
    assert e.project == before and e._undo == undo and e._redo == redo


def test_exact_end_boundary_and_one_fraction_beyond():
    e = make_editor(); span = e._clip('c').source_out - e._clip('c').source_in
    e.slip_clip('c', 300 - span)
    assert e._clip('c').source_out == 300
    before = deepcopy(e.project)
    with pytest.raises(ValueError): e.slip_clip('c', 300 - span + Fraction(1, 1000))
    assert e.project == before


def test_slip_rejects_new_endpoint_precision_overflow_atomically():
    e = make_editor()
    e.trim_clip_source('c', 0, Fraction(1, 999983))
    before = deepcopy(e.project); undo = deepcopy(e._undo)
    with pytest.raises(ValueError): e.slip_clip('c', Fraction(1, 999979))
    assert e.project == before and e._undo == undo


def test_slipping_generated_speech_preserves_synthesis_provenance(tmp_path):
    from test_tts_exact_timing import speech
    media = speech(tmp_path, 240000)
    mapping = ClipTimeMap(Fraction(1, 2), 31, Fraction(3, 2))
    clip = Clip('c', media.id, 2, 10, 0, duration_frames(mapping), time_map=mapping)
    e = Editor(Project(media=[media], clips=[clip])); provenance = deepcopy(media.speech_provenance)
    e.slip_clip('c', '4.5')
    assert e.project.media[0].speech_provenance == provenance
    assert e._clip('c').speed == Fraction(3, 2)
    assert e._clip('c').source_out == 35
