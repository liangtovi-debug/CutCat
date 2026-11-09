# External runtime for the immutable range oracle

The accepted application ZIP and its manifest in `../frozen_range_preview` are
unchanged. No render, graph, model, gate or comparison code is patched in memory.

`caption_runtime.py` is a separately frozen dependency loader copied byte-for-byte
from the reviewed runtime at commit `4857f08dc8f27fc94eacc60a501c2b258fb770f3`.
SHA-256: `c6789493649b544bcc87b7256142d8124a3e2a7804c590dae3d57077ef443e3d`.
It imports no application modules. The child verifies and executes those exact
bytes before Pillow or the frozen application, retains DLL handles and requires
real RAQM shaping. Windows still verifies the FriBidi source, license, binary
receipt, architecture, loaded DLL path and reported version. No BASIC fallback.

The Windows reference uses Gyan FFmpeg 7.1.1 full build, external and CI-only.
Its archive SHA-256 is independently recorded in Microsoft's winget repository:
https://github.com/microsoft/winget-pkgs/blob/414d8a8b90fc50b33319b665dabc81d7edfd0284/manifests/g/Gyan/FFmpeg/7.1.1/Gyan.FFmpeg.installer.yaml
The adjacent unchanged manifest has SHA-256
`529ea436d5cd1ef3f76a32db157d04e198287aa0eb22f328800f78d4ca8f2e5c`.
Archive: `d760e1b3574402ed18b4865851f87d87e73965a982e6453212df8621fed1c508`.
The helper verifies archive and executable hashes before use; it never replaces
candidate PATH or LUMEN_FFMPEG/LUMEN_FFPROBE. LUMEN_REFERENCE_FFMPEG and
LUMEN_REFERENCE_FFPROBE select reference-only tools. Windows refuses an absent
selection or different executable bytes. Linux/macOS require FFmpeg 7 and record
actual versions/hashes; their binaries are not claimed identical to Windows.

Each completed reference receipt records tool hashes/versions and shaping
versions. Module provenance must use genuine ZIP loaders with the exact archive
and member origins. The archive hash is rechecked in the child before importing
and after rendering. Final YUV and PCM comparisons remain byte-for-byte.

A compatible reference gate alone does not establish cross-version equivalence:
FFmpeg 7 reference vs FFmpeg 9 candidate must pass the real oracle scenes. Any
byte difference remains a failure requiring investigation, not a new tolerance.

Integrity guards reject optimized Python explicitly. Windows executable read
leases deny write/delete sharing for the lifetime of the reference render;
identity and byte hashes are checked after version probing and after rendering.
Non-Windows read descriptors detect later changes but do not prevent concurrent
writes: the current local reference is the trusted system installation, not an
approved installer for arbitrary Unix executable paths.

Local validation currently BLOCKS adoption of mixed-version byte equivalence:
reference 7.1.5 versus locally built candidate 9.0.2 has real YUV/PCM differences.
The same candidate code with system 7.1.5 matches the reference exactly. Disabling
7.1.5 CPU optimizations does not eliminate the cross-build differences. These
results do not establish the behavior of the pinned Windows 7.1.1 executable.
No Windows run is implied by this candidate alone.


## Same-runtime code oracle candidate

Both accepted and candidate renderers now run in separate fresh Python children,
using the same reference-only FFmpeg/ffprobe executables and pinned caption
bootstrap. Accepted imports remain strictly ZIP-owned; candidate imports must
belong to the current checkout. The parent process environment is untouched.
Candidate receipts record tool bytes and explicitly identify this as a code
oracle on FFmpeg 7, not production FFmpeg 9 coverage. Matrix comparisons require
identical runtime receipts as well as the unchanged exact YUV/PCM assertions.

Synthetic input construction and the real capture test still use the production
parent tools. Existing affine, compositor pixel, export and cache integration
tests continue using production FFmpeg 9; none are globally redirected to 7.
This separates application-code regression from runtime-version regression.
It does not establish output equivalence between FFmpeg versions. Separate
production range/full tests exercise the current compiler under its actual runtime;
they do not pretend to execute the frozen v1 compositor under FFmpeg 9.
The recorded cross-version differences remain known, unresolved evidence.

For Windows, provision the two tools separately: normal provision_ffmpeg.py for
production and provision_frozen_reference.py for the oracle. The latter writes
only LUMEN_REFERENCE_* variables. Both Windows workflows provision the reference separately and retain its receipt.
CI triggers are unchanged. No hosted run is implied. macOS retains the existing
production provisioner's pinned ffmpeg@7 version/revision/bottle and uses those
same tools for both code oracles; transitive Homebrew dependencies are recorded,
not fully pinned. Native platform behavior still requires validation.

To reproduce the separate cross-version experiment, explicitly select production
FFmpeg 9 in the parent and reference FFmpeg 7 in LUMEN_REFERENCE_*, then compare
`_candidate_range_av_in_process(project, start, end, directory)` against
`expected_range(project, frozen_full_av(project, full_directory), start, end)`.
Use exact tuple equality, with no tolerance. The underscore helper intentionally
retains the original render body and parent runtime; the public
`candidate_range_av` is now exclusively the common-runtime code oracle.
This diagnostic is expected to retain the previously recorded cross-version
failures; it is not reclassified as a passing regression test.


## Production-runtime integration coverage

`production_range_oracle.py` compiles the current complete graph without a
frame_window, using a fresh RenderEngine and the production tool selection.
Only after complete raw YUV/PCM output exists is it sliced for comparison with
another fresh range renderer. Twelve corresponding scene/rate cases cover four
windows each; five edge cases cover legacy rate/VFR phase, outside-range scalar
selection, motion, and empty-picture caption/sticker clocks. Two real-media
sensitivity tests first pass the unmodified comparison, then reject one changed
picture or audio byte. No tolerance or reference-runtime substitution is used.
Windows requires production FFmpeg/ffprobe 9 in the runtime receipt. This suite
shares the current compiler and complements, rather than replaces, frozen-code
regression. Local minimal FFmpeg 9 lacks drawtext/prores_ks; their failures stay
explicit until the full-featured Windows run validates those paths.

The reference ZIP contains GPLv3 LICENSE (35,147 bytes), SHA-256
`8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903`.
The provisioner verifies and preserves those exact license bytes and records the
member/hash in its receipt. The runtime remains external CI infrastructure, not
part of the shipped application bundle.
