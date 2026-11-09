"""Schema41 output-sample-local gain automation and atomic edit semantics."""
from native_path_fixture import bind_missing_project_paths
from copy import deepcopy
from dataclasses import asdict, fields, replace
from fractions import Fraction
import json

import pytest

from lumen.audio_gain import AudioGainKeyframe, MAX_AUDIO_GAIN_SAMPLE
from lumen.model import AudioMixAnchor, Clip, Editor, Media, Project, VERSION
from lumen.timing import sample_boundary
from audio_gain_legacy_fixture import strip_audio_gain_keyframes_field


def keys():
    return [AudioGainKeyframe(-4000, -9, 'hold'), AudioGainKeyframe(0, 0),
            AudioGainKeyframe(1602, -12), AudioGainKeyframe(16020, 5, 'hold'),
            AudioGainKeyframe(100000, 1)]


def sample(fps=Fraction(30000, 1001)):
    media = Media('m', '/missing.mov', 'Source', 1000, 64, 48, True, True,
                  fps.numerator, fps.denominator)
    return Editor(Project(fps_num=fps.numerator, fps_den=fps.denominator,
        media=[media], clips=[Clip('c', 'm', 0, 1, 10, 10, audio_gain_keyframes=keys())]))


def state(editor):
    return deepcopy((editor.project, editor._undo, editor._redo, editor.revision))


def test_schema41_appended_default_independence_strict_save_and_canonical_offsets(tmp_path):
    assert VERSION == 43 and fields(Clip)[-1].name == 'audio_gain_keyframes'
    a, b = Clip('a','m',0,0,0,1), Clip('b','m',1,0,0,1)
    a.audio_gain_keyframes.append(AudioGainKeyframe(0,-0.0))
    assert b.audio_gain_keyframes == []
    e=sample(); bind_missing_project_paths(e.project, tmp_path);before=deepcopy(e.project);data=e.project.to_dict()
    assert data['clips'][0]['audio_gain_keyframes'] == [asdict(k) for k in keys()]
    assert Project.from_dict(data)==before
    path=tmp_path/'saved.lumen';e.project.save(path)
    assert Project.load(path)==before and json.loads(path.read_text())['version']==43
    data['clips'][0].pop('audio_gain_keyframes')
    with pytest.raises(ValueError,match='Version 41 clips require'):Project.from_dict(data)


def historical(version):
    from test_master_gain_model import historical_payload
    from test_clip_composite_model import project
    if version < 34:
        return historical_payload(version)
    data=project().to_dict();data['version']=version
    if version < 35:
        from media_bins_legacy_fixture import strip_media_bins_fields
        strip_media_bins_fields(data)
    elif version < 36:
        from clip_groups_legacy_fixture import strip_clip_groups_field
        strip_clip_groups_field(data)
    elif version < 37:
        from track_edit_locks_legacy_fixture import strip_track_edit_locks_field
        strip_track_edit_locks_field(data)
    elif version < 38:
        from source_ranges_legacy_fixture import strip_source_ranges_field
        strip_source_ranges_field(data)
    elif version < 39:
        from timeline_markers_legacy_fixture import strip_timeline_markers_field
        strip_timeline_markers_field(data)
    elif version < 40:
        from source_audio_legacy_fixture import strip_source_audio_state_fields
        strip_source_audio_state_fields(data)
    else:
        strip_audio_gain_keyframes_field(data)
    return data


@pytest.mark.parametrize('version',range(1,41))
def test_each_prior_schema_migrates_empty_and_rejects_smuggled_empty(version):
    data=historical(version);before=deepcopy(data)
    loaded=Project.from_dict(data)
    assert loaded.clips and all(c.audio_gain_keyframes==[] for c in loaded.clips)
    assert data==before and loaded.to_dict()['version']==43
    data['clips'][0]['audio_gain_keyframes']=[]
    with pytest.raises(ValueError,match='Legacy clips cannot contain audio_gain_keyframes'):
        Project.from_dict(data)


@pytest.mark.parametrize('value',[None,{},'keys',False,(),[{}],
    [{'sample':0,'offset_db':0}],
    [{'sample':0,'offset_db':0,'interpolation':'linear','future':False}],
    [{'sample':True,'offset_db':0,'interpolation':'linear'}],
    [{'sample':1.0,'offset_db':0,'interpolation':'linear'}],
    [{'sample':MAX_AUDIO_GAIN_SAMPLE+1,'offset_db':0,'interpolation':'linear'}],
    [{'sample':-MAX_AUDIO_GAIN_SAMPLE-1,'offset_db':0,'interpolation':'linear'}],
    [{'sample':0,'offset_db':True,'interpolation':'linear'}],
    [{'sample':0,'offset_db':float('nan'),'interpolation':'linear'}],
    [{'sample':0,'offset_db':float('inf'),'interpolation':'linear'}],
    [{'sample':0,'offset_db':24.0001,'interpolation':'linear'}],
    [{'sample':0,'offset_db':-96.0001,'interpolation':'linear'}],
    [{'sample':0,'offset_db':0,'interpolation':'bezier'}],
    [{'sample':0,'offset_db':0,'interpolation':'linear'}]*129,
    [{'sample':1,'offset_db':0,'interpolation':'linear'}, {'sample':0,'offset_db':0,'interpolation':'hold'}],
    [{'sample':1,'offset_db':0,'interpolation':'linear'}, {'sample':1,'offset_db':0,'interpolation':'hold'}]])
def test_wire_rejects_noncanonical_or_unbounded_keyframes_without_mutation(value):
    data=sample().project.to_dict();data['clips'][0]['audio_gain_keyframes']=value
    before=deepcopy(data)
    with pytest.raises(ValueError):Project.from_dict(data)
    # NaN is not equality-comparable; identity-free strict input retention via repr.
    assert repr(data)==repr(before)


@pytest.mark.parametrize('version',[True,False,None,'40',0,-1,41,42])
def test_fixture_converter_refuses_nonlegacy_without_mutation(version):
    data={'version':version,'clips':[{'audio_gain_keyframes':[]}]};before=deepcopy(data)
    with pytest.raises(AssertionError):strip_audio_gain_keyframes_field(data)
    assert data==before


def test_fixture_converter_only_removes_one_field():
    data={'version':40,'clips':[{'id':'c','audio_gain_keyframes':[],'source_audio_enabled':False}],
          'sentinel':{'keep':[1,2]}}
    assert strip_audio_gain_keyframes_field(data)=={'version':40,
        'clips':[{'id':'c','source_audio_enabled':False}], 'sentinel':{'keep':[1,2]}}


@pytest.mark.parametrize('operation',[lambda e:e.move_clip('c',20),
    lambda e:e.slip_clip('c',15),lambda e:e.trim_clip('c',15,8),
    lambda e:e.trim_clip_source('c',12,18),lambda e:e.set_clip_speed('c',2),
    lambda e:e.trim_edge('c','right',-3),lambda e:e.update_clip('c',gain_db=-7,muted=True,fade_in=1)])
def test_output_local_keys_survive_whole_move_source_changes_speed_righttrim_and_scalar_controls(operation):
    e=sample();before=state(e);original=deepcopy(e._clip('c').audio_gain_keyframes)
    operation(e);assert e._clip('c').audio_gain_keyframes==original
    after=deepcopy(e.project);assert len(e._undo)==1 and e.revision==1
    assert e.undo() and e.project==before[0]
    assert e.redo() and e.project==after


@pytest.mark.parametrize('kind',['trim_left','edge_left','split'])
@pytest.mark.parametrize('fps',[Fraction(24),Fraction(24000,1001),Fraction(30000,1001)])
def test_left_rebase_subtracts_difference_of_global_boundaries_and_keeps_hidden_handles(kind,fps):
    e=sample(fps);before=state(e);old=e._clip('c');cut=3
    shift=sample_boundary(cut,fps)-sample_boundary(old.start,fps)
    expected=[replace(k,sample=k.sample-shift) for k in keys()]
    if kind=='trim_left':right=e.trim_clip_left('c',2)
    elif kind=='edge_left':
        preview=e.preview_trim_edge('c','left',2);assert state(e)==before
        assert preview.audio_gain_keyframes==expected
        right=e.trim_edge('c','left',2)
    else:
        left,right=e.split_clip('c',cut);assert left.audio_gain_keyframes==keys()
    assert right.audio_gain_keyframes==expected and len(expected)==5
    assert right.audio_gain_keyframes[0].sample<0 and right.audio_gain_keyframes[-1].sample>16000
    after=deepcopy(e.project);assert e.undo() and e.project==before[0]
    assert e.redo() and e.project==after


def test_fractional_boundary_difference_is_not_rounded_frame_delta():
    e=sample();shift=sample_boundary(2,e.project.fps)-sample_boundary(1,e.project.fps)
    assert shift!=sample_boundary(1,e.project.fps)
    e.trim_clip_left('c',1)
    assert e._clip('c').audio_gain_keyframes[1].sample==-shift


@pytest.mark.parametrize('delta',[-1,1,2])
def test_roll_retains_left_keys_and_rebases_every_right_key(delta):
    e=sample();e.project.clips.append(Clip('right','m',0,11,30,10,audio_gain_keyframes=keys()))
    before=state(e);cut=11+delta;shift=sample_boundary(cut,e.project.fps)-sample_boundary(11,e.project.fps)
    a,b=e.preview_roll_cut('c',cut);assert state(e)==before
    assert a.audio_gain_keyframes==keys()
    assert b.audio_gain_keyframes==[replace(k,sample=k.sample-shift) for k in keys()]
    e.roll_cut('c',cut);assert e._clip('right').audio_gain_keyframes==b.audio_gain_keyframes
    after=deepcopy(e.project);assert e.undo() and e.project==before[0]
    assert e.redo() and e.project==after


@pytest.mark.parametrize('operation',[lambda e:e.trim_clip_left('c',1),
    lambda e:e.preview_trim_edge('c','left',1),lambda e:e.trim_edge('c','left',1),
    lambda e:e.split_clip('c',2)])
def test_hidden_handle_overflow_rejects_atomically(operation):
    e=sample();e._clip('c').audio_gain_keyframes=[AudioGainKeyframe(-MAX_AUDIO_GAIN_SAMPLE,0)]
    before=state(e)
    with pytest.raises(ValueError):operation(e)
    assert state(e)==before


def test_budget_is_bounded_only_for_nonempty_envelope_and_start_is_not_clock_bound():
    e=sample(Fraction(24));c=e._clip('c');c.start=10**14;e.project.validate()
    assert c.audio_gain_keyframes==keys()
    c.start=0;c.duration=MAX_AUDIO_GAIN_SAMPLE//2000+1
    e.project.media[0].duration=10**9
    with pytest.raises(ValueError,match='sample budget'):e.project.validate()
    c.audio_gain_keyframes=[];e.project.validate()


def test_keyframe_edits_strict_transaction_noop_history_lock_and_dictionary_input():
    e=sample();before=state(e)
    e.update_clip('c',audio_gain_keyframes=deepcopy(keys()));assert state(e)==before
    desired=[AudioGainKeyframe(7,-12,'hold')]
    e.update_clip('c',audio_gain_keyframes=[asdict(k) for k in desired]);assert e._clip('c').audio_gain_keyframes==desired
    after=deepcopy(e.project);assert len(e._undo)==1
    assert e.undo() and e.project==before[0];assert e.redo() and e.project==after
    before=state(e)
    with pytest.raises(ValueError):e.update_clip('c',audio_gain_keyframes=[{'sample':0}])
    assert state(e)==before
    e.set_track_edit_lock(0,True);before=state(e)
    with pytest.raises(ValueError,match='edit-locked'):e.update_clip('c',audio_gain_keyframes=[])
    assert state(e)==before


@pytest.mark.parametrize('operation',['insert','overwrite'])
@pytest.mark.parametrize('track',range(4))
def test_source_edits_refuse_cutting_envelope_transaction(operation,track):
    from test_source_overwrite_model import sample as source_sample
    e=source_sample(track);e._clip('old').audio_gain_keyframes=keys();before=state(e)
    with pytest.raises(ValueError,match=f'Source {operation} cannot cut clips with audio gain keyframes yet'):
        getattr(e,f'plan_source_{operation}')('new',track,30,0,5)
    assert state(e)==before


def test_insert_whole_translation_preserves_local_keys_with_production_contract():
    from test_source_overwrite_model import sample as source_sample
    e=source_sample();e._clip('later').audio_gain_keyframes=keys();before=deepcopy(e._clip('later'))
    plan=e.plan_source_insert('new',0,70,0,10);e.apply_source_insert(plan)
    after=e._clip('later');assert after.start==before.start+10
    assert after.audio_gain_keyframes==before.audio_gain_keyframes


def test_separation_and_reattach_candidates_primitives_adopt_current_audio_envelope(tmp_path):
    from test_source_audio_separation_model import sample as separation_sample, separate
    from test_source_audio_reattach_model import reattach
    e=separation_sample(tmp_path);e._clip('v').audio_gain_keyframes=keys();before=deepcopy(e.project)
    audio=separate(e);audio_id=audio.id
    assert audio.audio_gain_keyframes==keys()
    assert audio.audio_gain_keyframes is not e._clip('v').audio_gain_keyframes
    changed=[AudioGainKeyframe(-1,-20,'hold'),AudioGainKeyframe(1234,2)]
    e.update_clip(audio.id,audio_gain_keyframes=changed)
    separated=deepcopy(e.project);retained=reattach(e,'v',audio_id)
    assert retained.audio_gain_keyframes==changed and retained.source_audio_enabled
    assert e.undo() and e.project==separated
    assert e.redo() and e._clip('v').audio_gain_keyframes==changed
    assert before.clips[0].audio_gain_keyframes==keys()


def test_separation_primitive_rejects_omitted_or_changed_envelope_atomically():
    from lumen.source_audio_separation import _candidate
    e=sample();project,audio=_candidate(e,'c',2,'new-audio')
    audio.audio_gain_keyframes=[];before=state(e)
    with pytest.raises(ValueError,match='exact source audio contract'):
        e._commit_source_audio_separation('c',audio)
    assert state(e)==before


def test_copy_keeps_output_keys_but_clears_historical_mix_anchor(tmp_path):
    from test_clip_copy_paste_model import sample as copy_sample,copy,paste
    e=copy_sample(tmp_path);e._clip('a').audio_gain_keyframes=keys()
    e._clip('a').audio_mix_anchor=AudioMixAnchor(1,4,'historical')
    clipboard=copy(e);(clone,)=paste(e,clipboard)
    assert clone.audio_gain_keyframes==keys() and clone.audio_mix_anchor is None
    assert clone.audio_gain_keyframes is not e._clip('a').audio_gain_keyframes


def test_genuine_accepted_schema40_fixture_preserves_every_historical_field():
    from hashlib import sha256
    from pathlib import Path
    path=Path(__file__).parent/'fixtures/schema40-before-audio-gain-keyframes.json'
    assert sha256(path.read_bytes()).hexdigest()=='1e6438166fffddbd931f9b2f0c443bad2a3ae667be0ef9fa54562bfd75f0174b'
    payload=json.loads(path.read_text());before=deepcopy(payload)
    assert payload['version']==40
    assert all('audio_gain_keyframes' not in c for c in payload['clips'])
    loaded=Project.from_dict(payload);assert payload==before
    current=loaded.to_dict();assert current['version']==43
    for clip in current['clips']:assert clip.pop('audio_gain_keyframes')==[]
    assert current.pop('av_links') == []
    current['version']=40;assert current==before


def test_roll_preview_and_commit_reject_expanded_envelope_budget_atomically():
    e=sample(Fraction(24));clip=e._clip('c');clip.start=0
    clip.duration=MAX_AUDIO_GAIN_SAMPLE//2000;clip.source_in=0
    e.project.media[0].duration=10**9
    e.project.clips.append(Clip('right','m',0,clip.end,10,10))
    e.project.validate();before=state(e);cut=clip.end+1
    for operation in (e.preview_roll_cut,e.roll_cut):
        with pytest.raises(ValueError,match='sample budget'):operation('c',cut)
        assert state(e)==before
