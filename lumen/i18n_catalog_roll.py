"""Explicit, non-ripple roll of one immediately adjacent cut."""
ZH_CN = {
    'Roll adjacent cut': '滚动相邻接点',
    'New cut · timeline frame': '新接点 · 时间线帧',
    'Apply roll edit': '应用滚动剪辑',
    'Absolute timeline frame for the selected clip’s outgoing cut, not an offset. Only a touching right clip on the same track can roll.': '输入所选片段出点的绝对时间线帧，不是偏移量。仅支持与同轨右侧紧邻片段滚动接点。',
    'Only these two clips change. Their combined span stays fixed; attached source audio follows. Captions, stickers and separate audio do not move. Existing fades are preserved; edits that cannot fit them are rejected.': '只修改这两个片段，合计时间范围不变；片段自带音频跟随源区间。字幕、贴纸和独立音频不移动。保留已有淡入淡出，无法容纳时拒绝操作。',
    'Right clip: {name}\nCut {old} → {new}; durations {left_duration} / {right_duration} frames. Combined span [{start}, {end}) stays fixed.\nSource ranges: [{left_in}, {left_out}) / [{right_in}, {right_out}).': '右侧片段：{name}\n接点 {old} → {new}；时长 {left_duration} / {right_duration} 帧。合计范围 [{start}, {end}) 不变。\n源区间：[{left_in}, {left_out}) / [{right_in}, {right_out})。',
}

# Joint roll has a separate four-role selection and captured draft; ordinary
# two-clip rolling remains available through the same inspector section.
ZH_CN.update({
    'Select exactly two complete adjacent linked A/V pairs, with no extra clips.': '请仅选择两对完整且相邻的关联音画，不要包含其他片段。',
    'The project, selection or active role changed. Cancel this roll draft and review the current cut again.': '项目、选区或当前角色已变化。请取消此次滚动草稿，重新检查当前接点。',
    'Only these four linked members change. Both tracks share the new cut; outer endpoints, links and other items stay fixed. Fades keep their lengths and follow the edited edge. Audio may change; rebuild preview to see and hear the result.': '只修改这四个关联成员。两条轨道共用新接点；外侧端点、关联和其他项目保持不变。淡入淡出保留时长并跟随修剪边缘。声音可能变化，请重新构建预览查看和试听。',
    'Absolute timeline frame for the shared cut between exactly two complete adjacent linked A/V pairs. Both video members must share one video track and both audio members one audio track.': '输入两对完整且相邻的关联音画之间共用接点的绝对时间线帧。两个视频成员必须同处一条视频轨道，两个音频成员必须同处一条音频轨道。',
    'Apply joint roll edit': '应用音画联合滚动剪辑',
    'Cancel roll draft': '取消滚动草稿',
    'Left V': '左侧视频 V',
    'Left A': '左侧音频 A',
    'Right V': '右侧视频 V',
    'Right A': '右侧音频 A',
    'Joint linked A/V roll · active role: {role}\nCut {old} → {new}; outer span [{start}, {end}) stays fixed.': '关联音画联合滚动 · 当前角色：{role}\n接点 {old} → {new}；外侧范围 [{start}, {end}) 保持不变。',
    '{role} · {name} · {clip_id}\nTimeline [{start}, {end}) · {duration} frames\nSource [{source_in}, {source_out})': '{role} · {name} · {clip_id}\n时间线 [{start}, {end}) · {duration} 帧\n源区间 [{source_in}, {source_out})',
    'Two linked A/V pairs · shared-cut roll only': '两对关联音画 · 仅编辑共用接点',
})
