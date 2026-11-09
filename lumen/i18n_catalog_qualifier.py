"""Explicit bilingual labels for one offline encoded H/S/Y′ range qualifier."""
ZH_CN = {
'H/S/Y′ qualifier · static frame': 'H/S/Y′ 限定器 · 静态画面',
'H/S/Y′ qualifier…': 'H/S/Y′ 限定器…',
'Static qualifier frame': '限定器静态画面',
'Use the preview selector to view range matte, source or adjusted pixels.': '通过预览选项查看范围蒙版、源画面或调整后像素。',
'Selected clip only · after input normalization, spatial restoration, primary, RGB and hue curves; before qualifier and LUT.\nFrozen RGB16 decode → float32 processing; display is clipped 8-bit. Not the composited preview.': '仅选中片段 · 输入归一化、空间画质修复、基础调色、RGB 与色相曲线之后；限定器与 LUT 之前。\n冻结的 RGB16 解码 → float32 处理；显示为截断后的 8 位画面。并非合成预览。',
'Ranges use encoded Rec.709 HSV saturation and Y′ luma, not HSL lightness. One color-range layer; no spatial mask, tracking or temporal smoothing. Entirely offline.': '范围采用编码域 Rec.709 的 HSV 饱和度与 Y′ 亮度，并非 HSL 明度。仅一个颜色范围层；不含空间蒙版、跟踪或时域平滑。完全离线。',
'Frame is stale. Close and reopen the qualifier for the current clip and playhead.': '画面已过期。请关闭并重新打开，为当前片段与播放头载入限定器。',
'Static preview': '静态预览',
'Range matte (even when correction disabled)': '范围蒙版（校正关闭时仍显示）',
'Source before qualifier': '限定器之前的源画面',
'Adjusted before LUT': '调整后画面（LUT 之前）',
'Matte: white selects, black excludes, gray feathers. Fitted-canvas padding can qualify; this is not a spatial mask.': '蒙版：白色选中、黑色排除、灰色羽化。适配画布的填充边缘也可能被选中；这不是空间蒙版。',
'Enable qualifier correction': '启用限定器校正',
'Color ranges and feather': '颜色范围与羽化',
'Adjust selected color': '调整选中颜色',
'Hue center (degrees)': '色相中心（度）',
'Hue half-width (degrees)': '色相半宽（度）',
'Hue feather (degrees)': '色相羽化（度）',
'HSV saturation minimum (%)': 'HSV 饱和度下限（%）',
'HSV saturation maximum (%)': 'HSV 饱和度上限（%）',
'HSV saturation feather (%)': 'HSV 饱和度羽化（%）',
'Y′ luma minimum (%)': 'Y′ 亮度下限（%）',
'Y′ luma maximum (%)': 'Y′ 亮度上限（%）',
'Y′ luma feather (%)': 'Y′ 亮度羽化（%）',
'Encoded exposure (stops)': '编码域曝光（档）',
'Luma-relative saturation (%)': '相对亮度的饱和度（%）',
'Hue rotation (degrees)': '色相旋转（度）',
'Exposure scales encoded code values, not scene-linear light. Saturation scales deviation from Rec.709 Y′.': '曝光缩放编码值，而非场景线性光。饱和度缩放颜色相对 Rec.709 Y′ 的偏离。',
'No project changes until Apply.': '点击应用前不会改变项目。',
'Qualifier status': '限定器状态',
'Apply qualifier': '应用限定器',
'Each minimum must be less than or equal to its maximum.': '各范围下限必须小于或等于上限。',
'Cannot use a frame from a different color stage. Close and reopen the qualifier.': '无法使用其他调色阶段的画面。请关闭并重新打开限定器。',
'{stage}\n{restoration}': '{stage}\n{restoration}',
'Clip grading is disabled. Settings can be saved, but the adjusted view and render bypass the correction.': '片段调色已关闭。可以保存设置，但调整后预览和渲染均不应用校正。',
'Correction is disabled. The matte still shows the selected range. No project changes until Apply.': '校正已关闭。蒙版仍显示选中范围。点击应用前不会改变项目。',
'Reading qualifier frame…': '正在读取限定器画面…',
}
EN = {key: key for key in ZH_CN}
ERROR_ZH_CN = dict(ZH_CN)
ZH_CN.update({
'Display samples {width}×{height} from {source_width}×{source_height}; export evaluates every pixel.': '显示从 {source_width}×{source_height} 采样的 {width}×{height} 像素；导出逐像素计算。',
'Feather extends outside each range; the three weights multiply. A 180° hue half-width includes gray. Narrower hue ranges exclude gray; out-of-range RGB is always excluded.': '羽化向各范围外侧延伸，三个权重相乘。色相半宽为 180° 时包含灰色；更窄的色相范围排除灰色。超出 RGB 范围的像素始终排除。',
'Hue / saturation / luma qualifier…': '色相／饱和度／亮度限定器…',
'FIXED CORRECTION ORDER\n709 decode → linear exposure → RGB lift/gain, gamma → 709 encode → legacy gain / contrast / saturation / balance / tones → master + RGB curves → hue / saturation curves → joint qualifier → LUT (bounded domain) → clip / quantize\nSDR Rec.709 only · no HDR / OCIO management': '固定校正顺序\n709 解码 → 线性曝光 → RGB 提升／增益、伽马 → 709 编码 → 传统增益／对比度／饱和度／色彩平衡／影调 → 主曲线与 RGB 曲线 → 色相／饱和度曲线 → 联合限定器 → LUT（有限定义域）→ 截断／量化\n仅支持 SDR Rec.709 · 不含 HDR／OCIO 色彩管理',
})
EN = {key: key for key in ZH_CN}
ERROR_ZH_CN = dict(ZH_CN)

# Persisted-model and float-pipeline failures use the standard error renderer.
ERROR_ZH_CN = {
    'Version 18 clips require qualifier settings': '版本 18 的片段必须包含限定器设置',
    'Legacy projects cannot contain qualifier settings': '旧版本项目不能包含限定器设置',
    'Qualifier requires exactly its documented settings fields': '限定器必须包含完整且仅限已定义的设置字段',
    'Invalid qualifier settings': '限定器设置无效',
    'Qualifier enabled must be a boolean': '限定器启用状态必须为布尔值',
    'Qualifier {name} must be finite and in {low}..{high}': '限定器参数 {name} 必须为 {low} 至 {high} 之间的有限数值',
    'Qualifier {name}_low must not exceed {name}_high': '限定器参数 {name} 的下限不能超过上限',
    'Unknown color sampling stage.': '未知的颜色取样阶段。',
}
ZH_CN.update(ERROR_ZH_CN)

# Click-to-replace sampling is deliberately distinct from additive qualification.
ZH_CN.update({
'Replace ranges from source click': '单击源画面替换范围',
'Pick replaces H ±10°, S/Y′ ±10 percentage points. Feather, adjustments and enable state stay unchanged.': '取样将替换为 H ±10°、S/Y′ ±10 个百分点。保留羽化、校正参数与启用状态。',
'Click a displayed source sample. This replaces all three ranges, not an additive selection.': '请单击显示的源取样点。将替换三个范围，并非追加选区。',
'Pick inside the active picture, not fitted-canvas padding.': '请在有效画面内取样，不要选择适配画布的填充边缘。',
'Source ({x}, {y}) · RGB {rgb} · H {h}° / S {s}% / Y′ {yprime}%\nRanges replaced; the matte shows the pending selection. No project changes until Apply.': '源坐标（{x}，{y}）· RGB {rgb} · H {h}° / S {s}% / Y′ {yprime}%\n已替换范围；蒙版显示待应用的选区。点击应用前不改变项目。',
})
EN.update({key: key for key in ZH_CN})
ERROR_ZH_CN.update({
'Qualifier sample requires a float32 RGB triplet': '限定器取样需要 float32 格式的 RGB 三通道值',
'Qualifier sample color values must be finite': '限定器取样颜色值必须为有限数值',
'Qualifier sample RGB must be within 0..1': '限定器取样 RGB 必须在 0 至 1 范围内',
'Qualifier sample has ambiguous hue (gray or near-gray)': '灰色或近灰色取样的色相不明确，请选择有彩色区域',
})
ZH_CN.update(ERROR_ZH_CN)
EN.update({key: key for key in ZH_CN})

# Detail sampling keeps every local fitted-canvas source pixel, not a proxy.
ZH_CN.update({
'Overview (sparse)': '全图（稀疏取样）',
'Detail · 512 px': '细节 · 512 像素',
'Detail · 256 px': '细节 · 256 像素',
'Detail · 128 px': '细节 · 128 像素',
'Detail · 64 px': '细节 · 64 像素',
'Source detail zoom': '源画面细节缩放',
'Detail center X': '细节中心 X 坐标',
'Detail center Y': '细节中心 Y 坐标',
'Double-click to inspect detail. Scroll to zoom; middle-drag to pan. X/Y are fitted-canvas pixel coordinates.': '双击查看细节；滚轮缩放，中键拖动平移。X/Y 为适配画布的像素坐标。',
'Overview: {width}×{height} samples, step {step}, from {source_width}×{source_height}. Small details may be missed; use Detail. Export evaluates every pixel.': '全图：从 {source_width}×{source_height} 每隔 {step} 像素取样，显示 {width}×{height}。细小区域可能遗漏，请切换细节模式。导出逐像素计算。',
'Detail: every pixel in {width}×{height} crop at ({x}, {y}) of {source_width}×{source_height} fitted canvas. Matte uses full-resolution local pixels; export covers the whole frame.': '细节：{source_width}×{source_height} 适配画布中，从（{x}，{y}）起的 {width}×{height} 裁切，逐像素取样。蒙版按局部全精度像素计算；导出覆盖整帧。',
})
EN.update({key: key for key in ZH_CN})

# Contextual tint is inspection only, never a source color or saved grade.
ZH_CN.update({
'Selection overlay · magenta 50%': '选区叠色 · 品红 50%',
'Overlay: magenta at 50% × range weight over clipped source. Excluded pixels retain source color. Display only; sampling always reads underlying float source.': '叠色：在截幅源画面上以 50% × 选区权重叠加品红；未选区域保留源色。仅供查看，取样始终读取底层浮点源色。',
'Click through the magenta overlay to sample underlying float source, not tint. Replaces all three ranges.': '点击穿透品红叠色，读取底层浮点源色而非叠加颜色。将替换全部三个范围。',
'Use the preview selector to view range matte, source, adjusted pixels or selection overlay.': '通过预览选项查看范围蒙版、源画面、调整后像素或选区叠色。',
'Selection overlay requires an RGB viewport and matching matte, at most 512×512': '选区叠色需要不超过 512×512 的 RGB 视图和相同尺寸的蒙版',
'Selection overlay values must be finite': '选区叠色数值必须为有限数值',
'Selection overlay matte must be within 0..1': '选区叠色蒙版权重必须在 0 至 1 范围内',
})
EN.update({key: key for key in ZH_CN})

ZH_CN.update({
'Source ({x}, {y}) · RGB {rgb} · H {h}° / S {s}% / Y′ {yprime}%\nRanges replaced; overlay is display only. Sampled underlying float source, not tint.': '源坐标（{x}，{y}）· RGB {rgb} · H {h}° / S {s}% / Y′ {yprime}%\n已替换范围；叠色仅供查看。取样读取底层浮点源色，而非叠加颜色。',
})
EN.update({key: key for key in ZH_CN})
ERROR_ZH_CN.update({key: value for key, value in ZH_CN.items()
                    if key.startswith('Selection overlay ') and key != 'Selection overlay · magenta 50%'})

# One static spatial restriction, in fitted-canvas coordinates.
ZH_CN.update({
'Ranges use encoded Rec.709 HSV saturation and Y′ luma, not HSL lightness. One color-range layer multiplied by one static window; no tracking or temporal smoothing. Entirely offline.': '范围采用编码域 Rec.709 的 HSV 饱和度与 Y′ 亮度，并非 HSL 明度。一个颜色范围层与一个静态窗口相乘；不含跟踪或时域平滑。完全离线。',
'Matte: color-range weight × window weight. White selects, black excludes, gray feathers. Fitted-canvas padding can qualify.': '蒙版：颜色范围权重 × 窗口权重。白色选中、黑色排除、灰色羽化。适配画布的填充边缘也可能被选中。',
'Static spatial window': '静态空间窗口',
'Window shape': '窗口形状',
'Off (color ranges only)': '关闭（仅颜色范围）',
'Ellipse': '椭圆',
'Rectangle': '矩形',
'Invert window only': '仅反转窗口',
'Window center X (% canvas width)': '窗口中心 X（画布宽度 %）',
'Window center Y (% canvas height)': '窗口中心 Y（画布高度 %）',
'Window width (% canvas width)': '窗口宽度（画布宽度 %）',
'Window height (% canvas height)': '窗口高度（画布高度 %）',
'Window feather (% shape radius)': '窗口羽化（形状半径 %）',
'Window coordinates use the fitted project canvas before clip position, scale, rotation and mirror. Width/height are full dimensions; feather extends outside the shape by a fraction of its radius. Invert affects only the window, never the color ranges. No tracking or keyframes.': '窗口坐标以适配后的项目画布为准，位于片段位置、缩放、旋转和镜像之前。宽高为完整尺寸；羽化按形状半径的比例向外延伸。反转仅作用于窗口，不反转颜色范围。不含跟踪或关键帧。',
})
EN.update({key: key for key in ZH_CN})
ERROR_ZH_CN.update({
'Window invert must be a boolean': '窗口反转状态必须为布尔值',
'Window shape must be off, ellipse or rectangle': '窗口形状必须为关闭、椭圆或矩形',
'Spatial windows require a two-dimensional RGB image': '空间窗口需要二维 RGB 图像',
'Invalid spatial window canvas coordinates': '空间窗口画布坐标无效',
'Spatial window samples must lie inside the canvas': '空间窗口取样点必须位于画布内',
})
ZH_CN.update(ERROR_ZH_CN)
EN.update({key: key for key in ZH_CN})
ERROR_ZH_CN['Version 18 requires exactly the legacy qualifier fields'] = '版本 18 必须包含完整且仅限旧版的限定器字段'
ZH_CN.update(ERROR_ZH_CN)
EN.update({key: key for key in ZH_CN})

ZH_CN['Overlay: magenta at 50% × color-range weight × window weight over clipped source. Excluded pixels retain source color. Display only; sampling always reads underlying float source.'] = '叠色：在截幅源画面上以 50% × 颜色范围权重 × 窗口权重叠加品红；未选区域保留源色。仅供查看，取样始终读取底层浮点源色。'
EN.update({key: key for key in ZH_CN})

# Display-only handles edit the existing static window's pending geometry.
ZH_CN.update({
'Edit window handles': '编辑窗口控制点',
'Move the center or shape interior. Drag edge handles to resize around the center. Escape cancels the current drag. Handles are display only; Apply saves all pending changes.': '拖动中心或形状内部可移动窗口；拖动边缘控制点可围绕中心缩放。按 Esc 取消本次拖动。控制点仅用于显示；点击应用才保存全部待应用修改。',
'Choose an ellipse or rectangle to edit window handles.': '选择椭圆或矩形后，即可编辑窗口控制点。',
'Window handles edit only the static shape. Double-click detail navigation is paused; scroll and middle-drag still navigate.': '窗口控制点仅编辑静态形状。编辑时暂停双击进入细节；仍可滚轮缩放、中键拖动平移。',
})
EN.update({key: key for key in ZH_CN})

# Independent bounded include/exclude color selections.
ZH_CN.update({
'Sampling mode': '取样模式',
'Replace': '替换',
'Add': '添加',
'Subtract': '减去',
'Pick source': '点击源画面取样',
'Edit range': '编辑范围',
'Remove selected range': '移除所选范围',
'Clear added/subtracted ranges': '清除添加和减去的范围',
'Base inclusion': '基础包含范围',
'Include {number}': '包含范围 {number}',
'Exclude {number}': '排除范围 {number}',
'Base inclusion cannot be removed; use Replace to start a new selection.': '基础包含范围不可移除；使用“替换”开始新的选择。',
'Base inclusion is retained. Add keeps separate ranges; Subtract removes matching colors. Up to 8 of each. Select a range to edit its bounds and feather.': '保留基础包含范围。“添加”保留独立范围；“减去”排除匹配颜色。两类各最多 8 个。选择范围可编辑边界与羽化。',
'Add range from source click': '从源画面点击添加范围',
'Subtract range from source click': '从源画面点击减去范围',
'Replace resets the selection. Add/Subtract keep independent ranges. Every click samples frozen float source; adjustments and window stay unchanged.': '“替换”重置选择；“添加/减去”保留独立范围。每次点击均读取冻结的浮点源画面；校正与窗口不变。',
'Choose a mode, then arm sampling and click once. No project changes until Apply.': '选择模式，开启取样后点击一次。点击应用前不会改变项目。',
'Click once to add an independent include/exclude range from underlying float source. Overlay tint is never sampled.': '点击一次，从底层浮点源画面添加独立的包含或排除范围。不会读取叠加层颜色。',
'Ranges use encoded Rec.709 HSV saturation and Y′ luma, not HSL lightness. Independent include ranges minus exclude ranges, multiplied by one static window; no tracking or temporal smoothing. Entirely offline.': '范围采用编码域 Rec.709 的 HSV 饱和度与 Y′ 亮度，并非 HSL 明度。独立包含范围扣除排除范围后，乘以一个静态窗口；无跟踪或时域平滑。完全离线。',
'Source ({x}, {y}) · RGB {rgb} · H {h}° / S {s}% / Y′ {yprime}%\nIndependent range sampled; overlay is display only. No project changes until Apply.': '源像素（{x}, {y}）· RGB {rgb} · H {h}° / S {s}% / Y′ {yprime}%\n已取样独立范围；叠加仅用于显示。点击应用前不会改变项目。',
})
EN.update({key: key for key in ZH_CN})

ZH_CN.update({
'Include': '包含',
'Exclude': '排除',
'Source ({x}, {y}) · {mode} range sampled.\nNo project changes until Apply.': '源像素（{x}, {y}）· 已取样{mode}范围。\n点击应用前不会改变项目。',
})
EN.update({key: key for key in ZH_CN})

# Pointwise matte levels, not spatial matte cleanup.
ZH_CN.update({
'Matte levels · pointwise': '蒙版黑白点 · 逐像素',
'Matte black point (%)': '蒙版黑点（%）',
'Matte white point (%)': '蒙版白点（%）',
'Reset matte levels': '重置蒙版黑白点',
'Matte black point must be strictly below white point.': '蒙版黑点必须严格小于白点。',
'Remap the combined color matte between black and white points, then apply the window. Defaults 0% / 100% preserve the original key. Pointwise only: no spatial denoise, blur or hole filling.': '先将合并后的颜色蒙版按黑白点重映射，再应用窗口。默认 0% / 100% 保持原蒙版不变。仅逐像素处理：不含空间去噪、模糊或孔洞填补。',
'Matte: refined color-matte weight × window weight. White selects, black excludes, gray feathers. Fitted-canvas padding can qualify.': '蒙版：细化后的颜色蒙版权重 × 窗口权重。白色选中、黑色排除、灰色羽化。适配画布的填充边缘也可能被选中。',
'Overlay: magenta at 50% × refined color-matte weight × window weight over clipped source. Excluded pixels retain source color. Display only; sampling always reads underlying float source.': '叠加：以 50% × 细化后的颜色蒙版权重 × 窗口权重，将洋红色覆盖于截断后的源画面。排除像素保留源色。仅用于显示；取样始终读取底层浮点源画面。',
'Ranges use encoded Rec.709 HSV saturation and Y′ luma, not HSL lightness. Independent include ranges minus exclude ranges, then matte levels and one static window; no tracking or temporal smoothing. Entirely offline.': '范围采用编码域 Rec.709 的 HSV 饱和度与 Y′ 亮度，并非 HSL 明度。独立包含范围扣除排除范围后，应用蒙版黑白点及一个静态窗口；无跟踪或时域平滑。完全离线。',
})
EN.update({key: key for key in ZH_CN})

ERROR_ZH_CN.update({'Matte black point must be strictly below white point': '蒙版黑点必须严格小于白点'})

# Spatial matte smoothing remains explicitly separate from temporal denoise.
ZH_CN.update({
'Matte refinement': '蒙版细化',
'Matte smoothing (%)': '蒙版平滑（%）',
'Reset matte refinement': '重置蒙版细化',
'Remap black/white points, blend with a weighted 3×3 average at a fixed radius of 1 project pixel, then apply the window. Smoothing 0% is unchanged; 100% is fully averaged. This may soften edges and small details; no temporal smoothing or hole filling.': '先重映射黑白点，再以固定 1 个项目像素半径混合 3×3 加权均值，最后应用窗口。平滑 0% 保持不变，100% 完全采用加权均值。可能柔化边缘和细节；无时域平滑或孔洞填补。',
'Fixed 1 project-pixel radius, after matte levels and before the window. Uses true adjacent source pixels in both overview and detail.': '固定半径为 1 个项目像素，位于蒙版黑白点之后、窗口之前。总览与细节均使用真实相邻源像素。',
'Ranges use encoded Rec.709 HSV saturation and Y′ luma, not HSL lightness. Independent include ranges minus exclude ranges, then matte levels, local matte smoothing and one static window; no tracking or temporal smoothing. Entirely offline.': '范围采用编码域 Rec.709 的 HSV 饱和度与 Y′ 亮度，并非 HSL 明度。独立包含范围扣除排除范围后，应用蒙版黑白点、局部蒙版平滑及一个静态窗口；无跟踪或时域平滑。完全离线。',
})
EN.update({key: key for key in ZH_CN})

ERROR_ZH_CN.update({
'Matte smoothing requires a nonempty two-dimensional RGB image': '蒙版平滑需要非空的二维 RGB 图像',
'Invalid matte smoothing canvas coordinates': '蒙版平滑的画布坐标无效',
'Matte smoothing samples must lie inside the canvas': '蒙版平滑取样点必须位于画布内',
'Cropped or sparse matte smoothing requires a dense matte_source with a one-pixel halo': '裁剪或稀疏视图的蒙版平滑需要包含周围一像素邻域的完整源像素',
'Invalid dense matte_source image or origin': '完整蒙版源图像或起点坐标无效',
'Dense matte_source must lie inside the canvas': '完整蒙版源图像必须位于画布内',
'Dense matte_source must cover every sample and its one-pixel halo': '完整蒙版源图像必须覆盖每个取样点及其周围一像素邻域',
'Dense matte_source samples must match the incoming RGB image': '蒙版源图像的取样值必须与输入 RGB 图像一致',
})
ZH_CN.update(ERROR_ZH_CN)
EN.update({key: key for key in ZH_CN})


# Signed local shrink/grow is a blend strength, not a variable pixel radius.
ZH_CN.update({
'Matte shrink / grow (%)': '蒙版收缩 / 扩张（%）',
'Negative shrinks with a 3×3 minimum; positive grows with a 3×3 maximum. 0% is unchanged; −100% / +100% use the full result. Fixed square radius of 1 project pixel, after levels and before smoothing/window. Uses true adjacent source pixels; not a pixel distance.': '负值以 3×3 最小值收缩，正值以 3×3 最大值扩张。0% 保持不变；−100% / +100% 完全采用处理结果。固定方形半径为 1 个项目像素，位于黑白点之后、平滑与窗口之前。使用真实相邻源像素；数值不是像素距离。',
'Fixed 1 project-pixel radius, after matte levels and shrink/grow, before the window. Uses true adjacent source pixels in both overview and detail.': '固定半径为 1 个项目像素，位于蒙版黑白点与收缩 / 扩张之后、窗口之前。总览与细节均使用真实相邻源像素。',
'Remap black/white points, shrink/grow with a 3×3 minimum/maximum, blend with a weighted 3×3 average, then apply the window. Each spatial step has a fixed 1 project-pixel radius. Amounts are blend strengths, not pixel distances. No tracking or temporal smoothing.': '先重映射黑白点，再以 3×3 最小值 / 最大值收缩或扩张，接着混合 3×3 加权均值，最后应用窗口。每个空间步骤的固定半径均为 1 个项目像素。数值代表混合强度，并非像素距离。无跟踪或时域平滑。',
'Ranges use encoded Rec.709 HSV saturation and Y′ luma, not HSL lightness. Independent include ranges minus exclude ranges, then matte levels, local shrink/grow, local matte smoothing and one static window; no tracking or temporal smoothing. Entirely offline.': '范围采用编码域 Rec.709 的 HSV 饱和度与 Y′ 亮度，并非 HSL 明度。独立包含范围扣除排除范围后，依次应用蒙版黑白点、局部收缩 / 扩张、局部蒙版平滑及一个静态窗口；无跟踪或时域平滑。完全离线。',
})
EN.update({key: key for key in ZH_CN})

ERROR_ZH_CN.update({
'Qualifier matte_morphology must be finite and in -1..1': '限定器的蒙版收缩 / 扩张强度必须是 -1 至 1 之间的有限数值',
'Spatial matte refinement requires a nonempty two-dimensional RGB image': '空间蒙版细化需要非空的二维 RGB 图像',
'Invalid spatial matte canvas coordinates': '空间蒙版的画布坐标无效',
'Spatial matte samples must lie inside the canvas': '空间蒙版取样点必须位于画布内',
'Cropped or sparse spatial matte refinement requires a dense matte_source with its full support halo': '裁剪或稀疏视图的空间蒙版细化需要包含完整处理邻域的完整源像素',
'Dense matte_source must cover every sample and its two-pixel halo': '完整蒙版源图像必须覆盖每个取样点及其周围两像素邻域',
})
ZH_CN.update(ERROR_ZH_CN)
EN.update({key: key for key in ZH_CN})

# Source sampling changes a single selected channel feather, never its core.
ZH_CN.update({
'Soften': '柔化',
'Feather channel': '羽化通道',
'H · hue': 'H · 色相',
'HSV-S': 'HSV-S',
'Y′ · luma': 'Y′ · 亮度',
'Feather · {range} · {channel}': '羽化 · {range} · {channel}',
'Soften selected range channel from source click': '从源画面取样柔化所选范围通道',
'Sample increases only the selected channel feather to at least 50% channel weight; not combined matte weight. Core bounds stay unchanged. Inside or already sufficient: no change. Gray has no hue; narrow hue ranges still exclude gray when sampling S/Y′.': '取样仅增大所选通道羽化，使该通道权重至少为 50%，并非最终蒙版权重。核心边界不变；位于核心内或羽化已足够时不变。灰色没有色相；取样 S/Y′ 时，窄色相范围仍会排除灰色。',
'Source ({x}, {y}) · RGB {rgb} · H {h}° / S {s}% / Y′ {yprime}%\nSelected channel feather sampled; core bounds unchanged. No project changes until Apply.': '源坐标（{x}，{y}）· RGB {rgb} · H {h}° / S {s}% / Y′ {yprime}%\n已取样所选通道羽化；核心边界不变。点击应用前不会改变项目。',
'undefined': '未定义',
'Feather increased': '羽化已增大',
'Feather unchanged': '羽化未变',
'Source ({x}, {y}) · {result}\n{range} · {channel}. No project changes until Apply.': '源坐标（{x}，{y}）· {result}\n{range} · {channel}。点击应用前不会改变项目。',
})
EN.update({key: key for key in ZH_CN})
ERROR_ZH_CN.update({
'Qualifier softness channel must be hue, saturation or luma': '限定器的柔化通道必须是色相、饱和度或亮度',
'Qualifier softness target must identify a valid range': '限定器的柔化目标必须指定有效范围',
'Qualifier base range index must be zero': '限定器的基础范围索引必须为零',
'Qualifier softness target range no longer exists': '限定器的柔化目标范围已不存在',
'Qualifier softness sample must be inside visible source content': '限定器的柔化取样必须位于可见源画面内',
'Qualifier hue softness would exceed 0.5; choose a closer sample or adjust the core range': '色相羽化将超过 180°；请选择更近的取样点或调整核心范围',
'Qualifier saturation softness would exceed 1; choose a closer sample or adjust the core range': '饱和度羽化将超过 100%；请选择更近的取样点或调整核心范围',
'Qualifier luma softness would exceed 1; choose a closer sample or adjust the core range': '亮度羽化将超过 100%；请选择更近的取样点或调整核心范围',
})
ZH_CN.update(ERROR_ZH_CN)
EN.update({key: key for key in ZH_CN})

# Independent exterior feathers preserve legacy symmetric values until edited.
ZH_CN.update({
'HSV saturation low feather (%)': 'HSV 饱和度低端羽化（%）',
'HSV saturation high feather (%)': 'HSV 饱和度高端羽化（%）',
'Y′ luma low feather (%)': 'Y′ 亮度低端羽化（%）',
'Y′ luma high feather (%)': 'Y′ 亮度高端羽化（%）',
'Low feather extends below the minimum; high feather extends above the maximum. Each is independent. Core bounds and the opposite edge stay unchanged.': '低端羽化向最小值以下延伸；高端羽化向最大值以上延伸。两端独立，核心边界与另一端保持不变。',
'Sample increases only the selected channel feather to at least 50% channel weight; not combined matte weight. For S/Y′, only the outside low or high edge changes. Core bounds and the opposite edge stay unchanged. Inside or already sufficient: no change. Gray has no hue; narrow hue ranges still exclude gray when sampling S/Y′.': '取样仅增大所选通道羽化，使该通道权重至少为 50%，并非最终蒙版权重。S/Y′ 仅改变取样所在的低端或高端外侧羽化，核心边界与另一端不变；位于核心内或羽化已足够时不变。灰色没有色相；取样 S/Y′ 时，窄色相范围仍会排除灰色。',
})
EN.update({key: key for key in ZH_CN})

ERROR_ZH_CN.update({
'Exterior feather requires saturation/luma and low/high': '外侧羽化必须指定饱和度或亮度通道以及低端或高端',
'Qualifier {name} must be None or finite and in 0..1': '限定器参数 {name} 必须为空或 0 至 1 之间的有限数值',
})
ZH_CN.update(ERROR_ZH_CN)
EN.update({key: key for key in ZH_CN})
ERROR_ZH_CN.update({
'Legacy qualifier range sets must be lists': '旧版本限定器范围组必须为列表',
'Legacy qualifier range sets must be bounded lists': '旧版本限定器范围组必须为不超过 8 项的列表',
'Legacy qualifier range requires exactly its documented fields': '旧版本限定器范围必须包含完整且仅限已定义的字段',
})
ZH_CN.update(ERROR_ZH_CN)
EN.update({key: key for key in ZH_CN})

ZH_CN.update({
'Use hue condition': '使用色相条件',
'Use HSV saturation condition': '使用 HSV 饱和度条件',
'Use Y′ luma condition': '使用 Y′ 亮度条件',
'Disabled conditions keep their values and contribute full weight. All three off selects all valid colors; an exclusion with all off clears the color matte. Replace/Add/Subtract sample only enabled base channels; Soften requires its selected channel enabled.': '关闭条件后保留数值，并视为完全符合。三项全关会选中所有有效颜色；排除范围三项全关会清空颜色蒙版。替换／添加／减去只采样基础范围已启用的通道；柔边采样要求所选通道已启用。',
'Exclude all colors?': '排除全部颜色？',
'An exclusion has all three conditions off. It excludes every valid color, so this qualifier correction will affect no pixels. Apply anyway?': '某个排除范围的三个条件全部关闭，会排除所有有效颜色，因此此限定器校正不会影响任何像素。仍要应用吗？',
'Enable the selected channel before sampling its feather': '请先启用所选通道，再采样其柔边',
'Qualifier hue_enabled must be a boolean': '限定器色相启用状态必须为布尔值',
'Qualifier saturation_enabled must be a boolean': '限定器饱和度启用状态必须为布尔值',
'Qualifier luma_enabled must be a boolean': '限定器亮度启用状态必须为布尔值',
})

ZH_CN.update({
'Feather extends outside each range; enabled weights multiply. Disabled hue or a 180° hue half-width includes gray. An enabled narrower hue condition excludes gray; out-of-range RGB is always excluded.': '羽化向各范围外侧延伸，已启用的条件权重相乘。关闭色相条件或色相半宽为 180° 时包含灰色；启用的窄色相条件排除灰色。超出 RGB 范围的像素始终排除。',
'Pick replaces enabled base channels with H ±10°, S/Y′ ±10 percentage points. Disabled channel values, feathers and switches stay unchanged.': '取样将基础范围已启用的通道替换为 H ±10°、S/Y′ ±10 个百分点。关闭通道的原值、羽化与开关保持不变。',
'Click through the magenta overlay to sample underlying float source, not tint. Replaces enabled base channels and clears extra ranges.': '点击穿透品红叠色，读取底层浮点源色而非叠加颜色。替换基础范围已启用的通道，并清除额外范围。',
'Click a displayed source sample. Replaces enabled base channels and clears extra ranges; disabled channel values stay unchanged.': '请单击显示的源取样点。替换基础范围已启用的通道并清除额外范围；关闭通道的原值保持不变。',
})

ZH_CN['Sample increases only the selected channel feather to at least 50% channel weight; not combined matte weight. For S/Y′, only the outside low or high edge changes. Core bounds and the opposite edge stay unchanged. Inside or already sufficient: no change. Gray has no hue; an enabled narrow hue condition still excludes gray when sampling S/Y′.'] = '取样仅增大所选通道羽化，使该通道权重至少为 50%，并非最终蒙版权重。S/Y′ 仅改变取样所在的低端或高端外侧羽化，核心边界与另一端不变；位于核心内或羽化已足够时不变。灰色没有色相；取样 S/Y′ 时，已启用的窄色相条件仍会排除灰色。'

# Qualifier-owned text remains in its established catalog, including animated windows.
ZH_CN.update({
    'Center and size are animated. Apply or cancel here, then use Window animation… in Color to edit keys. Shape, feather and color ranges remain editable here.': '中心和尺寸已有动画。请先应用或取消当前编辑，再到调色中的“窗口动画…”编辑关键帧。形状、羽化和颜色范围仍可在这里调整。',
    'Ranges use encoded Rec.709 HSV saturation and Y′ luma, not HSL lightness. Independent include ranges minus exclude ranges, then matte levels, local shrink/grow, local matte smoothing and one static or keyframed window; no tracking or temporal smoothing. Entirely offline.': '范围使用编码 Rec.709 的 HSV 饱和度和 Y′ 亮度，而非 HSL 明度。独立包含范围减去排除范围后，应用蒙版色阶、局部收缩／扩张、局部蒙版平滑和一个静态或关键帧窗口；不提供跟踪或时间平滑。全部离线处理。',
    'Window coordinates use the fitted project canvas before clip position, scale, rotation and mirror. Width/height are full dimensions; feather extends outside the shape by a fraction of its radius. Invert affects only the window, never the color ranges. Use Window animation… for manual center/size keys; no tracking.': '窗口坐标使用片段位置、缩放、旋转和镜像之前的适配项目画布。宽高是完整尺寸；羽化按半径比例向形状外延伸。反转仅影响窗口，不影响颜色范围。可在“窗口动画…”中手动设置中心／尺寸关键帧；不提供跟踪。',
})
EN.update({key: key for key in ZH_CN})
