"""Track-local ripple deletion: exact duration removal, atomic history and anchors."""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import math

import pytest

from lumen.model import Caption, Clip, ClipEffect, Editor, Grade, Media, Overlay, OverlayKeyframe, Project
from lumen.clip_transform import ClipTransform
from lumen.restoration_settings import RestorationSettings
from lumen.stabilization_settings import StabilizationSettings
from lumen.timing import ClipTimeMap


def make_editor(track=0, speed=Fraction(3, 2)):
    mapping = ClipTimeMap(Fraction(5, 4), Fraction(103, 4), speed)
    selected = Clip('selected', 'm', track, 10, 0, math.ceil((mapping.source_out-mapping.source_in)/speed), time_map=mapping)
    later = Clip('later', 'm', track, selected.end+7, 0, 17,
                 time_map=ClipTimeMap(Fraction(7, 4), Fraction(105, 4), Fraction(3, 2)),
                 fade_in=6, fade_out=11, gain_db=-4, muted=True,
                 grade=Grade(exposure=.3, lift=[.1, .2, .3]))
    if track < 2:
        later.effects=[ClipEffect('blur', 'blur', 1.5)]
        later.transform=ClipTransform(x=3, y=-2, scale=1.1, rotation=7, mirror_x=True)
        later.restoration=RestorationSettings(True, .2, .3, .4)
        later.stabilization=StabilizationSettings(True, .5, 7, 1.2, 'black')
    # Deliberately unsorted, with a gap on either side of the removed clip.
    clips=[later, Clip('before', 'm', track, 2, 0, 5), selected,
           Clip('last', 'm', track, later.end+9, 40, 3)]
    clips += [Clip(f'anchor-{t}', 'm', t, 6, 0, 80) for t in range(4) if t != track]
    return Editor(Project(media=[Media('m', '/tmp/synthetic.mp4', 'Synthetic', 20, 64, 48, True, True)],
                          clips=clips, captions=[Caption('caption', selected.end+1, 5, 'Fixed')],
                          overlays=[Overlay('overlay', '/tmp/sticker.png', 'a'*64, selected.end+2, 6,
                                            keyframes=[OverlayKeyframe(0)])]))


@pytest.mark.parametrize('track', range(4))
@pytest.mark.parametrize('speed', [Fraction(1,4), Fraction(2,3), Fraction(1), Fraction(3,2), Fraction(4)])
def test_ripple_exact_duration_all_settings_gaps_and_anchors(track, speed):
    e=make_editor(track, speed); before=deepcopy(e.project); selected=e._clip('selected')
    e.ripple_delete_clip('selected')
    expected=deepcopy(before)
    expected.clips=[replace(c, start=c.start-selected.duration)
                    if c.track==track and c.start>=selected.end else c
                    for c in expected.clips if c.id!='selected']
    assert e.project==expected  # Entire settings trees and all media provenance, not selected fields.
    assert e._clip('later').start-e._clip('before').end==10-7+7
    assert e._clip('last').start-e._clip('later').end==9
    assert Project.from_dict(e.project.to_dict())==expected
    assert len(e._undo)==1 and e.revision==1 and not e.can_redo
    assert e.undo() and e.project==before and not e.can_undo
    assert e.redo() and e.project==expected and not e.can_redo


@pytest.mark.parametrize('damage', ['overlap', 'selected-duration', 'later-origin', 'unrelated-track', 'caption', 'missing-id'])
def test_prevalidation_rejects_invalid_state_without_history_or_partial_changes(damage):
    e=make_editor(); e.ripple_delete_clip('selected'); e.undo()
    if damage=='overlap': e._clip('later').start=e._clip('selected').end-1
    elif damage=='selected-duration': e._clip('selected').duration=0
    elif damage=='later-origin': e._clip('later').source_in=999
    elif damage=='unrelated-track': e._clip('anchor-1').track=8
    elif damage=='caption': e.project.captions[0].duration=0
    before=deepcopy((e.project, e._undo, e._redo, e.revision))
    with pytest.raises(ValueError): e.ripple_delete_clip('missing' if damage=='missing-id' else 'selected')
    assert (e.project,e._undo,e._redo,e.revision)==before


def test_ordinary_delete_keeps_gaps_and_all_remaining_positions():
    e=make_editor(); before=deepcopy(e.project)
    e.delete_clip('selected'); before.clips=[c for c in before.clips if c.id!='selected']
    assert e.project==before


@pytest.mark.parametrize('selected', ['before', 'last'])
def test_first_and_last_clip_edges(selected):
    e=make_editor(); before=deepcopy(e.project); deleted=deepcopy(e._clip(selected))
    e.ripple_delete_clip(selected)
    for clip in e.project.clips:
        old=next(c for c in before.clips if c.id==clip.id)
        assert clip==replace(old, start=old.start-deleted.duration if old.track==deleted.track and old.start>=deleted.end else old.start)


def test_single_clip_becomes_empty_with_one_reversible_edit():
    e=make_editor(); e.project.clips=[e._clip('selected')]; e.project.captions=[]; e.project.overlays=[]
    before=deepcopy(e.project); e.ripple_delete_clip('selected')
    assert e.project.clips==[] and e.project.media==before.media
    assert e.undo() and e.project==before
    assert e.redo() and e.project.clips==[]


def test_generated_speech_provenance_and_partial_source_tail_survive(tmp_path):
    from test_tts_exact_timing import speech
    e=make_editor(track=2); media=speech(tmp_path, 480000)
    e.project.media.append(media); e._clip('later').media_id=media.id
    before=deepcopy(e.project.media); mapping=e._clip('later').timing
    e.ripple_delete_clip('selected')
    assert e.project.media==before and e._clip('later').timing==mapping
    assert Project.from_dict(e.project.to_dict())==e.project
