import hashlib
import json
import pytest
from scripts.runtime_timing import new_timing_token, timed_stage


def test_stage_receipt_has_duration_and_private_node_association(tmp_path,monkeypatch):
    monkeypatch.setenv('RANGE_ORACLE_EVIDENCE',str(tmp_path))
    monkeypatch.setenv('PYTEST_CURRENT_TEST','tests/private_name.py::test_case (call)')
    token=new_timing_token()
    with timed_stage('reference','compile',token):pass
    raw=(tmp_path/(token+'.jsonl')).read_text();events=[json.loads(x) for x in raw.splitlines()]
    assert [x['event'] for x in events]==['start','finished']
    assert events[1]['elapsed_seconds']>=0
    assert events[1]['cpu_seconds']>=0
    assert events[1]['monotonic_seconds']>=events[0]['monotonic_seconds']
    assert events[0]['node_sha256']==hashlib.sha256(b'tests/private_name.py::test_case').hexdigest()
    assert 'private_name' not in raw


def test_failure_records_type_without_private_exception_body(tmp_path,monkeypatch):
    monkeypatch.setenv('RANGE_ORACLE_EVIDENCE',str(tmp_path));token=new_timing_token()
    with pytest.raises(ValueError,match='PRIVATE-MEDIA-PATH'):
        with timed_stage('range','ffmpeg',token):raise ValueError('PRIVATE-MEDIA-PATH')
    raw=(tmp_path/(token+'.jsonl')).read_text();events=[json.loads(x) for x in raw.splitlines()]
    assert events[-1]['event']=='failed' and events[-1]['error_type']=='ValueError'
    assert 'PRIVATE-MEDIA-PATH' not in raw


def test_opt_in_only(tmp_path,monkeypatch):
    monkeypatch.delenv('RANGE_ORACLE_EVIDENCE',raising=False)
    with timed_stage('range','compile',new_timing_token()):pass
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('kind',['range','full'])
def test_ffmpeg_nonzero_is_failed_stage_with_original_error(tmp_path,monkeypatch,kind):
    from types import SimpleNamespace
    import sys
    import subprocess
    from lumen import render,media
    from scripts.benchmark_range_preview import _candidate_range_av_in_process
    from scripts.production_range_oracle import full_av
    monkeypatch.setenv('RANGE_ORACLE_EVIDENCE',str(tmp_path/'evidence'))
    class Engine:
        def __init__(self,**kwargs):pass
        def _compile(self,*args,**kwargs):return [],'null',None
        def close(self):pass
    monkeypatch.setattr(render,'RenderEngine',Engine)
    monkeypatch.setattr(render,'_composite_filter_threads',lambda p:1)
    monkeypatch.setattr(media,'ffmpeg_binary',lambda:sys.executable)
    monkeypatch.setattr(media,'ffprobe_binary',lambda:sys.executable)
    versions=iter(['ffmpeg','ffprobe'])
    def run(command,**kwargs):
        if command[-1]=='-version':return SimpleNamespace(stdout=next(versions)+' version 9.0.2',returncode=0)
        return SimpleNamespace(returncode=1,stderr=b'original private failure')
    monkeypatch.setattr(subprocess,'run',run)
    project=SimpleNamespace(fps_num=24,fps_den=1)
    with pytest.raises(RuntimeError,match='original private failure'):
        if kind=='range':_candidate_range_av_in_process(project,0,1,tmp_path/'output')
        else:full_av(project,tmp_path/'output')
    events=[json.loads(line) for p in (tmp_path/'evidence').glob('*.jsonl') for line in p.read_text().splitlines()]
    ffmpeg=[e['event'] for e in events if e['stage']=='ffmpeg']
    assert ffmpeg==['start','failed']
    assert 'original private failure' not in json.dumps(events)
