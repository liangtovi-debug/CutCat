"""SDR export contracts, limitations and validation in English and Chinese."""

VALIDATION_MESSAGES = {
    'Unknown export profile. Choose H.264 8-bit or ProRes 422 HQ 10-bit SDR.': '未知的导出格式。请选择 H.264 8 位或 ProRes 422 HQ 10 位 SDR。',
    'ProRes 10-bit SDR export supports only verified static PNG stickers. Animated frame-pack manifests are not yet supported; disable those stickers or choose H.264 8-bit.': 'ProRes 10 位 SDR 导出仅支持已验证的静态 PNG 贴纸。暂不支持动画贴纸包清单；请禁用这些贴纸，或选择 H.264 8 位。',
    'ProRes 10-bit SDR export supports Gaussian blur and radial vignette only. Other unverified clip effects are blocked. Disable them or choose H.264 8-bit.': 'ProRes 10 位 SDR 导出仅支持高斯模糊和径向暗角。其他未经验证的片段特效会被阻止。请禁用这些特效，或选择 H.264 8 位。',
}

ERROR_MESSAGES = {
    **VALIDATION_MESSAGES,
    'This FFmpeg build cannot apply radial vignette directly to 16-bit RGB video. Choose H.264 8-bit or install a compatible FFmpeg build.': '当前 FFmpeg 无法直接对 16 位 RGB 视频应用径向暗角。请选择 H.264 8 位，或安装兼容的 FFmpeg 版本。',
    'This FFmpeg build cannot apply Gaussian blur directly to 16-bit RGB video. Choose H.264 8-bit or install a compatible FFmpeg build.': '当前 FFmpeg 无法直接对 16 位 RGB 视频应用高斯模糊。请选择 H.264 8 位，或安装兼容的 FFmpeg 版本。',
    'Render timed out checking high-bit effect support.': '检查高位深特效支持时渲染超时。',
    'High-bit effect capability probe exceeded its output budget.': '高位深特效能力检查的输出超出安全限制。',
    'This FFmpeg build cannot composite stickers directly into 10-bit Rec.709 video. Choose H.264 8-bit or install a compatible FFmpeg build.': '当前 FFmpeg 无法将贴纸直接合成到 10 位 Rec.709 视频。请选择 H.264 8 位，或安装兼容的 FFmpeg 版本。',
    'High-bit sticker source identity is missing or unsupported. Verify a static PNG before rendering.': '高位深贴纸的源文件标识缺失或不受支持。请在渲染前验证静态 PNG。',
    'Render timed out checking high-bit sticker support.': '检查高位深贴纸支持时渲染超时。',
    'High-bit sticker capability probe exceeded its output budget.': '高位深贴纸能力检查的输出超出安全限制。',
    'This FFmpeg build cannot burn captions directly into 10-bit Rec.709 video. Choose H.264 8-bit or install a compatible FFmpeg build.': '当前 FFmpeg 无法将字幕直接烧录到 10 位 Rec.709 视频。请选择 H.264 8 位，或安装兼容的 FFmpeg 版本。',
    'Render timed out checking high-bit caption support.': '检查高位深字幕支持时渲染超时。',
    'High-bit caption capability probe exceeded its output budget.': '高位深字幕能力检查的输出超出安全限制。',
    'SDR export requires even width and height.': 'SDR 导出要求画面的宽度和高度均为偶数。',
    'Preview is an H.264 8-bit proxy; ProRes 10-bit is an export profile only.': '预览使用 H.264 8 位代理；ProRes 10 位仅用于导出。',
    'ProRes 10-bit SDR export filename must end in .mov.': 'ProRes 10 位 SDR 导出文件名必须以 .mov 结尾。',
    'Cannot check the ProRes 10-bit encoder. Install an FFmpeg build with prores_ks and zscale support.': '无法检查 ProRes 10 位编码器。请安装支持 prores_ks 和 zscale 的 FFmpeg 版本。',
    'This FFmpeg build lacks the required prores_ks 10-bit encoder. Choose H.264 8-bit or install a compatible FFmpeg build.': '当前 FFmpeg 缺少必需的 prores_ks 10 位编码器。请选择 H.264 8 位，或安装兼容的 FFmpeg 版本。',
    'Unexpected encoded pixel format, codec profile or SDR color tags': '编码结果的像素格式、编码规格或 SDR 色彩标签不符合预期',
    'ProRes 10-bit SDR output verification failed; no export was published: {exc}': 'ProRes 10 位 SDR 输出验证失败；未生成最终导出文件：{exc}',
}

ZH_CN = {
    'After · all clip corrections; 8-bit preview proxy': '调整后 · 所有片段校正；8 位预览代理',
    'Choose export format': '选择导出格式',
    'Export color and quality': '导出色彩与画质',
    'Choose the delivery format, then choose a new filename. Your project and source files stay unchanged.': '先选择输出格式，再选择新的文件名。项目和源素材保持不变。',
    'Delivery format': '输出格式',
    'H.264 · 8-bit SDR · MP4 (default)': 'H.264 · 8 位 SDR · MP4（默认）',
    'ProRes 422 HQ · 10-bit SDR · MOV': 'ProRes 422 HQ · 10 位 SDR · MOV',
    'Preview is an 8-bit H.264 proxy, even when exporting 10-bit. It is not a 10-bit output or display check; display calibration is not verified.': '即使导出 10 位文件，预览仍使用 8 位 H.264 代理，不能用于验证 10 位输出或显示效果；显示器校色尚未验证。',
    'Export validation': '导出检查',
    'Choose export filename…': '选择导出文件名…',
    'Cancel': '取消',
    'SDR Rec.709 only. The graded RGB16 cache clamps values to 0–1, then the export converts to 10-bit YUV 4:2:2. This is not HDR or an unbounded floating-point master.': '仅支持 SDR Rec.709。调色后的 RGB16 缓存将数值限制在 0–1，再转换为 10 位 YUV 4:2:2 输出。这不是 HDR，也不是保留超范围值的浮点母版。',
    'Existing basic captions and verified static PNG stickers are supported without reducing the background video to 8-bit. Gaussian blur and high-precision radial vignette are supported in native RGB16. Runtime checks require working 10-bit drawtext for captions, 10-bit overlay for stickers and RGB16 effects. Failures block export; there is no silent fallback. Animated sticker packs and unverified clip effects remain blocked. Disable them or choose H.264. ProRes files are substantially larger.': '支持现有基础字幕和已验证的静态 PNG 贴纸，不会将背景视频降为 8 位。高斯模糊和高精度径向暗角支持原生 RGB16 处理。运行时会检查字幕所需的 10 位 drawtext、贴纸所需的 10 位 overlay 及 RGB16 特效能力。检查失败会阻止导出，不会静默回退。动画贴纸包和未经验证的片段特效仍会阻止导出。请禁用这些内容，或选择 H.264。ProRes 文件体积明显更大。',
    'Sticker artwork is still rendered by Qt into an 8-bit RGBA layer. Legacy gamma-coded SDR numeric appearance is retained, not linear-light blending, sRGB-to-Rec.709 transfer conversion, or ICC/HDR color management.': '贴纸图像仍由 Qt 绘制到 8 位 RGBA 图层。保留旧版伽马编码 SDR 数值外观，不是线性光混合、sRGB 至 Rec.709 传递函数转换，也不包含 ICC/HDR 色彩管理。',
    'Standard SDR Rec.709 delivery: H.264, 8-bit YUV 4:2:0 in MP4. Smaller files for everyday sharing.': '常规 SDR Rec.709 输出：MP4 封装的 H.264，8 位 YUV 4:2:0。文件较小，适合日常分享。',
    'Uses the existing effects, sticker and caption pipeline. Higher-bit-depth sources are delivered as 8-bit; this profile is not HDR.': '使用现有的特效、贴纸和字幕处理流程。高位深源素材也会以 8 位输出；此格式不是 HDR。',
    'This export format cannot be used:\n{error}': '无法使用此导出格式：\n{error}',
    'Export new MOV · existing files are protected': '导出新的 MOV · 不覆盖已有文件',
    'QuickTime video (*.mov)': 'QuickTime 视频 (*.mov)',
    'SDR pipeline: RGB16 decode → float32 grade → clipped RGB16 → delivery. Preview/scopes use an 8-bit H.264 proxy, not a 10-bit monitor. Untagged input is an assumption, not Log detection.': 'SDR 流程：RGB16 解码 → float32 调色 → 截断至范围内的 RGB16 → 输出。预览与示波器使用 8 位 H.264 代理，并非 10 位监看。未标记色彩的输入依赖假设，并非自动识别 Log。',
    'Blur and radial vignette use encoded Rec.709 RGB code values, not linear light or HDR. Blur Sigma: 0–30 project pixels; vignette amount: 0–1. The high-precision radial vignette is not bit-identical to the legacy 8-bit preview. Zero or disabled effects are bypassed. Clip-effect keyframes are not supported. High-bit blur uses conservative CPU processing and may export more slowly. High-precision radial vignette can substantially slow export.': '模糊和径向暗角处理编码 Rec.709 RGB 码值，不是线性光或 HDR。模糊 Sigma 范围为 0–30 个项目像素；暗角强度为 0–1。高精度径向暗角与旧版 8 位预览并非逐位一致。零强度或禁用的特效直接旁路。暂不支持片段特效关键帧。高位深模糊采用保守 CPU 处理，导出速度可能较慢。高精度径向暗角可能显著降低导出速度。',
    **ERROR_MESSAGES,
}

# Source keys are the English copy; keeping both catalogs explicit also makes
# these messages straightforward to audit without translating user content.
EN = {source: source for source in ZH_CN}
