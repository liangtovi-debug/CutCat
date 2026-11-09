> Historical design, superseded in part on 2026-10-21 by the approved first
> offline implementation. See [implemented scope](CONTROLLED_ASSISTANT.md) and
> its validation record. The original design below is retained for context;
> deferred cloud/MCP/durable-audit sections are not implemented. The current
> project format is version 15 (decoded video-tail evidence), not the historical version 6 mentioned below.

# Deferred milestone: a controlled editing-proposal gate

**Design only.** This follows the delivered bilingual UI/LUT/native-validation
handoff. It does not implement an assistant, enable networking, select a paid
provider, request credentials, or authorize publication. Existing native-platform
validation limits still apply; this document supplies no new execution evidence.

**Priority update:** beauty/restoration, separate Effects/Stickers, and
speed/stabilization are the user's next UI/features priorities. This
controlled-assistant/API plan is deferred
behind that work. Retaining the design does not authorize implementation or
change the newly requested priority.

The product direction is **local by default, with explicitly optional cloud API
and external MCP integration later**. Earlier documents describe the current
local-only implementation. Their no-cloud runtime guarantees remain true for the
delivered app; optional connectivity is a future, separately accepted capability.

## 1. Build on the actual boundary

- `lumen/model.py`: `Editor._commit` validates an edit, restores its before-state
  on failure, and records independent project snapshots for undo/redo. The editor
  has no authoritative revision counter, proposal schema, or batch API. Calling
  several public editing methods would currently create several undo steps.
- `lumen/local_ai.py`: explicit file registration and identity verification plus
  a non-queuing, one-heavy-inference-at-a-time gate. Registration is not a trust
  certificate or a sandbox for a malicious executable.
- `lumen/subtitle_ai.py`: one-caption proofreading returns a frozen suggestion
  and runtime/model provenance. Its bounded parser rejects duplicate JSON keys,
  unwanted fields and unsafe output. It never edits the project.
- `lumen/ui_ai.py`: the current panel captures a caption and UI revision, shows
  a diff, asks before applying, and calls `Editor.update_caption`. That protection
  belongs below Qt before another adapter can safely use it. `ui.py`'s revision is
  also used for previews and must not be treated as a shared security boundary.

When resumed, the deliverable is a transport-independent gate used by the existing local
proofreader and by deterministic test proposals. No new general-purpose model or
natural-language timeline planner is needed to prove it.

## 2. Deliberately small command surface

Version 1 accepts only these two explicit commands against existing objects:

1. `caption.replace_text`: target caption ID and replacement text. Preserve ID,
   start, duration, language, caption style and every other object. Retain the
   proofreader's existing semantic checks on model-generated text. A human-edited
   proposal is separately labeled and still passes text/model validation.
2. `clip.set_gain_db`: target clip ID and absolute finite gain in the existing
   `-96..24` dB range, restricted to clips whose media has audio. Preserve mute,
   fades, timing, source, track, grade and effects. This is a numeric edit, not
   loudness normalization or a claim that the audio was analyzed.

The local model adapter emits the first command only. The second demonstrates a
non-text edit through a deterministic, explicitly chosen UI control/test fixture;
it does not imply that the existing proofreader understands editing requests.

Allow 1–8 commands, with no repeated target/field pair. Multiple selected captions
can exercise atomic batches using fixtures, but multi-caption inference is out of
scope. Targets must be in the request's captured, user-selected target set; never
resolve an ambiguous name, a timeline index, or a model-invented ID automatically.

Reject unknown command names and properties. In particular, there is no generic
`update_clip(**kwargs)`, JSON Patch, method-name dispatch, expression evaluation,
script, shell, arbitrary file path, URL, import, deletion, export, upload or plugin
execution command. Trim/move/ripple, effects, LUT changes, ASR/TTS generation and
project-wide edits remain outside this gate. Existing manual editor tools remain
available independently.

### Proposed schema contract

Check in a versioned JSON Schema and enforce it plus domain checks in the core.
Strict decoding rejects duplicate keys, NaN/Infinity, trailing content, excessive
nesting and invalid Unicode; use `additionalProperties: false` throughout and a
discriminated command union. No coercion of booleans, strings or floats into IDs,
integers or enum values. Cap the complete UTF-8 candidate at 64 KiB, nesting at 8,
and each replacement at the current proofreader limit of 1,000 characters and
2,048 UTF-8 bytes. Do not silently truncate or repair invalid proposals.

An adapter supplies only `schema_version` and `commands`. The application creates
an immutable trusted envelope around validated output:

- Fresh `proposal_id` and `request_id`, editor-session identity, monotonic base
  revision, selected target IDs, and canonical before-state fingerprint.
- Exact normalized commands, deterministic projected diff and their digest.
- Adapter identity, captured provenance, warnings and creation time.

These fields are captured locally, not accepted as facts from model output.
Canonicalization must match applied values; any text normalization happens before
the diff is displayed and approved. The schema and validator are shared future
adapter contracts, not a new project-file format.

## 3. Review, concurrency and one atomic edit

1. Capture a deep immutable project/selection snapshot and request context. Local
   inference continues using the current shared resource gate, timeout and
   cancellation contract. Refuse concurrent assistant generation rather than
   silently queueing requests. Cancellation invalidates the request token, even
   if a late callback returns successfully.
2. Decode and validate the entire candidate. Apply it to a disposable snapshot,
   run `Project.validate`, and derive the exact before/after diff locally. Model
   prose is untrusted explanation, never a substitute for that diff. Proposal
   generation and preview do not mutate the live project, history, or files.
3. Show affected objects, old/new text or dB values, warnings and provenance in
   the English/Chinese UI. Label this as an editing diff; audio/video has not been
   auditioned or rendered by the assistant. The user can reject or revise it.
   Revision creates a new digest and review; no old approval carries forward.
4. Only a direct local UI Apply action grants one-use approval for that exact
   proposal ID, digest, session and base revision. Generation, cloud consent,
   adapter output and an external client's request never grant Apply permission.
5. In one serialized editor operation, recheck session, monotonic revision,
   selection context, request validity, target existence and before-state
   fingerprint; then validate the candidate snapshot again. Check and commit
   must not be separated by an await, nested modal dialog or unlocked callback.
6. Commit the complete candidate once: one history entry, one revision increment,
   one preview invalidation, one dirty-state change and one application receipt.
   Undo restores the complete before-project; redo restores the exact accepted
   after-project without inference, network access or repeated approval. Do not
   implement a batch by invoking live public mutators in a loop.

Core-owned revisions advance on every successful project mutation and successful
undo/redo, including manual edits outside the assistant. Opening/replacing a
project creates a new session identity. An edit followed by undo must still make
an older proposal stale, even when the project contents match again. Preserve the
existing UI preview revision behavior while making core revision authoritative
for proposal application. Audit direct project-write paths during implementation;
all live mutations must pass the revision boundary. A fingerprint is additional
protection, not a replacement for that invariant.

Pending proposals are invalidated when the project, relevant selection, selected
adapter/model or request changes. No automatic rebasing, target substitution or
partial success. A validation error, stale response, cancellation, duplicate
Apply or failed commit leaves project, undo/redo and dirty state unchanged. A
no-op proposal produces no history entry. Apply is serialized on the editor/UI
thread; worker threads receive snapshots and never a mutable Editor reference.

Provenance includes schema/prompt versions, actual local runtime/model hashes,
adapter version, source selection fingerprint, proposed digest, human-reviewed
digest, accepted revision and UTC timestamp. Distinguish observed identities from
unverified provider/model claims. Keep bounded, in-memory session receipts for
accepted/no-op/rejected/undone/redone outcomes without raw prompts or credentials.
Receipts are not durable audit records in this milestone; restart clears them.
The accepted project edits still save/reopen using version 6. Persistent assistant
history would require its own privacy and migration decision.

## 4. Optional cloud contract, before any live adapter

The default remains local/offline. Missing models, a rejected response, timeout,
keychain failure or cancellation must never trigger cloud fallback or substitute
another provider. Local editing and the existing local adapters work with all
networking unavailable. Do not claim the app sandboxes a malicious local runtime.

The next milestone specifies and tests the cloud boundary with a fake transport
only. A future real provider adapter requires a separately approved provider and
implementation/validation scope. Before each outbound request, show:

- The actual provider, account/project if available, exact endpoint origin and
  model; relevant retention/processing disclosures and any known charge or limit.
- The exact user instruction, selected caption text and minimal selected-object
  values that will leave the device, including system/template content. Show
  omitted/unsupported data clearly. IDs can be request-local aliases; do not send
  full projects, media bytes, thumbnails, filenames, local paths, model files,
  environment variables or unrelated captions by default.
- An explicit **Send this data to this provider** confirmation. Editing the
  payload, selection, recipient or model invalidates it. Reject redirects or
  recipient changes instead of silently forwarding data or credentials.

Consent is bound to the exact serialized payload hash and recipient, not a broad
"AI enabled" switch. Sensitive caption contents must be visible in that preview.
The returned proposal still passes the same core validator and local Apply review.
Cancel before dispatch sends nothing; cancel after dispatch stops local processing
but cannot retract data already received. Do not automatically retry a request
with uncertain delivery or potentially duplicate cost. Redact payloads and secrets
from errors and diagnostic logs; do not claim unknown retention guarantees.

Future credentials belong in the operating system's secure credential store
(macOS Keychain, Windows Credential Manager, or an available Linux Secret Service
backend). Store only an opaque reference in application settings. Never put keys
in project JSON, model registries, command lines, inherited subprocess environments,
logs or portable exports. User entry/save/removal is explicit. If a secure store is
unavailable, fail closed for persistent storage; any session-memory alternative
requires an explicit user choice. This plan neither collects nor stores a key.

## 5. Adapter first, MCP as a separate phase

Once this deferred milestone is approved to resume, its implementation sequence
is core schema/transaction tests, wrapping
the existing local proofreader, then the bilingual review UI and fake-transport
privacy tests. This proves the shared gate without new live services.

A subsequent optional cloud adapter should implement only
snapshot → bounded candidate → shared review gate. It receives no Editor, shell,
filesystem capability or general tool-execution loop. Provider-native tool calls
are candidate data and cannot execute themselves.

External MCP is a later transport/integration phase, after the gate is proven.
Before implementation, clarify direction: an external MCP client proposing edits
to Lumen, or Lumen connecting to external MCP servers. The recommended first MCP
slice is the former: explicitly enabled, per-session access to a minimal selected
snapshot and proposal submission, with local Apply remaining mandatory. Do not
expose an unrestricted `apply`, shell, file read/write or generic editor endpoint.
Reading private project context is itself data sharing and needs a bounded,
explicit grant to the identified client; localhost is not sufficient authorization.

That phase must separately define transport/authentication, client identity,
minimal read scopes, payload limits, expiry/revocation and connection lifecycle.
Unknown clients and stale sessions fail closed. If the desired direction is
external servers, identify each server/tool and its transmitted data before use;
returned content still cannot bypass the command allowlist or local Apply gate.
No MCP listener, client connection, server process or persistent access is started
by this milestone or by merely opening a project containing assistant metadata.

## 6. Narrow implementation acceptance

Implementation is complete only when the following are demonstrated, with no live
cloud calls or credentials required:

1. Both allowed commands validate and produce exact previews. Existing local
   proofreading reaches the gate; it still rejects unsupported translation,
   numeric/line-structure changes and unsafe responses under its current rules.
   Unsupported commands/fields, forged target IDs, duplicate keys, booleans as
   numbers, non-finite values, oversized/nested input and instruction-like caption
   data are rejected or handled solely as data. Schema validation is not a claim
   that a suggestion preserves meaning.
2. An eight-command fixture commits as one undo step; undo/redo exactly restore
   all fields. Failure at any command or final validation leaves the live project
   and both history stacks unchanged. No-op and double-Apply cases are covered.
3. Manual edit, undo/redo, project replacement, selection/model change, cancellation
   and a late worker completion each invalidate the old proposal. A race between
   approval and commit cannot apply stale state. No background worker mutates it.
4. A fake cloud transport receives zero requests in offline mode, before consent,
   after payload/recipient changes, or after pre-dispatch cancellation. Its sole
   permitted payload exactly matches the reviewed bytes. Timeout/rejection never
   calls another provider; response content cannot invoke tools or approve edits.
5. English/Chinese review, reject, revise, Apply, busy, cancel, stale and undo flows
   have automated coverage; saved/reopened edits remain valid version-6 projects.
   Existing model, local-AI, caption, audio, color/LUT and UI regressions pass.
   Record actual test commands/results and native-platform checks separately;
   Linux tests do not establish Windows/macOS keychain or native UI behavior.

Stop here. A live provider adapter, durable assistant history and external MCP
transport each need their own bounded implementation decision. This milestone
does not authorize dependencies, paid services, credential setup, model downloads,
repository/license changes, publishing or claims of general autonomous editing.
