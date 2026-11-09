# Exact source-audio windows · 精确源音频窗口缓存

## What changed / 本轮变化

A short source selection is no longer rejected solely because AAC, MP3, FLAC or embedded video audio ends more than 30 seconds after its audio origin. The source viewer prepares exact **48 kHz stereo float32 PCM** from the stream origin, discards the prefix while streaming, and retains only requested sample intervals in session-owned temporary storage. No input seek (`-ss`, even zero), reset decoder history, estimated waveform or whole-source proxy is substituted.

短选区不再因为位于压缩音频原点 30 秒之后而被直接拒绝。首次准备仍要从音频原点顺序解码，前缀随读随丢，只保留请求的精确 float32 样本窗口；这不是常数时间随机定位，也不是项目代理媒体。窗口关闭后清除，预览和导出继续读取原始素材，schema37、`render.py`、`audio_timing.py` 与既有工程渲染语义不变。

The source normalization is deliberately identical to the unchanged audio compiler:

```text
asetpts=PTS-STARTPTS,
aresample=48000:async=1:first_pts=0,
aformat=sample_fmts=fltp:channel_layouts=stereo
```

Only after this normalization are absolute sample intervals selected. The cache holds interleaved `pcm_f32le`, without int16 quantization. Source-only audition still uses the existing int16/AAC playback proxy; its lossy output never enters an edit or export. The existing rational frame/sample boundaries and refusal of non-preserving fractional-rate cuts remain intact.

## Apply now verifies embedded audio too

Selected **and affected** video clips with audio receive the same real PCM completeness proof as audio-track sources, as well as the existing CFR/native-frame-hash proof. This closes the previous picture-track gap where compressed audio passed only a metadata endpoint guard.

A shorter embedded audio stream can contribute absent silence in a picture-track edit or audition **only after a clean, actually observed normalized decoder EOF**. Container/stream duration is a completeness claim, not EOF: copied AAC can produce real audible samples after its declared end, and those samples are retained, proven and auditioned exactly like the unchanged renderer. Every claimed sample must actually decode; EOF before the declared sample claim, corrupt output or non-finite selected audio rejects the entire operation. Audio-track overwrite still refuses a missing tail rather than padding it into plausible evidence.

Warm cache hits do not bypass checks: every Apply freshly inspects all selected/affected source identities and containers, validates cached-file hashes and revalidates sources after proof. The PCM and observed-EOF cache keys include normalization version, stream index, source identity and resolved FFmpeg executable state. EOF evidence has its own 32-entry LRU, is purged on source invalidation and viewer close, and cannot bypass fresh Apply checks even when no PCM bytes were retained. Generated media retains the recorded full-provenance check. Ordinary source changes/relinks reject stale proposals; portable sampled file identity is not an adversarial full-storage lock.

## Hard limits / 明确边界

| Resource | Limit |
|---|---|
| Selected plus affected merged proof ranges | **30 seconds combined**, unchanged |
| One audition | **30 seconds**, unchanged |
| Whole operation | **120 seconds maximum**, including lock wait, identity, probe, audio and video proof |
| Cold audio scan | **86,400,000 normalized samples**: 30 minutes cumulative across all cold source scans in one operation |
| Retained PCM + audition LRUs + pending files | **48 MiB combined** |
| Audition output | 24 MiB; at most four cached audition files |
| Sparse PCM windows | At most 32 merged disjoint windows per source; at most 32 PCM and 32 observed-EOF cache entries |
| Concurrency | One worker operation per viewer, with cancellation/deadline child reaping |

The scan budget is the **sum of from-origin endpoints of missing source windows**, not 30 minutes of selected material and not 30 minutes per source. A cached containing window needs no new origin decode, but retains source identity/probe/hash overhead. Non-overlapping new windows may require another cold origin scan. Gaps inside a multi-window scan are discarded, not cached. A first request beyond a suspected short-stream tail still budgets a conservative origin scan to its requested endpoint; metadata alone cannot bypass the cap. Once actual EOF is proved, later wholly absent windows need no new decoder. Resource limits reject before acceptance; no degraded, shifted or silently padded substitute is supplied.

中英文界面说明冷扫描与已缓存窗口的差异，显示扫描秒数并保留取消入口。取消会清除尚未完成的窗口文件并回收子进程；已完成且验证过的缓存可留到本次查看器关闭。缓存失效、损坏或淘汰后重新准备，不能用旧缓存绕过素材变更。关闭、重新链接、选择变化、锁轨、撤销/重做及新请求均拒绝旧进度/完成回调。

## Measured preparation costs (not playback benchmarks)

Synthetic 44.1 kHz mono AAC, one-second **audio-track Apply proof**, Linux/FFmpeg 7.1.5, three runs per endpoint. Each cold run uses a new viewer backend; OS file caching was **not** cleared. Both cold and warm timings include fresh identity/probe work. These are not native-device, arbitrary-codec or competitor performance claims.

| Requested endpoint | Cold median | Same-session warm median | Retained PCM |
|---|---:|---:|---:|
| 10 s | 0.145 s | 0.076 s | 384,000 bytes |
| 60 s | 0.207 s | 0.069 s | 384,000 bytes |
| 300 s | 0.451 s | 0.067 s | 384,000 bytes |

The 300-second cold scan processes 14,400,000 normalized samples while retaining one second. Cold cost grows with the origin-to-endpoint scan; warm reuse avoids decoding, not all verification. Full video CFR/hash proof and proxy encoding have additional costs and were deliberately excluded from these audio-only measurements. Reproducer/raw runs are in the matching `cutcat-exact-audio-cache-evidence/performance.json` and `measure_cache.py`.

## Comparison-driven next work / 继续对照补齐

Official documentation checked 2026-10-25; this is a workflow comparison, not hands-on performance testing or a paid-tier entitlement claim.

- **Premiere:** source In/Out, per-track source patching and separate Insert/Overwrite are documented. CutCat supports bounded late-audio source-window proof and now has a separate [single-track Insert candidate](SOURCE_MARKS_INSERT.md), but still lacks linked A/V patching and cross-track synchronization. [Adobe source patching](https://helpx.adobe.com/premiere/desktop/edit-projects/intro-to-editing/add-media-to-the-timeline-using-source-patching.html)
- **Final Cut Pro:** overwrite supports browser ranges, destination ranges/playhead and backward alignment. CutCat still offers source In + Out + captured destination start only, with conservative compatibility rejection. [Apple overwrite editing](https://support.apple.com/en-euro/guide/final-cut-pro/ver4e30026/mac)
- **DaVinci Resolve:** the Edit page documents a broader source-to-timeline editing toolset. This cache removes a specific early-source restriction; it does not establish Resolve, Premiere or Final Cut parity. [Blackmagic Edit page](https://www.blackmagicdesign.com/products/davinciresolve/edit)

The next source-editing candidate is [bounded single-track Insert](SOURCE_MARKS_INSERT.md), separately validated without changing this cache contract. Further priorities remain separately versioned mixed-rate/VFR timing (without changing old project pixels), linked A/V source patching, and broader relationship-safe source edit combinations. General realtime preview, persistent optimized/proxy media, multicam, collaborative workflows and native release validation remain separate work. No feature strip, automatic ungroup/unlock or renderer rewrite is hidden in this round.

## Verification and delivery boundaries

New tests compare every f32 byte against an independently constructed from-origin oracle for AAC/MP3/FLAC, 22.05/44.1/96 kHz resampling, late windows, offsets and nonzero stream starts. The actual unchanged full renderer is run before and after overwrite; retained PCM must remain byte-identical and inserted PCM must equal independent source samples. The independent review identified a copied-AAC tail that survived past its declared duration; that reproduced defect is preserved and corrected with actual EOF proof. New stream-copy cases cover 22.05/44.1/96 kHz, offsets/nonzero starts, selected and affected real renderer tails, zero-retained-byte warm silence, stale identities and EOF lifecycle/caps. Separate tests cover embedded short/missing audio, cancellation, decoder reaping, cache corruption, executable/source replacement, aggregate scan/storage/count caps and stale UI callbacks.

Only the three previous tests whose sole assertion was the intentionally replaced first-30-second origin rejection were narrowly updated. Their original file and exact diff are preserved in evidence; unrelated historical tests and renderer/audio compiler bytes are unchanged. Final acceptance requires matching frozen-source aggregate, independent review and extraction receipts. Focused passes alone are not full acceptance. No remote push, hosted CI, dependency changes, public repository, uploaded user footage or new Windows/macOS package is included.
