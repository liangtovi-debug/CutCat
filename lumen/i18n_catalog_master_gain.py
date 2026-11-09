"""Explicit manual project-level master gain; no automatic correction claims."""
ZH_CN = {
    'Master output gain': '主输出增益',
    'Master output gain…': '主输出增益…',
    'Master output gain · dB': '主输出增益 · dB',
    'Project-wide output gain · after the stereo mix, before audio encoding. Clip gain, mute and fades are unchanged.': '项目级输出增益 · 位于立体声混音之后、音频编码之前。片段增益、静音和淡入淡出保持不变。',
    'Range: −96 to +24 dB. 0 dB leaves the mix unchanged. −96 dB attenuates; it is not mute.': '范围：−96 至 +24 dB。0 dB 保持混音不变。−96 dB 仅衰减，并非静音。',
    'Reset draft to 0 dB': '将草稿重置为 0 dB',
    'Affects the entire mix, not the selected clip. Gain does not prevent clipping or repair already clipped audio. No automatic normalization, limiter or compressor.': '影响整个混音，而非所选片段。增益不能防止削波，也无法修复已经削波的音频。不包含自动归一化、限幅器或压缩器。',
    'Apply gain → Build preview and listen → Analyze timeline audio again. Existing analysis results become stale; preview and analysis never run automatically.': '应用增益 → 构建预览并试听 → 再次分析时间线音频。已有分析结果会失效；不会自动构建预览或执行分析。',
    'No project changes until Apply. Cancel discards this draft. One Undo restores the applied gain.': '点击“应用”前不会更改项目。“取消”丢弃此草稿。一次“撤销”可还原已应用的增益。',
    'Project changed. Close and reopen Master output gain before applying.': '项目已变化。请关闭并重新打开“主输出增益”后再应用。',
    'Wait for the current task to finish or cancel it before applying master gain.': '请等待当前任务完成或取消后，再应用主输出增益。',
    'Draft only · the project gain is unchanged until Apply.': '仅为草稿 · 点击“应用”前，项目增益保持不变。',
}
EN = {source: source for source in ZH_CN}
