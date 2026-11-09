"""Tiny full-scene affine trim proofs from immutable OLD maps plus request.

1x expected PCM uses original procedural stereo arrays and independent scalar
sample/gain/fade/sum math. Every lossless scene consumes production V and A in
one mux; no conservation shortcut or audio-only/nullsink is the scene proof.
Retime uses the accepted fixed schema40 local DSP plus independent envelopes,
not a claim to reimplement Rubber Band or preserve transient phase across trim.
Retain the existing exact RGB16 segment, full-frame color/ID, 2-ULP scalar PCM,
1-ULP fixed-DSP envelope and gain-free exact cancellation assertions.

All new inputs, muxes, delivery and compact diagnostic files stay in tmp_path.
The root runner owns TMPDIR/cache/basetemp and pins local FFmpeg/ffprobe bytes.
"""
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction
import hashlib
import json

import numpy as np
import pytest

from lumen.media import ffmpeg_available, probe
from lumen.model import AVLink, AV_LINK_AFFINE_ALIGNMENT, AudioMixAnchor, Clip, Editor, Project
from lumen.timing import ClipTimeMap
from test_audio_gain_pcm import independent_pcm
from test_linked_audio_roll_media import boundary, render_scene, short_sources, verified_scene
from test_linked_av_pcm import assert_sources_untouched, source_fingerprints, unlinked
from test_linked_av_roll_media import expected_frames, raw_unit_pcm
from test_linked_av_trim_media import pair_project, requested_project
from test_retime_video import HEIGHT, WIDTH, assert_delivery_frames, decode, engine, ff, segment_pixels
from test_source_audio_separation_pcm import samples

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason='Local FFmpeg/ffprobe required')


def affine_editor(sources, fps, geometry='leading', speed=Fraction(1)):
    """Seed independent unequal exact maps, never call trim/audio-roll/slip.

    The accepted strict fixture supplies nontrivial hidden keys, fades and
    suppressed V audio controls. Relocate untouched neighbours before the
    pair so right trims also exercise full-scene duration changes and gaps.
    """
    project = pair_project(sources[0], fps, speed=speed).project
    v, a, fixed_v, fixed_a = project.clips
    v.start, v.source_in, v.duration = 5, Fraction(49, 4), 10
    v.time_map = ClipTimeMap(v.source_in, v.source_in+10*speed-speed/4, speed)
    a.start, a.duration = (3, 14) if geometry == 'leading' else (7, 12)
    a.source_in = v.source_in+(a.start-v.start)*speed
    a.time_map = ClipTimeMap(a.source_in, a.source_in+a.duration*speed-speed/2, speed)
    # Keep the inherited A slot from the original pair, not a newly chosen slot.
    project.av_links = [AVLink('affine-pair', v.id, a.id, v.media_id, AV_LINK_AFFINE_ALIGNMENT)]
    project.media.append(sources[1].media)
    fixed_v.media_id = fixed_a.media_id = sources[1].media.id
    fixed_v.start, fixed_v.source_in, fixed_v.duration = 0, 25, 2
    fixed_a.start, fixed_a.source_in, fixed_a.duration = 0, 2, 2
    project.master_gain_db = -2.125
    project.validate()
    return Editor(project)


def facts(sources):
    return ({s.media.id: s.audio for s in sources},
            {s.media.id: s.pictures for s in sources})


def assert_own_trim(before, after, edge, delta):
    """Scalar endpoint/tail/offset checks independent of the candidate helper."""
    old = {c.id:c for c in before.clips}; link = before.av_links[0]
    assert after.av_links == before.av_links and after.media == before.media
    assert [c.id for c in after.clips] == [c.id for c in before.clips]
    for current in after.clips:
        prior = old[current.id]
        if current.id not in link.clip_ids:
            assert current == prior; continue
        assert current.start == prior.start+(delta if edge=='left' else 0)
        assert current.duration == prior.duration+(-delta if edge=='left' else delta)
        assert current.source_in == prior.source_in+(delta*prior.speed if edge=='left' else 0)
        assert current.source_out == prior.source_out+(delta*prior.speed if edge=='right' else 0)
        assert current.speed == prior.speed
        assert current.duration*current.speed-(current.source_out-current.source_in) == prior.duration*prior.speed-(prior.source_out-prior.source_in)
        assert current.source_in-current.start*current.speed == prior.source_in-prior.start*prior.speed
        assert (current.fade_in,current.fade_out,current.audio_mix_anchor,current.source_audio_enabled) == (prior.fade_in,prior.fade_out,prior.audio_mix_anchor,prior.source_audio_enabled)
    current = {c.id:c for c in after.clips}
    v,a = current[link.video_clip_id],current[link.audio_clip_id]
    ov,oa = old[v.id],old[a.id]
    assert (a.start-v.start,a.end-v.end,a.duration-v.duration) == (oa.start-ov.start,oa.end-ov.end,oa.duration-ov.duration)
    assert not v.source_audio_enabled and a.source_audio_enabled


CASES = [
    pytest.param(Fraction(24), 0, 'leading', 'video', 'left', -1, id='24-J-video-left-extend'),
    pytest.param(Fraction(24000,1001), -.113, 'trailing', 'audio', 'right', 1, id='23.976-L-audio-right-extend'),
    pytest.param(Fraction(30000,1001), .137, 'leading', 'audio', 'left', 1, id='29.97-J-audio-left-shorten'),
    pytest.param(Fraction(60000,1001), .137, 'trailing', 'video', 'right', -1, id='59.94-L-video-right-shorten'),
]


@pytest.mark.parametrize('fps,offset,geometry,member,edge,delta', CASES)
def test_affine_trim_full_scene_old_request_rgb_and_scalar_pcm(tmp_path, engine, fps, offset, geometry, member, edge, delta):
    sources, paths = short_sources(tmp_path/'sources', fps, offset)
    fingerprints = source_fingerprints(paths); raw, pictures = facts(sources)
    editor = affine_editor(sources, fps, geometry); before = deepcopy(editor.project)
    expected = requested_project(before, edge, delta)
    old_wanted, wanted = raw_unit_pcm(before, raw), raw_unit_pcm(expected, raw)
    old_segments, old_frames = expected_frames(before, pictures)
    selected, frames = expected_frames(expected, pictures)
    assert old_wanted != wanted and not np.array_equal(old_frames, frames)
    if fps == Fraction(30000,1001):
        qv = boundary(6,fps)-boundary(5,fps); qa = boundary(4,fps)-boundary(3,fps)
        assert (qv,qa) == (1602,1601)
        wrong = deepcopy(expected)
        wrong.clips[1].audio_gain_keyframes = [replace(k,sample=k.sample-qv) for k in before.clips[1].audio_gain_keyframes]
        assert raw_unit_pcm(wrong,raw) != wanted, 'Fixture must expose reuse of V sample delta for A'
        filled = deepcopy(expected)
        for clip in filled.clips[:2]:
            clip.time_map = ClipTimeMap(clip.source_in,clip.source_in+clip.duration*clip.speed,clip.speed)
        assert raw_unit_pcm(filled,raw) != wanted, 'Fixture must expose independently lost residual tails'
    old_scene, old_audio = verified_scene(engine,before,before,raw,tmp_path/'before.mkv')
    assert_delivery_frames(old_scene,old_frames)
    np.testing.assert_array_equal(segment_pixels(engine,before,before.clips[0]),old_segments['video'])
    history, revision = len(editor._undo), editor.revision
    preview = editor.preview_trim_linked_av(member,edge,delta)
    assert list(preview)==expected.clips[:2] and editor.project==before
    assert len(editor._undo)==history and editor.revision==revision
    editor.trim_linked_av(member,edge,delta)
    assert editor.project==expected and len(editor._undo)==history+1 and editor.revision==revision+1
    assert_own_trim(before,editor.project,edge,delta)
    scene,actual = verified_scene(engine,editor.project,expected,raw,tmp_path/'after.mkv')
    assert_delivery_frames(scene,frames)
    np.testing.assert_array_equal(segment_pixels(engine,editor.project,editor._clip('video')),selected['video'])
    assert actual!=old_audio and not np.array_equal(scene,old_scene)
    np.testing.assert_array_equal(scene[:2],old_scene[:2])
    np.testing.assert_array_equal(samples(actual)[:boundary(2,fps)],samples(old_audio)[:boundary(2,fps)])
    assert not np.count_nonzero(scene[2:expected.clips[0].start])
    a = expected.clips[1]; end = boundary(a.end,fps)
    assert not np.count_nonzero(samples(actual)[end-max(1,boundary(1,fps)//4):end])
    assert expected.duration_frames == before.duration_frames+(delta if edge=='right' else 0)
    saved=tmp_path/'affine-trim.lumen';editor.project.save(saved);reopened=Project.load(saved)
    assert reopened==expected
    if fps==Fraction(30000,1001):
        reopened_scene,reopened_audio=verified_scene(engine,reopened,expected,raw,tmp_path/'reopened.mkv')
        np.testing.assert_array_equal(reopened_scene,scene);assert reopened_audio==actual
    assert editor.undo() and editor.project==before
    if fps==Fraction(30000,1001):
        undo_scene,undo_audio=verified_scene(engine,editor.project,before,raw,tmp_path/'undo.mkv')
        np.testing.assert_array_equal(undo_scene,old_scene);assert undo_audio==old_audio
    assert editor.redo() and editor.project==expected
    if fps==Fraction(30000,1001):
        redo_scene,redo_audio=verified_scene(engine,editor.project,expected,raw,tmp_path/'redo.mkv')
        np.testing.assert_array_equal(redo_scene,scene);assert redo_audio==actual
    assert_sources_untouched(paths,fingerprints)


@pytest.mark.parametrize('fps,speed,geometry,member,edge,delta',[
    (Fraction(30000,1001),Fraction(3,2),'leading','audio','left',1),
    (Fraction(60000,1001),Fraction(2,3),'trailing','video','right',-1),
],ids=['affine-retime-left-shorten','affine-retime-right-shorten'])
def test_affine_trim_retimed_new_maps_fixed_dsp_and_independent_envelopes(tmp_path,engine,fps,speed,geometry,member,edge,delta):
    sources,paths=short_sources(tmp_path/'sources',fps,-.113)
    fingerprints=source_fingerprints(paths);_,pictures=facts(sources)
    editor=affine_editor(sources,fps,geometry,speed);before=deepcopy(editor.project)
    expected=requested_project(before,edge,delta)
    old_wanted=independent_pcm(unlinked(before));wanted=independent_pcm(unlinked(expected))
    assert old_wanted!=wanted
    old_scene,old_audio=render_scene(engine,before,tmp_path/'retimed-before.mkv')
    assert_delivery_frames(old_scene,expected_frames(before,pictures)[1])
    np.testing.assert_array_max_ulp(samples(old_audio),samples(old_wanted),maxulp=1)
    history=len(editor._undo);editor.trim_linked_av(member,edge,delta)
    assert editor.project==expected and len(editor._undo)==history+1
    assert_own_trim(before,editor.project,edge,delta)
    scene,actual=render_scene(engine,editor.project,tmp_path/'retimed-after.mkv')
    segments,frames=expected_frames(expected,pictures);assert_delivery_frames(scene,frames)
    np.testing.assert_array_equal(segment_pixels(engine,editor.project,editor._clip('video')),segments['video'])
    np.testing.assert_array_max_ulp(samples(actual),samples(wanted),maxulp=1)
    assert len(actual)==boundary(expected.duration_frames,fps)*8
    (tmp_path/'retime-oracle.json').write_text(json.dumps({'basis':'OLD/request maps; accepted schema40 fixed local DSP + independent scalar envelopes/mix','actual_pcm_sha256':hashlib.sha256(actual).hexdigest(),'expected_pcm_sha256':hashlib.sha256(wanted).hexdigest(),'max_ulp':1},indent=2)+'\n')
    assert editor.undo() and editor.project==before
    assert editor.redo() and editor.project==expected
    assert_sources_untouched(paths,fingerprints)


@pytest.mark.parametrize('muted',[False,True],ids=['audible-potential-order','muted-potential-order'])
def test_affine_trim_historical_order_refusal_and_safe_edits_exactly(tmp_path,engine,muted):
    fps=Fraction(24);sources,base_paths=short_sources(tmp_path/'sources',fps,0)
    numbered=sources[0];tiny_raw=(numbered.audio*np.float32(2**-25)).astype('<f4')
    positive_raw=np.tile(np.array([1,-1],'<f4'),(24000,1))
    negative_raw=np.tile(np.array([-1,1],'<f4'),(24000,1))
    def tagged_numbered(name,original):
        path=tmp_path/f'{name}.mkv'
        ff('-i',numbered.media.path,'-f','f32le','-ar','48000','-ac','2','-i','pipe:0',
           '-map','0:v','-map','1:a','-c:v','copy','-c:a','pcm_f32le',path,data=original.tobytes())
        np.testing.assert_array_equal(decode(path),numbered.pictures)
        decoded=ff('-i',path,'-map','0:a:0','-vn','-ar','48000','-ac','2','-c:a','pcm_f32le','-f','f32le','pipe:1')
        np.testing.assert_array_equal(samples(decoded),original)
        media=probe(path);assert media.audio_offset==0 and (media.width,media.height)==(WIDTH,HEIGHT)
        return media,path
    tiny,tp=tagged_numbered('tiny',tiny_raw)
    positive,pp=tagged_numbered('positive',positive_raw)
    negative,np_=tagged_numbered('negative',negative_raw)
    paths=(*base_paths,tp,pp,np_);fingerprints=source_fingerprints(paths)
    anchor=AudioMixAnchor(0,0,'shared-historical-descendant')
    editor=Editor(Project(width=WIDTH,height=HEIGHT,fps_num=24,media=[tiny,positive,negative],clips=[
        Clip('video',tiny.id,1,5,5,5,source_audio_enabled=False,audio_mix_anchor=anchor,
             time_map=ClipTimeMap(5,Fraction(39,4))),
        Clip('audio',tiny.id,3,3,3,8,muted=muted,audio_mix_anchor=anchor,
             time_map=ClipTimeMap(3,Fraction(21,2))),
        Clip('positive',positive.id,0,1,0,10,audio_mix_anchor=anchor),
        Clip('negative',negative.id,2,2,0,9,audio_mix_anchor=anchor)],
        av_links=[AVLink('affine-pair','video','audio',tiny.id,AV_LINK_AFFINE_ALIGNMENT)]))
    raw={tiny.id:tiny_raw,positive.id:positive_raw,negative.id:negative_raw}
    pictures={tiny.id:numbered.pictures,positive.id:numbered.pictures}
    before=deepcopy(editor.project);damaged=requested_project(before,'left',-2)
    old_potential,new_potential=deepcopy(before),deepcopy(damaged)
    old_potential.clips[1].muted=new_potential.clips[1].muted=False
    lo,hi=boundary(4,fps),boundary(8,fps)
    old_markers=samples(raw_unit_pcm(old_potential,raw))[lo:hi]
    assert np.count_nonzero(old_markers) and not np.count_nonzero(samples(raw_unit_pcm(new_potential,raw))[lo:hi])
    np.testing.assert_array_equal(old_markers,tiny_raw[lo:hi])
    old_scene,old_audio=verified_scene(engine,before,before,raw,tmp_path/'before-order.mkv')
    assert old_audio==raw_unit_pcm(before,raw)
    assert_delivery_frames(old_scene,expected_frames(before,pictures)[1])
    state=deepcopy((editor.project,editor._undo,editor._redo,editor.revision))
    for operation in (editor.preview_trim_linked_av,editor.trim_linked_av):
        with pytest.raises(ValueError,match='(?i)(mix|order)'):operation('audio','left',-2)
        assert (editor.project,editor._undo,editor._redo,editor.revision)==state
    for edge,delta in [('left',1),('right',1)]:
        expected=requested_project(before,edge,delta);wanted=raw_unit_pcm(expected,raw)
        editor.trim_linked_av('video',edge,delta)
        assert editor.project==expected;assert_own_trim(before,editor.project,edge,delta)
        scene,actual=verified_scene(engine,editor.project,expected,raw,tmp_path/f'safe-{edge}.mkv')
        assert actual==wanted  # Exact gain-free float32 cancellation, not an ULP budget.
        assert_delivery_frames(scene,expected_frames(expected,pictures)[1])
        assert editor.undo() and editor.project==before
    assert_sources_untouched(paths,fingerprints)


def test_affine_muted_trim_then_unmute_rebased_audio_not_suppressed_video(tmp_path,engine):
    fps=Fraction(30000,1001);sources,paths=short_sources(tmp_path/'sources',fps,-.113)
    fingerprints=source_fingerprints(paths);raw,pictures=facts(sources)
    editor=affine_editor(sources,fps);editor.update_clip('audio',muted=True)
    before=deepcopy(editor.project);expected=requested_project(before,'left',1)
    old_scene,old_audio=verified_scene(engine,before,before,raw,tmp_path/'muted-before.mkv')
    assert_delivery_frames(old_scene,expected_frames(before,pictures)[1])
    history=len(editor._undo);editor.trim_linked_av('audio','left',1)
    assert editor.project==expected and len(editor._undo)==history+1
    assert_own_trim(before,editor.project,'left',1)
    scene,actual=verified_scene(engine,editor.project,expected,raw,tmp_path/'muted-after.mkv')
    assert actual==old_audio and not np.array_equal(scene,old_scene)
    assert_delivery_frames(scene,expected_frames(expected,pictures)[1])
    editor.update_clip('audio',muted=False);expected.clips[1].muted=False
    assert editor.project==expected and not editor._clip('video').source_audio_enabled
    unmuted_scene,unmuted=verified_scene(engine,editor.project,expected,raw,tmp_path/'unmuted.mkv')
    np.testing.assert_array_equal(unmuted_scene,scene);assert unmuted!=old_audio
    wrong=deepcopy(expected);wrong.clips[0].source_audio_enabled=True
    assert raw_unit_pcm(wrong,raw)!=raw_unit_pcm(expected,raw),'Fixture must detect duplicate embedded V audio'
    assert_sources_untouched(paths,fingerprints)


def test_affine_trim_real_preview_export_cache_reopen_and_history(tmp_path,engine):
    fps=Fraction(30000,1001);sources,paths=short_sources(tmp_path/'sources',fps,-.113)
    fingerprints=source_fingerprints(paths);raw,pictures=facts(sources)
    editor=affine_editor(sources,fps);before=deepcopy(editor.project)
    expected=requested_project(before,'left',1)
    before_frames=expected_frames(before,pictures)[1];after_frames=expected_frames(expected,pictures)[1]
    previous=engine.preview(before);assert_delivery_frames(decode(previous,rgb16=False),before_frames)
    editor.trim_linked_av('video','left',1);assert editor.project==expected
    _,actual=verified_scene(engine,editor.project,expected,raw,tmp_path/'delivery-pcm.mkv')
    assert raw_unit_pcm(expected,raw)!=raw_unit_pcm(before,raw)
    current=engine.preview(editor.project)
    assert current!=previous and engine.preview(editor.project)==current
    exported=engine.render(editor.project,tmp_path/'affine-trim-export.mp4')
    decoded_audio=[]
    for path in (current,exported):
        assert_delivery_frames(decode(path,rgb16=False),after_frames)
        decoded=ff('-i',path,'-map','0:a:0','-vn','-ar','48000','-ac','2','-c:a','pcm_f32le','-f','f32le','pipe:1')
        assert boundary(expected.duration_frames,fps)<=len(samples(decoded))<boundary(expected.duration_frames,fps)+1024
        decoded_audio.append(decoded)
    assert decoded_audio[0]==decoded_audio[1]  # Same AAC paths, not AAC==exact PCM.
    saved=tmp_path/'delivery.lumen';editor.project.save(saved);reopened=Project.load(saved)
    assert reopened==expected and engine.preview(reopened)==current
    assert editor.undo() and editor.project==before and engine.preview(editor.project)==previous
    assert editor.redo() and editor.project==expected and engine.preview(editor.project)==current
    assert len(actual)==boundary(expected.duration_frames,fps)*8
    assert_sources_untouched(paths,fingerprints)
