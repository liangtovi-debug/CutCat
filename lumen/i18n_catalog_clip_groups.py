"""Bilingual persistent clip-group actions, affordances and scope."""
ZH_CN = {
    'Group clips': '片段编组',
    'Ungroup clips': '取消片段编组',
    'G{number}': '组{number}',
    'Group {number} · {count} clips. Ungroup to edit separately.': '编组 {number} · {count} 个片段。取消编组后可单独编辑。',
    'Grouped clips cannot be trimmed separately. Choose Edit → Ungroup clips first.': '编组片段不能单独修剪。请先选择“编辑 → 取消片段编组”。',
    'Selected {count} clips · Grouped\nUngroup to edit separately': '已选 {count} 个片段 · 含编组\n取消编组后可单独编辑',
    'Persistent groups select, move and delete together. Ungroup before trimming or editing one member.': '持久编组支持整体选择、移动和删除。修剪或单独编辑片段前，请先取消编组。',
    'Timeline clips': '时间线片段',
    '{groups} persistent groups; {selected} clips selected. Grouped clips move and delete together. Ungroup to edit separately.': '{groups} 个持久编组；已选 {selected} 个片段。编组片段整体移动和删除。取消编组后可单独编辑。',
    'Edit → Group clips saves a group of two or more ungrouped timeline clips. Click any member to select the whole group; Shift-click toggles the whole group. Move or delete complete groups together. Edit → Ungroup clips restores separate editing. Ungroup before trimming, splitting or changing one member. Captions and stickers are not group members; nested groups, merging groups and temporary group bypass are not supported.': '“编辑 → 片段编组”可将两个及以上尚未编组的时间线片段保存为一组。点击任一成员即可选择整组；按住 Shift 点击可切换整组选择。编组支持整体移动或删除。“编辑 → 取消片段编组”可恢复单独编辑。修剪、分割或修改单个成员前，请先取消编组。字幕和贴纸不属于片段编组；暂不支持嵌套编组、合并已有编组或临时绕过编组。',
}
EN = {source: source for source in ZH_CN}
