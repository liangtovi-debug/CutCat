# Controlled editing proposals: offline first milestone

This is a small **experimental, offline editing-review feature**, not an autonomous
video-editing agent. It adds two commands against explicitly selected existing
objects: `caption.replace_text` and `clip.set_gain_db`. The application does not
send footage, captions, credentials, or traces to a model provider. There is no
live API connection or cloud fallback in this milestone. A separate
[three-step settings dialog](API_SETTINGS.md) prepares configuration in memory
only; applying settings does not enable a remote model.

## What the user can do

In the AI panel, use **Controlled edits · offline** (explicitly labeled experimental in the panel). Select
one saved subtitle and/or one clip whose media has audio, enter the exact text
and/or absolute gain in dB, and choose **Preview exact changes**. The old/new
values are generated locally, not by a language model. Confirming that exact
preview applies the batch once; one Undo restores the whole batch and Redo
restores the accepted edit without inference. Revisions require a fresh preview.
English and Simplified Chinese are supported. This is not loudness analysis.

The existing optional local subtitle proofreader also uses the same gate. Its
model candidate still passes the existing numeric-fact, line-count, language and
bounded-proofreading checks. Human revisions are explicitly a separate manual
proposal and require another preview. Schema validity cannot prove that a model
preserved meaning: users must still review words and facts.

## Owned safety boundary

`lumen/controlled_assistant.py` validates the checked-in v1 schema plus stricter
runtime domain rules. Candidates are bounded to 64 KiB, depth 8, and 1–8 commands;
replacement text to 1,000 characters/2,048 UTF-8 bytes; finite gain to −96..24 dB.
Unknown/extra fields, duplicated JSON keys or target fields, booleans as numbers,
non-finite numbers, malformed Unicode and out-of-selection targets are refused.
No shell, generic method dispatcher, arbitrary path, URL, upload, import, export,
file write, trim, effect or project-wide action exists in this command surface.

The application creates frozen requests and proposals. Workers receive only a
minimal serialized snapshot of selected IDs/text/gain, never the Editor or full
project. The gate binds the reviewed diff to the request, editor session, monotonic
core revision, full before-state fingerprint, normalized commands, and after-state
fingerprint. Apply needs an exact one-use local approval and rechecks state after
the confirmation dialog, on the editor's owning thread. All commands are first
validated on a disposable project, then committed once. Failure restores the live
project without changing either history stack or the revision. No-op proposals
create no history entry and do not make the UI dirty.

Successful manual edits, undo and redo advance the core revision. Project
replacement starts a new session. A manual edit followed by Undo does not revive
an old proposal. The additional fingerprint detects currently observable direct
project mutation by legacy Python callers. These Python dataclasses are not a
sandbox against malicious in-process code: application code must use Editor
mutators, and external models never receive those objects. The UI invalidates
reviews on relevant project, target, provider, model, input and cancellation
changes, including late asynchronous completion and modal reentrancy.

Receipts are bounded (64), in memory only, and record accepted/no-op proposal
identity, digests, adapter, base/accepted revision, known local provenance and UTC
time without caption text. They are not durable audit records or a complete
undo-event audit. The assistant feature introduced no project metadata (format 14 at that milestone; later tail evidence uses format 15); no assistant metadata
is serialized into projects. Existing sound-library provenance is retained.

## Optional real OpenAI Agents SDK integration

`lumen/agents_adapter.py` is a replaceable proposal-only orchestration adapter using
the actual `Agent` and `Runner` APIs from **openai-agents==0.24.0 (MIT)**. Install the
optional `agents` extra only when intentionally testing that adapter. The normal
app does not import the SDK or require its dependencies.

The adapter in this milestone accepts only its built-in local fake Model with
explicit `test_mode=True`. It demonstrates the actual SDK loop with deterministic
synthetic output, not intelligent editing or a verified remote model. The GUI's
manual offline preview is independent of this optional SDK. No live OpenAI or
third-party gateway protocol, model name, account, price or credentials are
configured or claimed validated.

Tracing is disabled before SDK import and on every run. The pinned integration
installs a permanently disabled, processor-free tracing provider before any SDK
default exporter/client can exist. An already-initialized incompatible tracing
state fails closed. Ambient API keys do not select a model or enable traffic.
Model/tool data logging and OpenTelemetry are disabled. Cancellation, timeout,
malformed output and missing optional dependencies do not try a remote fallback.
The local fake model has no tools, handoffs, mutable editor, shell or file access.

Official references checked for this pin:
- https://openai.github.io/openai-agents-python/
- https://openai.github.io/openai-agents-python/ref/models/interface/
- https://openai.github.io/openai-agents-python/tracing/
- https://github.com/openai/openai-agents-python/blob/v0.24.0/LICENSE

## Validation and limits

See `CONTROLLED_ASSISTANT_VALIDATION.zh-CN.md` for the final recorded commands,
counts and measured output. New tests cover core transactions, real rendered
caption timing and decoded PCM gain, UI interruption/revision/cancel flows, and
fresh-process SDK fake-model integration with sockets/DNS forbidden and ambient
fake credentials. Synthetic local media is used; no user footage is uploaded.

This round is Linux-source validation only. No GitHub publication, remote CI,
Windows/macOS package build, native desktop verification, live inference,
credential storage or new model download is part of this deliverable. The existing
native packages do not acquire this feature until a separately validated build.
