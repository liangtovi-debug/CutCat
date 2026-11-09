# 四轨道持久编辑锁 / Persistent track edit locks

## 本轮边界

四条现有 Clip 轨道的轨道头分别提供编辑锁。鼠标单击或键盘聚焦后按空格切换；锁定状态保存到项目及恢复快照。锁定后仍可选择、查看片段，仍参与播放、分析、预览与导出。不会自动把插入重定向到另一轨道。字幕、贴纸轨道没有假锁按钮。

锁定保护整个片段记录，包括位置、源区间、速度、调色、效果、动画、稳定、音量、静音和淡变。禁止移入、移出、新增、删除，以及影响锁定片段的转场或分组关系变更。混合选择、跨轨分组和会碰到锁定下游片段的波纹操作整笔失败，不会跳过某些片段后部分成功。素材池中被锁定轨道引用的素材记录也不可改动；重新链接须先解锁所有引用该素材的轨道。在已有锁定片段时，项目画幅和帧率改变同样拒绝。

这不是静音、隐藏、同步锁、音画链接或最终画面冻结。素材箱整理、无关素材导入、项目名称、独立字幕、贴纸及主输出增益仍可编辑，可能改变最终画面或混音。其他程序在磁盘上修改源文件或 LUT 不受本功能保护。锁也不是安全或权限边界。

## 历史与格式

Schema 37 的 track_edit_locks 为四个实际布尔值；不接受 0/1、错误长度或缺失字段。版本 1–36 显式迁移为四轨未锁定，并拒绝注入新字段。旧版程序应拒绝打开新版保存格式，不承诺降级读取。

每次有效锁切换是一步撤销；重复相同状态不增加历史、清空重做或提高修订。撤销和重做有意恢复历史项目与当时锁状态，并非阻止历史恢复。打开其他项目、新建及恢复项目是明确的项目边界。

## Implementation contract

The owning-thread-only lock command admits only an exact lock-state-only expected snapshot. Generic callbacks cannot combine unlocking with content mutation. The centralized atomic before/after guard runs before the separate Group/Ungroup membership exception. It compares complete Clip payloads by ID, affected transitions and groups, and full referenced Media records against the originally locked tracks. Failed mutations restore the project without changing undo, redo or revision. Detached timeline previews use equivalent protection; commit remains authoritative.

Source relink checks current references before probing and immediately before replacing the editor. In-flight edits are fenced by current locks and revision/session checks. Playback and renderer behavior do not use edit locks as visibility or mute state.

## 与付费剪辑器对比 / Paid-editor comparison

2026-10-25 核对 Adobe 官方文档：[Track Lock](https://helpx.adobe.com/premiere/desktop/edit-projects/change-clip-sequence/track-lock-to-prevent-changes.html) 的核心是禁止锁定轨道的片段编辑和插入，同时继续预览及导出。本轮补齐这一类别的安全编辑工作流，四轨逐个控制；没有宣称覆盖 Premiere 全部轨道功能或所有付费功能。

[Sync Lock](https://helpx.adobe.com/premiere/desktop/edit-projects/change-clip-sequence/sync-lock-to-prevent-changes.html) 另行决定插入或波纹编辑中其他轨道是否跟随，不能与严格编辑锁混为一谈。本轮不新增同步锁、可变轨道数量、自动音画链接、轨道输出开关或 Shift 批量锁。之后仍需分别比较这些能力及原生平台验证。

## 验收证据与限制

本轮测试目标包括严格格式与全部旧格式迁移；命名编辑接口和通用回调；跨轨来源/目标、分组、转场、波纹下游保护；失败原子性、撤销重做及保存恢复；进行中任务和拖动的过期结果；双语、键盘、焦点及简单/专业布局；锁状态切换前后的编译图、解码图片和 PCM 精确一致；源文件不变。

最终结果只以同目录之外、绑定冻结源码哈希的本轮验收报告为准。局部测试不等于全量通过。Linux Qt offscreen 截图及测试不等于 Windows/macOS 原生安装包、真实读屏软件或硬件验证。本轮不发布远程分支、触发托管 CI、增加依赖或交付原生双平台包。
