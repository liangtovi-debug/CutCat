"""Model-free regression tests; actual multilingual scores live in validation artifacts."""
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
import json
import sys
import threading
import time

import numpy as np
import pytest

from lumen.model import Caption
from lumen import semantic_search as search
from lumen.semantic_search_worker import _pool


@pytest.fixture
def config(tmp_path, monkeypatch):
    model, tokenizer = tmp_path / 'model.onnx', tmp_path / 'tokenizer.json'
    model.write_bytes(b'controlled unit test model')
    tokenizer.write_text('{}')
    monkeypatch.setattr(search, 'MODEL_SHA256', search._hash(model))
    monkeypatch.setattr(search, 'TOKENIZER_SHA256', search._hash(tokenizer))
    return search.SearchConfig(str(model), str(tokenizer))


@pytest.fixture
def captions():
    return [Caption('actual-a', 0, 30, 'The dog is sleeping.'),
            Caption('actual-b', 45, 60, '预算不足，项目暂时停止。')]


@pytest.fixture
def encoder(monkeypatch):
    calls = []
    def encode(texts, config, paths, cancel=None, progress=None):
        search._cancel(cancel)
        calls.append(tuple(texts))
        vectors = np.zeros((len(texts), search.DIMENSION), dtype=np.float32)
        for i, text in enumerate(texts):
            vectors[i, int(text.startswith('预算'))] = 1.0
        if progress:
            progress(0.0)
            progress(1.0)
        return vectors, dict(engine_version='unit-runtime', tokenizer_version='unit-tokenizer', truncated=0, peak_rss_bytes=1024)
    monkeypatch.setattr(search, '_embed', encode)
    return calls


def test_exact_snapshot_results_provenance(config, captions, encoder):
    updates = []
    index = search.SemanticIndex.build(captions, config, progress=updates.append)
    result = index.search('预算为什么有问题？', limit=1)[0]
    assert result == search.CaptionSearchResult('actual-b', captions[1].text, 45, 60, 1.0)
    assert index.count == 2
    assert index.matches(captions)
    assert index.matches_config(config)
    assert index.provenance.model_sha256 == search.MODEL_SHA256
    assert index.provenance.tokenizer_sha256 == search.TOKENIZER_SHA256
    assert index.provenance.max_tokens == 128
    assert index.provenance.dimension == 384
    assert index.provenance.provider == 'CPUExecutionProvider'
    assert updates == [0.0, 1.0]
    with pytest.raises(FrozenInstanceError):
        result.text = 'invented'
    with pytest.raises(FrozenInstanceError):
        index.provenance.threads = 99
    with pytest.raises(ValueError):
        index._vectors[0, 0] = 99


@pytest.mark.parametrize('field,value', [('id', 'new-id'), ('text', 'New text'), ('start', 1), ('duration', 31)])
def test_invalidates_every_identity_field(config, captions, encoder, field, value):
    index = search.SemanticIndex.build(captions, config)
    setattr(captions[0], field, value)
    assert not index.matches(captions)
    result = index.search('dog', limit=1)[0]
    assert (result.id, result.text, result.start, result.duration) == ('actual-a', 'The dog is sleeping.', 0, 30)


def test_invalidates_order_add_remove_and_replacement(config, captions, encoder):
    index = search.SemanticIndex.build(captions, config)
    assert not index.matches(captions[::-1])
    assert not index.matches(captions[:1])
    assert not index.matches(captions + [Caption('c', 120, 10, 'New')])
    assert index.matches([replace(c) for c in captions])
    assert not index.matches([object()])
    assert not index.matches(None)
    captions[0].language = 'zh'
    assert index.matches(captions)  # Language metadata does not change embeddings.


def test_cached_vector_identity(config, captions, encoder):
    one = search.SemanticIndex.build(captions, config)
    two = search.SemanticIndex.build([replace(c) for c in captions], config)
    assert one.identity == two.identity
    assert len(one.identity) == len(one.caption_identity) == 64
    three = search.SemanticIndex.build(captions[::-1], config)
    assert one.identity != three.identity
    assert one.caption_identity != three.caption_identity
    assert not one.matches_config(replace(config, threads=1))
    assert encoder[:3] == [tuple(c.text for c in captions)] * 2 + [tuple(c.text for c in captions[::-1])]


def test_stable_ties_and_result_limit(config, encoder):
    captions = [Caption(str(i), i * 20, 10, f'caption {i}') for i in range(6)]
    index = search.SemanticIndex.build(captions, config)
    assert [r.id for r in index.search('query', limit=3)] == ['0', '1', '2']
    assert len(index.search('query', limit=100)) == 6


@pytest.mark.parametrize('query', ['', ' ', '\x00', pytest.param('x' * 10001, id='overlength'), '\ud800', 5, None])
def test_bad_query(config, captions, encoder, query):
    index = search.SemanticIndex.build(captions, config)
    with pytest.raises(search.SearchError, match='query'):
        index.search(query)


@pytest.mark.parametrize('limit', [0, -1, 101, True, 1.5, '5'])
def test_bad_result_limit(config, captions, encoder, limit):
    index = search.SemanticIndex.build(captions, config)
    with pytest.raises(search.SearchError, match='limit'):
        index.search('dog', limit=limit)


@pytest.mark.parametrize('threads', [0, 9, True, 1.5, None])
def test_bad_threads(config, captions, encoder, threads):
    with pytest.raises(search.SearchError, match='threads'):
        search.SemanticIndex.build(captions, replace(config, threads=threads))


def test_missing_wrong_model_and_tokenizer(config, captions, encoder):
    with pytest.raises(search.SearchError, match='existing local'):
        search.SemanticIndex.build(captions, replace(config, model_path='no-such-file.onnx'))
    Path(config.model_path).write_bytes(b'wrong model')
    with pytest.raises(search.SearchError, match='supported official'):
        search.SemanticIndex.build(captions, config)
    assert not encoder


def test_file_change_invalidates_query(config, captions, encoder):
    index = search.SemanticIndex.build(captions, config)
    Path(config.tokenizer_path).write_text('{"changed":true}')
    with pytest.raises(search.SearchError, match='supported official'):
        index.search('dog')
    assert len(encoder) == 1


def test_model_change_during_build_is_discarded(config, captions, encoder, monkeypatch):
    original = search._embed
    def mutate(*args, **kwargs):
        vectors, metadata = original(*args, **kwargs)
        Path(config.model_path).write_bytes(b'changed while running')
        return vectors, metadata
    monkeypatch.setattr(search, '_embed', mutate)
    with pytest.raises(search.SearchError, match='supported official'):
        search.SemanticIndex.build(captions, config)


def test_no_captions(config):
    with pytest.raises(search.SearchError, match='Add captions'):
        search.SemanticIndex.build([], config)


def test_aggregate_limits(config, captions, encoder, monkeypatch):
    monkeypatch.setattr(search, 'MAX_CAPTIONS', 1)
    with pytest.raises(search.SearchError, match='20,000'):
        search.SemanticIndex.build(captions, config)
    monkeypatch.setattr(search, 'MAX_CAPTIONS', 20000)
    monkeypatch.setattr(search, 'MAX_TOTAL_CHARACTERS', 1)
    with pytest.raises(search.SearchError, match='total caption'):
        search.SemanticIndex.build(captions, config)
    assert not encoder


@pytest.mark.parametrize('field,value', [('id', ''), ('id', '\ud800'), pytest.param('id', 'x'*1025, id='overlength-id'), ('start', -1), ('start', True), ('duration', 0), ('duration', 2**53), ('text', 'bad\rline')])
def test_bad_caption_fields(config, captions, encoder, field, value):
    setattr(captions[0], field, value)
    with pytest.raises(search.SearchError):
        search.SemanticIndex.build(captions, config)
    assert not encoder


def test_duplicate_ids(config, captions, encoder):
    captions[1].id = captions[0].id
    with pytest.raises(search.SearchError, match='Duplicate'):
        search.SemanticIndex.build(captions, config)


def test_cancel_before_build_and_query(config, captions, encoder):
    event = threading.Event()
    event.set()
    with pytest.raises(search.SearchCancelled):
        search.SemanticIndex.build(captions, config, cancel=event)
    event.clear()
    index = search.SemanticIndex.build(captions, config)
    event.set()
    with pytest.raises(search.SearchCancelled):
        index.search('dog', cancel=event)


def test_cancel_native_process_is_reaped(config, captions, monkeypatch):
    monkeypatch.setattr(search, '_worker_command', lambda a, b: [sys.executable, '-c', 'import time; time.sleep(60)'])
    original = search.subprocess.Popen
    processes = []
    def start(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(search.subprocess, 'Popen', start)
    event = threading.Event()
    timer = threading.Timer(0.35, event.set)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(search.SearchCancelled):
            search.SemanticIndex.build(captions, config, cancel=event)
    finally:
        timer.cancel()
    assert time.monotonic() - started < 3
    assert processes and processes[0].poll() is not None
    from lumen.local_ai import active_task
    assert active_task() is None


def test_shared_inference_gate(config, captions):
    from lumen.local_ai import inference_slot, AIResourceBusy
    with inference_slot('tts'):
        with pytest.raises(AIResourceBusy, match='Another local AI'):
            search.SemanticIndex.build(captions, config)


def test_worker_failure(config, captions, monkeypatch):
    monkeypatch.setattr(search, '_worker_command', lambda a, b: [sys.executable, '-c', 'raise SystemExit(8)'])
    with pytest.raises(search.SearchError, match='engine failed'):
        search.SemanticIndex.build(captions, config)


def test_missing_optional_runtime(config, captions, monkeypatch):
    monkeypatch.setattr(search, '_worker_command', lambda a, b: [sys.executable, '-S', '-m', 'lumen.semantic_search_worker', str(a), str(b)])
    with pytest.raises(search.SearchError, match='runtime is missing'):
        search.SemanticIndex.build(captions, config)


def test_controlled_worker_buffer(config, captions, tmp_path, monkeypatch):
    script = tmp_path / 'worker.py'
    script.write_text('''import json,sys,numpy as np
from pathlib import Path
r=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
v=np.zeros((len(r['texts']),384),dtype='<f4');v[:,0]=1
Path(r['output']).write_bytes(v.tobytes())
Path(sys.argv[2]).write_text(json.dumps(dict(engine_version='unit',tokenizer_version='unit',truncated=0,peak_rss_bytes=2048)),encoding='utf-8')
''', encoding='utf-8')
    monkeypatch.setattr(search, '_worker_command', lambda a, b: [sys.executable, str(script), str(a), str(b)])
    index = search.SemanticIndex.build(captions, config)
    assert [r.id for r in index.search('query')] == [c.id for c in captions]


# Pytest exports node IDs in PYTEST_CURRENT_TEST; keep large payloads out of IDs
# so Windows' 32,767-character environment-value limit cannot block the test.
@pytest.mark.parametrize('content', ['[1]', 'invalid', pytest.param('x' * 65537, id='oversized-response')])
def test_bad_worker_response(tmp_path, content):
    path = tmp_path / 'response.json'
    path.write_text(content)
    with pytest.raises(search.SearchError, match='invalid response'):
        search._read_response(path)


@pytest.mark.parametrize('bad', [np.zeros((1,384),np.float32), np.ones((1,384),np.float32), np.full((1,384),np.nan,np.float32), np.ones((1,1),np.float32), np.ones((1,384),np.float64)])
def test_bad_vectors(bad):
    with pytest.raises(search.SearchError):
        search._validated_vectors(bad, 1)


def test_masked_mean_then_l2_not_cls_or_padding():
    tokens = np.zeros((2,3,384),np.float32)
    tokens[0,0,0]=1; tokens[0,1,1]=1; tokens[0,2,:]=1000
    tokens[1,0,0]=3; tokens[1,1,1]=4; tokens[1,2,:]=1000
    values = _pool(tokens, np.array([[1,1,0],[1,1,0]]))
    assert np.allclose(values[0,:2], [2**-0.5,2**-0.5])
    assert np.allclose(values[1,:2], [.6,.8])
    assert np.allclose(np.linalg.norm(values,axis=1),1)


def test_frozen_worker_command(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert search._worker_command('a', 'b') == [sys.executable, '--lumen-search-worker', 'a', 'b']


@pytest.mark.parametrize('path', ['https://example.invalid/model.onnx', 'file:///tmp/model.onnx', '//server/share/model.onnx', r'\\server\share\model.onnx'])
def test_remote_paths_refused_before_access(config, captions, encoder, monkeypatch, path):
    def fail_if_touched(*args, **kwargs):
        raise AssertionError('Remote path must not be inspected')
    monkeypatch.setattr(search.Path, 'resolve', fail_if_touched)
    with pytest.raises(search.SearchError, match='existing local'):
        search.SemanticIndex.build(captions, replace(config, model_path=path))


def test_fifo_refused_without_blocking(config, captions, tmp_path):
    if not hasattr(search.os, 'mkfifo'):
        pytest.skip('FIFO test is POSIX-only')
    path = tmp_path / 'fifo'
    search.os.mkfifo(path)
    with pytest.raises(search.SearchError, match='existing local'):
        search.SemanticIndex.build(captions, replace(config, model_path=str(path)))
    with pytest.raises(search.SearchError, match='regular local'):
        search._hash(path)


def test_hash_size_limit(config):
    with pytest.raises(search.SearchError, match='bounded regular'):
        search._hash(Path(config.model_path), max_bytes=1)


def test_native_timeout_is_reaped(config, captions, monkeypatch):
    monkeypatch.setattr(search, 'MAX_WORKER_SECONDS', 0.05)
    monkeypatch.setattr(search, '_worker_command', lambda a, b: [sys.executable, '-c', 'import time; time.sleep(60)'])
    original = search.subprocess.Popen
    processes = []
    def start(*args, **kwargs):
        assert kwargs['stdout'] == kwargs['stderr'] == search.subprocess.DEVNULL
        process = original(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(search.subprocess, 'Popen', start)
    with pytest.raises(search.SearchError, match='timed out'):
        search.SemanticIndex.build(captions, config)
    assert processes and processes[0].poll() is not None
    from lumen.local_ai import active_task
    assert active_task() is None


def test_runtime_change_invalidates_cached_vectors(config, captions, encoder, monkeypatch):
    index = search.SemanticIndex.build(captions, config)
    original = search._embed
    def changed_runtime(*args, **kwargs):
        vectors, data = original(*args, **kwargs)
        data['engine_version'] = 'different-runtime'
        return vectors, data
    monkeypatch.setattr(search, '_embed', changed_runtime)
    with pytest.raises(search.SearchError, match='runtime changed'):
        index.search('query')


def test_direct_symlink_refused(config, captions, tmp_path):
    path = tmp_path / 'linked.onnx'
    try:
        path.symlink_to(config.model_path)
    except (OSError, NotImplementedError):
        pytest.skip('Creating symbolic links is unavailable')
    with pytest.raises(search.SearchError, match='existing local'):
        search.SemanticIndex.build(captions, replace(config, model_path=str(path)))


def test_overlength_query_is_rejected_not_silently_truncated(config, captions, encoder, monkeypatch):
    index = search.SemanticIndex.build(captions, config)
    original = search._embed
    def truncated_query(*args, **kwargs):
        vectors, data = original(*args, **kwargs)
        data['truncated'] = 1
        return vectors, data
    monkeypatch.setattr(search, '_embed', truncated_query)
    with pytest.raises(search.SearchError, match='128 model tokens'):
        index.search('a long query')
