"""Single captured interior gap closure; user IDs and source text stay verbatim."""
GAP_CLOSE_HELP = 'Timeline → Close one gap is separate from deleting a clip. Explicitly choose a target track, capture the playhead inside a gap between adjacent real clips, then Preview and Apply. The capture stays fixed when the live playhead or clip selection changes; changing track requires a fresh capture. It closes exactly one interior gap, not leading/trailing space or all gaps, and deletes zero clips. The target track participates; other tracks, captions and stickers are optional for this operation. Any participating intersection blocks, including wholly contained items. Unchecked items remain fixed and may lose synchronization. Every moved audible contribution must preserve local compilation and one common exact sample displacement, even on one track. Changed locks, groups and moving transition endpoints reject. No persistent Sync Lock, linked A/V, arbitrary range extraction or automatic caption-to-speech following. One atomic edit and one Undo; total extent includes fixed anchors.'
ZH_CN = {
    'Close one gap…': '关闭单个空隙…',
    'Close one gap': '关闭单个空隙',
    'Target gap track': '空隙目标轨道',
    'Choose gap track…': '选择空隙所在轨道…',
    'Capture playhead': '捕获播放头',
    'Close one interior gap on an explicit target track. Only the empty interval between two adjacent real clips is removed; no clip is deleted. Leading or trailing empty space is not a gap for this tool.': '关闭明确目标轨道上的一个内部空隙。仅移除两个相邻真实片段之间的空白区间，不删除任何片段。此工具不处理轨道开头或末尾的空白。',
    'Capture freezes the playhead for this operation. Moving the live playhead or selecting clips does not retarget it. Changing the target track clears the capture; capturing again clears the preview. Staged inspector timing is not used.': '捕获后，本次操作的播放头目标保持固定。移动实时播放头或选择片段不会改变目标。更换目标轨道会清除捕获；重新捕获会清除预览。不使用检查器中尚未应用的时间草稿。',
    'The target gap track always participates; other tracks are optional.': '空隙目标轨道必须参与；其他轨道可选。',
    'Participants ending at or before the gap start stay fixed. Participants starting at or after the gap end move left by the gap length. Any intersection blocks the edit, including an item wholly inside the gap.': '结束于空隙起点或之前的参与项保持不变。开始于空隙终点或之后的参与项按空隙长度向左移动。任何相交都会阻止编辑，包括完全位于空隙内的项目。',
    'Every moved audible clip must retain its full local audio compilation and the same exact integer sample displacement, even on one track. Changed locked tracks, affected groups and moving transition endpoints block the edit.': '每个移动的有声片段都必须保持完整局部音频编译结果，并采用同一个精确整数采样位移，即使只操作一条轨道也如此。需要更改的已锁定轨道、涉及片段组或移动转场端点时会阻止编辑。',
    'Project changed. Close and reopen Close one gap before applying.': '项目已变化。请关闭并重新打开“关闭单个空隙”后再应用。',
    'Wait for the current task to finish or cancel it before capturing, previewing or closing a gap.': '请等待当前任务结束或取消后，再捕获、预览或关闭空隙。',
    'Preview ready. Apply closes only this captured gap in one undoable edit. Deleted clips: 0.': '预览已就绪。“应用”仅关闭捕获的这一个空隙，整次编辑可一次撤销。删除片段：0。',
    'Choose a target track, capture the playhead inside an interior gap, then Preview before Apply.': '选择目标轨道，在内部空隙中捕获播放头，然后先预览再应用。',
    'No captured target. Choose the gap track explicitly; the media import track and selected clips are not used.': '尚未捕获目标。请明确选择空隙所在轨道；不使用素材导入轨道或所选片段。',
    'Captured target · clip track {track} · playhead frame {playhead}.': '已捕获目标 · 片段轨道 {track} · 播放头第 {playhead} 帧。',
    'Gap close blocked:\n{error}': '关闭空隙被阻止：\n{error}',
    'Gap [{start}, {end}) · {duration} frames\nLeft clip: {left}\nRight clip: {right}\nDeleted clips: 0': '空隙 [{start}, {end}) · {duration} 帧\n左侧片段：{left}\n右侧片段：{right}\n删除片段：0',
    GAP_CLOSE_HELP: '“时间线 → 关闭单个空隙”与删除片段是独立操作。明确选择目标轨道，在两个相邻真实片段之间的空隙内捕获播放头，然后预览并应用。实时播放头或片段选择变化不会改变捕获目标；更换轨道必须重新捕获。仅关闭一个内部空隙，不处理首尾空白或全部空隙，删除片段数量为零。目标轨道必须参与；其他轨道、字幕和贴纸仅在本次操作中可选参与。任何参与项与空隙相交都会阻止编辑，包括完全位于其中的项目。未勾选项目保持固定，可能失去同步。每个移动的有声项目都必须保持局部编译结果和同一个精确采样位移，即使只操作一条轨道也如此。需要更改的锁定轨道、片段组和移动转场端点会被拒绝。不包含持久同步锁、关联音画、任意区间提取或字幕自动带动语音。一次原子编辑，一次撤销；总时长包含固定锚点。',
}
ERROR_ZH_CN = {
    'No interior gap at the captured playhead on this track; choose the space between two clips': '此轨道的捕获播放头处没有内部空隙；请选择两个片段之间的空白',
    'Gap close participant intersects the gap; leave it fixed or choose another gap': '关闭空隙的参与项与空隙相交；请将其保持固定，或选择其他空隙',
    'Gap close would change relative audio synchronization at this sample-grid phase; choose another gap or project frame': '在此采样网格相位，关闭空隙会改变音频相对同步；请选择其他空隙或项目帧',
    'Invalid gap close plan': '关闭空隙计划无效',
    'Gap close plan is stale; capture and preview the current gap again': '关闭空隙计划已过期；请重新捕获并预览当前空隙',
    'Gap close plan was changed; capture and preview the current gap again': '关闭空隙计划已被更改；请重新捕获并预览当前空隙',
    'Gap close participating tracks must be a nonempty tuple': '关闭空隙的参与轨道必须为非空元组',
    'Gap close participating tracks must be unique and include the target track': '关闭空隙的参与轨道不得重复，且必须包含目标轨道',
    'Invalid gap target track': '空隙目标轨道无效',
    'Invalid captured gap playhead': '捕获的空隙播放头无效',
    'Invalid gap participating track': '关闭空隙的参与轨道无效',
    'Invalid gap follow captions': '关闭空隙的字幕跟随选项无效',
    'Invalid gap follow stickers': '关闭空隙的贴纸跟随选项无效',
    'Gap close cannot prove the local audio compiler contract': '关闭空隙无法证明局部音频编译结果保持不变',
    'Gap close would change downstream audio at this sample-grid phase; choose another edit interval or project frame': '在此采样网格相位，关闭空隙会改变后方音频；请选择其他编辑区间或项目帧',
}
ZH_CN.update(ERROR_ZH_CN)
EN = {source: source for source in ZH_CN}
