# Experimental Apple Log 1 input

Use **Import Apple Log 1 / BT.2020 (experimental)** to assign this source interpretation explicitly. Normal import and drag/drop retain the strict SDR behavior; neither filenames nor camera labels trigger Log detection. Selecting the inspector option cannot reinterpret a normally imported SDR asset.

This is a narrow synthetic-validated input profile, not a claim of camera-file compatibility, Apple Log 2 support, Apple LUT matching or DaVinci Resolve matching. It produces SDR Rec.709 through CutCat SDR shoulder v1, not HDR mastering.

Accepted streams require unknown/unspecified transfer metadata and explicitly tagged BT.2020 primaries, no HDR/Dolby side metadata, and one of:

- Full-range `rgb48le`, `rgb48be`, `gbrp16le`, or `gbrp16be`, with RGB or unknown matrix metadata.
- `yuv420p10le`, `yuv422p10le`, or `yuv444p10le`, explicitly tagged `bt2020nc` and full (`pc`) or limited (`tv`) range.

Known SDR, PQ or HLG transfer tags conflict and are rejected rather than overridden. Other pixel formats, unknown YUV matrix/range, and unknown primaries fail closed. Active stabilization is not supported for this experimental input.

The import assignment is stored on the media and inherited by newly added clips. It is independent of the creative grade: Before/After and correction bypass retain normalization. Relink re-probes using the same interpretation and rejects conflicts before replacing any project state. Schema 29 persists the new media field; legacy projects default to Rec.709 and cannot claim the new Apple Log encoding under an old schema number.

Preview and scopes remain 8-bit H.264 proxies. The normalized/grading path retains RGB16 and float processing before clipping for SDR delivery. This is not a calibrated 10-bit display check or an unbounded floating-point master.
