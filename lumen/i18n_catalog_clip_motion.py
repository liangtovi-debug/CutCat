"""Bounded clip-motion persistence, evaluation and edit diagnostics."""

ERROR_ZH_CN = {
    'Motion keyframes require typed keyframe records': '画面动画关键帧必须使用有效的关键帧记录',
    'Motion keyframe frame must be a bounded signed integer': '画面动画关键帧帧号必须为有效范围内的有符号整数',
    'Motion interpolation must be hold or linear': '画面动画插值必须为保持或线性',
    'Motion keyframe requires exactly its documented fields': '画面动画关键帧必须且仅包含规定的完整字段',
    'Motion keyframes must be a list of at most 128 entries': '画面动画关键帧必须为最多 128 项的列表',
    'Motion keyframe frames must be strictly increasing and unique': '画面动画关键帧帧号必须严格递增且不能重复',
    'Motion evaluation frame must be an integer or Fraction': '画面动画评估帧号必须为整数或精确分数',
    'Motion evaluation requires at least one keyframe; use the static path': '评估画面动画至少需要一个关键帧；无关键帧时请使用静态画面路径',
    'Motion rebasing requires a clip': '调整画面动画时间原点需要有效的片段',
    'Motion animation delta must be an integer': '画面动画时间偏移必须为整数帧',
    'Motion requires a picture clip on a video track': '画面动画仅支持视频轨上的画面片段',
    'Version 32 clips require motion_keyframes': '版本 32 的片段必须包含画面动画关键帧字段',
    'Legacy clips cannot contain motion_keyframes': '旧版本片段不能包含画面动画关键帧字段',
    'Motion keyframe x must be finite and in -16384..16384': '画面动画关键帧水平位置必须为 -16384 至 16384 的有限数值',
    'Motion keyframe y must be finite and in -16384..16384': '画面动画关键帧垂直位置必须为 -16384 至 16384 的有限数值',
    'Motion keyframe scale must be finite and in 0.1..4': '画面动画关键帧缩放必须为 0.1 至 4 的有限数值',
    'Motion keyframe opacity must be finite and in 0..1': '画面动画关键帧不透明度必须为 0 至 1 的有限数值',
}
