"""English-source / Simplified Chinese local quality-restoration strings."""

ZH_CN = {
    'Before · restoration + color off': '调整前 · 关闭修复与调色',
    'Viewer comparison only: Before bypasses quality restoration, every clip grade and LUT in a render snapshot. Input normalization, stabilization, effects and overlays stay active. Export always uses the stored project settings.':
        '仅用于预览对比：“调整前”在渲染快照中跳过画质修复、所有片段的调色和 LUT。输入标准化、画面稳定、特效与叠加图层仍然生效。导出始终使用项目中保存的设置。',
    'Before · restoration and grades bypassed': '调整前 · 已跳过修复与调色',
    'Before · ALL clip grades, LUTs and restoration bypassed': '调整前 · 已跳过全部片段的调色、LUT 和画质修复',
    'Rendering BEFORE · bypass restoration and grades… Timeline stays editable.': '正在渲染调整前预览 · 跳过修复与调色… 仍可编辑时间线。',
    'Deblock and denoise can soften real detail. Sharpening can amplify noise and halos. Start with low strengths.':
        '去块和降噪可能使真实细节变柔和。锐化可能增强噪点和光晕。请从低强度开始。',

    'Quality restoration': '画质修复',
    'Enable quality restoration': '启用画质修复',
    'Turn off to bypass restoration while retaining all three strengths. Apply to update preview and export.':
        '关闭后可暂时跳过修复并保留三项强度。应用后，预览和导出使用新设置。',
    'Deblock': '去块',
    'Denoise': '降噪',
    'Sharpen': '锐化',
    'Soften small compression-block edges. Start low to protect texture.':
        '减轻细小压缩块边缘。请从低强度开始，保留纹理。',
    'Reduce fine noise within each frame. Higher values can soften texture.':
        '减少每帧内的细小噪点。较高强度可能使纹理变柔和。',
    'Add gentle edge contrast. Higher values can emphasize noise and halos.':
        '轻度增强边缘对比度。较高强度可能突出噪点和光晕。',
    'Local, conservative processing of each frame: deblock, denoise, then sharpen. Disabled by default; 0 skips a step. Apply, then use Before / After preview.':
        '在本地逐帧保守处理：先去块，再降噪，最后锐化。默认关闭；强度为 0 时跳过该步骤。应用后生成预览，对比处理前后。',
    'No AI, super-resolution or recovery of lost detail. This spatial method does not analyze motion or guarantee flicker-free video. Start low and check moving footage. Source files stay unchanged.':
        '不含 AI、超分辨率或丢失细节重建。这种空间处理不分析运动，也不保证消除视频闪烁。请从低强度开始，并检查运动画面。不会修改源文件。',
    'Apply restoration': '应用画质修复',
    'Reset restoration': '重置画质修复',
}

ERROR_ZH_CN = {
    'Invalid normalized float picture transform output': '归一化浮点画面变换的输出无效。',
    'Cannot locate the external FFmpeg executable.': '无法找到外部 FFmpeg 可执行文件。',
    'FFmpeg changed while its restoration cache identity was being checked.': '检查画质修复缓存标识时，FFmpeg 文件已更改。',
    'Invalid restoration active picture geometry': '画质修复的有效画面几何信息无效。',
    'Invalid float restoration output': '浮点画质修复输出无效。',
    'Active picture probe exceeded its output budget.': '有效画面探测输出超过大小限制。',
    'Cannot inspect active picture geometry.': '无法检查有效画面的几何信息。',
    'Decoder did not report active picture dimensions.': '解码器未提供有效画面尺寸。',
    'Active picture geometry exceeds the project canvas.': '有效画面尺寸超过项目画布。',
    'Render timed out inspecting active picture geometry.': '渲染时检查有效画面几何信息超时。',
    'Restoration requires a nonempty float32 HWC RGB image': '画质修复要求非空的 float32 HWC RGB 图像。',
    'Restoration image dimensions must not exceed 16384': '画质修复图像的宽度和高度均不得超过 16384。',
    'Restoration RGB samples must be finite': '画质修复 RGB 采样值必须为有限数值。',
    'Restoration cancelled.': '画质修复已取消。',
    'Restoration cancellation must be an event or callable': '画质修复的取消信号必须为事件或可调用对象。',
    'Restoration produced non-finite values': '画质修复产生了非有限数值。',
    'Restoration requires exactly enabled, deblock, denoise and sharpen':
        '画质修复设置必须且只能包含 enabled、deblock、denoise 和 sharpen。',
    'Invalid restoration settings': '画质修复设置无效。',
    'Restoration enabled must be a boolean': '画质修复启用状态必须为布尔值。',
    'Restoration {name} must be finite and in 0.0..1.0':
        '画质修复参数 {name} 必须为 0.0..1.0 范围内的有限数值。',
    'Restoration requires a picture clip on a video track': '画质修复仅适用于视频轨道上的视频片段。',
    'Version 9 clips require restoration settings': '第 9 版项目的片段必须包含画质修复设置。',
    'Legacy clips cannot contain restoration settings': '旧版片段不能包含画质修复设置。',
    'Unknown restoration property': '未知的画质修复属性。',
}
ZH_CN.update(ERROR_ZH_CN)
