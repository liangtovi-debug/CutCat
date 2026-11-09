"""Bounded, explicit-local multilingual transcript retrieval. Never downloads.

The optional native runtime is isolated in a short-lived process. The index
keeps only immutable source captions and normalized 384-dimensional vectors.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

import numpy as np

from .model import Caption, MAX_CAPTION_FRAME, normalize_caption_text
from .file_identity import is_plain_file, path_matches_descriptor, same_file_state

MODEL_REPOSITORY = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
# These immutable release identities are filled from the official repository,
# not a third-party conversion. Unknown model graphs are deliberately refused.
MODEL_REVISION = 'a2817895426db05d2c8baa424e48042c723c8ba2'
MODEL_FILENAME = 'onnx/model_quint8_avx2.onnx'
MODEL_SHA256 = '98a01d88b7de996cdea58c32ca71208c09968d143798814b2ea09d3439dc334f'
TOKENIZER_SHA256 = '2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8'
MAX_CAPTIONS = 20_000
MAX_TOTAL_CHARACTERS = 4_000_000
MAX_MODEL_BYTES = 300 * 1024**2
MAX_TOKENIZER_BYTES = 32 * 1024**2
MAX_TOKENS = 128
BATCH_SIZE = 8
DIMENSION = 384
PREPROCESSING = 'official-tokenizer/128/attention-mask-mean/l2/v1'
MAX_WORKER_SECONDS = 30 * 60


class SearchError(ValueError):
    """A local search operation failed without modifying the transcript."""


class SearchCancelled(SearchError):
    pass


@dataclass(frozen=True)
class SearchConfig:
    model_path: str
    tokenizer_path: str
    threads: int = 2


@dataclass(frozen=True)
class CaptionSearchResult:
    id: str
    text: str
    start: int
    duration: int
    score: float


@dataclass(frozen=True)
class SearchProvenance:
    model_repository: str
    model_revision: str
    model_filename: str
    license: str
    model_sha256: str
    tokenizer_sha256: str
    engine: str
    engine_version: str
    tokenizer_version: str
    provider: str
    preprocessing: str
    dimension: int
    max_tokens: int
    batch_size: int
    threads: int
    truncated_captions: int
    worker_peak_rss_bytes: int | None


@dataclass(frozen=True)
class _CaptionSnapshot:
    id: str
    text: str
    start: int
    duration: int


def _cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise SearchCancelled('Semantic search cancelled.')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _snapshot(captions, cancel=None):
    if not isinstance(captions, (list, tuple)) or len(captions) > MAX_CAPTIONS:
        raise SearchError('Search supports at most 20,000 captions.')
    result, ids, total = [], set(), 0
    for caption in captions:
        _cancel(cancel)
        if not isinstance(caption, Caption):
            raise SearchError('Search requires caption entries.')
        identifier, text = caption.id, caption.text
        if not isinstance(identifier, str) or not identifier.strip() or len(identifier) > 1024 or any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in identifier):
            raise SearchError('Invalid caption ID.')
        if identifier in ids:
            raise SearchError('Duplicate caption ID.')
        ids.add(identifier)
        try:
            if normalize_caption_text(text) != text:
                raise ValueError('unnormalized line breaks')
        except ValueError as exc:
            raise SearchError('Invalid caption text.') from exc
        for name, low in (('start', 0), ('duration', 1)):
            value = getattr(caption, name)
            if type(value) is not int or not low <= value <= MAX_CAPTION_FRAME:
                raise SearchError('Invalid caption timing.')
        if caption.start + caption.duration > MAX_CAPTION_FRAME:
            raise SearchError('Caption end exceeds supported frame range.')
        total += len(text)
        if total > MAX_TOTAL_CHARACTERS:
            raise SearchError('Search supports at most 4,000,000 total caption characters.')
        result.append(_CaptionSnapshot(identifier, text, caption.start, caption.duration))
    return tuple(result)


def _snapshot_hash(snapshot):
    return hashlib.sha256(_json([asdict(c) for c in snapshot]).encode('utf-8')).hexdigest()


def _hash(path, cancel=None, max_bytes=MAX_MODEL_BYTES):
    digest = hashlib.sha256()
    selected = path.lstat()
    if not is_plain_file(selected) or not 0 < selected.st_size <= max_bytes:
        raise SearchError('Search assets must be bounded regular local files.')
    # Nonblocking open avoids hanging if a chosen file is replaced with a FIFO.
    flags = os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_BINARY', 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if not is_plain_file(before) or not 0 < before.st_size <= max_bytes:
            raise SearchError('Search assets must be bounded regular local files.')
        if not path_matches_descriptor(selected, before):
            raise SearchError('Search asset changed while being opened.')
        total = 0
        while block := stream.read(1024**2):
            _cancel(cancel)
            total += len(block)
            if total > max_bytes:
                raise SearchError('Search asset exceeded its file size limit.')
            digest.update(block)
        after = os.fstat(stream.fileno())
    now = path.lstat()
    if (total != before.st_size or not is_plain_file(now) or not same_file_state(before, after)
            or not same_file_state(selected, now) or not path_matches_descriptor(now, after)):
        raise SearchError('Search asset changed while being verified.')
    return digest.hexdigest()


def _assets(config, cancel=None):
    _cancel(cancel)
    if not isinstance(config, SearchConfig):
        raise SearchError('A SearchConfig is required.')
    if type(config.threads) is not int or not 1 <= config.threads <= 8:
        raise SearchError('Search CPU threads must be an integer from 1 to 8.')
    paths, hashes = [], []
    for raw, maximum, expected, label in (
        (config.model_path, MAX_MODEL_BYTES, MODEL_SHA256, 'ONNX model'),
        (config.tokenizer_path, MAX_TOKENIZER_BYTES, TOKENIZER_SHA256, 'tokenizer.json'),
    ):
        try:
            raw = os.fspath(raw)
            if not isinstance(raw, str) or '://' in raw or raw.startswith(('//', '\\\\')):
                raise ValueError('Only local filesystem paths are accepted')
            expanded = Path(raw).expanduser()
            if str(expanded).startswith(('//', '\\\\')) or expanded.is_symlink():
                raise ValueError('Select an actual local file rather than a link')
            path = expanded.resolve(strict=True)
            if not path.is_file() or not 0 < path.stat().st_size <= maximum:
                raise ValueError('invalid file size')
            actual = _hash(path, cancel, maximum)
        except SearchCancelled:
            raise
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            raise SearchError(f'Select an existing local {label} file within the documented size limit. No download is performed.') from exc
        if actual != expected:
            raise SearchError(f'The local {label} does not match the supported official model revision. See docs/LOCAL_SEARCH.md for the exact files and SHA-256 hashes.')
        paths.append(path)
        hashes.append(actual)
    return tuple(paths), tuple(hashes)


def _worker_command(request, response):
    if getattr(sys, 'frozen', False):
        return [sys.executable, '--lumen-search-worker', str(request), str(response)]
    return [sys.executable, '-m', 'lumen.semantic_search_worker', str(request), str(response)]


def _read_response(path):
    try:
        with path.open('rb') as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError('oversized response')
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError('invalid response')
        return value
    except (OSError, ValueError, TypeError) as exc:
        raise SearchError('Local search worker returned an invalid response.') from exc


def _validated_vectors(vectors, count):
    if vectors.dtype != np.float32 or vectors.shape != (count, DIMENSION) or not np.isfinite(vectors).all():
        raise SearchError('Local search worker returned invalid embedding vectors.')
    if count and not np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-4):
        raise SearchError('Local search worker returned non-normalized embedding vectors.')
    vectors.flags.writeable = False
    return vectors


def _embed(texts, config, paths, cancel=None, progress=None):
    """Run one bounded worker and always kill/reap on cancellation or failure."""
    from .local_ai import inference_slot
    _cancel(cancel)
    if progress:
        progress(0.0)
    with tempfile.TemporaryDirectory(prefix='lumen-search-') as directory:
        root = Path(directory)
        request, response = root / 'request.json', root / 'response.json'
        output, status = root / 'vectors.f32', root / 'progress.json'
        request.write_text(_json(dict(texts=list(texts), model_path=str(paths[0]),
            tokenizer_path=str(paths[1]), threads=config.threads,
            output=str(output), progress=str(status))), encoding='utf-8')
        with inference_slot('semantic-search', cancel=cancel):
            _cancel(cancel)
            try:
                process = subprocess.Popen(_worker_command(request, response), stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=False)
            except OSError as exc:
                raise SearchError('Cannot start the local search worker.') from exc
            last_progress = 0.0
            started = time.monotonic()
            try:
                while True:
                    _cancel(cancel)
                    if time.monotonic() - started > MAX_WORKER_SECONDS:
                        raise SearchError('Local search timed out; reduce the transcript size and try again.')
                    try:
                        code = process.wait(timeout=0.1)
                        break
                    except subprocess.TimeoutExpired:
                        pass
                    if progress and status.exists():
                        try:
                            value = _read_response(status).get('progress')
                            if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and last_progress < value < 1:
                                progress(value)
                                last_progress = value
                        except SearchError:
                            pass  # A progress-file replacement is not a result.
                _cancel(cancel)
                data = _read_response(response) if response.exists() else {}
                if code:
                    raise SearchError(data.get('error') if isinstance(data.get('error'), str) else
                        'Local search engine failed. Check the optional ONNX Runtime and tokenizers installation.')
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        if not data or not all(isinstance(data.get(key), str) and data[key].strip()
                               for key in ('engine_version', 'tokenizer_version')):
            raise SearchError('Local search worker returned no runtime provenance.')
        truncated = data.get('truncated')
        peak = data.get('peak_rss_bytes')
        if type(truncated) is not int or not 0 <= truncated <= len(texts) or (peak is not None and (type(peak) is not int or peak <= 0)):
            raise SearchError('Local search worker returned invalid provenance.')
        try:
            expected = len(texts) * DIMENSION * 4
            if output.stat().st_size != expected:
                raise SearchError('Local search worker returned an invalid vector buffer size.')
            with output.open('rb') as stream:
                vectors = np.frombuffer(stream.read(expected), dtype='<f4').reshape(len(texts), DIMENSION)
            vectors = _validated_vectors(vectors, len(texts))
        except (OSError, ValueError) as exc:
            raise SearchError('Local search worker returned invalid embedding vectors.') from exc
    _cancel(cancel)
    if progress:
        progress(1.0)
    return vectors, data


class SemanticIndex:
    """Immutable caption snapshot plus bounded local vectors; never a generator.

    Reuse an index only while ``matches(current_captions)`` is true. A model file
    change is independently rejected before every query. No disk vector cache
    or transcript upload is performed.
    """

    def __init__(self, snapshot, config, paths, hashes, vectors, provenance):
        self._snapshot = snapshot
        self._config = config
        self._paths = paths
        self._hashes = hashes
        self._vectors = _validated_vectors(vectors, len(snapshot))
        self.provenance = provenance
        self.caption_identity = _snapshot_hash(snapshot)
        self.identity = hashlib.sha256(_json(dict(captions=self.caption_identity,
            model=hashes[0], tokenizer=hashes[1], preprocessing=PREPROCESSING,
            engine_version=provenance.engine_version,
            tokenizer_version=provenance.tokenizer_version)).encode('utf-8')).hexdigest()

    @property
    def count(self):
        return len(self._snapshot)

    @classmethod
    def build(cls, captions, config, cancel=None, progress=None):
        _cancel(cancel)
        snapshot = _snapshot(captions, cancel)
        if not snapshot:
            raise SearchError('Add captions before building a semantic search index.')
        paths, hashes = _assets(config, cancel)
        vectors, data = _embed([c.text for c in snapshot], config, paths, cancel, progress)
        if _assets(config, cancel) != (paths, hashes):
            raise SearchError('Search model files changed while building the index; the result was discarded.')
        provenance = SearchProvenance(MODEL_REPOSITORY, MODEL_REVISION, MODEL_FILENAME,
            'Apache-2.0', hashes[0], hashes[1], 'onnxruntime', data['engine_version'],
            data['tokenizer_version'], 'CPUExecutionProvider', PREPROCESSING, DIMENSION,
            MAX_TOKENS, BATCH_SIZE, config.threads, data['truncated'], data.get('peak_rss_bytes'))
        return cls(snapshot, config, paths, hashes, vectors, provenance)

    def matches(self, captions):
        try:
            return _snapshot(captions) == self._snapshot
        except (SearchError, TypeError, AttributeError):
            return False

    def matches_config(self, config):
        """Cheap setting comparison; search also rehashes files before inference."""
        return config == self._config

    def search(self, query, limit=5, cancel=None, progress=None):
        _cancel(cancel)
        try:
            query = normalize_caption_text(query)
        except ValueError as exc:
            raise SearchError('Enter a nonempty search query of at most 10,000 characters.') from exc
        if type(limit) is not int or not 1 <= limit <= 100:
            raise SearchError('Search result limit must be an integer from 1 to 100.')
        paths, hashes = _assets(self._config, cancel)
        if (paths, hashes) != (self._paths, self._hashes):
            raise SearchError('Search model files changed; rebuild the index.')
        vectors, data = _embed([query], self._config, paths, cancel, progress)
        if data['truncated']:
            raise SearchError('Shorten the search query to at most 128 model tokens, including special tokens. Its ending would otherwise be discarded.')
        if (data['engine_version'], data['tokenizer_version']) != (self.provenance.engine_version, self.provenance.tokenizer_version):
            raise SearchError('Search runtime changed; rebuild the index.')
        if _assets(self._config, cancel) != (paths, hashes):
            raise SearchError('Search model files changed during the query; rebuild the index.')
        # A simple rowwise dot avoids a second BLAS thread pool in the GUI
        # process; ONNX's explicitly configured CPU threads do the heavy work.
        scores = np.einsum('ij,j->i', self._vectors, vectors[0], optimize=False)
        _cancel(cancel)
        # Stable ties preserve the actual source order; scores are cosine
        # similarities, not probabilities or confidence in a generated answer.
        order = np.argsort(-scores, kind='stable')[:limit]
        return [CaptionSearchResult(c.id, c.text, c.start, c.duration,
                float(np.clip(scores[int(i)], -1.0, 1.0)))
                for i in order for c in [self._snapshot[int(i)]]]
