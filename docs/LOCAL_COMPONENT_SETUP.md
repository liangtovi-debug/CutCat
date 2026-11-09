# Local AI component setup / 本地 AI 组件设置

Status: local source milestone, 2026-10-16. Windows/macOS packages have **not** been rebuilt or validated for this change. Existing beauty experiments remain isolated; this does not add automatic face inference.

## Using the dialog

Open **Settings → Local AI component setup**, or the matching button in the Local AI panel. 中文：打开 **设置 → 本地 AI 组件设置**，或本地 AI 面板中的同名按钮。

1. Choose files already present on your computer: whisper.cpp executable and multilingual model; extracted Kokoro pack and its exact version; MiniLM ONNX and tokenizer; llama.cpp executable and Qwen GGUF.
2. Run diagnostics. The default checks regular files, sizes, limited file headers, installed package metadata and required pack entries. Hashing is an explicit optional scan; large files take time. Cancel is available both in the dialog and through the editor's existing task cancellation.
3. Save and apply paths. This updates the existing feature fields and invalidates their old verification. It does not change captions, media, timeline, color settings or undo history. Paths persist in the application configuration directory as `component-setup.json`, not in projects.
4. Use each feature's existing verification/inference workflow. A matching hash or installed package version is **not** proof that a model can run, that a native ABI is compatible, or that output quality is acceptable.

中文操作：选择已在本机的运行程序、模型和分词器；语音包必须选择正确版本。先运行诊断，可按需计算文件哈希；检查后保存并应用路径。路径修改会使原有模型验证失效，仍需使用对应功能的验证流程。哈希匹配和依赖版本匹配不能证明推理正常。

## What the statuses mean

- Not configured / 未配置: select an existing local asset.
- Missing / 缺失: the selected path does not exist.
- Needs attention / 需要处理: invalid format/header, oversized or special file, wrong pinned hash/version, or another explicit error.
- Metadata available / 元数据可用: limited inspection passed; no executable was launched.
- File hash verified / 文件哈希已验证: bytes match a supported pinned identity; inference remains untested.
- Unavailable / 不可用: an optional dependency is absent from the interpreter/build actually running CutCat.

Diagnostics never install packages, fetch models, import optional native inference libraries, execute selected binaries, or send media anywhere. Explicit URL/UNC paths, leaf symlinks and special files are rejected during inspection. This is not an operating-system network sandbox: it cannot prove that an arbitrary mounted filesystem is local, or certify the behavior of a third-party runtime when the user later runs it.

## Runtime and packaging limitations

ASR and subtitle assistance use the explicit whisper.cpp/llama.cpp executable paths. TTS and semantic search use the **running Python interpreter**, or the **running frozen application**. Installing dependencies in an unrelated Python environment does not add them to a frozen app. There is deliberately no nonfunctional “external Python” field.

Expected source-environment dependencies are declared in `pyproject.toml`: TTS uses sherpa-onnx and sherpa-onnx-core 1.12.26; search uses onnxruntime 1.22.1 and tokenizers 0.21.4. This dialog reports missing dependencies, but does not install them. Model redistribution and the Kokoro pack's component licensing remain separate review requirements. No models or optional runtime binaries are included in this source archive.

ASR/media workflows still require FFmpeg/ffprobe, configured using the existing documented environment/PATH mechanism. This dialog does **not** diagnose those media binaries. It does not run a sample inference or certify telemetry behavior of installed third-party packages.

中文限制：语音合成和语义搜索使用当前 Python 或当前打包应用中的依赖，安装到另一个 Python 并不能修复打包应用。此窗口不安装依赖、不下载模型、不运行推理，也不检查 FFmpeg/ffprobe。原有媒体工具要求及模型许可要求仍然适用。

## Safety and state behavior

Diagnostics share the editor's job lifecycle. Closing a dialog cancels its outstanding scan; stale results cannot populate a reopened dialog. Applying paths is blocked while jobs are active or if another panel changed the original fields. Retrying waits for the previous job to finish. Preferences use bounded, versioned JSON and atomic replacement; malformed preferences show an actionable startup status instead of preventing the editor from opening. No credentials, caption text or media content are stored in these preferences.

## Validation

See the separate local validation record for exact test counts, screenshots, environment and limitations. The added tests cover metadata-only behavior, cancellation, identity mismatch, regular-file safety, bounded preferences, model verification invalidation, dialog lifecycle, EN/ZH text, startup restore, and project-state invariance. Synthetic inputs validate diagnostics, not AI inference quality.
