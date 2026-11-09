# Window keyframe timing editor / 窗口关键帧时间编辑

## Scope

The visual strip edits the existing single qualifier window's clip-local integer timeline frames. It is a timing UI, not a new animation engine. Existing center/size normalization, Hold/Linear evaluation, fractional source timing, trim/split rules and schema 30 remain unchanged. No automatic tracking, curves/easing, rotation keys or general color-keyframe tracks are added.

The dialog is staged. Navigation uses a local reference frame and does not move the main project playhead. Frame decoding remains explicit. All accepted edits are applied to the selected clip in one existing Apply transaction; Cancel discards them. An unchanged Apply does not create a new undo item.

## Interaction and safety

- Visible keys can be selected and moved along a visual timing strip. Time positions snap to integer frames within 0 through clip duration minus one.
- An exact frame control remains available for long clips and closely spaced keys.
- Moving onto an occupied frame is rejected, preserving both keys. Explicit Add / replace remains the existing geometry replacement action.
- Out-of-clip keys remain stored and are labeled read-only, rather than clamped or silently removed. These keys can still affect interpolation within the visible clip.
- An interrupted drag is not a project edit. Escape, lost focus, stale context and dialog dismissal cancel the active gesture.
- Existing Previous/Next, Add / replace, Remove, and Make current geometry static retain their meanings. The static action intentionally removes all keys, including hidden keys, as its tooltip explains.
- Hold and Linear retain CutCat's current outgoing-key semantics. These labels do not claim equivalence to Resolve's static/dynamic modes.

## Comparison checked 2026-10-24

Blackmagic's official Color page describes shape-based Power Windows, on-screen positioning and sizing, and automatic tracking. Its Edit page describes diamond keyframe controls and visual timing/curve editors. These establish a useful workflow reference, not algorithmic or performance equivalence.

The prior CutCat window-animation dialog exposed a local frame slider and numeric geometry controls but no visual key timing strip. This iteration narrows that usability gap: visible positions, direct timing moves, exact frame edits and explicit collision handling, without changing the renderer. Resolve still has substantially broader grading, tracking, curve editing and real-time facilities.

Sources:
- https://www.blackmagicdesign.com/products/davinciresolve/color
- https://www.blackmagicdesign.com/products/davinciresolve/edit

## 中文说明

本轮只增强已有单个调色窗口的关键帧时间编辑：可视时间条、选择和整帧拖动、精确帧号修改，以及重复帧冲突拒绝。片段外的关键帧保留并标明，不会自动裁掉或挤到边界。

这里的参考帧不是主时间线播放头；切换位置不自动解码、不修改工程。全部操作暂存，点击应用后才形成一次已有撤销事务；取消不保存。保持／线性插值、保存格式 schema30、裁切／分割语义和渲染路径均不改变。不是自动跟踪、贝塞尔曲线、实时 GPU 或达芬奇功能对等。

## Validation boundary

Local Qt offscreen tests and synthetic-media checks do not establish native Windows/macOS packaging, signing, hardware acceleration, native accessibility or real footage compatibility. Current test counts, exclusions and archive hashes are reported by the matching frozen validation receipts, not by historical delivery text.
