> Subsequent reconstructed candidate (2026-10-30): [exact affine-linked through-split](AFFINE_LINKED_PAIR_SPLIT.md) supports qualified 1× v1/v2 maps with independent residual tails. Older split refusal statements below describe the earlier milestone; joint roll remains strict-v1-only. No historical pass claim transfers.

# Schema43 affine A/V relationship foundation

Status: implemented candidate, **not yet product-tested or released** (2026-10-28).
This is milestone 1 of the bounded J/L audio-cut work. There is no new audio-only
roll, reset, or UI entry point in this slice. Native Windows/macOS acceptance is
separate. The accepted joint-roll implementation remains strict-v1-only.

## Persistent contract

`same-source-zero-offset-v1` is unchanged: same explicit Media with picture and
audio, V/A roles and source-audio ownership, equal start, duration and the entire
rational source map. Ordinary Link still creates only v1. No links are inferred
and no modes are normalized on load/save.

`same-source-affine-sync-v2` permits distinct edges but requires exact rational
speed and source-time intercept equality:

```
rV == rA
source_in_V - start_V * rV == source_in_A - start_A * rA
```

Each clip independently retains the existing bounded map, available-media,
positive-duration, tail, fade, local-key and track validation. In particular,
`D = ceil((E-O)/r)` and each residual lies in `[0,r)`; the pair's residuals need
not match. At start 0 and 1×, `[10,20.5)` and `[10,20.25)` both last 11 frames and
are affine-synchronized, but are **not** strict-v1-equal. This foundation has no
normalization/reset command which could discard either partial tail.

Schema 1–41 continue rejecting even an empty injected `av_links` field.
Schema42 requires its existing exact link records and accepts only v1, including
when a v2 record happens to have equal geometry. Schema43 requires the same
fields and permits only the two named modes. Future versions and modes reject.
The writer emits 43; historical serialization or frozen renderer code is not
rewritten to accept it.

## Operation capabilities

| Operation | v1 | v2 in this foundation |
|---|---|---|
| Save/load/recovery, active-role parameters | Retained | Retained, no mode conversion |
| Complete-pair move | Existing exact audio-translation proof | Same proof; muted A checked too |
| Complete-pair copy/paste | Existing identity/dependency checks | Mode and unequal maps retained; fresh mix slots |
| Complete-pair delete, explicit unlink | Retained | Retained; unlink never enables embedded sound |
| Joint slip/edge trim/through-split/four-role roll | Existing qualified behavior | Reject at candidate/qualification/planner/worker/Apply boundaries, even no-op |
| Source overwrite/insert/gap/ripple affecting linked members | Reject during planning and Apply | Same early rejection, even if final affine relation would remain valid |
| Unrelated overwrite/insert/gap/ripple | Retained | Retained; unrelated links do not block |
| Independent timing/track/source/FPS or generic matched edits | Existing central fence | Same fence, no new authorization |
| Group/transition/separate/reattach while linked | Reject | Reject; unlink is not enough for unequal-map reattach |
| Audio-only cut roll and align/reset | Not introduced | Deferred to milestone 2 |

The schema validator does not replace the generic edit fence. No transaction
exception was broadened. The legacy slip/trim candidates now validate detached
copies, so rejected qualification cannot canonicalize live scalar values.
The slip UI capability rejects v2; split and roll qualification use the same
model gate, while drag trim reaches the same pure refusal path.

## Genuine schema42 fixture ledger

`tests/fixtures/schema42-before-affine-av-links.json` is 9,243 bytes, SHA-256
`e343deddab36a51128981229a10d607d993ba56c81c215a1482a03dc4c54565c`.
It was serialized **before changing the candidate writer** by the accepted
schema42 model at `/tmp/cutcat-linked-pair-roll-r2/lumen/model.py`, verified
before and after with SHA-256
`31f9a98e80188f9bfb4617a2d01f683ef40a959de2f7779a6bad66a0c1d69fb3`.
The serializer checked its actual imported module path and VERSION=42, created
Media `m` with 24 fps picture+audio, suppressed V0 `v` and enabled A0 `a` at
start 5/source In 10/duration 12, and strict link `l`. It wrote sorted, two-space
JSON with trailing newline and verified a baseline-model round trip. No media
file was opened and bytecode writes were disabled. This is a synthetic fixture
produced by the real accepted historical writer, not a schema43 payload relabeled
42. The existing genuine schema41 fixture and frozen41 render oracles are intact.

Current-output assertions across older feature tests now expect 43. Their
historical fixture labels, explicit legacy field-removal gates and frozen reader
limits remain unchanged. Strict wire-field tests cover both 42 and 43.

## Pending proof

New tests cover rational intercept/tail algebra, schema boundaries, failure
atomicity, legacy gates, worker-before-I/O rejection, planner/Apply agreement,
whole-pair copy/move/history/recovery and ownership restrictions. Source-level
AST parsing is not product acceptance. Root-owned bounded execution, exact test
receipts and independent review must establish results on the frozen bytes.
Later audio-only roll will need its own four-member lock checks, atomic link+A
transaction, untouched V identity/value, exact key rebasing, mix-order proof,
full-scene decoded-picture and independent PCM oracles before a UI is shipped.
