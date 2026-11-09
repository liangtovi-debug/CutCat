"""Known-neutral SDR white balance labels without camera-temperature claims."""
ERROR_ZH_CN = {
    'White balance enabled must be a boolean': '白平衡启用状态必须为布尔值',
    'White balance gains require three finite numbers in 0.25..4': '白平衡增益需要三个 0.25 至 4 之间的有限数值',
    'Version 27 clips require white balance settings': '版本 27 的片段必须包含白平衡设置',
    'Legacy projects cannot contain white balance settings': '旧版项目不能包含白平衡设置',
}
ZH_CN = {
    **ERROR_ZH_CN,
    'Enable white balance': '启用白平衡',
    'Neutral-reference white balance': '中性参照白平衡',
    'Enable neutral-reference white balance': '启用中性参照白平衡',
    'Pick known neutral gray / white…': '吸取已知中性灰／白…',
    'Reset white balance': '重置白平衡',
    'Linear RGB gains: {gains}': '线性 RGB 增益：{gains}',
    'Source neutral correction before exposure. Warm / cool and Green / magenta remain separate artistic controls.': '在曝光之前校正素材的中性颜色。冷暖与绿／洋红仍为独立的风格调整。',
    'Neutral-reference white balance · static frame': '中性参照白平衡 · 静态画面',
    'Static source frame for neutral-reference white balance': '用于中性参照白平衡取样的静态素材画面',
    'Apply sampled white balance': '应用取样白平衡',
    'Reading neutral-reference frame…': '正在读取中性参照取样画面…',
    'Click a known neutral gray or white surface. Dark, clipped, padding and excessive-gain samples are rejected.': '点击已知的中性灰或白色表面。拒绝过暗、截断、填充边缘或增益过大的样本。',
    'No usable neutral reference here: choose an unclipped, moderately bright gray or white picture pixel.': '此处没有可用的中性参照：请选择画面内未截断、亮度适中的灰色或白色像素。',
    'Linear RGB gains {gains} · pixel ({x}, {y}). No grade change yet.': '线性 RGB 增益 {gains} · 像素（{x}，{y}）。尚未更改调色。',
    'Selected clip only · after input normalization and spatial restoration; before white balance and all creative grading.\nRGB16 decode → float32 sample; image display is clipped 8-bit. Not the composited preview.': '仅选中片段 · 输入归一化与空间修复之后；白平衡及所有创意调色之前。\nRGB16 解码 → float32 取样；显示画面为截断后的 8 位图像。不是合成预览。',
    'Choose a surface you know is neutral gray or white under the scene light, not an arbitrary colored object. Linear Rec.709 RGB gains preserve the sampled neutral’s luminance, not whole-image brightness. No Kelvin, camera RAW, HDR or chromatic-adaptation claim.': '选择场景光照下已知应为中性灰或白的表面，而非任意有色物体。线性 Rec.709 RGB 增益保留取样中性点的亮度，不保证整幅画面亮度不变。不提供开尔文色温、相机 RAW、HDR 或色适应处理。',
    'FIXED CORRECTION ORDER\n709 decode → neutral-reference white balance → linear exposure → RGB lift/gain, gamma → 709 encode → legacy gain / contrast around pivot / saturation / balance / tones → master + RGB curves → hue / saturation curves → joint qualifier → LUT (bounded domain) → clip / quantize\nSDR Rec.709 only · no HDR / OCIO management': '固定校正顺序\n709 解码 → 中性参照白平衡 → 线性曝光 → RGB 提升／增益、伽马 → 709 编码 → 传统增益／围绕中心的对比度／饱和度／色彩平衡／影调 → 主曲线与 RGB 曲线 → 色相／饱和度曲线 → 联合限定器 → LUT（有限定义域）→ 截断／量化\n仅支持 SDR Rec.709 · 不含 HDR／OCIO 色彩管理',
}
EN = {source: source for source in ZH_CN}
