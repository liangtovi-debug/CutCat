# 剪辑猫 / CutCat 原生测试准备说明（schema32）

更新：2026-10-24。私有仓库：https://github.com/liangtovi-debug/LumenEdit

## 当前状态：源码已累计，当前原生包尚未验证

当前源码为 schema32，新增画面位置、缩放与不透明度关键帧，并保留静态画中画，并包含窗口动画与可视关键帧时间编辑、H/HSV-S/Y′ 限定器、当前帧调色检查、实验性 Apple Log 1 显式输入和受限的 ProRes 422 HQ 10 位 SDR 导出。它不是旧原生包的功能证明。

最近核实的历史原生 CI 是 [38837683338](https://github.com/liangtovi-debug/LumenEdit/actions/runs/38837683338)，提交 cb47ef721b8d6cc4358aa016c59a67096b338b94（schema9）。该运行成功不代表本次累计源码已经在 Windows/macOS 通过；核查时 artifacts 列表为空，仓库没有 Releases，不能提供有效的当前包下载。旧包哈希与截图只作为历史记录，不用于校验本轮。

本轮先完成私有源码整合和原生验证准备。Windows x64 / macOS 15 arm64 的当前提交测试、打包、重新解压和冻结程序 smoke 必须全部通过，才可交付对应未签名测试包。运行前需核实已有免费 CI 配额；不能把代码授权当成付费授权。源码推送时跳过 CI，不声称已有新的原生结果。

**请只用素材和工程副本测试。旧 schema9 程序不能打开 schema32 工程；不支持降级保存。** 内部 `lumen`、`.lumen` 格式身份和 `LumenEdit-Draft` 程序名保留。新设置默认简体中文，可在界面切换 English；专业/简易切换不应改变工程内容。

以下启动说明适用于未来通过当前提交验证的完整原生测试包，并不表示该包已经存在。

## LUT 怎么用

1. 导入普通 SDR Rec.709 视频并加入时间线，选中片段。
2. 切换「专业」，打开片段的「颜色」页，向下找到「加载 .cube LUT…」。
3. 选择本地3D `.cube` 文件，调整「LUT 强度」0–1；0旁路，1完整应用。「移除 LUT」可取消，并可撤销。
4. 生成预览后导出。保存工程会保留路径与强度；LUT不会嵌入工程，请保留该文件。

支持2–65阶3D .cube，不支持1D、组合shaper或其他LUT格式。混合发生在曲线之后的Rec.709编码RGB中，预览和导出共用评估器。不要把未知相机Log/HDR转换LUT当成普通Rec.709创意LUT。详见 [LUT说明](LUT_WORKFLOW.md)。

## 验证边界

自动界面验收使用Qt offscreen和单独校验的Noto测试字体，字体不在包中。正常界面依赖操作系统已安装的中文字体。不能据此宣称干净Windows/macOS桌面字体回退、中文输入法、所有DPI/显示器或物理音频设备已验收。Windows10/11实机、Intel Mac、Windows ARM和更早macOS尚未验证，最低系统版本未确定。若出现方框或启动问题，请保留截图/系统信息反馈，不要改变安全设置。

## 先准备两个外部依赖

1. FFmpeg 和 ffprobe：包中没有这两个程序。完整测试需要支持 `drawtext`、`zscale`、`gblur`、`vignette`、`rubberband`、`vidstabdetect`、`vidstabtransform` 及 FFV1、libx264、AAC、`prores_ks`。高位深字幕/贴纸还需实际通过 RGB16/透明合成像素能力检查，列出滤镜名称不等于可用。
   - 历史 Windows CI 使用、当前脚本仍固定的是 Gyan FFmpeg 9.0.2 essentials： https://github.com/GyanD/codexffmpeg/releases/tag/9.0.2 。FFmpeg 官方下载入口列有 Windows 提供方：https://ffmpeg.org/download.html 。将下载的完整 FFmpeg 包解压到自己的目录，保留许可证。
   - 历史 Mac CI 使用、当前脚本仍固定的是 Homebrew `ffmpeg@7` 7.1.5 revision 3：https://formulae.brew.sh/formula/ffmpeg@7 。如已安装 Homebrew，可自行运行 `brew install ffmpeg@7`；未安装者请先阅读官方安装说明 https://brew.sh 。安装依赖需要联网，完成后剪辑/渲染不依赖网络。
2. 本地字体：要渲染中文字幕，请在字幕面板的 「字体」/ `Font family` 选填真实存在、覆盖中文的字体名称；必要时在 「字体目录」/ `Font folder` 选择字体目录。默认字体不一定存在。CI 的测试字体不会自动出现在你的电脑上；缺字或字体缺失会报错，不会静默联网下载。

测试包自带 Python/Qt 等基础运行库，不需要另外安装 Python。可选 AI 模型和运行程序没有打包，也不会自动下载。

## Windows 启动

完整解压 ZIP，保留 `LumenEdit-Draft.exe` 旁的 `_internal` 目录，不要只复制 exe。

在 PowerShell 中把下面的示例路径改成自己的实际路径，然后启动：

```powershell
$env:LUMEN_FFMPEG = 'C:\Tools\ffmpeg\bin\ffmpeg.exe'
$env:LUMEN_FFPROBE = 'C:\Tools\ffmpeg\bin\ffprobe.exe'
& $env:LUMEN_FFMPEG -version
& $env:LUMEN_FFPROBE -version
& 'C:\你的解压目录\LumenEdit-Draft\LumenEdit-Draft.exe'
```

这两个变量只影响当前启动环境；如果 FFmpeg/ffprobe 已经位于应用能继承的 PATH，也可直接启动程序。

## Mac 启动

在 Apple Silicon Mac 上解压 tar.gz，保留完整 `.app`，不要拆散内部 Frameworks 或符号链接。建议通过配置好依赖路径的终端启动，Finder 不一定继承 shell 的 PATH。

```sh
export LUMEN_FFMPEG="$(brew --prefix ffmpeg@7)/bin/ffmpeg"
export LUMEN_FFPROBE="$(brew --prefix ffmpeg@7)/bin/ffprobe"
"$LUMEN_FFMPEG" -version
"$LUMEN_FFPROBE" -version
"/你的解压目录/LumenEdit-Draft.app/Contents/MacOS/LumenEdit-Draft"
```

若不是 Homebrew 安装，把两个变量改为实际可执行文件的绝对路径。不要设置为文件夹。

两个包都没有可信发行签名；Mac 未公证，PyInstaller 的本地 ad-hoc 签名不等于开发者认证。若系统阻止运行，请保留提示并反馈，不要关闭安全保护、删除隔离标记或绕过警告。此时可以先审阅源码，正式签名/公证仍是后续发行门槛。

## 首次测试建议（只用素材副本）

1. 导入一段 10–30 秒的普通 SDR Rec.709 视频。
2. 添加到时间线，分割、裁剪、撤销，再保存工程。
3. 一键切换 简易 / 专业（Simple / Professional），确认画面和已有参数保持不变。
4. 调整曝光/曲线，加入原始贴纸，创建字幕并选好本地字体。
5. 生成预览，再导出到新文件；关闭后重开工程，比较时间、声音与画面。
6. 反馈操作步骤、系统版本、芯片/CPU、FFmpeg 版本和报错截图。无需上传私人素材，可用短合成素材复现。

预览需要生成缓存，并非实时 GPU 特效预览。请预留临时磁盘空间。常规导出为 8-bit H.264/AAC；另有受限 ProRes 422 HQ 10 位 SDR（不是 HDR），支持已验证的基础字幕、静态 PNG 贴纸、高斯模糊和暗角。动画贴纸包及不支持的组合会明确拒绝。Apple Log 1 仅为显式、实验性输入并映射到 SDR，只经合成素材验证，不承诺任意 iPhone/HEVC/ProRes 文件。HDR、RAW、OCIO、完整 Log 库、自动窗口跟踪、多机位及完整专业时间线仍未完成。

## AI 与音色限制

这个冻结测试包不包含 sherpa-onnx、ONNX Runtime、tokenizers、模型、eSpeak 字典、whisper.cpp 或 llama.cpp。TTS 和语义搜索不能直接在本包中推理。源码环境中的中文/英文 ASR、TTS、字幕助手、语义搜索证据不等于本包已通过原生推理验收。不要向冻结程序额外 `pip install`，它不会因此自动获得可选运行库。独立本地模型包和原生推理安装流程仍需专门验证。

## 依赖诊断与失败信息

- 缺少 FFmpeg/ffprobe：先在启动程序的同一终端检查上述两个绝对路径及 `-version`；不要填写文件夹，也不要把 AI 组件设置当成媒体工具安装器。
- 缺少滤镜/编码器或 RGB16 能力：保存完整错误和 FFmpeg 版本。源码环境可运行 `python scripts/provision_ffmpeg.py --report build/ffmpeg-provenance.json`，不加 `--install` 只检查已有工具，不下载安装。
- 缺字体：选择本机真实存在并覆盖字幕字符的字体；测试字体不随包分发，不静默下载。
- 原生包失败：保留 `result.json`、构建提交、系统/CPU、依赖报告和复现步骤，不上传私密素材。不要关闭 OS 安全检查来强行启动。

## 文件校验与交付证据

当前没有新的 Windows/macOS 测试包或可用 SHA256。未来每个平台必须附精确提交、测试/跳过明细、依赖清单、解压后冻结程序结果及最终归档 SHA256；不能复用旧包校验值。详见 [原生验收门槛](NATIVE_VALIDATION_GATE.md)。

所有编辑/渲染均在客户本机完成；当前受控助手仅作离线提案，API 设置不启用远程模型，不自动上传素材或调用付费 API。没有公开发布、安装器、自动更新或商业发行许可完成的承诺。
