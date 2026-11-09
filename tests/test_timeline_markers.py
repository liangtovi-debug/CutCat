"""Strict persistent annotations are metadata, not rendered or ripple content."""
from copy import deepcopy
from dataclasses import asdict, fields, FrozenInstanceError, replace
from fractions import Fraction
import hashlib
import json
from pathlib import Path
from threading import Thread
from uuid import uuid4

import pytest

from lumen.model import Clip, Editor, Media, Project, VERSION
from lumen.project_recovery import RecoveryStore
from lumen.timeline_markers import (TimelineMarker, TIMELINE_MARKER_COLORS,
    MAX_TIMELINE_MARKERS, MAX_TIMELINE_MARKER_NAME, MAX_TIMELINE_MARKER_COMMENT,
    MAX_TIMELINE_MARKER_FRAME, MAX_TIMELINE_MARKER_TEXT,
    timeline_marker_in_bounds, timeline_markers_from_dict,
    validate_timeline_marker, validate_timeline_markers)
from timeline_markers_legacy_fixture import strip_timeline_markers_field


def marker(**changes):
    return replace(TimelineMarker('mark','<b>批注 🎬</b>','第一行\nSecond\tline','yellow',10,None),**changes)


def sample():
    return Project(media=[Media('m','/missing.mov','Source',120,64,48,True,False)],
                   clips=[Clip('left','m',0,0,0,10),Clip('right','m',0,15,20,10)])


def state(editor):
    return deepcopy((editor.project,editor._undo,editor._redo,editor.revision,editor.session_id))


def legacy(version):
    p={'format':'lumen-project','version':version}
    if version>=33:p['video_transitions']=[]
    if version>=34:p['master_gain_db']=0.0
    if version>=35:p.update(media_bins=[],media_bin_ids={})
    if version>=36:p['clip_groups']=[]
    if version>=37:p['track_edit_locks']=[False]*4
    if version>=38:p['source_ranges']=[]
    return p


@pytest.mark.parametrize('version',range(1,39))
def test_all_old_versions_migrate_empty_without_mutating_input(version):
    value=legacy(version);before=deepcopy(value)
    loaded=Project.from_dict(value)
    assert loaded.timeline_markers==[] and value==before
    assert loaded.to_dict()['version']==VERSION==43


@pytest.mark.parametrize('version',range(1,39))
@pytest.mark.parametrize('value',[[],None,[asdict(marker())]])
def test_every_old_version_rejects_new_field_even_empty(version,value):
    with pytest.raises(ValueError,match='Legacy projects cannot contain timeline markers'):
        Project.from_dict({**legacy(version),'timeline_markers':value})


def test_current_field_required_forward_fence_and_default_list_independent():
    a,b=Project(),Project();a.timeline_markers.append(marker())
    assert b.timeline_markers==[]
    value=a.to_dict();value.pop('timeline_markers')
    with pytest.raises(ValueError,match='require timeline markers'):Project.from_dict(value)
    value=b.to_dict();value['version']=VERSION+1
    with pytest.raises(ValueError,match='Unsupported'):Project.from_dict(value)
    assert fields(Project)[-2].name=='timeline_markers' and fields(Project)[-1].name=='av_links'


def test_genuine_accepted_schema38_fixture_preserves_all_historical_fields():
    # Captured with unmodified accepted schema38 serializer before code edits.
    path=Path(__file__).parent/'fixtures/schema38-before-timeline-markers.json'
    assert hashlib.sha256(path.read_bytes()).hexdigest()=='e776826e1eb543a262607733734ef7d57855fd843ae620870ba95336f5acfdec'
    original=json.loads(path.read_text());before=deepcopy(original)
    assert original['version']==38 and 'timeline_markers' not in original and original['source_ranges']
    loaded=Project.from_dict(original)
    assert loaded.timeline_markers==[] and original==before
    current=loaded.to_dict();assert current.pop('timeline_markers')==[]
    assert current['version']==43;current['version']=38
    assert current.pop('av_links') == []
    for clip in current['clips']:
        assert clip.pop('source_audio_enabled') is True
        assert clip.pop('audio_mix_anchor') is None
        assert clip.pop('audio_gain_keyframes') == []
    assert current==before


@pytest.mark.parametrize('version',range(1,39))
def test_explicit_legacy_helper_strips_only_marker_field(version):
    value={'version':version,'timeline_markers':[],'sentinel':{'keep':[1,2]}}
    before=deepcopy(value)
    assert strip_timeline_markers_field(value) is value
    assert value=={k:v for k,v in before.items() if k!='timeline_markers'}
    assert before['timeline_markers']==[]


@pytest.mark.parametrize('version',[True,False,None,'38',0,-1,39,40])
def test_legacy_helper_refuses_invalid_and_current_versions(version):
    value={'version':version,'timeline_markers':[]};before=deepcopy(value)
    with pytest.raises(AssertionError):strip_timeline_markers_field(value)
    assert value==before


@pytest.mark.parametrize('value',[None,{},(),'',3,True,[None],[{}],[[]],[True]])
def test_malformed_collections_rejected_in_memory_and_on_wire(value):
    p=sample();p.timeline_markers=value
    with pytest.raises(ValueError):p.validate()
    data=sample().to_dict();data['timeline_markers']=value
    with pytest.raises(ValueError):Project.from_dict(data)


@pytest.mark.parametrize('field',[f.name for f in fields(TimelineMarker)])
def test_all_wire_fields_required_including_nullable_end(field):
    data=sample().to_dict();item=asdict(marker());item.pop(field);data['timeline_markers']=[item]
    with pytest.raises(ValueError,match='fields'):Project.from_dict(data)


@pytest.mark.parametrize('field',['clip_id','media_id','duration','chapter','state','verified','future'])
def test_unknown_fields_never_ignored(field):
    data=sample().to_dict();item=asdict(marker());item[field]='unknown';data['timeline_markers']=[item]
    with pytest.raises(ValueError,match='fields'):Project.from_dict(data)


@pytest.mark.parametrize('key,value',[
    ('id',''),('id',' '),('id','i'*129),('id',None),('id',True),('id','x\0y'),
    ('name',''),('name',' '),('name','n'*129),('name',False),('name',[]),('name',None),
    ('name','a\nb'),('name','a\tb'),('name','a\rb'),('name','a\x7fb'),('name','\ud800'),
    ('name','A\u2028B'),('name','A\u2029B'),('comment',None),('comment',True),
    ('comment','c'*4097),('comment','a\0b'),('comment','a\rb'),('comment','\udfff'),
    ('comment','a\u2028b'),('comment','a\u2029b'),('comment','a\x85b'),
    ('color',None),('color',True),('color',[]),('color','Yellow'),('color','#ffff00'),
    ('start_frame',True),('start_frame',False),('start_frame',-1),('start_frame',.5),
    ('start_frame','1'),('start_frame',MAX_TIMELINE_MARKER_FRAME+1),
    ('end_frame',True),('end_frame',False),('end_frame',-1),('end_frame',.5),
    ('end_frame','20'),('end_frame',10),('end_frame',9),('end_frame',MAX_TIMELINE_MARKER_FRAME+1),
], ids=lambda value: (value if len(value)<30 else f'text-{len(value)}') if isinstance(value,str) else repr(value))
def test_invalid_fields_reject_in_memory_and_wire_without_mutation(key,value):
    with pytest.raises(ValueError):validate_timeline_marker(marker(**{key:value}))
    data=sample().to_dict();data['timeline_markers']=[asdict(marker())];data['timeline_markers'][0][key]=value
    before=deepcopy(data)
    with pytest.raises(ValueError):Project.from_dict(data)
    assert data==before


@pytest.mark.parametrize('color',TIMELINE_MARKER_COLORS)
def test_plain_text_unicode_permitted_comment_empty_and_all_colors(color):
    p=sample();p.timeline_markers=[marker(color=color),marker(id='other',comment='',start_frame=0,end_frame=1)]
    assert Project.from_dict(p.to_dict())==p


def test_maximum_values_counts_and_duplicate_id_fence():
    m=marker(id='i'*128,name='n'*MAX_TIMELINE_MARKER_NAME,comment='c'*MAX_TIMELINE_MARKER_COMMENT,
             start_frame=MAX_TIMELINE_MARKER_FRAME-1,end_frame=MAX_TIMELINE_MARKER_FRAME)
    validate_timeline_marker(m)
    records=[marker(id=str(i)) for i in range(MAX_TIMELINE_MARKERS)]
    validate_timeline_markers(records)
    with pytest.raises(ValueError,match='at most'):validate_timeline_markers([*records,marker(id='overflow')])
    with pytest.raises(ValueError,match='Duplicate'):validate_timeline_markers([m,m])
    with pytest.raises(ValueError,match='Duplicate'):timeline_markers_from_dict([asdict(m),asdict(m)])
    with pytest.raises(ValueError,match='at most'):timeline_markers_from_dict([{}]*(MAX_TIMELINE_MARKERS+1))


@pytest.mark.parametrize('start,end,duration,wanted',[
    (0,None,0,False),(0,None,1,True),(1,None,1,False),(9,None,10,True),
    (0,1,0,False),(0,1,1,True),(0,10,10,True),(0,11,10,False),
    (9,10,10,True),(10,11,10,False),(MAX_TIMELINE_MARKER_FRAME,None,0,False),
])
def test_whole_annotation_half_open_navigation_bounds(start,end,duration,wanted):
    assert timeline_marker_in_bounds(marker(start_frame=start,end_frame=end),duration) is wanted


@pytest.mark.parametrize('duration',[True,False,-1,None,0.0,'30'])
def test_navigation_duration_requires_nonnegative_integer(duration):
    with pytest.raises(ValueError):timeline_marker_in_bounds(marker(),duration)


def update(editor,m,**changes):
    values={k:v for k,v in asdict(m).items() if k!='id'};values.update(changes)
    return editor.update_timeline_marker(m.id,**values)


def test_each_edit_single_undo_and_noop_keeps_redo_identity_and_revision():
    e=Editor(sample());before=deepcopy(e.project)
    m=e.add_timeline_marker('Point',10,comment='<b>text</b>');first=deepcopy(e.project)
    assert len(e._undo)==e.revision==1
    changed=update(e,m,name='Range',end_frame=20,color='pink');second=deepcopy(e.project)
    assert changed.id==m.id and len(e._undo)==e.revision==2
    e.delete_timeline_marker(m.id);assert len(e._undo)==e.revision==3
    assert e.undo() and e.project==second
    assert e.undo() and e.project==first
    snapshot=state(e);assert update(e,m)==m and state(e)==snapshot
    assert e.redo() and e.project==second
    assert e.redo() and not e.project.timeline_markers
    for _ in range(3):assert e.undo()
    assert e.project==before
    with pytest.raises(FrozenInstanceError):m.name='Not mutable'


def test_same_frame_and_overlapping_ranges_have_distinct_ids_and_are_allowed():
    e=Editor(sample())
    values=[e.add_timeline_marker('Same',10),e.add_timeline_marker('Same',10),
            e.add_timeline_marker('Same',10,20),e.add_timeline_marker('Overlap',5,15)]
    assert len({m.id for m in values})==4 and e.project.timeline_markers==values


@pytest.mark.parametrize('action',[
    lambda e:e.add_timeline_marker('',10),lambda e:e.add_timeline_marker('A',True),
    lambda e:e.add_timeline_marker('A',10,10),lambda e:e.add_timeline_marker('A',0,color='bad'),
    lambda e:e.delete_timeline_marker('missing'),lambda e:update(e,marker(id='missing')),
    lambda e:update(e,marker(),end_frame=9),lambda e:update(e,marker(),comment=None),
    lambda e:e._commit(lambda:e.project.timeline_markers.clear()),
])
def test_invalid_or_generic_mutation_atomic_history_rollback(action):
    p=sample();p.timeline_markers=[marker()];e=Editor(p);before=state(e)
    with pytest.raises(ValueError):action(e)
    assert state(e)==before


def test_marker_transaction_cannot_modify_render_content_or_other_metadata(monkeypatch):
    e=Editor(sample());before=state(e)
    original=e._Editor__commit
    def malicious(change,**kwargs):
        def injected():change();e.project.name='Unexpected'
        return original(injected,**kwargs)
    monkeypatch.setattr(e,'_Editor__commit',malicious)
    with pytest.raises(ValueError,match='only timeline marker metadata'):e.add_timeline_marker('A',0)
    assert state(e)==before


def test_failure_during_validation_rolls_back_everything(monkeypatch):
    e=Editor(sample());before=state(e)
    original=Project.validate
    def fail(project):
        original(project)
        if project.timeline_markers:raise ValueError('forced failure')
    monkeypatch.setattr(Project,'validate',fail)
    with pytest.raises(ValueError,match='forced failure'):e.add_timeline_marker('A',0)
    assert state(e)==before


def test_markers_independent_of_locked_tracks_and_do_not_extend_duration():
    p=sample();p.track_edit_locks=[True]*4;e=Editor(p);original=deepcopy(p);duration=p.duration_frames
    m=e.add_timeline_marker('Far range',MAX_TIMELINE_MARKER_FRAME-1,MAX_TIMELINE_MARKER_FRAME)
    assert e.project.duration_frames==duration and not timeline_marker_in_bounds(m,duration)
    update(e,m,name='Still editable');e.delete_timeline_marker(m.id)
    assert e.project==original


@pytest.mark.parametrize('action',[
    lambda e:e.move_clip('right',30),lambda e:e.trim_clip('right',20,2),
    lambda e:e.delete_clip('right'),lambda e:e.delete_clips(['left','right']),
    lambda e:e.apply_source_insert(e.plan_source_insert('m',0,15,50,55)),
    lambda e:e.apply_gap_close(e.plan_gap_close(0,12)),
    lambda e:e.apply_ripple_delete(e.plan_ripple_delete('left')),
])
def test_clip_and_ripple_edits_never_move_clamp_or_delete_markers(action):
    e=Editor(sample());m=e.add_timeline_marker('Fixed',15,25);before=deepcopy(e.project.timeline_markers)
    action(e);assert e.project.timeline_markers==before
    e.project.validate();assert Project.from_dict(e.project.to_dict()).timeline_markers==before


def test_marker_edit_invalidates_preexisting_edit_plan_even_if_output_identical():
    e=Editor(sample());plan=e.plan_gap_close(0,12)
    e.add_timeline_marker('Annotation',5);before=state(e)
    with pytest.raises(ValueError):e.apply_gap_close(plan)
    assert state(e)==before


def test_frame_rate_changes_keep_exact_frame_coordinates_no_hidden_retime():
    e=Editor(sample());m=e.add_timeline_marker('Fixed frames',15,25)
    e._commit(lambda:setattr(e.project,'fps_num',24))
    assert e.project.timeline_markers==[m]
    assert Fraction(m.start_frame,1)/e.project.fps==Fraction(5,8)
    assert e.undo() and e.project.fps==30 and e.project.timeline_markers==[m]


def test_empty_shortened_project_save_reopen_recovery_preserves_outside_markers(tmp_path):
    e=Editor(sample());m=e.add_timeline_marker('范围',10,100,comment='<b>not HTML</b>\n备注')
    e.delete_clips(['left','right']);assert e.project.duration_frames==0
    assert e.project.timeline_markers==[m] and not timeline_marker_in_bounds(m,0)
    path=tmp_path/'工程.lumen';e.project.save(path);loaded=Project.load(path)
    assert loaded.timeline_markers==[m] and loaded.duration_frames==0
    with RecoveryStore(tmp_path/'recovery') as store:
        entry=store.write(loaded,str(uuid4()),path)
        restored=store.load(entry)
    assert restored.timeline_markers==[m] and restored.duration_frames==0
    recovered=Editor(restored);update(recovered,m,name='Manage outside');recovered.delete_timeline_marker(m.id)
    assert recovered.project.timeline_markers==[]


def test_pure_metadata_validation_and_commands_never_touch_source_files(monkeypatch):
    e=Editor(sample())
    def forbidden(*a,**kw):pytest.fail('Marker metadata attempted I/O')
    for name in ('stat','open','resolve'):monkeypatch.setattr(Path,name,forbidden)
    m=e.add_timeline_marker('A',0);update(e,m,name='B');e.project.validate()
    assert Project.from_dict(e.project.to_dict())==e.project
    e.delete_timeline_marker(m.id)


@pytest.mark.parametrize('action',['add','update','delete'])
def test_worker_cannot_mutate_editor(action):
    p=sample();p.timeline_markers=[marker()];e=Editor(p);before=state(e);errors=[]
    def attempt():
        try:
            if action=='add':e.add_timeline_marker('Worker',0)
            elif action=='update':update(e,marker(),name='Worker')
            else:e.delete_timeline_marker('mark')
        except ValueError as exc:errors.append(str(exc))
    thread=Thread(target=attempt);thread.start();thread.join()
    assert errors and 'owning thread' in errors[0] and state(e)==before


def budget_markers():
    records=[];remaining=MAX_TIMELINE_MARKER_TEXT
    while remaining:
        identifier=str(len(records));overhead=len(identifier)+1
        count=min(MAX_TIMELINE_MARKER_COMMENT,remaining-overhead)
        assert count>=0
        records.append(TimelineMarker(identifier,'M','🎬'*count,'yellow',0,None))
        remaining-=overhead+count
    return records


def test_aggregate_text_budget_exact_boundary_and_one_over_all_entrypoints():
    records=budget_markers()
    assert sum(len(m.id)+len(m.name)+len(m.comment) for m in records)==MAX_TIMELINE_MARKER_TEXT
    validate_timeline_markers(records)
    wire=[asdict(m) for m in records]
    assert timeline_markers_from_dict(wire)==records
    # Worst case non-BMP JSON escaping is 12 bytes per code point. Even the
    # ASCII-escaped representation remains under the existing 32MiB cap.
    assert len(json.dumps(wire,ensure_ascii=True,indent=2).encode()) < 32*1024*1024
    over=[*records[:-1],replace(records[-1],comment=records[-1].comment+'🎬')]
    with pytest.raises(ValueError,match='2000000 code-point'):validate_timeline_markers(over)
    with pytest.raises(ValueError,match='2000000 code-point'):
        timeline_markers_from_dict([asdict(m) for m in over])
    p=Project(timeline_markers=over)
    with pytest.raises(ValueError,match='2000000 code-point'):p.validate()


def test_aggregate_overbudget_edit_leaves_content_history_and_revision_unchanged():
    records=budget_markers();e=Editor(Project(timeline_markers=records));before=state(e)
    with pytest.raises(ValueError,match='2000000 code-point'):e.add_timeline_marker('A',0)
    assert state(e)==before
    with pytest.raises(ValueError,match='2000000 code-point'):
        update(e,records[-1],comment=records[-1].comment+'A')
    assert state(e)==before
    e.delete_timeline_marker(records[-1].id)
    assert len(e._undo)==1 and e.undo() and e.project.timeline_markers==records


@pytest.mark.parametrize('key',['id','name','comment'])
@pytest.mark.parametrize('codepoint',[
    *range(0xFDD0,0xFDF0),
    *(plane*0x10000+suffix for plane in range(17) for suffix in (0xFFFE,0xFFFF)),
],ids=lambda value:f'U+{value:04X}')
def test_all_unicode_noncharacters_rejected_memory_and_wire_without_normalization(key,codepoint):
    value='Before'+chr(codepoint)+'After'
    record=marker(**{key:value})
    with pytest.raises(ValueError,match='Invalid timeline marker '+key):validate_timeline_marker(record)
    payload=sample().to_dict();payload['timeline_markers']=[asdict(record)];before=deepcopy(payload)
    with pytest.raises(ValueError,match='Invalid timeline marker '+key):Project.from_dict(payload)
    assert payload==before


@pytest.mark.parametrize('key',['id','name','comment'])
@pytest.mark.parametrize('codepoint',[
    0xA0,0xAD,0x61C,0x200B,0x200C,0x200D,0x200E,0x200F,0x2060,0xFEFF,
    0xFDCF,0xFDF0,0xFFFD,0x10000,0x1FFFD,0x10FFFD,0x1F3AC,
],ids=lambda value:f'U+{value:04X}')
def test_other_valid_unicode_is_preserved_exactly_not_broadly_restricted(key,codepoint):
    value='Before'+chr(codepoint)+'After'
    record=marker(**{key:value});validate_timeline_marker(record)
    project=sample();project.timeline_markers=[record]
    wire=project.to_dict();restored=Project.from_dict(wire)
    assert getattr(restored.timeline_markers[0],key)==value and restored==project
    assert json.loads(json.dumps(wire,ensure_ascii=False))==wire


@pytest.mark.parametrize('codepoint',[0xFDD0,0xFDD1,0xFDEF,0xFFFF,0x10FFFE,0x10FFFF],
                         ids=lambda value:f'U+{value:04X}')
def test_noncharacter_edits_reject_atomically_without_destroying_valid_text(codepoint):
    e=Editor(sample());m=e.add_timeline_marker('Valid\u00A0name',0,comment='Keep\ufeff\ntext')
    before=state(e)
    with pytest.raises(ValueError,match='Invalid timeline marker name'):
        e.add_timeline_marker('A'+chr(codepoint)+'B',0)
    assert state(e)==before
    with pytest.raises(ValueError,match='Invalid timeline marker comment'):
        update(e,m,comment='A'+chr(codepoint)+'B')
    assert state(e)==before
