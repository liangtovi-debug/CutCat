"""Final-composite still preview; distinct from grade-only and playback views."""
ZH_CN = {
    'Matches H.264/playback 8-bit composition before encoding; not a ProRes 10-bit, HDR or calibrated-monitor reference.': '对应 H.264／播放预览的编码前 8 位合成；不是 ProRes 10 位、HDR 或校准监视器参考。',
    'Final composite still preview': '最终合成静帧预览',
    'Final composite still preview…': '最终合成静帧预览…',
    'Final still…': '最终静帧…',
    'Final composite still · Rec.709 SDR · No audio': '最终合成静帧 · Rec.709 SDR · 无声音',
    'Final composite still · display only': '最终合成静帧 · 仅供显示',
    'Refresh explicitly to inspect the paused playhead. Opening this window does not render.': '暂停后，点击刷新检查播放头画面。打开此窗口不会自动渲染。',
    'No verified final composite still.': '尚无已验证的最终合成静帧。',
    'Original SDR canvas, up to 1920 × 1080. Full composition, not realtime playback or a proxy. Active stabilization, cross dissolves and unsupported combinations require Build full timeline preview.': '使用原尺寸 SDR 画布，最高 1920 × 1080。完整合成，但不是实时播放或代理。启用的防抖、交叉溶解及不支持的组合请使用「构建完整时间线预览」。',
    'Source, LUT and sticker checks run in the background. This separate 8-bit display does not replace playback, scopes or reference stills.': '后台检查源文件、LUT 和贴纸。本独立 8 位显示不会替换播放缓存、示波器或参考静帧。',
    'Waits briefly after changes; retains only one running task and the latest request. Cancel waits for another edit or an explicit refresh.': '修改后短暂等待，仅保留一个运行任务和最新请求。取消后等待下一次修改或手动刷新。',
    'Refresh final frame': '刷新最终画面',
    'Changes pending · final still refresh after a short pause.': '画面已修改 · 短暂停留后刷新最终静帧。',
    'Rendering the final target frame… New requests replace this one.': '正在渲染目标帧的最终画面… 新请求将替换本次请求。',
    'Checking source, LUT and sticker dependencies before display…': '显示前正在检查源文件、LUT 和贴纸依赖…',
    'Rendering final composite still…': '正在渲染最终合成静帧…',
    'Checking final still dependencies…': '正在检查最终静帧依赖…',
    'Final frame unavailable · {error} Use Build full timeline preview.': '最终静帧不可用 · {error} 请使用「构建完整时间线预览」。',
    'A source, LUT or sticker changed. Refresh for a newly verified final frame.': '源文件、LUT 或贴纸已改变。请刷新，重新验证最终画面。',
    'Verified final composite still · SDR · No audio': '已验证的最终合成静帧 · SDR · 无声音',
    'Timeline frame {frame} · original {width} × {height} · background dependency rechecks': '时间线第 {frame} 帧 · 原尺寸 {width} × {height} · 后台复查依赖',
    'Final still is stale. Refresh for the current project and playhead.': '最终静帧已过期。请刷新当前工程和播放头画面。',
}

ERROR_ZH_CN = {
    'Cannot prepare final-frame preview: {error}': '无法生成最终静帧预览：{error}',
    'Final-frame preview timed out.': '最终静帧预览已超时。',
    'Final-frame timeout must be positive and finite.': '最终静帧超时必须是有限的正数。',
    'Final-frame preview requires an even SDR canvas no larger than 1920 x 1080.': '最终静帧预览要求不超过 1920 × 1080、宽高均为偶数的 SDR 画布。',
    'Choose an existing integer timeline frame.': '请选择时间线范围内的整数帧。',
    'Final-frame preview does not support an active video transition.': '最终静帧预览不支持当前帧正在进行的视频转场。',
    'Final-frame preview does not support active stabilization.': '最终静帧预览不支持已启用的防抖。',
    'Final-frame preview currently supports original SDR input, not Apple Log.': '最终静帧预览目前仅支持原始 SDR 输入，不支持 Apple Log。',
    'Final-frame preview requires verified self-contained sources; manifests and external-reference formats are unsupported.': '最终静帧预览要求已验证的自包含源文件；不支持清单或外部引用格式。',
    'The project changed while detaching the requested frame. Refresh again.': '分离请求帧的快照时工程已改变，请重新刷新。',
    'A final-frame dependency changed during capture. Refresh again.': '捕获期间最终静帧的依赖已改变，请重新刷新。',
}
ZH_CN.update(ERROR_ZH_CN)

# Stable still-only font guard failures also feed the shared backend catalog.
FONT_ERROR_ZH_CN = {
    'Final-frame fonts must be regular local files; use Build preview.': '最终静帧的字体必须是常规本地文件；请使用「构建预览」。',
    'Final-frame font exceeds 64 MiB; use Build preview.': '最终静帧的字体超过 64 MiB；请使用「构建预览」。',
    'Final-frame font character map exceeds its supported bounds; use Build preview.': '最终静帧字体的字符映射超出支持范围；请使用「构建预览」。',
    'Final-frame font discovery exceeds 2048 directories; use Build preview.': '最终静帧的字体查找超过 2048 个目录；请使用「构建预览」。',
    'Final-frame font changed while opening; refresh again.': '打开最终静帧字体时文件已改变；请重新刷新。',
    'Final-frame font changed while reading; refresh again.': '读取最终静帧字体时文件已改变；请重新刷新。',
    'Incomplete final-frame font collection; use Build preview.': '最终静帧的字体集合不完整；请使用「构建预览」。',
    'Final-frame font collection exceeds 64 faces or is incomplete; use Build preview.': '最终静帧的字体集合超过 64 个字体或不完整；请使用「构建预览」。',
    'Final-frame fonts require uncompressed SFNT/TTC, not WOFF or another format; use Build preview.': '最终静帧的字体要求未压缩的 SFNT/TTC 格式，不接受 WOFF 或其他格式；请使用「构建预览」。',
    'Invalid final-frame font header; use Build preview.': '最终静帧的字体文件头无效；请使用「构建预览」。',
    'Final-frame font exceeds 256 tables or is incomplete; use Build preview.': '最终静帧的字体超过 256 个数据表或不完整；请使用「构建预览」。',
    'Final-frame font table exceeds its verified file; use Build preview.': '最终静帧的字体数据表超出已验证文件的范围；请使用「构建预览」。',
    'Final-frame font discovery exceeds 20000 entries; use Build preview.': '最终静帧的字体查找超过 20000 个条目；请使用「构建预览」。',
    'Incomplete final-frame font names; use Build preview.': '最终静帧的字体名称记录不完整；请使用「构建预览」。',
    'Final-frame font name table exceeds supported bounds; use Build preview.': '最终静帧的字体名称表超出支持范围；请使用「构建预览」。',
    'Final-frame font discovery exceeds 4096 fonts; use Build preview.': '最终静帧的字体查找超过 4096 个字体；请使用「构建预览」。',
    'Final-frame decoded font names exceed 4 MiB or the verified table; use Build preview.': '最终静帧解码后的字体名称超过 4 MiB 或已验证数据表的范围；请使用「构建预览」。',
}
ERROR_ZH_CN.update(FONT_ERROR_ZH_CN)
ZH_CN.update(FONT_ERROR_ZH_CN)
