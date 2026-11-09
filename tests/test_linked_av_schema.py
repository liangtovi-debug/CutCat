"""Strict schema42 boundary and byte-verified genuine accepted schema41 fixture."""
from copy import deepcopy
from dataclasses import fields
from hashlib import sha256
import json
from pathlib import Path
import pytest
from lumen.model import AVLink, Project, VERSION
from av_links_legacy_fixture import strip_av_links_field
from test_audio_gain_keyframes_model import historical


@pytest.mark.parametrize('version',range(1,42))
def test_all_old_versions_have_no_links_and_reject_even_empty_smuggling(version):
    if version==41:
        data=json.loads((Path(__file__).parent/'fixtures/schema41-before-av-links.json').read_text())
    else:data=historical(version)
    before=deepcopy(data);loaded=Project.from_dict(data)
    assert loaded.av_links==[] and data==before and loaded.to_dict()['version']==43
    data['av_links']=[]
    with pytest.raises(ValueError,match='Legacy projects cannot contain A/V links'):Project.from_dict(data)


def test_genuine_schema41_independent_aligned_pair_is_not_automatically_linked():
    path=Path(__file__).parent/'fixtures/schema41-before-av-links.json'
    assert sha256(path.read_bytes()).hexdigest()=='2657505dfa34cbb22b4ad494c00400c57d8ecea7e9c53a964484a1ce999d861c'
    data=json.loads(path.read_text());before=deepcopy(data)
    assert data['version']==41 and 'av_links' not in data
    project=Project.from_dict(data);assert project.av_links==[] and data==before
    current=project.to_dict();assert current['version']==VERSION==43
    assert current.pop('av_links')==[];current['version']=41
    assert current==before


def test_current_required_forward_fence_and_appended_list_independence():
    a,b=Project(),Project();a.av_links.append(AVLink('l','v','a','m'))
    assert b.av_links==[] and fields(Project)[-1].name=='av_links'
    data=b.to_dict();data.pop('av_links')
    with pytest.raises(ValueError,match='Version 42'):Project.from_dict(data)
    data=b.to_dict();data['version']=44
    with pytest.raises(ValueError,match='Unsupported'):Project.from_dict(data)


@pytest.mark.parametrize('version',range(1,42))
def test_testonly_converter_removes_only_new_metadata(version):
    data={'version':version,'av_links':[],'sentinel':{'keep':[1,2]}}
    before=deepcopy(data)
    assert strip_av_links_field(data) is data
    assert data=={key:value for key,value in before.items() if key!='av_links'}


@pytest.mark.parametrize('version',[True,False,None,'41',0,-1,42,43])
def test_testonly_converter_does_not_strip_current_invalid_or_future(version):
    data={'version':version,'av_links':[]};before=deepcopy(data)
    with pytest.raises(AssertionError):strip_av_links_field(data)
    assert data==before


def test_frozen_oracle_projection_changes_only_empty_links_and_schema(tmp_path):
    from scripts.benchmark_final_frame_preview import write_frozen_project, write_project
    from test_linked_av_model import sample
    project=sample().project;before=deepcopy(project);current=project.to_dict()
    target=tmp_path/'frozen.json';write_frozen_project(project,target)
    expected=deepcopy(current);assert expected.pop('av_links')==[];expected['version']=41
    assert json.loads(target.read_text())==expected and project==before
    target=tmp_path/'current.json';write_project(project,target)
    assert json.loads(target.read_text())==current and project==before


def test_frozen_oracle_projection_refuses_nonempty_links_without_output(tmp_path):
    from scripts.benchmark_final_frame_preview import write_frozen_project
    from test_linked_av_model import sample
    project=sample(link=True).project;before=deepcopy(project);target=tmp_path/'reject.json'
    with pytest.raises(AssertionError):write_frozen_project(project,target)
    assert not target.exists() and project==before and project.av_links


@pytest.mark.parametrize('standalone',[False,True])
def test_frozen_oracle_projection_is_read_by_pinned_schema41_not_current_reader(tmp_path,standalone):
    import subprocess,sys
    from scripts.benchmark_final_frame_preview import (ROOT,BASELINE_VISUAL_DEPENDENCIES,
        FROZEN_BOOTSTRAP,frozen_baseline,frozen_environment,write_frozen_project,write_project)
    from test_linked_av_model import sample
    project=sample().project;target=tmp_path/'frozen.json';current=tmp_path/'current.json'
    write_frozen_project(project,target);write_project(project,current)
    root=frozen_baseline(ROOT if standalone else None)
    code=FROZEN_BOOTSTRAP+r'''
import hashlib,json,pathlib,sys
import lumen.model as m
assert m.VERSION==41
assert hashlib.sha256(pathlib.Path(m.__file__).read_bytes()).hexdigest()==sys.argv[3]
payload=json.loads(pathlib.Path(sys.argv[1]).read_text());before=json.dumps(payload,sort_keys=True)
assert m.Project.from_dict(payload).to_dict()==payload
assert json.dumps(payload,sort_keys=True)==before
try:m.Project.from_dict(json.loads(pathlib.Path(sys.argv[2]).read_text()))
except ValueError as error:assert str(error)=='Unsupported project format or version'
else:raise AssertionError('Frozen reader accepted schema43')
'''
    result=subprocess.run([sys.executable,'-c',code,str(target),str(current),
        BASELINE_VISUAL_DEPENDENCIES['lumen/model.py']],cwd=root,
        env=frozen_environment(root,tmp_path),capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr.decode(errors='replace')


@pytest.mark.parametrize('standalone',[False,True])
@pytest.mark.parametrize('hostile_override',[False,True])
def test_direct_frozen_preview_worker_binds_verified_modules(tmp_path,standalone,hostile_override):
    import os,subprocess,sys
    from scripts.benchmark_final_frame_preview import ROOT,frozen_baseline,write_frozen_project
    target=tmp_path/'frozen.json';write_frozen_project(Project(),target)
    hostile=tmp_path/'untrusted';hostile.mkdir()
    (hostile/'model.py').write_text("raise AssertionError('Inherited module path was executed')")
    root=frozen_baseline(ROOT if standalone else None)
    code=r'''
import hashlib,importlib,json,pathlib,sys,types
from scripts import benchmark_final_frame_preview as b
missing=pathlib.Path(sys.argv[2]).with_name('deliberately-missing-project.json')
assert not missing.exists()
try:b._worker(types.SimpleNamespace(runtime_root=pathlib.Path(sys.argv[1]),worker='preview',project=missing,frame=0))
except FileNotFoundError:pass
else:raise AssertionError('Missing project unexpectedly loaded')
m=importlib.import_module('lumen.model')
assert m.VERSION==41
assert hashlib.sha256(pathlib.Path(m.__file__).read_bytes()).hexdigest()==b.BASELINE_VISUAL_DEPENDENCIES['lumen/model.py']
print(json.dumps({'reader_schema':m.VERSION}))
'''
    env=dict(os.environ,PYTHONPATH=str(ROOT),PYTHONDONTWRITEBYTECODE='1')
    for key in ('CUTCAT_FROZEN_MODULES','CUTCAT_FROZEN_CAPTIONS'):
        env.pop(key,None)
        if hostile_override:env[key]=str(hostile)
    result=subprocess.run([sys.executable,'-c',code,str(root),str(target)],cwd=ROOT,
                          env=env,capture_output=True,timeout=30)
    assert result.returncode==0,result.stderr.decode(errors='replace')
    assert json.loads(result.stdout)['reader_schema']==41
