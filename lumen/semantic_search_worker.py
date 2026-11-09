"""Private local-only ONNX embedding worker, isolated for cancellation/RSS.

No transformers, model hub, network client, remote code, or automatic download.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys


def _write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    os.replace(temporary, path)


def _peak_rss():
    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value if sys.platform == 'darwin' else value * 1024)
    except (ImportError, AttributeError, OSError):
        return None


def _pool(tokens, attention_mask):
    import numpy as np
    if tokens.ndim != 3 or tokens.shape[:2] != attention_mask.shape or tokens.shape[2] != 384:
        raise ValueError('Expected batch x tokens x 384 token embeddings')
    mask = attention_mask.astype(np.float32)[..., None]
    pooled = np.sum(tokens * mask, axis=1) / np.maximum(mask.sum(axis=1), 1e-9)
    norms = np.linalg.norm(pooled, axis=1, keepdims=True)
    if not np.isfinite(pooled).all() or np.any(norms < 1e-9):
        raise ValueError('Invalid pooled embeddings')
    return (pooled / norms).astype('<f4')


def main(argv=None):
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 2:
        return 2
    request, response = map(Path, args)
    try:
        # Prevent the Rust tokenizer from using an uncontrolled thread pool.
        os.environ['TOKENIZERS_PARALLELISM'] = 'false'
        for name in ('OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'OMP_NUM_THREADS'):
            os.environ[name] = '1'
        try:
            import numpy as np
            import onnxruntime as ort
            import tokenizers
        except ImportError:
            _write_json(response, {'error': 'Optional local search runtime is missing. Install the documented onnxruntime and tokenizers extras; no download is performed by the app.'})
            return 2
        ort.disable_telemetry_events()
        from .semantic_search import BATCH_SIZE, MAX_TOKENS, MAX_CAPTIONS, MAX_TOTAL_CHARACTERS
        if request.stat().st_size > MAX_TOTAL_CHARACTERS * 6 + 2 * 1024**2:
            raise ValueError('Oversized request')
        data = json.loads(request.read_text(encoding='utf-8'))
        texts = data['texts']
        if not isinstance(texts, list) or not 1 <= len(texts) <= MAX_CAPTIONS or any(not isinstance(s, str) or not 0 < len(s) <= 10000 for s in texts) or sum(map(len, texts)) > MAX_TOTAL_CHARACTERS:
            raise ValueError('Invalid text batch')
        threads = data['threads']
        if type(threads) is not int or not 1 <= threads <= 4:
            raise ValueError('Invalid CPU thread count')
        tokenizer = tokenizers.Tokenizer.from_file(data['tokenizer_path'])
        pad_id = tokenizer.token_to_id('<pad>')
        if pad_id is None or tokenizer.token_to_id('<s>') is None or tokenizer.token_to_id('</s>') is None:
            raise ValueError('Incompatible official tokenizer')
        tokenizer.enable_truncation(max_length=MAX_TOKENS, strategy='longest_first')
        tokenizer.enable_padding(direction='right', pad_id=pad_id, pad_token='<pad>')
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        options.enable_cpu_mem_arena = False
        options.enable_mem_pattern = False
        options.add_session_config_entry('session.intra_op.allow_spinning', '0')
        options.add_session_config_entry('session.inter_op.allow_spinning', '0')
        session = ort.InferenceSession(data['model_path'], sess_options=options,
                                       providers=['CPUExecutionProvider'])
        names = {item.name for item in session.get_inputs()}
        if not {'input_ids', 'attention_mask'} <= names or not names <= {'input_ids', 'attention_mask', 'token_type_ids'}:
            raise ValueError('Unsupported model inputs')
        output_name = session.get_outputs()[0].name
        truncated = 0
        with Path(data['output']).open('wb') as output:
            for start in range(0, len(texts), BATCH_SIZE):
                encodings = tokenizer.encode_batch(texts[start:start + BATCH_SIZE], add_special_tokens=True)
                truncated += sum(bool(e.overflowing) for e in encodings)
                ids = np.asarray([e.ids for e in encodings], dtype=np.int64)
                mask = np.asarray([e.attention_mask for e in encodings], dtype=np.int64)
                if ids.shape[1] > MAX_TOKENS:
                    raise ValueError('Tokenizer exceeded maximum sequence length')
                feed = {'input_ids': ids, 'attention_mask': mask}
                if 'token_type_ids' in names:
                    feed['token_type_ids'] = np.asarray([e.type_ids for e in encodings], dtype=np.int64)
                embeddings = session.run([output_name], feed)[0]
                output.write(_pool(embeddings, mask).tobytes())
                _write_json(Path(data['progress']), {'progress': min(0.99, (start + len(encodings)) / len(texts))})
        _write_json(response, {'engine_version': ort.__version__, 'tokenizer_version': tokenizers.__version__,
                              'truncated': truncated, 'peak_rss_bytes': _peak_rss()})
        return 0
    except Exception:
        # Native diagnostics and private transcript text stay out of UI errors.
        _write_json(response, {'error': 'Local semantic search failed: incompatible runtime, model, tokenizer, or input. Use the exact documented official model files.'})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
