from dataclasses import replace
import threading
import pytest
from lumen.local_ai import (inference_slot,active_task,AIResourceBusy,InferenceCancelled,LocalAIError,
                           inspect_model_files,verify_registered,ModelRegistry)

def test_slot_busy_and_release_after_failure():
    with pytest.raises(RuntimeError):
        with inference_slot('first'):
            assert active_task()=='first'
            with pytest.raises(AIResourceBusy):
                with inference_slot('second'):pass
            raise RuntimeError('failure')
    assert active_task() is None
    with inference_slot('next'):assert active_task()=='next'

def test_cancel_and_thread_contention():
    cancel=threading.Event();cancel.set()
    with pytest.raises(InferenceCancelled):
        with inference_slot('cancelled',cancel):pass
    errors=[]
    def request():
        try:
            with inference_slot('other'):pass
        except Exception as e:errors.append(e)
    with inference_slot('owner'):
        thread=threading.Thread(target=request);thread.start();thread.join(2)
    assert len(errors)==1 and isinstance(errors[0],AIResourceBusy)

def test_registry_roundtrip_replaces_same_paths_and_detects_tamper(tmp_path):
    model=tmp_path/'model.gguf';model.write_bytes(b'original')
    record=inspect_model_files('subtitle-assistant',{'model':model},label='Local test')
    registry=ModelRegistry(tmp_path/'config'/'models.json');registry.register(record)
    assert registry.records()==[record];assert verify_registered(record)==record
    other=replace(record,id='replacement',label='Updated');registry.register(other)
    assert registry.records()==[other]
    model.write_bytes(b'modified')
    with pytest.raises(LocalAIError,match='changed'):verify_registered(record)
    assert not list((tmp_path/'config').glob('.lumen-models-*'))

@pytest.mark.parametrize('kind',['unknown','',None])
def test_unknown_categories_rejected(kind,tmp_path):
    with pytest.raises(LocalAIError):inspect_model_files(kind,{'model':tmp_path/'x'})

@pytest.mark.parametrize('path',['https://example.com/model','//host/model','file:///tmp/model'])
def test_no_remote_paths(path):
    with pytest.raises(LocalAIError,match='local'):inspect_model_files('semantic-search',{'model':path})

def test_size_cancel_and_symlink_limits(tmp_path,monkeypatch):
    import lumen.local_ai as module
    file=tmp_path/'model';file.write_bytes(b'abcd')
    monkeypatch.setattr(module,'MAX_MODEL_BYTES',3)
    with pytest.raises(LocalAIError,match='limit'):inspect_model_files('asr',{'model':file})
    cancel=threading.Event();cancel.set()
    with pytest.raises(InferenceCancelled):inspect_model_files('asr',{'model':file},cancel=cancel)
    link=tmp_path/'link'
    try:link.symlink_to(file)
    except OSError:pytest.skip('Symlink creation unavailable')
    with pytest.raises(LocalAIError,match='symbolic'):inspect_model_files('asr',{'model':link})

def test_registry_oversize_and_corruption(tmp_path):
    p=tmp_path/'registry.json';p.write_text('{"version":2,"models":[]}')
    with pytest.raises(LocalAIError):ModelRegistry(p).records()
    p.write_bytes(b' '* (1024*1024+1))
    with pytest.raises(LocalAIError):ModelRegistry(p).records()

def test_asr_uses_shared_gate(tmp_path):
    from lumen.asr import transcribe,ASRConfig,ASRError
    with inference_slot('subtitle-assistant'):
        with pytest.raises(ASRError,match='Another local AI task'):
            transcribe(tmp_path/'source.wav',ASRConfig('runtime','model'))
