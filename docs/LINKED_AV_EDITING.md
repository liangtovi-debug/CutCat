> Current schema43 extension: [affine-linked paired-edge trim](AFFINE_LINKED_EDGE_TRIM.md) moves both corresponding edges by one common timeline delta while preserving existing J/L offsets and independent tails. [Affine source slip](AFFINE_LINKED_SOURCE_SLIP.md) remains a separate content-only operation. [Affine through-split](AFFINE_LINKED_PAIR_SPLIT.md) is now a separate bounded candidate; joint roll remains strict-v1-only. The strict-v1 rounds below are historical; no later acceptance is inferred.

> Subsequent candidate: [qualified linked A/V through-split](LINKED_AV_PAIR_SPLIT.md) adds a separately worker-qualified integer1× subset. Unsupported-split statements below describe this earlier round; other limits remain. No later acceptance is inferred from this note.

# Same-source linked A/V and joint source slip / 同源音画关联与联合源滑移

Current extension: [joint non-ripple edge trim](LINKED_AV_EDGE_TRIM.md) now stages both members of exactly one selected pair. The original source-slip contract below is unchanged.

## Scope and workflow

Schema **42** persists one explicit 1:1 pair: one V0/V1 picture clip with its
source-audio component disabled, and one enabled A0/A1 audio clip. Both must
reference the same Media containing picture and sound, have identical timeline
start/duration and the complete exact rational source map. A user-muted A clip
is still enabled and may be linked. There is no auto-pairing, timing adjustment,
source extraction, source write or implicit restoration of video sound.

1. Select the two independent aligned clips, then **Edit → Link same-source A/V**.
2. Clicking either member selects the complete pair. Choose **Inspect linked
   video · V** or **Inspect linked audio · A** before editing that role's parameters.
   Role selection does not permit independent timing.
3. Move or delete the complete pair atomically, or Copy/Paste it on the original
   tracks with new clip and link identities. Pair moves prove complete local audio
   compilation and one common sample displacement, including muted audio; an
   incompatible fractional-frame phase rejects instead of silently shifting sound.
4. **Joint source slip…** accepts an exact new source-in. Preview checks source/LUT
   freshness on a cancellable worker; Apply checks again before one atomic edit.
   Both source-in/out values change together, preserving rational source span,
   speed, partial tail, timeline position/duration, local picture/window/audio
   keys (including hidden keys), gains, fades and historical mix anchors.
5. **Unlink A/V** removes only metadata. Video source audio stays disabled.
   Unlink first for independent timing, grouping or strict explicit reattachment.

链接／取消链接只改变元数据，不改变实际图像与混音。联合源滑移则改变所选源内容；
不声称滑移前后声音相同。双方严格引用同一源映射，不是重新同步或外录音频匹配。
完整复制沿用新音频贡献策略：新 clip/link ID，复制品不继承旧贡献的历史混音锚点。

## Conservative boundaries

- No one-to-many links, external recorder sync, offsets, J/L cuts, resync, linked
  split/roll/speed/track-remap, or linked ripple/insert/overwrite/gap edit.
  Operations touching linked members reject; unrelated edits keep working.
- Links cannot overlap groups or transition endpoints. Either member's edit lock
  blocks changed pair operations. Partial deletion never cascades invisibly.
- Generic transactions cannot bypass the timing fence by editing both members
  simultaneously. Only the narrow validated pair operations may change the pair.
- The pair's V/A gain, mute, visual parameters and local keys remain role-specific.
  Changing one role does not copy values or clear hidden settings on the other.
- Existing dependency budgets remain: ordinary local media use the established
  bounded sampled identity, generated audio/LUTs use full hashes, and dependencies
  are observed twice per worker batch. This is freshness, not a file lock or an
  adversarial full-file seal. No owner-thread file hashing, probing or decoding.
- Project/revision/selection/active-role changes and Undo/Redo ABA invalidate staged
  work. Link/Unlink may carry valid completed playback preview content; the strict
  final-still identity expires and requires a refresh. Slip requires rebuilt output.
- Schemas 1–41 migrate to no links; even an empty injected `av_links` field rejects
  in old versions. Schema42 requires the list, validates exact link records and
  rejects unknown future versions. Merely aligned old clips never become linked.

## Official workflow comparison (checked 2026-10-27)

[Adobe Premiere's linking guide](https://helpx.adobe.com/ae_en/premiere/desktop/add-audio-effects/basic-audio-editing/link-audio-and-video-clips.html)
describes joint moving/trimming and one video linked to multiple audio instances.
CutCat now addresses explicit persistent identity, selection and a narrow joint
source-slip workflow. The next bounded candidate adds same-source 1:1 joint edge trim, but not the broader multi-component model.

[Final Cut Pro's audio-view guide](https://support.apple.com/en-my/guide/final-cut-pro/ver549f212d/mac)
distinguishes expanded-but-attached A/V from detached independent audio, and
supports split editing. CutCat's four-track 1:1 relation is not Apple's connected
or magnetic timeline model, expanded component lanes or J/L editing.

The current edge-trim candidate defines local-envelope, source-tail and mixing-slot policy explicitly. The next bounded relationship gap is linked split, followed by explicit independent A/V timing
and source patching. Continuous interactive playback/proxies and professional
color monitoring remain separate gaps. These are workflow comparisons, not
paid-tier exclusivity, a product-completeness percentage or native parity claims.

## Evidence and delivery gates

Baseline: accepted final-frame source schema41, 994 files, tree identity
`f07de4e97c68cc4b4f218c17e07e5dffbb0b01fdf5756c76bbda9f6f11cb7fda`.
This round changes the project model and UI but not render/audio compilers or
runtime dependencies. See [real RGB/PCM oracle](LINKED_AV_ORACLE.md), strict model,
schema, freshness, copy, UI, localization and DPI tests. Durable evidence lives
outside the source tree and binds the frozen candidate hashes.

Focused tests and independent review do not replace aggregate/fresh-extraction
acceptance. Remote/main, hosted CI and Windows/macOS native-package delivery are
separate gates; none is asserted by this local candidate document.
