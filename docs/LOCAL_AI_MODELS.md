# Optional local AI models and shared resource limits

This is source-run experimental functionality. The application does not download
models, upload media/text, call inference APIs, or fall back to a cloud service.
Select existing local files explicitly and verify them in the Local AI tab.
The registry stores file paths, sizes, SHA-256 identities and a local registration
time. File verification is not a certification of a license or executable safety.
Use only trusted official runtime sources; a selected native executable runs code
on the same computer.

## Setup

Install source dependencies explicitly:

```sh
python -m pip install -e ".[test,search,tts]"
```

- Subtitle assistant: select the documented CPU `llama-completion` executable and
  Qwen3.5-0.8B Q4_0 GGUF. Exact tested revision/hashes and official source links:
  `LOCAL_SUBTITLE_AI.md` and `artifacts/subtitle-ai-validation/`.
- Semantic subtitle search: select the pinned multilingual MiniLM AVX2 UINT8 ONNX
  and its exact official tokenizer.json. Runtime pins: onnxruntime1.22.1 and
  tokenizers0.21.4. See `LOCAL_SEARCH.md`. Only Linux x86_64 was actually tested;
  an ARM/native-platform-compatible pack still needs separate validation.
- Voices: select an existing complete Kokoro v1.0 orv1.1 pack and its matching
  version in Captions, then verify. The installed/verified pack determines the
  selectable catalog. Do not interchange IDs between versions. See local TTS
  and voice-browser documentation. No voice model is bundled.

The default frozen draft build excludes optional TTS/search runtime packages.
Qwen/Whisper executables and every model pack remain external. Native Windows/macOS
packaging and inference behavior are unverified, even though CI is prepared.

## Reviewed editing and truthful search

The assistant proofreads one saved subtitle. It returns a proposal and visible
text diff; nothing changes until Apply reviewed text only is confirmed. Caption
ID, language, start and duration stay unchanged. One undo restores the previous
text. Stale project/selection/model results are discarded. Numeric changes,
extensive rewrites, translations and changed line structure have conservative
rejection checks, but those checks do not prove preservation of meaning or facts.
Review remains essential. Suggestion provenance is available in its warning
label tooltip and validation artifacts; a persistent per-caption AI edit audit
trail is not implemented.

Semantic search indexes actual saved caption IDs/text/frame times. Results are
ranked existing captions, never generated summaries. Double-click or Jump selects
the actual caption and frame. Caption edits invalidate the index; model changes
invalidate both registration and index. Model/tokenizer/runtime identities are
checked again at query time. Similarity is not confidence, and an unrelated query
can still return a nearest caption. There is no claimed reliable no-answer detector.

Long captions use only the first128 model tokens; the UI reports the truncated
caption count. Overlength queries are rejected with an instruction to shorten
rather than silently dropping query text. Indexes are kept in memory, not saved
in project files. Rebuild after opening a project.

## Resources and privacy

One heavy AI inference task runs per application process across ASR, TTS,
proofreading and semantic search. Competing requests fail clearly; there is no
hidden queue. CPU thread settings are capped at4. Native workers are isolated,
bounded and killed/reaped on cancel/timeout. This does not impose a machine-wide
quota across several separately launched editor instances, or limit concurrent
video rendering.

Qwen uses a4096-token context, at most512 output tokens, and one caption of at
most1000 characters/2048 UTF-8 bytes. Its measured peak child RSS was about980MiB.
Search uses128 tokens,384-dimensional normalized embeddings, batches of8,
at most20,000 captions/4million aggregate characters. Measured worker peak RSS
was about544MiB; workers exit after each operation, leaving only vectors and the
caption snapshot in app memory. Measurements are small Linux test fixtures, not
hardware guarantees or overall app memory totals.

Local model registration bounds files to4GiB each/8GiB total, rejects special
files and URL/UNC paths, hashes with cancellation and change detection, and saves
its own small JSON atomically. Model contents are not copied into projects.
Temporary audition/suggestion/index processing remains local. ASR/TTS/AI inference
was exercised on synthetic test content, never uploaded user media.

OS-enforced disconnected cold-start and system-call network tracing were not
established. Code-path inspection and actual explicit-file inference are the
evidence available; do not equate them with a completed network-isolation audit.
