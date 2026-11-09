"""Schema40 structural audio state, immutable anchors and generic-edit fences."""
from copy import deepcopy
from dataclasses import FrozenInstanceError, asdict, replace
from fractions import Fraction

import pytest

from lumen.audio_components import (audio_mix_order_key, clip_emits_source_audio,
    effective_audio_mix_anchor, AUDIO_MIX_ORDER_ALGORITHM)
from lumen.audio_mix import AudioMixBuilder, compile_audio_mix
from lumen.audio_analysis import audio_settings_fingerprint
from lumen.model import AudioMixAnchor, Clip, Editor, Media, Project, VERSION
from lumen.timing import ClipTimeMap, duration_frames
from source_audio_legacy_fixture import strip_source_audio_state_fields


def sample():
    return Project(width=64,height=48,media=[Media('m','/missing.mov','Source',20,64,48,True,True)],
                   clips=[Clip('v','m',0,3,1,10)])


def detached(editor, target=2):
    source=editor.project.clips[0]
    return Clip('independent','m',target,source.start,source.source_in,source.duration,
                gain_db=source.gain_db,muted=source.muted,fade_in=source.fade_in,
                fade_out=source.fade_out,time_map=deepcopy(source.time_map),
                audio_mix_anchor=AudioMixAnchor(*effective_audio_mix_anchor(source)))


def state(editor):
    return deepcopy((editor.project,editor._undo,editor._redo,editor.revision,editor.session_id))


def old_minimal(version):
    value={'format':'lumen-project','version':version}
    if version>=33:value['video_transitions']=[]
    if version>=34:value['master_gain_db']=0.0
    if version>=35:value.update(media_bins=[],media_bin_ids={})
    if version>=36:value['clip_groups']=[]
    if version>=37:value['track_edit_locks']=[False]*4
    if version>=38:value['source_ranges']=[]
    if version>=39:value['timeline_markers']=[]
    return value


@pytest.mark.parametrize('version',range(1,40))
@pytest.mark.parametrize('field,value',[('source_audio_enabled',True),('audio_mix_anchor',None)])
def test_all_legacy_versions_reject_new_clip_fields_even_defaults(version,field,value):
    # Reject before any other absent historical clip field can obscure the fence.
    payload={**old_minimal(version),'clips':[{field:value}]}
    with pytest.raises(ValueError,match='Legacy clips cannot contain source audio separation fields'):
        Project.from_dict(payload)


@pytest.mark.parametrize('field',['source_audio_enabled','audio_mix_anchor'])
def test_both_new_fields_mandatory_current_and_defaults_canonical(field):
    payload=sample().to_dict();assert payload['version']==VERSION==43
    assert payload['clips'][0]['source_audio_enabled'] is True
    assert payload['clips'][0]['audio_mix_anchor'] is None
    payload['clips'][0].pop(field)
    with pytest.raises(ValueError,match='Version 40 clips require'):Project.from_dict(payload)


@pytest.mark.parametrize('value',[0,1,None,'true',[],{}])
def test_emission_gate_is_strict_bool(value):
    p=sample();p.clips[0].source_audio_enabled=value
    with pytest.raises(ValueError,match='source_audio_enabled'):p.validate()
    data=sample().to_dict();data['clips'][0]['source_audio_enabled']=value
    with pytest.raises(ValueError):Project.from_dict(data)


@pytest.mark.parametrize('value',[{},[],False,0,'anchor',{'track':0,'start':3},
    {'track':0,'start':3,'clip_id':'v','future':True},{'track':True,'start':3,'clip_id':'v'},
    {'track':4,'start':3,'clip_id':'v'},{'track':0,'start':-1,'clip_id':'v'},
    {'track':0,'start':1.0,'clip_id':'v'},{'track':0,'start':3,'clip_id':''},
    {'track':0,'start':3,'clip_id':'bad\x00id'}])
def test_anchor_wire_is_exact_strict_and_bounded(value):
    data=sample().to_dict();data['clips'][0]['audio_mix_anchor']=value
    with pytest.raises(ValueError):Project.from_dict(data)


def test_anchor_immutable_historical_not_live_reference():
    anchor=AudioMixAnchor(0,3,'deleted-original')
    with pytest.raises(FrozenInstanceError):anchor.start=9
    p=sample();p.clips[0].audio_mix_anchor=anchor
    p.validate();assert Project.from_dict(p.to_dict())==p
    assert effective_audio_mix_anchor(p.clips[0])==(0,3,'deleted-original')


@pytest.mark.parametrize('version',range(1,40))
def test_legacy_fixture_helper_removes_only_two_clip_fields(version):
    data={'version':version,'clips':[{'id':'v','source_audio_enabled':True,'audio_mix_anchor':None}],
          'sentinel':{'keep':[1,2]}}
    assert strip_source_audio_state_fields(data)=={'version':version,'clips':[{'id':'v'}],'sentinel':{'keep':[1,2]}}


@pytest.mark.parametrize('version',[True,False,None,'39',0,-1,40,41])
def test_legacy_fixture_helper_refuses_current_future_invalid(version):
    payload={'version':version,'clips':[{'source_audio_enabled':True}]};before=deepcopy(payload)
    with pytest.raises(AssertionError):strip_source_audio_state_fields(payload)
    assert payload==before


@pytest.mark.parametrize('field,value',[('source_audio_enabled',False),('audio_mix_anchor',AudioMixAnchor(0,0,'a'))])
def test_generic_update_and_callback_cannot_change_structural_state(field,value):
    e=Editor(sample());before=state(e)
    with pytest.raises(ValueError,match='immutable'):e.update_clip('v',**{field:value})
    assert state(e)==before
    with pytest.raises(ValueError,match='structural audio state'):
        e._commit(lambda:setattr(e.project.clips[0],field,value))
    assert state(e)==before


def test_generic_addition_cannot_inject_special_audio_state():
    e=Editor(sample());before=state(e);new=detached(e)
    with pytest.raises(ValueError,match='approved separation or copy'):
        e._commit(lambda:e.project.clips.append(new))
    assert state(e)==before


def test_exact_handoff_undo_redo_unmute_and_deletion_are_independent():
    e=Editor(sample());e.project.clips[0].muted=True;before=state(e)
    a=e._commit_source_audio_separation('v',detached(e));after=deepcopy(e.project)
    assert e.revision==before[3]+1 and len(e._undo)==len(before[1])+1
    v=e._clip('v');assert not v.source_audio_enabled and v.muted and a.muted
    assert a.media_id==v.media_id and a.audio_mix_anchor==AudioMixAnchor(0,3,'v')
    assert not clip_emits_source_audio(v,e.project.media[0])
    e.undo();assert e.project==before[0]
    e.redo();assert e.project==after
    e.update_clip('v',muted=False);assert not clip_emits_source_audio(e._clip('v'),e.project.media[0])
    e.delete_clip(a.id);assert not e._clip('v').source_audio_enabled
    e.undo();e.delete_clip('v');assert e._clip(a.id).audio_mix_anchor.clip_id=='v'


@pytest.mark.parametrize('field,value',[('gain_db',-5),('muted',True),('fade_in',1),('source_audio_enabled',False),
    ('audio_mix_anchor',AudioMixAnchor(1,0,'other')),('start',5),('duration',9)])
def test_handoff_primitive_rejects_smuggled_audio_changes(field,value):
    e=Editor(sample());a=detached(e);setattr(a,field,value);before=state(e)
    with pytest.raises(ValueError):e._commit_source_audio_separation('v',a)
    assert state(e)==before


def test_anchor_only_changes_final_mix_labels_never_picture_traversal():
    p=sample();p.clips=[Clip('v1','m',1,0,0,10),Clip('v0','m',0,0,0,10),Clip('a0','m',2,0,0,10)]
    e=Editor(p);before=AudioMixBuilder(p);audio=detached(e,3)
    # source helper expects first clip; set exact detached media contract manually.
    source=e._clip('v0');audio.id='a1';audio.start=0;audio.source_in=0
    audio.audio_mix_anchor=AudioMixAnchor(0,0,'v0')
    e._commit_source_audio_separation('v0',audio)
    builder=AudioMixBuilder(e.project)
    assert [c.id for c in builder.clips]==['v0','v1','a0','a1']
    assert '[silence][a3][a1][a2]amix' in builder.final_fragment(['a1','a2','a3'])
    assert '[silence][a0][a1][a2]amix' in before.final_fragment(['a0','a1','a2'])


def test_audio_fingerprint_tracks_gate_anchor_and_order_algorithm():
    p=sample();original=audio_settings_fingerprint(p)
    p.clips[0].source_audio_enabled=False;disabled=audio_settings_fingerprint(p)
    p.clips[0].audio_mix_anchor=AudioMixAnchor(0,0,'old');anchored=audio_settings_fingerprint(p)
    assert len({original,disabled,anchored})==3 and AUDIO_MIX_ORDER_ALGORITHM


@pytest.mark.parametrize('kind',['disabled','anchored'])
def test_special_state_split_rejects_atomically_but_independent_trims_preserve(kind):
    e=Editor(sample());clip=e.project.clips[0]
    if kind=='disabled':clip.source_audio_enabled=False
    else:clip.audio_mix_anchor=AudioMixAnchor(0,3,'historical')
    before=state(e)
    with pytest.raises(ValueError,match='Splitting separated'):e.split_clip('v',7)
    assert state(e)==before
    e.trim_clip('v',2,8)
    assert e._clip('v').source_audio_enabled==clip.source_audio_enabled
    assert e._clip('v').audio_mix_anchor==clip.audio_mix_anchor


def test_private_copy_primitive_cannot_mint_a_historical_handoff():
    e=Editor(sample());before=state(e)
    with pytest.raises(ValueError,match='without historical mix anchors'):
        e._commit_clip_paste([detached(e)],[])
    assert state(e)==before
    copy=deepcopy(e.project.clips[0]);copy.id='disabled-copy';copy.start=20
    copy.source_audio_enabled=False
    e._commit_clip_paste([copy],[])
    assert e._clip(copy.id).source_audio_enabled is False


@pytest.mark.parametrize('labels', [('a0','a1'), ('external_right','external_left'), ('a1','a0'), ()])
def test_no_anchor_final_stage_keeps_exact_legacy_supplied_label_api(labels):
    from lumen.audio_mix import AudioMixBuilder
    from schema39_audio_mix_fixture import AudioMixBuilder as Accepted39Builder
    project = Project(clips=[Clip('v','m',0,7,0,30)], master_gain_db=-12.375)
    assert AudioMixBuilder(project).final_fragment(labels) == Accepted39Builder(project).final_fragment(labels)


@pytest.mark.parametrize('labels', [('unknown',), ('a0','a0')])
def test_anchored_final_stage_rejects_unknown_or_duplicate_labels(labels):
    from lumen.audio_mix import AudioMixBuilder
    project = Project(clips=[Clip('v','m',0,7,0,30,
        audio_mix_anchor=AudioMixAnchor(0,7,'historical'))])
    with pytest.raises(ValueError, match='distinct known'):
        AudioMixBuilder(project).final_fragment(labels)


@pytest.fixture
def _source_audio_locale_restore():
    from lumen.i18n import get_locale, set_locale
    previous = get_locale()
    try:
        yield
    finally:
        set_locale(previous)

# Append to the new source-audio tests; leave established tests unchanged.
# No baseline checkout is needed at test runtime.
import ast
from pathlib import Path
from string import Formatter


def _source_audio_raised_error_grammars(module):
    tree = ast.parse(Path(module.__file__).read_text(encoding='utf-8'))
    result = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)
                and node.exc.args):
            continue
        value = node.exc.args[0]
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            result.add(value.value)
        elif isinstance(value, ast.JoinedStr):
            parts = []
            for part in value.values:
                if isinstance(part, ast.Constant):
                    parts.append(part.value)
                else:
                    assert isinstance(part, ast.FormattedValue)
                    assert isinstance(part.value, ast.Name)
                    assert part.format_spec is None and part.conversion == -1
                    parts.append('{' + part.value.id + '}')
            result.add(''.join(parts))
    return result


def test_source_audio_backend_error_grammars_have_owned_catalogs():
    from lumen import source_audio_separation, audio_components, audio_mix
    from lumen.i18n_catalog_errors import ZH_CN as central
    from lumen.i18n_catalog_source_audio_separation import ERROR_ZH_CN as owned
    for module in (source_audio_separation, audio_components):
        assert _source_audio_raised_error_grammars(module) <= owned.keys()
    # Mixer includes its older non-finite-number error; retain its established
    # central ownership instead of requiring it in the new feature catalog.
    assert _source_audio_raised_error_grammars(audio_mix) <= central.keys()


def test_source_audio_errors_render_in_both_locales_without_scanning_values(_source_audio_locale_restore):
    from lumen.i18n import set_locale, tr_error
    from lumen.i18n_catalog_errors import ZH_CN as central
    from lumen.i18n_catalog_source_audio_separation import ERROR_ZH_CN as owned
    for source, translated in owned.items():
        source_fields = {name for _, name, _, _ in Formatter().parse(source) if name}
        translated_fields = {name for _, name, _, _ in Formatter().parse(translated) if name}
        assert source_fields == translated_fields
        assert source in central
        values = {name: f'/private/Source overwrite_{{unchanged}}_中文_{name}.mov'
                  for name in source_fields}
        message = source.format(**values)
        set_locale('en'); assert str(tr_error(message)) == message
        set_locale('zh-CN'); rendered = str(tr_error(message))
        assert rendered != message and not rendered.startswith('技术错误详情：')
        assert all(value in rendered for value in values.values())
    set_locale('en')


def test_separated_source_cut_guard_has_dedicated_overwrite_and_insert_aliases(_source_audio_locale_restore):
    from lumen.i18n import set_locale, tr_error
    from lumen.i18n_catalog_source_overwrite import ERROR_ZH_CN as source_catalog
    from lumen.source_insert import insert_error_message
    message = 'Source overwrite cannot cut separated source audio clips yet'
    alias = insert_error_message(message)
    assert alias == 'Source insert cannot cut separated source audio clips yet'
    assert message in source_catalog and alias in source_catalog
    for source, word, forbidden in ((message, '覆盖', '插入'), (alias, '插入', '覆盖')):
        set_locale('en'); assert str(tr_error(source)) == source
        set_locale('zh-CN'); rendered = str(tr_error(source))
        assert word in rendered and forbidden not in rendered
        assert not rendered.startswith('技术错误详情：')
    set_locale('en')


def test_source_audio_structural_identifier_errors_are_explicitly_translated(_source_audio_locale_restore):
    from lumen.model import _text
    from lumen.i18n import set_locale, tr_error
    from lumen.i18n_catalog_source_audio_separation import ERROR_ZH_CN as owned
    # These identifiers are developer-authored schema/transaction labels, not
    # user-facing names. _text raises them indirectly, so a Raise-only AST
    # inventory intentionally needs this complementary validator coverage.
    for name in ('audio mix anchor clip id', 'new audio clip id',
                 'source clip id', 'new separated audio clip id'):
        try:
            _text('\x00', name, True)
        except ValueError as exc:
            message = str(exc)
        else:
            raise AssertionError('Expected strict identifier rejection')
        assert message in owned
        set_locale('en'); assert str(tr_error(message)) == message
        set_locale('zh-CN'); rendered = str(tr_error(message))
        assert rendered != message and not rendered.startswith('技术错误详情：')
    set_locale('en')
