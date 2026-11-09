"""Bilingual measured master-gain proposal workflow."""
ZH_CN = {
    'Measured master-gain proposal': '实测主输出增益建议',
    'Measure a detached draft of the final stereo mix. Only Apply changes master output gain; clip gain, mute and fades stay unchanged.': '实测独立草稿的最终立体声混音。只有应用才会更改主输出增益；片段增益、静音和淡入淡出保持不变。',
    'Target loudness': '目标响度',
    'Estimated true-peak ceiling': '估算真峰值上限',
    '−18 LUFS and −2 dBTP are editable starting points, not delivery standards. Gain alone may not reach both targets.': '−18 LUFS 和 −2 dBTP 只是可编辑的起点，并非交付标准。仅调节增益可能无法同时满足两个目标。',
    'Measure gain proposal': '实测增益草稿',
    'This is gain only, not normalization, a limiter or compression. Estimated true peak is pre-encoding; codecs can change peaks. Build preview and listen after Apply, then analyze again.': '这里只调节增益，不进行响度归一化、限幅或压缩。真峰值估算针对编码前的信号；编码可能改变峰值。应用后请构建预览试听，再重新分析。',
    'Apply measured gain': '应用实测增益',
    'Targets changed. Measure a new draft before applying.': '目标已更改。请先重新实测草稿，再应用。',
    'Project changed. Close this proposal and analyze again.': '项目已更改。请关闭此建议并重新分析。',
    'Gain proposal unavailable · {error}': '无法生成增益建议 · {error}',
    'Checking measured gain proposal…': '正在检查实测增益建议…',
    'Proposal canceled. The project and gain are unchanged.': '建议已取消。项目和增益未更改。',
    'Measuring a detached draft… The project is unchanged.': '正在实测独立草稿…项目未更改。',
    'Measured draft: {lufs:.1f} LUFS · {peak:.2f} dBTP\nMaster gain: {gain:.1f} dB · change: {delta:+.1f} dB': '实测草稿：{lufs:.1f} LUFS · {peak:.2f} dBTP\n主输出增益：{gain:.1f} dB · 变化：{delta:+.1f} dB',
    'Measured draft exceeds the peak ceiling. Nothing was applied.': '实测草稿超出峰值上限。未应用任何更改。',
    'Peak-constrained or target not reached. Review the measured values before Apply.': '受峰值限制或未达到目标。请确认实测数值后再应用。',
    'Measured draft ready. Apply rechecks sources before changing master gain.': '实测草稿已就绪。应用时会重新检查源文件，再更改主输出增益。',
    'Rechecking original and draft sources before Apply…': '应用前正在重新检查原始混音和草稿的源文件…',
}
ZH_CN.update({
    'Silence (−∞)': '静音（−∞）',
    'Estimated true peak uses finite four-times FIR reconstruction with a sample-peak lower-bound safeguard. This is not an encoded-file check or compliance certification.': '估算真峰值使用有限长度的四倍 FIR 重建，并以采样峰值作为下限保护。这不是编码后文件检查或合规认证。',
    'Measured draft: {lufs:.1f} LUFS · target: {target:.1f} LUFS\nTarget difference: {miss:+.1f} LU\nSample peaks L / R: {sample_left} / {sample_right} dBFS\nEstimated true peaks L / R: {peak_left} / {peak_right} dBTP\nPeak ceiling: {ceiling:.1f} dBTP\nMaster gain: {gain:.1f} dB · change: {delta:+.1f} dB': '实测草稿：{lufs:.1f} LUFS · 目标：{target:.1f} LUFS\n目标差值：{miss:+.1f} LU\n采样峰值 左 / 右：{sample_left} / {sample_right} dBFS\n估算真峰值 左 / 右：{peak_left} / {peak_right} dBTP\n峰值上限：{ceiling:.1f} dBTP\n主输出增益：{gain:.1f} dB · 变化：{delta:+.1f} dB',
})
EN = {key: key for key in ZH_CN}
