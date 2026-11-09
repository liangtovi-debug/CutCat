# Final-frame regression evidence: two distinct scopes

The historical compiler and current still backend must not be compared across
FFmpeg versions. Windows production uses pinned FFmpeg 9.0.2, whereas the accepted
historical compositor requires the independently pinned FFmpeg 7 runtime. An old
capability-probe failure is not a measured frame-byte difference.

## Immutable-code comparison

`final_frame_code_oracle.py` runs two independent children, one loading the
unchanged historical full renderer and one loading current `final_frame_preview`.
Both use `oracle_child_environment` and the pinned caption bootstrap before any
application or Pillow import. Both verify tool hashes/versions and retain the
reference tool leases until completion. The parent environment is untouched.
Module origins, actual font face/hash, shaping versions and media-tool identities
are checked in the receipts. Different runtime/font receipts reject the evidence.

Historical renderer and dependency SHA-256 values and all frozen files remain
unchanged. Only the historical transport lowers schema 43 to schema 41, and only
for scenes with no A/V links. Current still input retains schema 43. The original
all-frame, four-rate/six-scene matrix, omission sensitivity, precision, transfer,
thread-selector and TTC checks retain exact YUV equality, not tolerances.
The full-HD cancellation test still runs in the parent production environment.

The synthetic `make_scene` fixture uses the Noto Sans CJK SC 2.004 asset when
`LUMEN_TEST_CJK_FONT_DIR` is provided, after its bytes and license are verified.
This does not change the product's default font. OTF coverage is not TTC coverage;
the collection-specific test still reports a skip when no collection is installed.

The performance benchmark's `frozen_environment`, `measure` and worker default
runtime selection are unchanged. Code-oracle timings must not be represented as
old-versus-new performance speedups.

## Production integration

`production_final_frame_oracle.py` uses current `production_range_oracle.full_av`
to render the full graph without a frame window, then compares its YUV frame
slices with actual current still captures. The parent production tools remain in
use; on Windows the full-graph helper requires FFmpeg/ffprobe 9. These are exact
integration checks of two current paths, not an independent historical oracle.
They cover the four-rate/six-scene all-frame matrix, default effects, restoration
and SDR transfers, pure-motion thread selection, and one-byte corruption.

Linux FFmpeg 7 runs only establish Linux FFmpeg 7 results. Linux FFmpeg 9 results
and Windows native results must be recorded separately, with versions, tool hashes,
font identities and actual pass/fail/skip counts. No result here establishes
cross-runtime pixel identity or a successful package build.
