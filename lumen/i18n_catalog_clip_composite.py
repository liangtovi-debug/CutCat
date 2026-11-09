"""Static PIP controls and strict schema diagnostics."""
from .clip_composite import CLIP_COMPOSITE_ERROR

ERROR_ZH_CN = {
    'Composite filter threads must be 1, 2, or 4': '画面合成滤镜线程数必须为 1、2 或 4',
    'This FFmpeg build cannot apply verified Gaussian blur to 8-bit picture-in-picture clips. Install a compatible FFmpeg build, disable clip blur, or reset clip compositing.': '此 FFmpeg 版本无法对 8 位画中画片段应用经过验证的高斯模糊。请安装兼容的 FFmpeg 版本、关闭片段模糊，或重置画面合成。',
    'Render timed out checking picture-in-picture blur support.': '检查画中画模糊支持时渲染超时。',
    'Picture-in-picture blur capability probe exceeded its output budget.': '画中画模糊能力检查的输出超出安全限额。',
    CLIP_COMPOSITE_ERROR: '此 FFmpeg 版本无法以经过验证的 SDR 精度合成变换后的片段。请安装支持 zscale 和原生全色度透明通道的兼容 FFmpeg 版本，或将片段不透明度重置为 100% 并关闭变换范围外透明。',
    'Render timed out checking clip compositing support.': '检查画面合成支持时渲染超时。',
    'Clip compositing capability probe exceeded its output budget.': '画面合成能力检查的输出超出安全限额。',
    'Composite probe timeout must be finite and nonnegative': '画面合成检查的超时时长必须为非负有限数值',
    'Compositing requires a picture clip on a video track': '画面合成仅支持视频轨上的画面片段',
    'Version 31 clips require compositing settings': '版本 31 的片段必须包含画面合成设置',
    'Legacy clips cannot contain compositing settings': '旧版本片段不能包含画面合成设置',
    'Invalid clip composite': '画面合成设置无效',
    'Composite requires exactly transparent_outside_transform and opacity': '画面合成必须且仅包含变换外部透明开关和不透明度',
    'Composite transparent_outside_transform must be a boolean': '变换外部透明开关必须为布尔值',
    'Composite opacity must be finite and in 0..1': '画面不透明度必须为 0 至 1 的有限数值',
    'Composite canvas dimensions must be integers in 1..16384': '合成画布尺寸必须为 1 至 16384 的整数',
    'Composite high_bit must be a boolean': '高位深合成开关必须为布尔值',
    'Composite canvas must be even for its output chroma subsampling': '合成画布尺寸必须符合输出色度采样的偶数要求',
}
ZH_CN = {
    'Transparent outside transform': '变换范围外透明',
    'Reveal lower video outside transformed canvas': '变换画布外透明，显示下层视频',
    'Picture opacity': '画面不透明度',
    'Apply compositing': '应用画面合成',
    'Reset compositing': '重置画面合成',
    'Static, centered on the fitted canvas. X/Y move in project pixels; positive rotation is clockwise. Outside is black, not transparent by default. Enable compositing below to reveal lower video. Build preview to see changes.': '静态变换，以适配后的画布中心为基准。位移以项目像素计，正角度顺时针旋转。外部默认填黑、不透明。启用下方合成开关可显示下层视频。构建预览后查看。',
    'Transparency reveals only the transformed canvas exterior, not fit/pad black bars; source alpha is ignored. Opacity affects the whole picture, including at zero; audio stays unchanged.': '透明开关仅显示变换画布外的下层画面，不穿透适配黑边；忽略素材自身的透明通道。不透明度影响整幅画面，设为零也不改变音频。',
    'Transform and compositing apply separately. Applying or resetting either refreshes other unapplied values.': '画面变换与合成分别应用。应用或重置任一项后，其他尚未应用的数值将刷新。',
    **ERROR_ZH_CN,
}
