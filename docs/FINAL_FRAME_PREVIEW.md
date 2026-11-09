# Final composite still preview / 最终合成静帧预览

## Candidate and accepted baseline / 候选与已接受基线

This is a **local candidate**, based on locally accepted schema41 source
`2c70ebb1e31733ee4d95fe20a43152ae0d38f5df` (clip volume keyframes). It does not
change the schema, audio, export, full-preview renderer or dependencies. Focused
checks, exact-pixel oracle, UI captures and performance measurements are separate
from the final frozen-source aggregate/extraction gates. Remote/main/CI and
Windows/macOS package acceptance remain separate; none is implied here.

本轮解决“改完画面必须等待整段编码才能看最终合成结果”的一部分等待：只检查暂停播放头的一帧。
不是实时播放器、代理生成／切换、GPU 管线、HDR 显示管理，也不宣称付费编辑器等价。

## Use / 使用

Open **Final composite still preview** from the viewer or Playback menu. Opening
does not render. Pause, seek, then choose **Refresh final frame**. The separate
window is labelled **Final composite still · Rec.709 SDR · No audio**. Its image
includes final effects, geometry, layers, stickers and captions for the supported
frame. It does not replace a valid playback cache or supply pixels to scopes,
color-reference tools or selected-clip color pickers.

Optional **Automatically refresh while paused** debounces changes. Only one job
runs and one newest request is retained. Cancel prevents automatic retry until
another edit or explicit refresh. Close and playback start cancel pending work.
**Build full timeline preview** remains the explicit alternative for sound,
playback and unsupported cases; it is not silently run by the still tool.

## Supported contract / 有界支持

- Original even-sized SDR canvas, at most **1920 × 1080**. No reduction before
  source normalization, spatial grade, blur or geometry.
- At most the existing two active video tracks, in the renderer's track/start/ID
  traversal order; uncovered timeline intervals are black.
- Existing Rec.709, sRGB and linear-Rec.709 interpretation, full float grading,
  LUT and qualifier/window animation, including the existing restoration ingress.
- Active sources must pass the existing source viewer's self-contained container
  allowlist in a bounded worker probe, including on revalidation. Concat/HLS/DASH,
  image sequences and external-reference formats reject: merely checking the
  selected manifest's timestamps would not cover changed child media.
- Static/animated position, scale and opacity; existing static rotation/mirrors
  and transparent transformed exterior. Source images carrying alpha remain
  explicitly unsupported by this ingress; clip-generated alpha is supported.
- Existing ordered blur/vignette; static PNG or explicit animated PNG packs;
  frame-accurate captions after stickers. Original local fonts are required.
- Active stabilization, an active cross-dissolve interval, Apple Log/HDR/unknown
  source combinations, excessive canvas or invalid dependencies reject with an
  instruction to use the existing full preview. Settings are never cleared or
  effects silently omitted. Unsupported components outside this frame are not
  rendered; project structure still validates.
- No audio file decode, waveform, mix, encoder, gain-envelope evaluation or
  playback-cache mutation. Audio-only spans show a silent black picture.

## Exact picture boundary / 画面与时间合同

1. One-frame ingress preserves the production legacy trim/timestamp phase or
   exact rational `floor(source_in + local_frame * speed)` mapping. A decoder
   may traverse earlier source frames. This is **not** a random-access guarantee.
2. Grade/window evaluation uses output clip-local time. The full native canvas
   stays float32 until the same clamp/round RGB16 boundary as `grade_segment`.
3. A single planar GBR16 intermediate per active clip enters the frozen
   H.264/preview composition branches: effect order, static/animated geometry,
   alpha and track composition. Trusted motion `N` expressions evaluate at the
   original clip-local frame, rather than restarting at zero.
4. Sticker evaluation uses the original global frame and pack-local animation
   frame. Captions use original half-open global frame gates, after stickers.
5. Authoritative comparison bytes are the renderer's final **limited-range
   Rec.709 YUV420p before H.264 encoding**. Only then is a separate RGB888 display
   conversion made. The tool is not a ProRes 10-bit final-frame reference.

The frozen renderer's global scalar-CPU blur flag is preserved by a metadata-only
scan, even if its triggering clip is elsewhere. Otherwise SIMD conversion
rounding can differ at a sticker in an empty frame. This scan does not decode,
grade or composite that tail clip. The original renderer file remains unchanged.

## Freshness, cancellation and resource bounds / 新鲜度与资源

Capture and subsequent dependency verification run on registered background
workers. Pure in-memory context, object/session identity, revisions and explicit
seek/play generations reject obsolete successes **and errors**, including ABA
changes. Source stat identities use the existing ingress contract; active LUTs,
sticker manifest and every PNG in an active pack are content-verified. Fonts are
resolved off the GUI thread. This path bounds font discovery to 2048 directories,
20,000 visited entries and 4096 candidate fonts, individual fonts to 64 MiB and
collections to 64 faces; excess rejects rather than silently substitutes.
Only uncompressed SFNT/TTC with bounded, in-file tables and character/name-map
expansion is admitted; suffix-spoofed WOFF or unsupported maps require full
preview. These checked limits and measured RSS are not a certification of every
third-party font parser or operating-system call.
Caption helpers accept optional checks; their default full-render behavior is
unchanged. Displayed frames are periodically rechecked by a
worker; external mutations are discovered at verification boundaries, not by an
instantaneous filesystem lock or real-time OS guarantee.

Only two active graded frames and one sticker canvas are needed. Request-owned
temporary files are removed on success, error, cancellation and timeout. There
is no full MP4, complete graded clip cache or unrelated-tail render. `work`
reports emitted grade/sticker/composite counts and raw intermediate bytes;
decoder traversal before the target is explicitly not counted as random access.
The existing synthetic FFmpeg capability probes remain additional constant
startup work (including tiny multi-frame probe patterns); counters describe
project pictures, not those independent capability tests. Source geometry and
picture ingress each request the target, so a normal active clip has two
target-emitting decode invocations; restoration can add its origin-geometry
check. Observed subprocess traces accompany performance evidence.
The cooperative operation deadline is at most 120 seconds, checked between
bounded processing/IO steps and during subprocess polling. It cannot preempt an
individual operating-system file call or bounded font-library call; no leaking
timeout thread is created to pretend otherwise. Normal source/asset/LUT
limits still apply. Large, slow or unsupported inputs may require full preview.

## Independent acceptance / 独立验收

`tests/test_final_frame_preview_oracle.py` launches the **frozen baseline and its
dependencies in a separate subprocess**, renders its complete final YUV420p
stream, and compares each requested target byte-for-byte. Rates are 24, 25,
24000/1001 and 30000/1001; fixtures include numbered frames, source/grade clocks,
alpha and motion, effects, LUT/window animation, caption boundaries, static and
animated stickers and gaps. No H.264 tolerance is used to hide a mismatch.

Final frozen focused groups passed separately: **41 oracle tests** (331 target
pixel comparisons, no skips), **60 source-contract/independent-review tests**,
and **142 UI/existing-UI regression tests**. These are scoped groups, not a full
repository aggregate or fresh-extraction acceptance claim. Earlier partial,
failed and superseded runs remain identified in the evidence rather than being
counted as final passes.

`scripts/benchmark_final_frame_preview.py` measures three cold process runs for
the same target/source in 30/300-frame scenes. Reports must name resolution,
time-to-frame, process-tree RSS, temporary storage, cancellation and GUI heartbeat
separately. The target is at least 50% lower median wait than the whole 300-frame
preview and no linear picture-work increase from unrelated tail length.

### Measured candidate V2 result (2026-10-26, Linux)

After the self-contained-source preflight fix, three fresh Python processes and
fresh renderer caches per cell, **320 × 180,
24 fps**, sustained two-track composition, one shared 303-frame source and
identical target frame 5 (source frames 5/4). OS page caches were **not** forcibly
dropped. Times are process launch to returned target bytes, not GUI display or
realtime playback. All six still outputs exactly matched frozen pre-encoding
pixels; the old full preview includes its real MP4/audio preparation and target
decode, not an artificially shortened render.

| Path | 30-frame median | 300-frame median | Maximum observed tree RSS | Maximum observed temporary footprint |
|---|---:|---:|---:|---:|
| Final composite still | 3.497 s | 3.488 s | 117.99 MiB | 1.86 MiB |
| Existing full preview → target | 8.118 s | 57.813 s | 162.72 MiB | 25.32 MiB |

The 300-frame wait was **93.97% lower**, meeting the 50% target. The still
300/30-time ratio was **0.9976**, not proportional to the tenfold tail. Independent
tracing saw exactly two project-sized grade calls, target-only geometry/picture
ingress and one project sticker/final composition, with no MP4/MKV still cache.
Extra synthetic capability probes remain included in these measured times.

A separate **1920 × 1080** still took **13.483 s**, with **368.16 MiB** sampled
peak process-tree RSS and **41.27 MiB** temporary footprint. This meets the
768 MiB measured target; it is **not** a 1080p full-300-frame speed comparison.
Preparation cancellation returned 0.050 s after its request on this fixture;
this is not a universal hard cancellation bound. RSS and named/anonymous temp
files were sampled every 25 ms and can miss shorter peaks. GUI display/heartbeat,
active-stage cancellation and final-source acceptance have separate receipts.

Actual offscreen GUI rendering plus worker revalidation at 320 × 180 displayed
the verified frame in **3.475–3.997 s** (median **3.684 s**). A 16 ms heartbeat
timer's largest observed gap was **71.91 ms**; early 1080p cancellation reached
idle in **35–53 ms**. These are observations, not hard responsiveness bounds or
a complete 1080p GUI latency test. EN/中文 captures at scale factors 1, 1.5 and 2
show the whole canvas and action footer at the tested narrow sizes. Overflowing
help remains scrollable instead of hiding the picture or controls.

The speed fixture uses DejaVu Latin captions. A real Noto Sans CJK SC TTC Chinese
caption also passed the exact-pixel oracle, but its font path was materially
slower; the above timings must not be generalized to all fonts. Its combined
full-oracle-plus-capture test duration is not a standalone preview benchmark.

Exact test counts, actual measurements and selected EN/ZH/narrow/high-DPI
captures are recorded with the candidate evidence. Linux offscreen evidence
does not certify native Windows/macOS behavior. Aggregate/extraction acceptance
must match the final frozen candidate, not this accepted baseline.

## Official comparison and remaining gaps / 官方对照与后续缺口

Checked 2026-10-26; these are workflow references, not competitor benchmarks or
claims that every listed feature is paid-only:

- [Premiere ingest/proxy workflow](https://helpx.adobe.com/premiere/desktop/organize-media/ingest-proxy-workflow/ingest-and-proxy-workflow.html)
  covers creation, attachment and reconnection. A CutCat still is not that workflow.
- [Final Cut Pro playback quality](https://support.apple.com/en-gb/guide/final-cut-pro/ver2fd7a8b94/mac)
  distinguishes full/reduced resolution and original/optimized/proxy playback.
  This round keeps full spatial processing but has no continuous playback engine.
- [Resolve Color](https://www.blackmagicdesign.com/products/davinciresolve/color)
  describes wide-gamut/HDR and 32-bit processing. Our SDR still must not be
  presented as professional HDR/color-managed monitoring.
- [剪映国内官网](https://www.jianying.com/) lists masks, keyframes, color tools,
  multicam and multiple timelines. Its China product is not the international
  CapCut entitlement matrix; this still preview does not close those other gaps.

下一步仍应按真实瓶颈比较：连续交互播放／代理契约、关系安全 A/V 链接编辑、专业色彩管理。
继续保留已有源查看、帧导航、整段播放缓存、音量关键帧及有界编辑能力，不能将它们重新写成“尚未实现”。
