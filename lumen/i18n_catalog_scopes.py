"""Decoded-preview scopes: explicitly bounded SDR scope and reference labels."""

ZH_CN = {
    'Scopes': '示波器',
    'Scope mode': '示波器模式',
    'RGB parade': 'RGB 分量波形',
    'Vectorscope · Rec.709': '矢量示波器 · Rec.709',
    'Histogram + luma': '直方图 + 亮度波形',
    '8-bit decoded Rec.709 preview': '8 位解码后 Rec.709 预览',
    '8-bit decoded Rec.709 preview · {width}×{height} samples': '8 位解码后 Rec.709 预览 · {width}×{height} 采样',
    'No preview frame · build or play preview.': '暂无预览帧，请生成或播放预览。',
    'Preview out of date · rebuild preview for scopes.': '预览已过期，请重新生成预览以更新示波器。',
    'Targets: 75% dotted / 100% solid RGB amplitude · not HDR': '色标：75% 虚线 / 100% 实线 RGB 幅度 · 非 HDR',
    'Encoded RGB / Y′ 0–255 · logarithmic density · not HDR': '编码 RGB / Y′ 0–255 · 对数密度显示 · 非 HDR',
    'Scopes use sampled 8-bit RGB from the decoded Rec.709 preview, not source or 10-bit export values. The preview converter has already decoded video range; no second range expansion is applied. Sampling can miss small details. Upstream clipping, HDR, and display calibration cannot be measured here. Supported YUV frames use explicit matrix/range conversion; already-RGB backend frames are passed through, not color-verified. These scopes do not guarantee colorimetric equivalence.': '示波器采样自解码后的 Rec.709 预览 8 位 RGB，而非源素材或 10 位导出的数值。预览转换器已完成视频范围解码，不会再次扩展范围。采样可能遗漏细小细节，无法测量上游已截断的数值、HDR 或显示器校色。支持的 YUV 帧按明确的矩阵／范围转换；后端已转为 RGB 的帧直接显示，不代表已验证其色彩；示波器不保证色度学一致性。',
    'Cb is horizontal and Cr is vertical, using encoded Rec.709 R′G′B′. Targets are ideal color bars at 75% and 100% encoded RGB amplitude (low channels are zero), not saturation percentages or legal-range limits.': '横轴为 Cb，纵轴为 Cr，使用编码后的 Rec.709 R′G′B′ 计算。色标对应 RGB 编码幅度为 75% 与 100% 的理想彩条（低电平通道为零），不是饱和度百分比，也不是视频合法范围界限。',
}

EN = {source: source for source in ZH_CN}
