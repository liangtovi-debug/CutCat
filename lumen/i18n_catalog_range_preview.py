"""Explicit bounded final A/V range playback, not a realtime/HDR promise."""
ZH_CN = {
    'Preview around playhead': '预览播放头附近',
    'Preview around playhead…': '预览播放头附近…',
    'Rendered range · Rec.709 SDR · 48 kHz stereo mix': '已渲染片段 · Rec.709 SDR · 48 kHz 立体声混音',
    'Rendered range preview · display only': '已渲染片段预览 · 仅供显示',
    'Build explicitly, then Play. Opening this window does not render.': '请先构建，再点击播放。打开此窗口不会自动渲染。',
    'No verified range preview.': '尚无已验证的片段预览。',
    'Seconds before and after playhead': '播放头前后各多少秒',
    'Loop range': '循环播放片段',
    'Each loop rechecks dependencies before restarting. A short gap may occur.': '每次循环播放前都会复查依赖，可能出现短暂停顿。',
    'Up to 10 seconds, rendered at the original SDR canvas. Not realtime editing, HDR, or native-output validation. Unsupported combinations require the full timeline preview.': '使用原尺寸 SDR 画布渲染，最长 10 秒。这不是实时剪辑、HDR 或原生输出验证。不支持的组合请使用完整时间线预览。',
    'Source checks run in the background. This session preview does not replace full playback, scopes, reference stills or Undo history.': '后台检查源文件。本次会话的片段预览不会替换完整播放、示波器、参考静帧或撤销历史。',
    'Build range preview': '构建片段预览',
    'Wait for the current task to finish or cancel it before building this range.': '请等待当前任务完成或将其取消，再构建此片段。',
    'Rendering the bounded final range…': '正在渲染有界的最终片段…',
    'Checking range dependencies in the background…': '正在后台检查片段依赖…',
    'Building range preview…': '正在构建片段预览…',
    'Checking range preview…': '正在检查片段预览…',
    'Releasing range preview files…': '正在释放片段预览文件…',
    'Range preview unavailable · {error} Use Build full timeline preview.': '片段预览不可用 · {error} 请使用「构建完整时间线预览」。',
    'A source or dependency changed. Build the range again before playback.': '源文件或依赖已改变。请重新构建片段后再播放。',
    'Timeline frames [{start}, {end}) · rendered range only': '时间线帧 [{start}, {end}) · 仅播放已渲染片段',
    'Verified range ready. Press Play to hear the final mix.': '已验证的片段已就绪。点击播放可聆听最终混音。',
    'Range playback paused.': '片段播放已暂停。',
    'Playing rendered range · SDR · final audio mix': '正在播放已渲染片段 · SDR · 最终混音',
    'Range playback finished.': '片段播放已结束。',
    'Range playback unavailable · {error}': '无法播放片段 · {error}',
    'Range preview is stale. Build it again for the current project and playhead.': '片段预览已过期。请为当前工程和播放头重新构建。',
    'Range preview canceled. The project and full timeline preview are unchanged.': '片段预览已取消。工程和完整时间线预览均未改变。',
}

ERROR_ZH_CN = {
    'Unsupported bounded grade frame window.': '不支持该有界调色帧区间。',
    'Range playback supports Rec.709 SDR H.264 only.': '片段播放仅支持 Rec.709 SDR H.264。',
    'Composite motion frame offset must be a nonnegative integer': '合成运动帧偏移必须是非负整数',
    'Composite blur probe timeout must be finite and nonnegative': '合成模糊探测超时必须是有限的非负数',
    'Range source/capability probe exceeded its output budget.': '片段源文件／能力探测超出输出大小限制。',
    'Range preview canceled.': '片段预览已取消。',
    'Range preview timed out.': '片段预览已超时。',
    'Range preview timeout must be positive and finite.': '片段预览超时必须是有限的正数。',
    'Choose a nonempty existing integer frame range.': '请选择时间线内非空的整数帧区间。',
    'Choose a non-empty integer timeline range.': '请选择非空的整数时间线区间。',
    'Range preview supports at most 10 seconds.': '片段预览最长支持 10 秒。',
    'Range preview supports at most 10 seconds and 600 frames.': '片段预览最长支持 10 秒且不超过 600 帧。',
    'Range preview needs an even SDR canvas up to 1920 x 1080.': '片段预览要求不超过 1920 × 1080、宽高均为偶数的 SDR 画布。',
    'Range preview does not support an active video transition.': '片段预览不支持区间内启用的视频转场。',
    'Range preview supports at most 32 intersecting clips.': '片段预览最多支持 32 个与区间相交的片段。',
    'Range preview does not support active stabilization.': '片段预览不支持已启用的防抖。',
    'Range preview supports original SDR input, not Apple Log.': '片段预览仅支持原始 SDR 输入，不支持 Apple Log。',
    'Range picture decoding requires more than 300 seconds of source history.': '该片段的画面解码需要超过 300 秒的源历史。',
    'Range audio requires more than 30 seconds of clip history.': '该片段的音频需要超过 30 秒的片段历史。',
    'Range audio source history exceeds 300 seconds.': '该片段的音频源历史超过 300 秒。',
    'The full audio compiler placement changed.': '完整音频编译器的定位方式已改变。',
    'Range playback output did not match its frame/audio contract.': '片段播放输出不符合其帧数／音频约定。',
    'Project changed before range preparation.': '片段准备前工程已改变。',
    'Project or source dependencies changed during range preparation.': '片段准备期间工程或源依赖已改变。',
    'Range preview exceeded its 1 GiB session-cache budget.': '片段预览超出 1 GiB 会话缓存上限。',
}
# Backend errors retain their exact stable English grammar. Register the known
# fallback-bearing forms too; never scan or translate arbitrary nested details.
_FALLBACK = 'Use Build full timeline preview for this project/range.'
_FALLBACK_ZH = '请为此工程／片段构建完整时间线预览。'
for _reason, _target in tuple(ERROR_ZH_CN.items()):
    ERROR_ZH_CN[_reason + ' ' + _FALLBACK] = _target + ' ' + _FALLBACK_ZH
ERROR_ZH_CN[_FALLBACK] = _FALLBACK_ZH
ERROR_ZH_CN['Cannot prepare range preview: {error} ' + _FALLBACK] = (
    '无法准备片段预览：{error} ' + _FALLBACK_ZH)
ZH_CN.update(ERROR_ZH_CN)
ZH_CN.update({
    'Range position · not playing': '片段位置 · 未播放',
    'Range position · timeline frame {frame}': '片段位置 · 时间线第 {frame} 帧',
})
ZH_CN['Some owned range preview files could not be released: {error} New range builds are disabled for this session; full preview remains available.'] = '部分会话片段预览文件无法释放：{error} 本次会话已停用新的片段构建；完整预览仍可使用。'
ZH_CN['Waiting for the previous range task to stop… Latest request retained.'] = '正在等待上一次片段任务停止… 已保留最新请求。'

ZH_CN.update({
    'Range preview': '片段预览',
    'Set preview In': '设置预览入点',
    'Set preview Out (inclusive)': '设置预览出点（包含当前帧）',
    'Build and loop selection': '构建并循环播放选区',
    'Clear preview selection': '清除预览选区',
    'Session preview · In {start} · Out {end} (inclusive)': '会话预览 · 入点 {start} · 出点 {end}（含末帧）',
    'Set both preview In and Out on the timeline first.': '请先在时间线上设置预览入点和出点。',
    'Preview In must precede Out within the current timeline. Out includes its frame.': '预览入点不得晚于出点，且均须位于当前时间线内。出点包含其所在帧。',
})
