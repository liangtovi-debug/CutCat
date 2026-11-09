"""Synthetic evidence rejection tests; never a native execution claim."""
import hashlib
import json
import pytest
from lumen.model import VERSION as PROJECT_VERSION
from scripts.write_native_manifest import make_manifest, fingerprint

SOURCE = {'schema':1,'commit':'a'*40,'tree':'b'*40,'source_sha256':'c'*64,'tracked_files':20,'clean':True}

def write(root,name,value):
    p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(value),encoding='utf-8')

def alter(root,name,changes):
    value=json.loads((root/name).read_text());value.update(changes);write(root,name,value)

def fixture(root,system='win32',machine='AMD64'):
    for name in ('dist/LumenEdit-Draft-Windows.zip','dist/LumenEdit-Draft-macOS.tar.gz','dist/python-dependencies.txt','build/ffmpeg-provenance.json','build/test-font-provenance.json'):
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'synthetic evidence')
    nodes=['tests/test_fixture.py::test_a','tests/test_fixture.py::test_b']
    digest=hashlib.sha256(json.dumps(nodes,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()
    outcomes={'passed':1,'skipped':1}
    status={'shard':0,'expected_nodes':2,'success':True,'returncode':0,'timed_out':False,'outcomes':outcomes}
    summary={'schema':1,'success':True,'exact_once':True,'paths':['tests'],'root':str(root.resolve()),'collected_nodes':2,'verified_nodes':2,'total_shards':1,'collection_sha256':digest,'outcomes':outcomes,'completed_shards':[status],'source_provenance_start':SOURCE,'source_provenance_end':SOURCE}
    write(root,'build/test-shards/summary.json',summary)
    write(root,'build/test-shards/plan.json',{'schema':1,'collection_sha256':digest,'collected':nodes,'shards':[nodes]})
    receipt={'schema':1,'exitstatus':0,'collected':nodes,'started':[],'finished':[],'reports':[],'collection_reports':[]}
    write(root,'build/test-shards/collection/receipt.json',receipt)
    receipt.update(started=nodes,finished=nodes,reports=[{'nodeid':node,'when':phase,'outcome':'skipped' if node==nodes[1] and phase=='call' else 'passed'} for node in nodes for phase in ('setup','call','teardown')])
    write(root,'build/test-shards/shard-000/receipt.json',receipt);write(root,'build/test-shards/shard-000/status.json',status)
    junit=b'<testsuites><testsuite><testcase name="test_a"/><testcase name="test_b"><skipped/></testcase></testsuite></testsuites>'
    (root/'build/test-shards/shard-000/results.xml').write_bytes(junit);(root/'build/test-shards/results.xml').write_bytes(junit)
    color={'synthetic_media_only':True,'downloads_or_user_media_used':False,'animation':{'schema':PROJECT_VERSION,'source_unchanged':True},'apple_log_experimental':{'source_unchanged':True},'highbit_caption_sticker_chain':{'source_unchanged':True},'ui':{'apply_once_undo_redo_and_persistence':True}}
    caption={'raqm':'0.11.0','fribidi':'1.0.18','harfbuzz':'14.5.0'}
    write(root,'build/caption-runtime.json',{'status':'passed','after':caption})
    write(root,'build/native-smoke/result.json',{'caption_shaping':caption,'passed':True,'frozen':True,'platform':system,'machine':machine,'built_source':SOURCE,'color_pipeline_runtime_checks':color})
    archive=root/'dist'/('LumenEdit-Draft-Windows.zip' if system=='win32' else 'LumenEdit-Draft-macOS.tar.gz')
    exe=root/'build/archived-package'/('LumenEdit-Draft/LumenEdit-Draft.exe' if system=='win32' else 'LumenEdit-Draft.app/Contents/MacOS/LumenEdit-Draft')
    exe.parent.mkdir(parents=True,exist_ok=True);exe.write_bytes(b'synthetic executable')
    write(root,'build/native-smoke-launch/process-result.json',{'schema':1,'success':True,'returncode':0,'timed_out':False,'platform':system,'archive':fingerprint(archive),'archive_path':str(archive.resolve()),'executable':str(exe.resolve()),'executable_fingerprint':fingerprint(exe),'result_path':str((root/'build/native-smoke/result.json').resolve())})
    return root

@pytest.mark.parametrize('system,machine',[('win32','AMD64'),('darwin','arm64')])
def test_manifest_binds_artifact_and_evidence(tmp_path,system,machine):
    root=fixture(tmp_path,system,machine);r=make_manifest(root,SOURCE,system,machine)
    assert r['source_provenance']==SOURCE and r['test_nodes']==2
    assert len(r['archive']['sha256'])==64
    assert {'tests','frozen_smoke','python_dependencies','ffmpeg_provenance','test_font_provenance','test_plan','test_collection_receipt','test_junit','shard_000_receipt','shard_000_junit','native_smoke_launch','caption_runtime'}==set(r['evidence'])
    assert r['unsigned_internal_draft'] and not r['physical_desktop_validated'] and not r['security_attestation']

@pytest.mark.parametrize('field,value',[('passed',False),('frozen',False),('platform','linux'),('machine','arm64'),('color_pipeline_runtime_checks',None),('built_source',None)])
def test_source_or_wrong_native_smoke_rejected(tmp_path,field,value):
    root=fixture(tmp_path);alter(root,'build/native-smoke/result.json',{field:value})
    with pytest.raises(ValueError):make_manifest(root,SOURCE,'win32','AMD64')

@pytest.mark.parametrize('change',[{'success':False},{'exact_once':False},{'verified_nodes':3},{'verified_nodes':True},{'outcomes':{'failed':2}},{'outcomes':{'passed':-1,'skipped':3}},{'paths':['tests/test_fixture.py']},{'root':'/stale/source'},{'collected_nodes':1},{'collection_sha256':'d'*64},{'completed_shards':[]},{'unstarted_shards':[0]},{'total_shards':2},{'schema':2}])
def test_incomplete_subset_or_inconsistent_tests_rejected(tmp_path,change):
    root=fixture(tmp_path);alter(root,'build/test-shards/summary.json',change)
    with pytest.raises(ValueError):make_manifest(root,SOURCE,'win32','AMD64')

@pytest.mark.parametrize('commit,system,machine',[('main','win32','AMD64'),('a'*40,'linux','x86_64'),('a'*40,'darwin','x86_64'),('a'*40,'win32','arm64')])
def test_wrong_commit_and_unsupported_target_rejected(tmp_path,commit,system,machine):
    fixture(tmp_path)
    with pytest.raises(ValueError):make_manifest(tmp_path,dict(SOURCE,commit=commit),system,machine)

@pytest.mark.parametrize('name',['dist/LumenEdit-Draft-Windows.zip','dist/python-dependencies.txt','build/ffmpeg-provenance.json','build/test-font-provenance.json','build/native-smoke/result.json','build/test-shards/summary.json','build/test-shards/plan.json','build/test-shards/collection/receipt.json','build/test-shards/shard-000/receipt.json','build/test-shards/shard-000/status.json','build/test-shards/shard-000/results.xml','build/test-shards/results.xml'])
def test_missing_evidence_never_certified(tmp_path,name):
    fixture(tmp_path);(tmp_path/name).unlink()
    with pytest.raises((ValueError,OSError)):make_manifest(tmp_path,SOURCE,'win32','AMD64')

def test_fingerprint_rejects_empty_file(tmp_path):
    p=tmp_path/'empty';p.touch()
    with pytest.raises(ValueError):fingerprint(p)

@pytest.mark.parametrize('section',['animation','apple_log_experimental','highbit_caption_sticker_chain','ui'])
def test_failed_color_phase_cannot_be_certified(tmp_path,section):
    root=fixture(tmp_path);p=root/'build/native-smoke/result.json';v=json.loads(p.read_text());v['color_pipeline_runtime_checks'][section]['passed']=False;write(root,'build/native-smoke/result.json',v)
    with pytest.raises(ValueError):make_manifest(root,SOURCE,'win32','AMD64')

@pytest.mark.parametrize('key',['source_provenance_start','source_provenance_end'])
def test_stale_test_source_cannot_be_stamped_current(tmp_path,key):
    root=fixture(tmp_path);alter(root,'build/test-shards/summary.json',{key:dict(SOURCE,commit='d'*40)})
    with pytest.raises(ValueError):make_manifest(root,SOURCE,'win32','AMD64')

def test_stale_frozen_source_cannot_be_stamped_current(tmp_path):
    root=fixture(tmp_path);alter(root,'build/native-smoke/result.json',{'built_source':dict(SOURCE,source_sha256='e'*64)})
    with pytest.raises(ValueError):make_manifest(root,SOURCE,'win32','AMD64')

@pytest.mark.parametrize('change',[{'started':[]},{'finished':[]},{'reports':[]},{'exitstatus':1}])
def test_incomplete_receipt_cannot_be_certified(tmp_path,change):
    root=fixture(tmp_path);alter(root,'build/test-shards/shard-000/receipt.json',change)
    with pytest.raises(ValueError):make_manifest(root,SOURCE,'win32','AMD64')

def test_failed_or_short_junit_cannot_be_certified(tmp_path):
    root=fixture(tmp_path);(root/'build/test-shards/shard-000/results.xml').write_text('<testsuites/>')
    with pytest.raises(ValueError):make_manifest(root,SOURCE,'win32','AMD64')

def test_stale_empty_color_evidence_rejected(tmp_path):
    root=fixture(tmp_path);alter(root,'build/native-smoke/result.json',{'color_pipeline_runtime_checks':{}})
    with pytest.raises(ValueError):make_manifest(root,SOURCE,'win32','AMD64')


@pytest.mark.parametrize('name',['dist/LumenEdit-Draft-Windows.zip','build/archived-package/LumenEdit-Draft/LumenEdit-Draft.exe'])
def test_replaced_archive_or_executable_cannot_reuse_smoke(tmp_path,name):
    root=fixture(tmp_path);(root/name).write_bytes(b'changed after smoke')
    with pytest.raises(ValueError):make_manifest(root,SOURCE,'win32','AMD64')

@pytest.mark.parametrize('change',[{'success':False},{'returncode':1},{'timed_out':True},{'archive':{}},{'archive_path':'stale.zip'},{'result_path':'stale.json'},{'executable':'stale.exe'},{'executable_fingerprint':{}}])
def test_stale_or_failed_launcher_cannot_certify_archive(tmp_path,change):
    root=fixture(tmp_path);alter(root,'build/native-smoke-launch/process-result.json',change)
    with pytest.raises(ValueError):make_manifest(root,SOURCE,'win32','AMD64')

@pytest.mark.parametrize('schema', [PROJECT_VERSION - 1, PROJECT_VERSION + 1, True, None])
def test_manifest_requires_exact_current_project_schema_in_runtime_evidence(tmp_path, schema):
    root = fixture(tmp_path)
    name = 'build/native-smoke/result.json'
    report = json.loads((root / name).read_text())
    report['color_pipeline_runtime_checks']['animation']['schema'] = schema
    write(root, name, report)
    with pytest.raises(ValueError, match=f'current schema-{PROJECT_VERSION}'):
        make_manifest(root, SOURCE, 'win32', 'AMD64')


@pytest.mark.parametrize('change', [{'status':'failed'}, {'after':{}}, {'after':{'raqm':'other'}}])
def test_caption_runtime_mismatch_cannot_be_certified(tmp_path,change):
    root=fixture(tmp_path);alter(root,'build/caption-runtime.json',change)
    with pytest.raises(ValueError,match='caption shaping'):
        make_manifest(root,SOURCE,'win32','AMD64')
