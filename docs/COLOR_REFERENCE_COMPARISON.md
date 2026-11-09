# Cross-clip reference comparison

## Purpose and competitor comparison

This feature supports manual shot matching: freeze one rendered frame, move to another clip, and compare their color and tone without relying on visual memory. It does not automatically alter a grade.

Blackmagic Design's [official Resolve Color page](https://www.blackmagicdesign.com/products/davinciresolve/color), reviewed on 2026-10-23, describes Gallery stills, image wipes between clips and split-screen comparison. CutCat previously exposed only Before/After renders of the current timeline. This is a bounded workflow improvement, not a Resolve Gallery or matching-algorithm clone.

## Data and quality contract

The reference is a copy of the accepted decoded preview image, not a source frame, pre-qualifier float stage, RAW image, HDR reference or high-bit-depth export. The current preview is an 8-bit compressed Rec.709 SDR cache decoded by Qt, then converted from supported YUV using explicit matrix/range metadata by the application. Already-RGB backend frames are passed through and not color-verified. Compression, chroma subsampling and display limitations remain. The earlier Qt CPU conversion discrepancy has been isolated and replaced for supported formats; see [preview conversion contract](PREVIEW_COLOR.md). Reference capture always preserves the actual displayed application pixels exactly. This is not monitor calibration or a colorimetric export guarantee.

One still is retained in memory for this session. Capture rejects frames exceeding 8,192 pixels on either side or 4,096 × 4,096 pixels in total; the immutable RGBA pixel payload is therefore at most 64 MiB. Oversized capture leaves an existing still intact. It has its own immutable pixel bytes and frozen provenance. Capturing again replaces that still; clearing releases it. No source media is uploaded and no reference is written to disk or into project JSON. Closing the app or changing projects discards the still. Saving a project does not preserve it.

The frozen reference must retain the decoded timestamp, preview mode and project revision of the frame that was actually shown. Source provenance identifies the timeline contributors at that displayed frame, not whichever inspector item happens to be selected. Grade edits and newer previews never change the existing reference pixels.

## Viewer-only behavior

Side-by-side and wipe modes compare the reference and current decoded preview. Neither overlays nor comparison controls enter the render graph, project serialization or undo history. View changes do not mark the project dirty. Export always uses stored project settings, regardless of the selected comparison or Before/After view.

Images retain aspect ratio and are fitted into their comparison areas; letterboxing is intentional. This is normalized image comparison, not geometric alignment, stabilization or pixel registration across different shots. The scopes analyze only the accepted current decoded image, never the combined comparison image or reference. Old or unavailable current frames must not be presented as current measurements.

## Freshness and interruption contract

Only a valid frame belonging to the active preview generation and revision may be captured or presented as current. Pending source changes, seeks and outdated renders invalidate the current image and capture eligibility until the relevant decoded frame arrives. Rejected late frames must not replace the viewer image. Open, new and recovery transitions clear project-specific display state.

### Native seek dispatch

A reproducible Qt 6.8.3 / PySide6 6.8.3 native lock inversion was captured during paused seeking. The Python-bound synchronous `setPosition` path held the GIL while waiting on a Qt connection mutex; a decoder worker destroying a renderer held that mutex while entering `QAudioOutput.disconnectNotify` through Python override lookup. Moving only the independent test observer to a queued connection did not fix it.

The viewer therefore requests `setPosition` through Qt's native queued slot on the GUI thread, not a Python timer callback invoking the synchronous binding. Pending target/provenance guards remain active, obsolete queued calls are removed when retiring a player, and failed dispatch disables current capture. This preserves UI-thread ownership without moving widgets or multimedia objects to a Python worker. Native diagnostic instrumentation is test-only and is not shipped in the source package.

## Validation and limitations

Validation uses synthetic media only. Coverage includes two differently graded clips, actual Qt decoding, timestamp/pixel evidence, immutable capture, stale-generation and seek interruption cases, reference/current aspect fitting, bilingual layout and high DPI, project history, and unchanged preview/ProRes exports. Actual final pass counts are recorded in the matching acceptance report after all frozen checks finish.

The deliverable is source tested with Linux Qt offscreen, not Windows/macOS native installer verification. This round does not update remote main. Supported mapped-YUV display conversion is now explicit; native platform qualification remains unverified. Later gaps include real white balance with independently checked chromatic adaptation, persistent grade galleries, automatic matching, scene-linear/HDR reference pipelines and native platform qualification. Existing warm/cool RGB offsets are not Kelvin white balance.
