# Optional local subtitle assistance

This experimental feature proofreads **one selected subtitle** using an explicitly
selected local executable and model. It proposes text only. It does not inspect
audio/video, translate, change timestamps, invent missing dialogue, or automatically
apply anything. Tiny language models can still alter meaning or miss errors;
review the full original/suggested diff before explicitly applying a proposal.

## Required local files

The app has **no model downloader, cloud inference, server, or fallback**. Acquire
trusted files separately, review their terms, then select the local paths. Do not
select an arbitrary executable supplied by an untrusted party.

The validated configuration is:

- Runtime: official [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp),
  release `v0.6.0`, source commit
  `8345f333951c661d166b00e6f9362e553768f292`.
- Executable: `llama-completion` (`llama-completion.exe` on Windows), **not** the
  newer interactive `llama-cli` front end or `llama-server`. This adapter relies on
  the completion program's documented flags and fixed end-of-text marker.
- Model: [ggml-org/Qwen3.5-0.8B-GGUF](https://huggingface.co/ggml-org/Qwen3.5-0.8B-GGUF),
  `Qwen3.5-0.8B-Q4_0.gguf`, **563,036,064 bytes** (563 MB decimal / 537 MiB).
- Pinned model upload revision: `9447f74101aeb4e93621884dfa36ee8effb8831b`.
- SHA-256: `57d1997790d1744fba5b40a7317df71ea5e2acee28c47e78f0cce39c0703f8cf`.
- [Pinned model download](https://huggingface.co/ggml-org/Qwen3.5-0.8B-GGUF/resolve/9447f74101aeb4e93621884dfa36ee8effb8831b/Qwen3.5-0.8B-Q4_0.gguf).

This is a text-only use of the model. No vision projector is needed or bundled.
Other model families use different prompt templates and are not validated. A GGUF
extension alone does not establish compatibility. The program fails closed on
runtime errors or rejected output; it does not download a substitute model.

## Resource and privacy boundaries

- CPU-only, two threads by default; explicit range 1–4, including prompt processing.
- Fixed 4,096-token context, batch 256, microbatch 128, 32–512 generated tokens.
- One nonempty caption, at most 1,000 characters and 2,048 UTF-8 bytes.
- Model file at most 2 GiB; runtime executable at most 256 MiB. Those limits are
  not a guarantee of total process memory. File verification uses bounded,
  cancellation-aware, nonblocking regular-file reads and identity checks; URL and
  UNC inputs are rejected by the adapter itself.
- One shared heavy AI job at a time across the app. A second task is refused with
  a clear busy message, rather than silently queued or loading another model.
- A 180-second inference limit. Cancellation kills and reaps the isolated process,
  including while the model is loading. All caption/grammar temporary files are
  removed on success, failure, timeout, or cancellation.
- No model stays loaded after the request. Repeated requests reload it, trading
  latency for predictable release of memory.
- Local paths only; `--offline`, no GPU offload, no network URL or model-host flags.
  Inherited llama/GGML settings that could select remote models, RPC devices, extra
  sessions or logging are removed. The test build disables OpenSSL. The app does
  not claim to sandbox a deliberately malicious user-selected executable.
- The caption is written inside a private temporary directory. It is not put in
  command-line arguments or copied into surfaced runtime error logs. The app does
  not transmit caption text, model files, audio, video, or metadata to a service.

The Qwen3.5 non-thinking text template treats the caption as JSON data, escapes
literal model-role delimiters, and asks for minimal proofreading without new facts.
A grammar restricts output to one `text` field. Validation rejects malformed,
duplicate-key, empty, oversized, wrapped, translated, numerically altered, or
extensively rewritten responses. These checks reduce risk; they do not prove
semantic equivalence, correct names, factual accuracy, or instruction robustness.

## Integration contract

`lumen.subtitle_ai.AssistantConfig(runtime_path, model_path, threads=2,
max_tokens=512)` configures `suggest_caption(text, config, mode='proofread',
cancel=None, progress=None)`.

The frozen `CaptionSuggestion` preserves exact `original_text`, `suggested_text`,
`mode`, immutable runtime/model SHA-256 provenance, and warning strings. A caller
must show the original and suggestion, require explicit apply, confirm that the
selected caption still matches its captured ID and source text, and preserve its
ID, timing, style and other fields. Applying should be one undoable text edit.
Cancel, closing the dialog, an unchanged suggestion, or a stale selection must
not alter the project. This adapter itself never imports or mutates the editor
model.

`AssistantError` is a recoverable input/runtime/output error;
`AssistantCancelled` indicates cancellation. The shared gate may raise its own
`LocalAIError`/`AIResourceBusy` subclasses. Progress values are stages, not an ETA.

## Build used for Linux validation

Use an official source archive for the pinned commit above. A normal C++17
compiler and CMake are required. The validation used CMake 4.1.3 and a static
CPU build, with no bundled runtime/model added to the editor deliverable:

```sh
cmake -S llama.cpp -B build -DCMAKE_BUILD_TYPE=Release \
  -DGGML_NATIVE=OFF -DGGML_CUDA=OFF -DGGML_VULKAN=OFF \
  -DGGML_METAL=OFF -DGGML_BLAS=OFF -DLLAMA_OPENSSL=OFF \
  -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF \
  -DLLAMA_BUILD_SERVER=OFF -DBUILD_SHARED_LIBS=OFF
cmake --build build --target llama-completion -j 4
```

The test build's CPU instruction requirements are recorded in the validation
manifest. This is not a portable Windows/macOS binary. Build/test on each target
OS; do not infer native UI, packaging, speed, or memory validation from Linux.

## Component licensing and evidence

- [llama.cpp license at the tested commit](https://github.com/ggml-org/llama.cpp/blob/8345f333951c661d166b00e6f9362e553768f292/LICENSE): MIT, with bundled third-party components subject to their own notices.
- The GGUF maintainer model card labels the weights Apache-2.0 and identifies
  [Qwen/Qwen3.5-0.8B](https://huggingface.co/Qwen/Qwen3.5-0.8B) as its base model.
  Preserve model-specific license/attribution information if distribution is
  separately approved; this implementation does not authorize redistribution.
- Source hashes, exact fixture text, actual inference outputs, runtime timing and
  focused test results are recorded under `artifacts/subtitle-ai-validation/`.
  Only synthetic caption fixtures are used. Models, runtime binaries and downloaded
  third-party source stay outside the editor tree in a separate local test cache.

The small fixture suite establishes that actual local inference works for those
cases. It is not a quality benchmark, translation feature, general video assistant,
or a production/readiness claim.

### Recorded Linux smoke results

The real 0.8B Q4_0 model corrected `teh camera is ready` to `The camera is ready`
and `今天天汽很好，我们去公园吧` to `今天天气很好，我们去公园吧`. In the same
five-fixture run, the numeric/date and bilingual two-line cases were rejected by
the numeric-preservation and line-structure checks. An instruction-like caption
was copied unchanged. This is why suggestions remain review-only and rejection is
an expected safe outcome, rather than a reason to silently weaken validation.

Five short fixtures each took roughly 3–4 seconds here, including file hashing and
model loading. The maximum child-process resident memory was about 980 MiB; this
excludes the editor and is not a target-device memory guarantee. Cancellation
during actual native model loading killed and reaped the process in about 0.6
seconds including preceding hashes. See the machine-readable result for exact
timings and the limits of these measurements. No Windows/macOS claim is made.
