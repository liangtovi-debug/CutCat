"""Track edit locks, independent of playback routing."""
ZH_CN = {
    'Editing locked\nUnlock all affected tracks\nto edit.\nPlayback and export\nare unchanged.': '编辑已锁定\n请先解锁所有\n受影响的轨道再编辑。\n播放和导出\n不受影响。',
    'Lock': '锁定',
    'Locked': '已锁定',
    'Edit lock · {track}': '编辑锁定 · {track}',
    'Track is locked for editing. Activate to unlock; playback and export are unchanged.': '轨道已锁定编辑。激活以解锁；播放和导出不受影响。',
    'Track is editable. Activate to lock editing; playback and export are unchanged.': '轨道可编辑。激活以锁定编辑；播放和导出不受影响。',
    'A selected track is edit-locked. Unlock every affected track to edit; playback and export are unchanged.': '所选轨道已锁定编辑。请解锁所有受影响的轨道后再编辑；播放和导出不受影响。',
    'This source is used by an edit-locked track. Unlock all referencing tracks before replacing it.': '此源素材被已锁定编辑的轨道使用。请先解锁所有引用它的轨道，再替换素材。',
    'The chosen track is edit-locked. Unlock it to insert media.': '目标轨道已锁定编辑。请解锁后再插入素材。',
    'Project changed while relinking. Choose the source again.': '重新链接时工程已更改。请重新选择源素材。',
}
ERROR_ZH_CN = {
    'Version 37 projects require track edit locks': '版本 37 工程必须包含轨道编辑锁定状态',
    'Legacy projects cannot contain track edit locks': '旧版工程不能包含轨道编辑锁定状态',
    'Track lock changes may change only the requested lock state': '轨道锁定操作只能更改请求的锁定状态',
    'Use set_track_edit_lock to change track edit locks': '请使用轨道锁定操作更改轨道编辑锁定状态',
    'Track is edit-locked. Unlock the track before editing it.': '轨道已锁定编辑。请先解锁轨道再编辑。',
    'Unlock the track before changing its groups or transitions': '请先解锁轨道，再更改片段组或转场',
    'Unlock the track before changing its source media': '请先解锁轨道，再更改其源素材',
    'Unlock populated tracks before changing project dimensions or frame rate': '请先解锁含有片段的轨道，再更改工程尺寸或帧率',
    'Track edit lock must be a boolean': '轨道编辑锁定状态必须是布尔值',
    'Track edit locks must be a list of exactly four booleans': '轨道编辑锁定状态必须是包含四个布尔值的列表',
}
ERROR_ZH_CN['This source is used by an edit-locked track. Unlock all referencing tracks before replacing it.'] = ZH_CN['This source is used by an edit-locked track. Unlock all referencing tracks before replacing it.']
ERROR_ZH_CN['Project changed while relinking. Choose the source again.'] = ZH_CN['Project changed while relinking. Choose the source again.']
ZH_CN.update(ERROR_ZH_CN)
EN = {source: source for source in ZH_CN}
