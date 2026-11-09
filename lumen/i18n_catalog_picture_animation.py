"""Picture motion editor strings, independently owned from model diagnostics."""
ZH_CN = {
'None': '无',
'Animate picture…': '画面关键帧…',
'Picture animation': '画面动画',
'Picture animation · staged keys': '画面动画 · 待应用关键帧',
'Picture keyframe timeline': '画面关键帧时间轴',
'Picture animation context changed. Close and reopen this editor.': '画面动画上下文已改变，请关闭并重新打开编辑器。',
'Picture keys use clip-local project frames. Position, scale and opacity are absolute. Speed and slip do not retime keys. Hidden keys are retained. Hold or Linear only.': '关键帧使用片段局部的项目帧数。位置、缩放及不透明度为绝对值；变速和滑移不改变关键帧时间。保留隐藏关键帧，仅支持保持或线性插值。',
'No live composite preview here. Apply, then Build Preview to see the moving picture. Rotation, mirrors and transparent outside remain static; audio is unchanged.': '这里不提供实时合成预览。应用后请构建预览查看动画。旋转、镜像及外部透明开关仍为静态；音频不变。',
'Unadded values are discarded when changing frames. Add / replace key before Apply.': '切换帧会丢弃尚未添加的数值。应用前请先添加或替换关键帧。',
'Hidden keys retained (read-only): {frames}': '已保留的隐藏关键帧（只读）：{frames}',
'Make current picture static': '将当前画面设为静态',
'Keep the evaluated position, scale and opacity at this frame and remove ALL keys, including hidden keys? Only Apply changes the project.': '保留当前帧计算后的位置、缩放及不透明度，并移除全部关键帧（包括隐藏关键帧）？仅点击应用后才会修改工程。',
'A key already exists at that frame.': '该帧已有关键帧。',
'Select one video clip to edit picture animation.': '请选择一个视频片段以编辑画面动画。',
'Picture animation editor requires 1 to 2,147,483,648 frames.': '画面动画编辑器支持 1 至 2,147,483,648 帧的片段。',
'Animated values are owned by picture keys. Rotation and mirrors remain static.': '位置、缩放及不透明度由画面关键帧控制，旋转和镜像仍为静态。',
}

from .clip_composite import CLIP_MOTION_ERROR
ZH_CN[CLIP_MOTION_ERROR] = '此 FFmpeg 版本无法以经过验证的 SDR 精度生成画面动画。请安装兼容的 FFmpeg，或将画面动画转换为静态后重置画面合成。本次没有回退为静态首帧或低精度输出。'
