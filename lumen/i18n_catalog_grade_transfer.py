"""English-source / Simplified Chinese private creative-grade transfer text."""

ZH_CN = {
    'Copy grade': '复制调色',
    'Paste grade': '粘贴调色',
    'Cannot copy grade': '无法复制调色',
    'Cannot paste grade': '无法粘贴调色',
    'Copies all creative controls, correction enable/bypass, curves and LUT strength. Source input normalization, restoration, stabilization, transforms, effects and audio are not copied.':
        '复制全部创意调色参数、调色启用／旁路状态、曲线及 LUT 强度。不复制源输入标准化、画质修复、画面稳定、变换、特效和音频。',
    'Keep the committed grade privately in this window, including across project changes. Nothing is sent to the system clipboard. Closing this window clears the copy.':
        '将已应用的调色私密保存在当前窗口，切换项目后仍可粘贴。不写入系统剪贴板，关闭此窗口后清除。',
    'Replace one selected picture clip’s complete creative grade in one undo step. The target’s input normalization stays unchanged. Any assigned LUT must still be the same readable file.':
        '一次替换所选画面片段的完整创意调色，可一步撤销。目标片段的输入标准化保持不变。已指定的 LUT 必须仍为同一个可读取且未更改的文件。',
    'No grade copied in this window.': '当前窗口尚未复制调色。',
    'Selected clip already has this creative grade. Input normalization is unchanged.':
        '所选片段已具有相同的创意调色，输入标准化保持不变。',
    'Grade copied. The original LUT file must stay readable and unchanged, even when bypassed.':
        '已复制调色。原始 LUT 文件必须保持可读取且内容不变，即使当前处于旁路状态。',
    'Grade copied privately in this window. Select a picture clip to paste.':
        '调色已私密保存在此窗口。选择一个画面片段即可粘贴。',
}

ERROR_ZH_CN = {
    'Copied grade data is invalid. Copy the grade again.': '复制的调色数据无效，请重新复制调色。',
    'Select a picture clip on a video track to copy or paste a grade.': '请选择视频轨道上的画面片段，再复制或粘贴调色。',
    'Grade LUT must be a local regular .cube file.': '调色 LUT 必须是本地普通 .cube 文件。',
    'Grade LUT exceeds 64 MiB.': '调色 LUT 文件超过 64 MiB。',
    'Grade LUT changed while it was being read. Try copying the grade again.': '读取调色 LUT 时文件已发生变化，请重新复制调色。',
    'Cannot read the grade LUT. Locate the original .cube file and copy the grade again.': '无法读取调色 LUT。请找到原始 .cube 文件后重新复制调色。',
    'Grade LUT is invalid. Choose a valid 3D .cube file and copy the grade again.': '调色 LUT 无效。请选择有效的三维 .cube 文件后重新复制调色。',
    'No grade has been copied in this window.': '当前窗口尚未复制任何调色。',
    'Copied grade LUT identity is missing. Copy the grade again.': '复制的调色缺少 LUT 文件标识，请重新复制调色。',
    'Copied grade LUT has changed or moved. Restore the original file or copy the grade again.': '复制的调色所用 LUT 已更改或移动。请恢复原始文件，或重新复制调色。',
}
ZH_CN.update(ERROR_ZH_CN)
