"""Encoded Rec.709 contrast pivot: bilingual controls and honest domain labels."""

ERROR_ZH_CN = {
    'Version 26 clips require contrast_pivot': '版本 26 的片段必须包含对比度中心',
    'Legacy projects cannot contain contrast_pivot': '旧版项目不能包含对比度中心',
    'contrast_pivot must be finite and in 0..1': '对比度中心必须为 0 至 1 之间的有限数值',
}

ZH_CN = {
    'Contrast pivot': '对比度中心',
    'Reset only the basic color controls, including contrast pivot. Advanced color, LUT and input settings are preserved.': '仅重置基础颜色控件，包括对比度中心。保留高级颜色、LUT 和输入设置。',
    'Encoded Rec.709 RGB after legacy code gain: (value − pivot) × contrast + pivot. Default pivot 0.5 preserves legacy contrast. At Contrast 1, pivot has no effect. Not linear-light or Log contrast.': '在传统编码值增益之后，对编码的 Rec.709 RGB 计算：（数值 − 中心）× 对比度 + 中心。默认中心 0.5 保留原有对比度效果。对比度为 1 时，中心不影响画面。这不是线性光或 Log 对比度。',
    'Encoded Rec.709 RGB, not linear light. Pivot has no effect at Contrast 1.': '使用编码的 Rec.709 RGB，而非线性光。对比度为 1 时，中心不影响画面。',
    'FIXED CORRECTION ORDER\n709 decode → linear exposure → RGB lift/gain, gamma → 709 encode → legacy gain / contrast around pivot / saturation / balance / tones → master + RGB curves → hue / saturation curves → joint qualifier → LUT (bounded domain) → clip / quantize\nSDR Rec.709 only · no HDR / OCIO management': '固定校正顺序\n709 解码 → 线性曝光 → RGB 提升／增益、伽马 → 709 编码 → 传统增益／围绕中心的对比度／饱和度／色彩平衡／影调 → 主曲线与 RGB 曲线 → 色相／饱和度曲线 → 联合限定器 → LUT（有限定义域）→ 截断／量化\n仅支持 SDR Rec.709 · 不含 HDR／OCIO 色彩管理',
    **ERROR_ZH_CN,
}

EN = {source: source for source in ZH_CN}
