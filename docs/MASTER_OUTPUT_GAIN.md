# 手动主输出增益 / Manual master output gain

This document describes the independent manual-gain dialog. A separate optional [measured loudness proposal](MEASURED_LOUDNESS_PROPOSAL.md) now starts from a verified current analysis report; it does not change the manual dialog or imply normalization.

本文描述独立的手动主增益对话框。另有可选择的[实测响度增益建议](MEASURED_LOUDNESS_PROPOSAL.md)，从当前已验证的分析结果开始，不改变手动对话框，也不等同于归一化。

## 中文

“时间线 → 主输出增益…”调整整个项目的最终立体声混音，不依赖所选片段；菜单和“键盘快捷键”中的命令均支持中英文，默认不分配快捷键，也不增加时间线工具栏行。

1. 打开“主输出增益”，输入 −96 至 +24 dB。0 dB 保持原混音不变；−96 dB 只是衰减，并非静音。“将草稿重置为 0 dB”也只修改草稿。
2. 点击“应用”才更改项目。取消、Esc 和关闭均丢弃草稿；一次撤销还原本次增益修改。未修改的应用不新增撤销记录。保存与重新打开项目会保留已应用的主增益。
3. 手动“构建预览”并试听。主增益位于各片段增益、静音、时序、淡入淡出和求和之后，编码之前；预览、导出与音频分析使用同一混音路径。片段参数不变。
4. 手动“分析时间线音频”重新测量 LUFS、采样峰值、真峰值估算及采样超限。旧结果在增益变化后失效，不会自动重新分析或构建预览。

增益影响整个混音，不是单片段音量。它不能保证防止削波，不能修复原素材已削波的音频，也不包含自动归一化、响度目标建议、限幅、压缩、主静音或实时电平表。需要只调整一个片段时，继续使用“音频”检查器中的“增益”。编码仍可能改变峰值，编码前测量不是成片达标认证。

对话框打开期间可切换片段或移动播放头，不会改变草稿的项目级范围。项目、编辑会话或修订改变后，旧草稿禁止应用，需关闭再打开；切换项目或退出时自动丢弃草稿。当前渲染或分析任务未结束时可以编辑草稿，但须等待结束或取消任务再应用。对话框打开、编辑、重置和有效性检查不访问源文件或调用解码器。

## English

Timeline → Master output gain… adjusts the final stereo mix for the entire project, regardless of clip selection. The menu and searchable keyboard command are bilingual, unassigned by default, and add no timeline toolbar row.

1. Stage a value from −96 to +24 dB. 0 dB preserves the mix; −96 dB attenuates and is not mute. Reset draft to 0 dB changes only the draft.
2. Apply commits once. Cancel, Escape and window close discard the draft. One Undo restores the previous gain; unchanged Apply adds no undo entry. Applied gain is saved in the project.
3. Explicitly Build preview and listen. Master gain runs after clip gain, mute, timing, fades and summing, before encoding. Preview, export and analysis share this path. Clip settings do not change.
4. Explicitly Analyze timeline audio again. Existing LUFS, sample/estimated true-peak and overload results are invalidated when gain changes. Neither preview nor analysis runs automatically.

Gain affects the entire mix, not one selected clip. It cannot guarantee clipping prevention or repair audio already clipped at its source. There is no automatic normalization, loudness-target suggestion, limiter, compressor, master mute or live meter. Use Gain in the Audio inspector to change just one clip. Encoding can still change peaks; pre-encoding measurements are not encoded-file compliance certification.

Selection and playhead changes do not invalidate this project-wide draft. Project, editor-session and revision changes do, requiring close/reopen. Project switching and application exit discard unapplied drafts. While rendering or analysis is running, the draft remains editable but Apply waits for completion or cancellation. Opening, staging, resetting and checking the dialog perform no source-file inspection or decoding.

## Local UI verification / 本地界面验证

Focused tests cover Apply/Undo/Redo/no-op, repeated Apply, exact loaded gain precision, pending numeric input, Reset-to-zero rounding, Cancel/Escape/close/reopen, empty projects, selection independence, stale sessions/revisions, job blocking, analysis invalidation, bilingual text/search and migration of older custom shortcuts.

Run `python -m pytest tests/test_master_gain_ui.py tests/test_master_gain_ui_dpi.py` and `python scripts/capture_master_gain_ui.py --output <local-evidence-directory> --scale 1.5`. The capture checks actual Chinese/English glyphs, 10/13 pt fonts, normal/narrow dialogs, 1366×768 and 1000×760 workspaces, persistent visible Apply/Cancel controls, wrapped reachable text and no horizontal scrolling. Scale 1 and 1.5 are exercised separately. These offscreen Qt checks and images are not native Windows/macOS packaging, playback or desktop validation.
