"""Explicit bilingual staged audio-cut dialog; sources and IDs are never translated."""
from .ui_linked_audio_roll import (AUDIO_ROLL_SELECTION, AUDIO_ROLL_SCOPE,
    AUDIO_ROLL_LIMITS, AUDIO_ROLL_STALE)

ZH_CN = {
    'Roll linked audio cut…': '滚动关联音频接点…',
    'Align audio cut to picture': '将音频接点对齐到画面',
    'Apply audio-only roll': '应用仅音频滚动剪辑',
    'Pause playback before audio-only roll.': '请先暂停播放，再进行仅音频滚动剪辑。',
    AUDIO_ROLL_SELECTION: '请准确选择两对完整且相邻的关联音画，并选定其中一个 V 或 A 当前角色。',
    AUDIO_ROLL_SCOPE: '只移动共享的音频接点，两个画面片段保持完全不变。请检查下方四个角色，再一次应用。不会读写源文件，也不会渲染预览。',
    AUDIO_ROLL_LIMITS: '音频淡入淡出保持原长度，并跟随编辑后的边缘。所选源内容和音频输出可能改变。“将音频接点对齐到画面”只暂存重置，仍需点击应用。只有完整的有理数时间映射（包括不足一帧的源尾部）一致时，才恢复严格关联。这不是交叉淡化、音频重叠、外录同步或波纹编辑。',
    AUDIO_ROLL_STALE: '此音频接点预览已过期。请取消后，按当前项目、选区和当前角色重新打开。',
    'Requested audio cut · whole project frame': '请求的音频接点 · 整数项目帧',
    'Timeline bounds: {minimum} to {maximum}; source handles, fades and keys may narrow this range. Picture cut stays at {picture}.': '时间线边界：{minimum} 至 {maximum}；源余量、淡入淡出及关键帧可能进一步缩小范围。画面接点保持在 {picture}。',
    'J cut · incoming audio starts before picture': 'J 剪辑 · 后段音频先于画面开始',
    'L cut · outgoing audio continues after picture': 'L 剪辑 · 前段音频延续到画面切换后',
    'Aligned audio and picture cuts': '音频与画面接点已对齐',
    'Enter a valid audio cut to review J/L alignment.': '请输入有效音频接点，以检查 J/L 对齐关系。',
    'Fixed picture cut: {picture}\nCurrent audio cut: {current}\nRequested audio cut: {requested}\n{kind}': '固定画面接点：{picture}\n当前音频接点：{current}\n请求的音频接点：{requested}\n{kind}',
    '{role} · {name} · {clip_id}\nPicture unchanged: [{start}, {end}) · source [{source_in}, {source_out})': '{role} · {name} · {clip_id}\n画面不变：[{start}, {end}) · 源范围 [{source_in}, {source_out})',
    '{role} · {name} · {clip_id}\nAudio [{old_start}, {old_end}) → [{start}, {end})\nSource [{old_in}, {old_out}) → [{source_in}, {source_out})': '{role} · {name} · {clip_id}\n音频 [{old_start}, {old_end}) → [{start}, {end})\n源范围 [{old_in}, {old_out}) → [{source_in}, {source_out})',
    'Finish the active dialog, popup or text composition before applying the audio cut.': '请先完成当前对话框、弹出菜单或输入法组合，再应用音频接点。',
    'Cannot stage audio-only roll: {error}': '无法暂存仅音频滚动剪辑：{error}',
    'No change is staged. Existing audio edges and link modes will stay unchanged.': '尚未暂存更改。现有音频边缘与关联模式保持不变。',
    'Alignment is staged only. Apply restores strict links only where every source-map field matches.': '对齐操作仅已暂存。应用时，只有全部源映射字段一致的片段对才恢复严格关联。',
    'Audio-only roll is staged. Picture stays fixed; Apply changes both audio roles once.': '仅音频滚动剪辑已暂存。画面保持不动；应用时会一次修改两个音频角色。',
}
ERROR_ZH_CN = {
    'Audio cut must be a whole project frame within the displayed bounds.': '音频接点必须为显示范围内的整数项目帧。',
}
ZH_CN.update(ERROR_ZH_CN)
EN = {source: source for source in ZH_CN}
