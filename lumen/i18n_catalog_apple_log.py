"""Narrow, explicit experimental Apple Log 1 input; never camera autodetection."""
ZH_CN = {
    'Import Apple Log 1': '导入 Apple Log 1',
    'BT.2020 · experimental': 'BT.2020 · 实验性',
    'Import Apple Log 1 / BT.2020 (experimental)': '导入 Apple Log 1 / BT.2020（实验性）',
    'Apple Log 1 / BT.2020 (experimental)': 'Apple Log 1 / BT.2020（实验性）',
    'Media (*.mp4 *.mov *.mkv);;All files (*)': '媒体 (*.mp4 *.mov *.mkv);;所有文件 (*)',
    'Explicit assignment, not camera detection. Tested synthetic inputs only; no Apple Log 2, Apple LUT or Resolve matching claim.': '手动指定，不会自动识别相机。仅以合成素材验证；不支持 Apple Log 2，不声称匹配 Apple LUT 或 Resolve。',
    'Assign source encoding, not a creative look. Apple Log 1 requires explicit experimental import and strict metadata; other camera Log, wide-gamut and HDR inputs remain unsupported.': '指定源编码，而非创意风格。Apple Log 1 需使用实验性专用导入并通过严格元数据检查；其他相机 Log、广色域和 HDR 输入仍不支持。',
    'Apple Log 1 / BT.2020 is experimental and tested with synthetic inputs only. No Apple Log 2, camera compatibility, Apple LUT or Resolve matching claim. Output remains clipped SDR Rec.709; no HDR mastering.': 'Apple Log 1 / BT.2020 为实验性功能，仅用合成素材验证。不支持 Apple Log 2，不保证相机兼容性，不声称匹配 Apple LUT 或 Resolve。输出仍为截断到范围内的 SDR Rec.709，不支持 HDR 母版。',
    'Requires BT.2020 primaries and unknown transfer tags. Only full-range RGB16, or verified 10-bit YUV with BT.2020 non-constant matrix and explicit range.': '要求 BT.2020 原色标签和未知传递函数标签。仅限全范围 RGB16，或已验证的 10 位 YUV（BT.2020 非恒定亮度矩阵及明确范围标签）。',
    'Unsupported input encoding': '不支持此输入编码',
    'Unsupported media input encoding': '不支持此媒体输入编码',
    'Apple Log input requires video': 'Apple Log 输入必须包含视频',
    'Legacy projects cannot contain Apple Log input encoding': '旧版项目不能包含 Apple Log 输入编码',
    'Input encoding must be rec709, srgb, linear_rec709 or apple_log_bt2020': '输入编码必须为 rec709、srgb、linear_rec709 或 apple_log_bt2020',
}
ZH_CN.update({
    'Input color': '输入色彩',
    'Experimental Apple Log 1 requires unknown transfer and explicit BT.2020 primaries; conflicting SDR/HDR tags are not overridden.': '实验性 Apple Log 1 要求未知传递函数标签和明确的 BT.2020 原色标签；不会覆盖冲突的 SDR/HDR 标签。',
    'Experimental Apple Log RGB requires full-range RGB16 and RGB matrix metadata.': '实验性 Apple Log RGB 要求全范围 RGB16 和 RGB 矩阵元数据。',
    'Experimental Apple Log YUV requires 10-bit BT.2020 non-constant-luminance matrix and explicit full/limited range.': '实验性 Apple Log YUV 要求 10 位、BT.2020 非恒定亮度矩阵及明确的全范围或有限范围标签。',
    'Experimental Apple Log supports only full-range RGB16 or explicitly tagged 10-bit YUV; this pixel format is unsupported.': '实验性 Apple Log 仅支持全范围 RGB16 或明确标记的 10 位 YUV；不支持此像素格式。',
    'Import this source explicitly as Apple Log 1 / BT.2020 before assigning experimental Log normalization.': '请先使用 Apple Log 1 / BT.2020 专用导入，再指定实验性 Log 标准化。',
    'This source was explicitly imported as Apple Log 1 / BT.2020; keep its Apple Log input interpretation.': '此素材已明确按 Apple Log 1 / BT.2020 导入；请保留其 Apple Log 输入解释。',
    'Experimental Apple Log 1 / BT.2020 → CutCat SDR shoulder v1 → Rec.709. Synthetic decoder validation only; not Apple Log 2, Apple LUT or Resolve matching.': '实验性 Apple Log 1 / BT.2020 → 剪辑猫 SDR 高光肩部映射 v1 → Rec.709。仅以合成素材验证解码；不支持 Apple Log 2，不声称匹配 Apple LUT 或 Resolve。',
    'Experimental Apple Log input does not support active stabilization.': '实验性 Apple Log 输入不支持启用防抖。',
})

# Include exact import wrappers without translating arbitrary diagnostic text.
ZH_CN.update({'Cannot inspect media: ' + source: '无法检查媒体：' + translation
              for source, translation in list(ZH_CN.items())
              if source.startswith(('Experimental Apple Log', 'Apple Log input', 'Unsupported media input'))})
EN = {source: source for source in ZH_CN}
ERROR_MESSAGES = ZH_CN
