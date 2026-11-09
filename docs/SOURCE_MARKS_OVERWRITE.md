> Schema38 successor: [named source ranges](SAVED_SOURCE_RANGES.md) can now preserve selected bounds. Unsaved working marks/cache remain session-only; the original edit and rendering contracts below are unchanged.

# Source marks and bounded single-track overwrite

This document describes the conservative non-rippling **Overwrite** mode. The source viewer now also offers a separate [bounded single-track Insert mode](SOURCE_MARKS_INSERT.md). Neither is full three/four-point editing, linked A/V source patching, cross-track synchronization, persistent proxies or a live GPU editor.

## Workflow

1. Select one media-bin item and open **Open source viewer…**. Existing double-click and Add behavior still append the whole source.
2. Inspect an exact source frame. The source cursor is independent of the timeline playhead. Positions are whole **project frames**, not native camera frame numbers.
3. Mark In and Out. The internal interval is half-open `[In, Out)`; Mark Out stores the cursor plus one, including the selected frame. Marks are session-only and are cleared when the viewer closes. Reset explicitly selects the complete source interval.
4. Optionally audition from the source cursor, at most 30 seconds. This is a separate source-only preview after input normalization, not a project preview. Timeline grades, effects, captions, overlays and the mix are absent. Exact cursor/marks are not inferred from approximate media-player position. Marking is disabled during audition; stop and inspect an exact frame before marking.
5. Choose a target track and capture the current timeline playhead. Moving the timeline playhead later does not silently change this target.
6. Preview the destination interval, affected clips and retained pieces. Then **Apply overwrite once**. Worker-side source verification runs before a single undoable transaction; verification is not itself an edit.

I/O marking shortcuts work only on the focused source image. Text fields, spinboxes, other dialogs and the timeline retain their existing input behavior.

## Atomic semantics

Only the chosen Clip track and destination `[start, start + Out - In)` interval change. Fully covered clips are removed. Left/right overlap preserves the outside piece. Interior overlap creates two retained pieces. The original ID stays on the left piece, or on the sole right piece; a second right piece receives a new ID. The inserted source clip is ungrouped and uses the normal clip-track audio semantics. No linked A/V patching is added.

Other tracks, captions, stickers, downstream positions and project settings remain unchanged. Static supported grade/LUT, gain, mute and opaque compositing settings remain on retained pieces. The new source clip starts with ordinary insertion defaults, including explicit input normalization where applicable.

The entire edit is rejected if any affected clip is grouped, a transition endpoint, on an edit-locked track, retimed, has a partial source tail, fades, motion/window animation, clip effects, non-default stabilization/restoration, transforms or transparent compositing. Untouched complex clips elsewhere remain untouched. Nothing is silently ungrouped, stripped, shortened, stretched or unlocked.

At fractional frame rates, some cuts cannot preserve the exact existing 48 kHz sample selection on both sides. Those cuts are rejected rather than shifting retained audio by one sample. Choose another project-frame boundary. Audio-only tracks retain their existing source-audio semantics.

## Deliberate compatibility boundary

Project schema stays **37**. The accepted renderer, historical rendering tests, native schema declarations and old project serialization are not changed by this feature.

The historical integral-1x renderer trims source timestamps before normalizing frame rate. This can differ from source-origin project-grid inspection for mixed-rate sources, and occasionally at coarse timestamp boundaries even when nominal rates match. An exploratory independent numbered-frame test demonstrated this difference for native 24/60 fps sources in a 29.97 fps project. Changing that behavior would alter the established appearance of existing projects. A proposed shared-renderer correction was therefore rejected and reverted, not shipped or disguised by changing historical assertions.

Picture overwrite requires matching actual source/project rates and verified constant-rate, complete-frame ranges. Mixed-rate inspection is available, but applying a mixed-rate picture overwrite is not. Before Apply, a bounded worker checks actual timestamps and native-resolution decoded-frame hashes for the selected and affected ranges against the unchanged legacy selection path. Any mismatch, incomplete evidence or unsupported range rejects the edit. This is intentionally stricter than trusting an average-frame-rate tag.

The merged selected and affected source ranges must fit an aggregate **30-second proof budget**, for picture and audio-track overwrite alike. Long sources can still be navigated with bounded frame inspection; exact audio windows now use a bounded from-origin f32 scan/cache described below, for PCM and compressed sources alike. This does not authorize a full-source transcode or an unbounded overwrite proof. Every selected/affected audio stream, including embedded picture audio, requires a bounded decoded-sample proof even when a container reports a duration; a header alone cannot prove that a damaged stream contains its claimed tail. Broader mixed-rate/VFR editing needs a separately versioned timing design.

## Resource and lifecycle limits

- Opening creates session state and a bounded frame request, never a full-source proxy.
- Inspection/audition decode and file checks run off the GUI thread. One frame is streamed at a time.
- Preview canvas is at most 640 × 360; supported project rates are at most 120 fps. Source dimensions, output bytes and diagnostics are capped.
- An audition lasts at most 30 seconds and its output is capped at 24 MiB. PCM and audition LRUs plus all pending staging share a 48 MiB hard storage cap. At most four audition and 32 PCM cache entries are retained; no more than 32 sparse windows per source are prepared. Disk-space preflight reserves headroom.
- Requests have one 120-second maximum deadline covering lock wait, file checks and all source proof, with cancellation and bounded subprocess cleanup. Video stills retain bounded preroll seeking.
- Audio (PCM, AAC, MP3, FLAC and embedded audio alike) is normalized from origin without input `-ss`, then exact stereo f32 windows are retained while prefixes/gaps are discarded. The former first-30-second source endpoint limit is replaced by a cumulative **30-minute normalized origin-scan budget per operation**, while selected/proof ranges still total at most 30 seconds. Warm containing windows avoid new decoding but never identity/probe/hash checks. This is not constant-time random access or a project/export substitute. [Exact cache contract and cold/warm measurements](EXACT_SOURCE_AUDIO_CACHE.md).
- Shorter embedded sound may contribute silence to video audition and picture-track proof only after a clean actual decoded EOF proves absence and all declared samples have decoded successfully. Duration metadata is not EOF: audible AAC padding beyond it remains real source content. Observed-EOF evidence has a bounded 32-entry source/decoder-bound session LRU and never bypasses fresh Apply identity checks. Missing claimed samples still reject; padding never conceals damaged media. Audio-track proof remains non-padding.
- Selection changes, relink, New/Open, Close/Cancel, edits, lock changes and undo/redo invalidate pending results or proposals. Apply rechecks captured editor/session/revision and source identities.
- Source identity uses portable file-state checks and bounded sampled hashes, not a storage lock or full-file cryptographic immutability guarantee. Generated speech/sound assets additionally require their recorded full-file provenance hash under a 64 MiB/read-time cap. Ordinary edits/replacements are detected; adversarial storage rewriting bytes and all metadata is not covered.
- Only supported self-contained single-file containers are accepted. Reference/manifest demuxers such as ffconcat, HLS/DASH and image sequences are rejected: hashing only a manifest cannot identify changing child media. This viewer does not recursively fingerprint or cache external dependencies.
- Source files are never written. Audition proxies and exact PCM windows live only in owned session temporary storage and are cleaned up. Project media paths and export inputs stay original.

## Validation and release scope

Tests cover the pure atomic planner, retained sample boundaries, numbered-frame delivery, real delayed source audio, static settings retention, save/undo/recovery, interrupted Qt worker flows, actual source-inspection-to-Apply playback, EN/Chinese live language and narrow/high-DPI layouts. Final source-bound receipts and archive hashes belong to the separately delivered validation report.

Linux offscreen tests do not constitute Windows/macOS packages, hardware playback verification, signing, a calibrated monitor or paid-editor feature parity. This round does not publish a repository, trigger hosted CI, install dependencies or expose footage.
