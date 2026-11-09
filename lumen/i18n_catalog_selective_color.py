"""Selective hue controls and their explicit SDR scope."""
ZH_CN = {
    'Hue vs Hue': '色相对色相',
    'Hue vs Saturation': '色相对饱和度',
    'Selective hue curve': '选择性色相曲线',
    'Drag points; release commits once. Double-click adds. Delete removes an interior point. Arrows adjust; Shift is finer. Page Up / Down selects a point. Escape cancels.': '拖动控制点，松开后记为一步撤销。双击新增，Delete 删除内部点。方向键调整，按住 Shift 精调。Page Up / Down 选点，Esc 取消。',
    'Source hue · red wraps': '原始色相 · 红色首尾相连',
    'Curve point': '曲线控制点',
    'Point': '控制点',
    'Source hue · degrees': '原始色相 · 度',
    'Source hue': '原始色相',
    'Hue shift': '色相偏移',
    'Saturation factor': '饱和度倍率',
    'Hue shift · degrees': '色相偏移 · 度',
    'Saturation multiplier': '饱和度倍率',
    'Add point': '新增控制点',
    'Remove point': '删除控制点',
    'Piecewise linear · 0° / 360° stay linked.\nDrag or use exact fields. Escape cancels a drag.': '分段线性插值 · 0° 与 360° 联动。\n可拖动或精确输入，Esc 取消拖动。',
    '{number} · {degrees}°': '{number} · {degrees}°',
    'Shift only chosen hues. Neutral: 0°. Gray stays gray.': '只偏移选定色相。0° 为中性，灰色不变色。',
    'Scale saturation by source hue. Neutral: 1×. Gray stays gray.': '按原始色相调整饱和度。1 倍为中性，灰色不变色。',
    'FIXED CORRECTION ORDER\n709 decode → linear exposure → RGB lift/gain, gamma → 709 encode → legacy gain / contrast / saturation / balance / tones → master + RGB curves → hue / saturation curves → LUT (bounded domain) → clip / quantize\nSDR Rec.709 only · no HDR / OCIO management': '固定校正顺序\n709 解码 → 线性曝光 → RGB 提升/增益、伽马 → 709 编码 → 旧版增益 / 对比度 / 饱和度 / 平衡 / 色调 → 主曲线与 RGB 曲线 → 色相 / 饱和度曲线 → LUT（限定输入范围）→ 截断 / 量化\n仅支持 SDR Rec.709 · 不支持 HDR / OCIO 色彩管理',
}
ZH_CN['Blend after master, RGB and selective hue curves in encoded Rec.709 RGB: 0 keeps the pre-LUT image, 1 applies the full LUT. LUT input is bounded by its declared domain.'] = '在主曲线、RGB 曲线和选择性色相曲线之后，使用编码后的 Rec.709 RGB 混合：0 保留 LUT 前的画面，1 完整应用 LUT。LUT 输入受其声明范围限制。'
ERROR_ZH_CN = {
    'Version 17 clips require both selective hue curves': '版本 17 的片段必须包含两条选择性色相曲线',
    'Legacy projects cannot contain selective hue curves': '旧版项目不能包含选择性色相曲线',
    '{name} y endpoints must match for a periodic hue curve': '周期色相曲线 {name} 的首尾输出值必须一致',
}
ZH_CN.update(ERROR_ZH_CN)
EN = {source: source for source in ZH_CN}
