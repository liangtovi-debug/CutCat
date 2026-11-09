"""Atomic same-track roll with independent exact speeds and partial tails."""
from copy import deepcopy
from fractions import Fraction

import pytest
from lumen.model import Caption, Clip, Editor, Media, Overlay, OverlayKeyframe, Project
from lumen.timing import ClipTimeMap, duration_frames


def make_editor(left_speed=Fraction(3, 2), right_speed=Fraction(2, 3)):
    a = ClipTimeMap(Fraction(1, 2), 31, left_speed)
    b = ClipTimeMap(20, Fraction(101, 2), right_speed)
    left = Clip('left', 'm', 0, 10, 0, duration_frames(a), time_map=a, fade_in=2, fade_out=3)
    right = Clip('right', 'm', 0, left.end, 0, duration_frames(b), time_map=b, fade_in=3, fade_out=2)
    media = Media('m', '/tmp/source.mp4', 'Source', 10, 64, 48, True, True)
    return Editor(Project(media=[media], clips=[left, right, Clip('later', 'm', 0, right.end, 100, 12),
                  Clip('audio', 'm', 2, 0, 0, 200)], captions=[Caption('caption', 20, 10, 'Fixed')],
                  overlays=[Overlay('sticker', '/tmp/asset.png', 'a'*64, 23, 8, keyframes=[OverlayKeyframe(0)])]))


@pytest.mark.parametrize('left_speed', [Fraction(1, 4), Fraction(2, 3), Fraction(1), Fraction(3, 2), Fraction(4)])
@pytest.mark.parametrize('right_speed', [Fraction(1, 4), Fraction(2, 3), Fraction(1), Fraction(3, 2), Fraction(4)])
@pytest.mark.parametrize('delta', [-2, 3])
def test_roll_exact_partial_tails_outer_span_settings_and_anchors(left_speed, right_speed, delta):
    e = make_editor(left_speed, right_speed); before = deepcopy(e.project)
    left, right = before.clips[:2]; cut = left.end + delta
    preview = e.preview_roll_cut(left.id, cut)
    assert e.project == before and not e.can_undo
    a, b = e.roll_cut(left.id, cut)
    assert (a, b) == preview
    assert a.timing == ClipTimeMap(left.source_in, left.source_out + delta * left.speed, left.speed)
    assert b.timing == ClipTimeMap(right.source_in + delta * right.speed, right.source_out, right.speed)
    assert (a.start, a.end, b.start, b.end) == (left.start, cut, cut, right.end)
    for old, new in ((left, a), (right, b)):
        assert old.duration * old.speed - (old.source_out-old.source_in) == new.duration * new.speed - (new.source_out-new.source_in)
        for field in ('id', 'media_id', 'track', 'speed', 'fade_in', 'fade_out', 'gain_db', 'muted', 'grade', 'effects', 'restoration', 'stabilization'):
            assert getattr(new, field) == getattr(old, field)
    assert e.project.clips[2:] == before.clips[2:]
    assert e.project.captions == before.captions and e.project.overlays == before.overlays
    assert e.project.duration_frames == before.duration_frames
    after = deepcopy(e.project)
    assert Project.from_dict(after.to_dict()) == after
    assert len(e._undo) == 1 and e.undo() and e.project == before
    assert e.redo() and e.project == after


@pytest.mark.parametrize('cut', [-1, 0, 10, 77, 1000, True, 30.0, Fraction(30), '30'])
def test_invalid_boundary_is_atomic_and_keeps_redo(cut):
    e = make_editor(); e.roll_cut('left', 33); e.undo()
    before, undo, redo = deepcopy((e.project, e._undo, e._redo))
    with pytest.raises(ValueError): e.roll_cut('left', cut)
    assert (e.project, e._undo, e._redo) == (before, undo, redo)


def test_noop_preserves_redo_and_returns_current_clips():
    e = make_editor(); e.roll_cut('left', 33); e.undo()
    before, undo, redo = deepcopy((e.project, e._undo, e._redo))
    a, b = e.roll_cut('left', 31)
    assert a is e.project.clips[0] and b is e.project.clips[1]
    assert (e.project, e._undo, e._redo) == (before, undo, redo)


@pytest.mark.parametrize('change', ['gap', 'different_track', 'missing'])
def test_requires_actual_same_track_adjacency(change):
    e = make_editor()
    if change == 'gap':
        e.project.clips[1].start += 1; e.project.clips[2].start += 1
    elif change == 'different_track': e.project.clips[1].track = 1
    else: e.project.clips.pop(1)
    e.project.validate(); before = deepcopy(e.project)
    with pytest.raises(ValueError, match='adjacent'): e.roll_cut('left', 32)
    assert e.project == before and not e.can_undo


@pytest.mark.parametrize('side', ['left', 'right'])
def test_fades_reject_shortening_instead_of_clamping(side):
    e = make_editor(); c = e._clip(side); c.fade_in = c.duration; c.fade_out = c.duration
    e.project.validate(); before = deepcopy(e.project)
    with pytest.raises(ValueError, match='fade'): e.roll_cut('left', 30 if side == 'left' else 32)
    assert e.project == before and not e.can_undo
    # Overlapping in/out fades remain valid when their individual lengths fit.
    e.roll_cut('left', 32 if side == 'left' else 30)


def test_source_bounds_and_exact_limit():
    e = make_editor(Fraction(4), Fraction(1, 4))
    e.project.clips[0].fade_in = e.project.clips[0].fade_out = 0
    # Right source origin would become negative when the cut moves far left.
    e.project.clips[1].time_map = ClipTimeMap(Fraction(1, 4), Fraction(123, 4), Fraction(1, 4))
    e.project.clips[1].source_in = Fraction(1, 4)
    e.project.clips[1].duration = 122
    e.project.validate()
    before = deepcopy(e.project)
    with pytest.raises(ValueError): e.roll_cut('left', 16)
    assert e.project == before
    # Selected source's outgoing handle reaches the conservative bound exactly.
    e = make_editor(); e.project.clips[0].time_map = ClipTimeMap(Fraction(537, 2), 299, Fraction(3, 2)); e.project.clips[0].source_in=Fraction(537,2)
    e.project.validate(); before = deepcopy(e.project)
    with pytest.raises(ValueError, match='source media'): e.roll_cut('left', 32)
    assert e.project == before


def test_precision_overflow_is_atomic():
    e = make_editor()
    c = e.project.clips[0]
    c.time_map = ClipTimeMap(0, Fraction(9999830-1, 999983), Fraction(1, 999979)*999978)
    c.source_in = 0; c.duration = duration_frames(c.time_map)
    e.project.clips[1].start = c.end
    e.project.validate(); before = deepcopy(e.project)
    with pytest.raises(ValueError, match='rational bounds'): e.roll_cut('left', c.end+1)
    assert e.project == before and not e.can_undo


def test_generated_audio_roll_does_not_rewrite_provenance(tmp_path):
    from test_tts_exact_timing import speech
    media = speech(tmp_path, 240000)
    e = make_editor(); e.project.media = [media]
    e.project.clips = e.project.clips[:2]
    for c in e.project.clips: c.media_id = media.id; c.track = 2
    e.project.validate(); provenance = deepcopy(media.speech_provenance)
    e.roll_cut('left', 33)
    assert e.project.media[0].speech_provenance == provenance
    assert Project.from_dict(e.project.to_dict()) == e.project


def test_unsorted_clips_neighbor_lookup_and_one_frame_edges():
    e=make_editor(Fraction(1),Fraction(1)); e.project.clips=e.project.clips[::-1]
    for c in e.project.clips: c.fade_in=c.fade_out=0
    right=e._clip('right'); right.time_map=ClipTimeMap(50,Fraction(161,2)); right.source_in=50
    e.project.validate(); before=deepcopy(e.project)
    e.roll_cut('left',11)
    assert e._clip('left').duration==1 and e._clip('right').end==before.clips[-2].end
    assert e.undo()
    e.roll_cut('left',e._clip('right').end-1)
    assert e._clip('right').duration==1


def test_exact_outgoing_source_boundary_is_inclusive_as_endpoint():
    e=make_editor(); c=e._clip('left')
    c.time_map=ClipTimeMap(268,Fraction(597,2),Fraction(3,2)); c.source_in=268
    e.project.validate()
    # One whole timeline frame adds 1.5 exact source-frame units.
    e.roll_cut('left',32); assert e._clip('left').source_out==300
    before=deepcopy(e.project)
    with pytest.raises(ValueError,match='source media'): e.roll_cut('left',33)
    assert e.project==before
