# Optional Melo local TTS integration

This is a source-only local Linux milestone. Existing Windows/macOS test packages
have not been rebuilt and do not contain this addition. Kokoro remains the default.
No model/runtime redistribution or subjective naturalness certification is implied.

## What is implemented

- Explicit experimental `melo-zh_en-v1` selection in captions and local component setup.
- One documented embedded voice, sid 0; Chinese/English, no invented gender or accent label.
- Five exact pinned files: model, tokens, lexicon, date FST and number FST.
- CPU two threads, one native batch, 300-character ceiling checked before worker launch.
- Native 44.1 kHz PCM with immutable exact sample count, original text, rate and asset hashes.
- Existing audition, saved take, duration conflict decision, undo and project format retained.
- Native reported unsupported characters (including punctuation) reject the output.
  This detects the engine's reported omissions; it cannot detect every acoustic misreading.
- Pack switching invalidates verification and audition; stale verification jobs cannot restore it.

CutCat introduces no automatic pronunciation, punctuation, or numeric rewriting.
The existing editor normalization of CRLF line endings to LF remains unchanged. The native
frontend applies the explicit date-then-number FST sequence to its reading input.
No automatic text splitting or new reading defaults are introduced.

## Reproduce locally

Use the declared Python dependencies in `pyproject.toml`, including the optional
TTS dependencies. Obtain the pinned public files yourself as documented in
[LOCAL_TTS.md](LOCAL_TTS.md) and [MELO_MODEL_MANIFEST.json](MELO_MODEL_MANIFEST.json).
The app does not download them. Keep model assets outside the source tree.

Run the actual editor acceptance with a fresh output directory:

```
QT_QPA_PLATFORM=offscreen PYTHONPATH=. python scripts/run_network_denied.py -- \
  python scripts/verify_melo_setup_integration.py \
  --model-pack /local/melo-zh_en --output-dir /local/new-acceptance-output \
  --ui-language zh-CN --font-family 'Noto Sans CJK SC'
```

The network-denial harness is Linux-only. It is evidence of this process's local
execution, not a Windows/macOS privacy certification or physical audio-device test.
See [LOCAL_MANDARIN_BACKEND_EVALUATION.md](LOCAL_MANDARIN_BACKEND_EVALUATION.md)
for official fixed sources, measured CPU/RAM, frontend and licensing limitations.

## Verified result (2026-10-19)

- Final frozen affected-feature regression: **636 passed**, exit 0, 73.69 s.
  All 201 Python/config hashes matched before/after. This is not a new full-app suite.
- Two actual network-denied Qt workflows passed. The definitive repeat used
  stable declared dependencies, unchanged source/model hashes and clean teardown.
  It checked metadata/full verification, EN/ZH real auditions, live worker cancel
  and retry, exact text/character-limit errors, nonmutating auditions, full-take
  insertion, overflow decisions, undo/redo, save/reopen/relocation, preview/export.
- Saved Chinese audio: 313,856 native samples at 44,100 Hz; 7.116916 s,
  rounded up only for the 214-frame clip span. The 229-frame test timeline was
  7.633333 s including its placement offset. Preview/export graph and decoded
  PCM matched, with zero-sample native PCM alignment error in the tested case.
- Cancellation after observing a real worker PID cleaned up in 0.243 s in the
  recorded run. No partial WAV was published; all workers/Qt roots exited.
- After those native runs, only the shared EN/ZH resource-warning strings and
  their test assertions changed: long/slow takes may use several GiB of RAM.
  Core/inference/render code is unchanged. Final 636-test run includes this notice.
- Independent read-only review found no remaining P1/P2 production issue after
  fixing a legacy Kokoro-only QA loop and acceptance-runner setup assumptions.

Preserved unsuccessful validation attempts: the first aggregate stopped at
collection because the restored environment lacked the already-declared Pillow
and regex dependencies. Exact pinned dependencies were restored from PyPI.
An extended aggregate including unchanged heavy caption-render tests subsequently
reached its 360-second external limit, without a reported assertion failure;
its unfinished cases are not counted as passed. Logs/hashes are retained in
`initial-missing-dependencies/` and `extended-render-timeout/` in the evidence ZIP.
The final 636-test selection excludes that extended rendering file; actual
preview/export is separately covered by both real editor acceptance runs.

No new native Windows/macOS build, physical audio-device test, subjective
naturalness certification, complete package-redistribution audit, or unrestricted
Windows telemetry validation is claimed. Source and model assets remain separate.
