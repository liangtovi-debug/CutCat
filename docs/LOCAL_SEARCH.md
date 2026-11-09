# Local semantic transcript search (experimental)

This feature searches the meaning of **existing saved subtitle text**. It does not generate answers, new snippets, timestamps, or captions. Results carry the exact saved caption ID, text, start frame, duration, and cosine similarity. A result can be relevant even when the query uses different words or a different language.

## Explicit setup; no app downloader

1. Install the optional source-runtime dependencies deliberately: `python -m pip install -e '.[search]'`. The tested versions are `onnxruntime==1.22.1` and `tokenizers==0.21.4`, on Python 3.12 with NumPy 2.2.4. This installation is a separate online setup step; the app never installs dependencies or fetches models.
2. Obtain these two exact files from the official Sentence Transformers repository yourself and retain them outside the source tree. They are not included in this delivery:
   - [ONNX UINT8 AVX2 model](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/resolve/a2817895426db05d2c8baa424e48042c723c8ba2/onnx/model_quint8_avx2.onnx), 118,453,870 bytes.
   - [Complete tokenizer.json](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/resolve/a2817895426db05d2c8baa424e48042c723c8ba2/tokenizer.json), 9,081,518 bytes.
3. In Local AI, choose the existing model and tokenizer files, then verify/import their paths. Build the local subtitle index and search. Double-click a result or select it and use “Jump to selected result.” Model registration is separate from semantic compatibility checks; inference enforces the hashes below.
4. After changing subtitle text, timing, order, or IDs, rebuild the index. Changing model paths also invalidates it. Files are verified again before inference; changing the model or tokenizer on disk is refused. URL/UNC paths, direct symbolic links, and non-regular files such as FIFOs are rejected before reading; hashing is size-bounded and detects file changes.

Only this exact optimized ONNX graph is currently accepted. The graph is x86/AVX2-targeted; actual execution was tested on Linux x86_64. Native Windows, macOS, and ARM execution/packaging are unverified. The repository has other quantized variants, but this implementation does not silently accept or substitute them. Standard draft packages may omit this optional runtime; source-mode validation does not establish native-package support.

## Immutable provenance

- Repository: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- Revision: `a2817895426db05d2c8baa424e48042c723c8ba2`
- File: `onnx/model_quint8_avx2.onnx`
- Model SHA-256: `98a01d88b7de996cdea58c32ca71208c09968d143798814b2ea09d3439dc334f`
- Tokenizer SHA-256: `2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8`

The official model card declares Apache-2.0. ONNX Runtime is MIT; Hugging Face Tokenizers is Apache-2.0. These component labels do not replace reviewing all upstream terms before redistribution. No model weights, tokenizer assets, third-party runtime binaries, or downloaded training data are redistributed here. Download verification records and small configuration evidence are in `artifacts/search-validation/source-provenance.json`.

## Actual embedding pipeline

The complete official tokenizer is loaded with `tokenizers.Tokenizer.from_file`. The downloaded file uses **Unigram**, a precompiled normalizer, and its saved whitespace/metaspace pre-tokenizer and template post-processor. It is not a WordPiece tokenizer; reconstructing one would produce incorrect embeddings. Special tokens are `<s>` (ID 0), `<pad>` (ID 1), and `</s>` (ID 2).

The official [sentence-transformer configuration](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/blob/a2817895426db05d2c8baa424e48042c723c8ba2/sentence_bert_config.json) specifies a **128-token** maximum, even though the underlying transformer can handle more positions. We truncate including special tokens at 128 and dynamically right-pad within each batch using the real padding ID. No extra hand-written lowercasing, segmentation, or Chinese token splitting is applied.

The ONNX graph produces token embeddings with dimension 384. Its [pooling configuration](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/blob/a2817895426db05d2c8baa424e48042c723c8ba2/1_Pooling/config.json) specifies mean pooling. We multiply token embeddings by the attention mask, sum over tokens, divide by the non-padding token count, then L2-normalize. CLS-only pooling and unmasked averaging are not used. Index and query embeddings use the identical pipeline. Dot products of normalized vectors produce cosine similarities.

There is no keyword fallback, bag-of-words approximation, hard-coded semantic dictionary, answer-generation prompt, or network call. The app does not import Transformers or PyTorch. The `tokenizers` package can install a hub-client dependency, but this feature only calls its local-file loader, never a hub or pretrained-download API.

## Memory, cancellation, and consistency

- CPU execution only; default 2 inference threads, configurable 1–8, sequential ONNX execution with one inter-op thread. Tokenizer parallelism and ONNX busy-spinning are disabled. Worker BLAS/OpenMP auxiliary pools are limited to one thread; parent ranking uses a rowwise dot without BLAS dispatch.
- At most 20,000 captions, 10,000 characters per caption, and 4,000,000 aggregate caption characters. Caption IDs are limited to 1,024 characters. Query text is limited to 10,000 characters and must fit within 128 model tokens including special tokens; a token-overlength query is rejected with a request to shorten it. Result limits are 1–100.
- At most 8 captions × 128 tokens per inference batch. Vectors are 384 float32 values per caption: 30,720,000 bytes (29.3 MiB) at 20,000 captions, in addition to source text and application overhead.
- Model files are capped at 300 MiB and tokenizer files at 32 MiB, and must match the pinned hashes. Actual model plus tokenizer files are about 121.6 MiB.
- A shared in-process inference gate prevents overlap with other participating heavy local AI operations. It reports busy rather than building an unbounded job queue.
- Every build/query runs in a short-lived child process. Cancellation polls approximately every 100 ms, kills and reaps that process even during model loading, and removes temporary request/vector files. A 30-minute wall-time limit also kills a stuck worker; native stdout/stderr are discarded rather than accumulated in a logfile. The native session exits after completion; idle indexes retain only source snapshots and vectors. This saves idle memory at the cost of reloading the model for each query.
- Inputs and float buffers are bounded, but the app does not enforce a hard OS memory quota. Peak native memory depends on runtime and platform; the GUI and parent process consume additional memory. Temporary worker files contain local transcript text until the worker completes; ordinary cleanup removes them. A machine crash can leave OS temporary data and is outside the normal cancellation cleanup guarantee.
- The cache is **in memory only**. Its identity hashes the ordered caption ID/text/start/duration snapshot, model and tokenizer hashes, preprocessing version, and runtime versions. Embedding arrays are read-only. No pickle/vector database or disk index is loaded automatically.
- `matches(captions)` compares the current complete ordered snapshot; edits to text, timing, IDs, order, additions, and deletions invalidate it. Language labels alone do not affect this embedding pipeline. A retained old object returns its old exact snapshot; callers must check `matches` before displaying/jumping. The UI does so and discards stale in-flight results.

## API

```python
from lumen.semantic_search import SearchConfig, SemanticIndex

config = SearchConfig('/local/model_quint8_avx2.onnx', '/local/tokenizer.json', threads=2)
index = SemanticIndex.build(project.captions, config, cancel=stop_event, progress=on_progress)
if index.matches(project.captions):
    hits = index.search('资金不足导致暂停招聘', limit=5, cancel=stop_event)
    # hits contain exact CaptionSearchResult(id, text, start, duration, score).
```

`SearchError` reports validation/runtime failures; `SearchCancelled` reports cancellation. The shared inference gate can also report `LocalAIError`/`AIResourceBusy`. A progress callback receives a float from 0 through 1. Provenance records runtime versions, hashes, CPU provider, preprocessing parameters, truncated-caption count, and observed peak worker RSS when the OS exposes it. Missing runtime/model files produce a local error; there is no cloud fallback.

## Actual validation and limitations

`artifacts/search-validation/actual-results.json` contains full actual cosine scores, exact fixture captions, expected ranks, source times, machine/runtime metadata, and measured latency. The committed validation script has no downloader:

```sh
python artifacts/search-validation/validate_search.py /path/to/local/verified/cache
python artifacts/search-validation/validate_numerics.py /path/to/local/verified/cache
python -m pytest tests/test_semantic_search.py
```

The cache layout for that script is `onnx/model_quint8_avx2.onnx` plus `tokenizer.json`; the app itself accepts the two exact files at any selected local paths.

The recorded Linux x86_64 run indexed 16 captions in 1.28 s with 544 MiB peak worker RSS, and had 1.37 s median query latency, using 2 threads. All 10 prewritten English/Chinese paraphrase and cross-language queries ranked their intended source caption first, including selected lexical distractors. These are a small, intentionally clear regression fixture, **not** a representative retrieval benchmark, a hardware guarantee, or general accuracy estimate. Repeated-run figures vary. An additional actual-model check in `numerics-and-cancellation.json` confirms correct special-token/padding IDs, 128-token truncation, unit vector norms, and cancellation during native startup in 0.41 s with the inference gate released.

The report also records unrelated/no-answer and contradictory queries. The unrelated bank-account question returned an unrelated hard-disk caption with similarity 0.226; there was no account number in the source. The index always returns nearest neighbors, including when no caption answers the question. Similar wording can receive a high score despite opposite meaning. A similarity score is not factual confidence, a probability, or a reliable no-answer detector. The feature is retrieval, not a question-answering model.

Long captions are truncated to their first 128 tokens; later facts may be missed, and the UI reports the truncated-caption count. Queries that exceed 128 model tokens are rejected instead of silently using their prefix. Each caption is embedded independently, so fragmented subtitle sentences can lose context across cue boundaries. Negation, names, numbers, rare terminology, colloquial Chinese, mixed-language slang, and distinctions between nearly identical scenes need broader evaluation. There is no document reranker or generated explanation. Use the returned source text and exact timeline position to verify every match.
