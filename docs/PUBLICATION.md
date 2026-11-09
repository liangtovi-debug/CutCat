# 源码发布说明 / Source publication notes

## 中文

本仓库是 CutCat 的独立源码快照，建立了新的提交历史。原私有开发仓库保持私有，没有迁移其 Git 历史、问题、拉取请求、Actions 日志、构建产物或用户素材。公开提交使用 GitHub noreply 身份。已有内部包名 `lumen`、命令和环境变量保留，以避免仅为改名改变程序行为。

此次公开内容以已检查的当前源码为基础。除项目介绍、发布及许可文档外，仅同步更新了一处贴纸许可元数据测试断言，使其检查新的项目许可引用；程序、媒体素材字节、功能断言及工作流定义未改变。另仅从一份第三方 CC0 音效声明中移除了作者联系邮箱，保留作者、来源和完整许可声明，并同步更新证据哈希和脱敏说明；音频内容不变。项目原创部分适用仓库根目录 [LICENSE](../LICENSE)，独立许可的材料继续适用其既有许可。特别是 CC0 音效与目录和 Apache-2.0 的 Apple Log 代码不受新增禁止售卖条款约束。

这不是安装包发行或完整测试通过声明。历史测试记录与旧文档只能证明其对应版本。最近一次原私有仓库完整 Windows 回归未通过；本次发布没有重新构建或宣称解决该问题。Windows/macOS 安装、播放、签名、公证以及实际打包依赖的许可证合规仍是单独事项。

公开仓库的 GitHub Actions 保持禁用。`scripts/fetch_contrast_v25_archive.py` 和部分完整测试依赖私有历史 v25 基线；该基线没有随此次发布，也没有通过放宽断言或替换预期结果来隐藏这一限制。应用源码启动本身不需要该历史归档。源码中的旧私有 CI 链接可能无法公开访问；它们不授权访问私有资料。

按照项目所有者的要求，此次发布后停止开发和测试。不设置公开仓库与私有仓库的自动同步，不继续构建或后台修复。未来若重新授权更新，应只导出重新审查过的文件快照、使用 noreply 提交并保留第三方声明，不能合并或推送原私有提交历史。

## English

This repository is an independent CutCat source snapshot with a fresh commit history. The original development repository remains private. Its Git history, issues, pull requests, Actions logs, build artifacts and user media have not been migrated. Public commits use GitHub noreply identities. Existing internal `lumen` package names, commands and environment variables are retained to avoid changing program behaviour merely for branding.

This publication is based on the reviewed current source. Besides project introduction, publication and licensing documents, one sticker-licence metadata assertion was updated to check the new project-licence reference. Application code, media asset bytes, functional assertions and workflow definitions are unchanged. Additionally, only the author contact email was omitted from one third-party CC0 sound notice, while retaining the author, source and full licence statements. Corresponding evidence hashes and redaction notes were updated; audio content is unchanged. The original project material is covered by the root [LICENSE](../LICENSE), while separately licensed material retains its existing terms. In particular, CC0 sounds and catalogues and the Apache-2.0 Apple Log code are not subject to the new no-sale restriction.

This is not an installer release or a claim of a fully passing test suite. Historical receipts and documents apply only to their corresponding versions. The latest full Windows regression in the original private repository did not pass; this publication neither rebuilds nor claims to fix it. Native Windows/macOS installation, playback, signing, notarization and compliance of actually bundled dependencies remain separate matters.

GitHub Actions remains disabled in the public repository. `scripts/fetch_contrast_v25_archive.py` and some full-regression tests require a private historical v25 baseline. That baseline is not included, and assertions or expected outputs have not been weakened or substituted to hide the limitation. Starting the application from source does not require that historical archive. Old private CI links in the source may be inaccessible to public readers; they do not grant access to private material.

At the project owner's request, development and testing stop after this publication. No automatic synchronization, ongoing builds or background fixes are enabled. If updates are authorized again later, export only newly reviewed file snapshots, commit with a noreply identity and retain third-party notices. Do not merge or push the original private commit history.
