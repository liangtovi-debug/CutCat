# 素材箱与安全整理 / Non-destructive media bins

## 工作流

素材区提供“全部素材”和“未归档”虚拟范围，以及用户创建的嵌套素材箱。虚拟范围不写入工程。单个素材箱显示直属素材；通过树导航子箱，或使用“全部素材”跨箱搜索。素材箱和素材选择以稳定 ID 识别，不依赖列表行号或文件名，因此同名文件不会误操作。

- 创建、重命名、移动素材箱；将所选素材显式移动到目标素材箱。
- 在当前范围按名称／路径搜索，按视频／音频及时间线已用／未用筛选。查询仅使用内存元数据，不在每次输入时读取磁盘或联网。文件缺失标记通过可取消的后台状态检查更新；检查绑定当前工程、素材 ID 和路径，过期结果不会改写新工程。筛选输入不触发磁盘扫描。打开工程时的缺失提醒与显式重新链接检查保留。
- 双击素材箱只切换范围；素材条目可按现有操作加入时间线。筛选隐藏的素材不参与后续选择操作。
- 只能删除空素材箱。只能移除未被任何时间线片段引用的素材条目；静音或停用片段仍算引用。混合已用／未用选择整体拒绝，绝不级联删除片段。
- 整理动作只改变工程元数据，不移动、不覆盖、不删除原始文件。每次有效批量动作只需一次撤销；无变化操作不清空重做。

## 导入与持久化

导入时捕获当前工程会话和目标素材箱；路径规范化与媒体探测在工作线程进行。完成时再次检查会话、请求、取消状态、目标存在性和重复路径。失效结果被拒绝，不会悄悄改投根目录。成功候选作为单一批次提交，保留逐文件失败信息。工程关闭／切换不能让迟到的回调改动另一个工程。

schema35 增加 Project.media_bins（稳定 id、name、parent_id）及 media_bin_ids（素材 ID 到箱 ID；缺项即未归档）。Media 本身、源路径、色彩解释和生成音频来源记录不变。素材箱上限 1024、嵌套深度 16、名称 128 字符。无效父节点、重复 ID、环或悬空成员拒绝加载。版本 1–34 迁移为空组织；旧版本文档伪装注入新字段会拒绝。版本 35 要求显式存储这两个字段。

重新链接保留素材 ID 和归属。整理不改变时间线、转场端点、画中画、音频混合或渲染图。当前预览缓存指纹包含完整工程，所以整理可能保守地使缓存失效，需要重新构建预览；这是性能边界，不是输出变化。

## 竞品对照与限制

Adobe Premiere 提供 [素材箱组织](https://helpx.adobe.com/premiere/desktop/organize-media/file-organization/bins-overview.html) 与 [素材搜索](https://helpx.adobe.com/premiere/desktop/organize-media/file-organization/search-options-in-premiere.html)。DaVinci Resolve 的 [媒体页面](https://www.blackmagicdesign.com/products/davinciresolve/media) 包含手动 bins 和 Smart Bins。本轮只补齐基础手动分层整理、范围搜索和安全清理，不宣称智能素材箱、语义搜索、代理、自动分组、协作锁定或付费版本整体对等。

## English summary

Manual nested bins keep project organization separate from source media. All media/Unfiled are virtual views. A selected bin shows its direct members; use the tree for child bins or All media for cross-bin search. In-memory name/path, type and usage filtering operates within the selected scope; hidden selections cannot accidentally be moved or appended. Bin deletion requires an empty bin; source removal requires every selected source to be unused, counting all timeline clips including muted clips. Original media files remain untouched. Missing-source badges refresh through cancellable background checks bound to the current project, media ID and path. Filter typing does not trigger disk scans; explicit relink and project-open checks remain available.

Imports stage canonicalization/probing off the GUI thread, bind results to the original editor/session/request/destination, reject stale or canceled completion, and atomically commit the accepted unique batch with per-file error reporting. Existing generated-speech/sound validation remains separate and unchanged.

Schema35 persists bounded, validated bin structure and membership, not new media properties. Relinking retains membership by media ID. Output semantics remain unchanged; conservative preview-cache invalidation is currently permitted. Local offscreen Qt and synthetic-media tests do not establish native Windows/macOS package readiness or actual hardware behavior. Matching final evidence is required before calling the source verified.
