# 从这里开始

这是独立的新剪辑软件工程，临时工作名为 **Lumen Edit**，没有改动 StableFlowPro。

当前交付的是可以实际运行、剪辑和导出的视频编辑原型。它还不是功能齐全的商用成品，也没有证据证明体验已经超过剪映或 Final Cut Pro。

## 已经跑通的闭环

导入本地视频/音频 → 添加到四轨时间线 → 分割、移动、裁切 → 调整颜色与声音 → 渲染预览 → 保存/重开工程 → 导出 MP4。

- 两条画面轨，保留素材原音；两条独立音乐轨
- 按整数帧编辑、边缘吸附、撤销/重做
- 颜色参数、3D Cube LUT、RGB 直方图和亮度波形
- 音量、静音、淡入淡出
- 相对路径工程、缺失素材重链接、原文件保护
- 可取消的实际渲染，预览和导出使用同一个处理图

画面轨上层遮盖下层，不是带透明度、变换和关键帧的完整合成系统。已有 float32 SDR 调色运算：线性曝光、RGB Lift/Gamma/Gain 色轮、主曲线和 RGB 曲线。解码与中间传输为 16-bit RGB，最终交付仍为 8-bit H.264。RGB gain 保留编码值增益含义；真正线性曝光请使用 Linear exposure。当前没有 OCIO/HDR 或校准监看。

## 运行

需要 Python 3.12，以及单独安装、可调用的 FFmpeg 和 ffprobe。请按照根目录 README 的 Windows/macOS 步骤安装依赖。

在项目目录执行：

```sh
python -m venv .venv
```

Windows PowerShell：

```powershell
.venv\Scripts\python.exe -m pip install -e ".[test,build]"
.venv\Scripts\python.exe -m lumen
```

macOS：

```sh
.venv/bin/python -m pip install -e ".[test,build]"
.venv/bin/python -m lumen
```

界面目前为英文。依次使用 Import → Add to selected track → 时间线选中片段 → Inspector 修改 → Build preview → Export film。

修改工程后，需要重新 Build preview，才能观看最新合成和听到最新混音。当前不是实时 GPU 渲染器。

## 可直接打开的示例

打开 `artifacts/demo-verified/first-cut.lumen`。素材均为程序生成的测试画面与音调，随工程存放。`first-cut.mp4` 是这个工程真实导出的结果。

## 验证与下一阶段

本地 Linux 环境完成调色升级后的自动化回归测试，另做了 30 分钟合成素材的帧数和末尾音频同步检查。详情见 `VALIDATION.md`。

Windows 和 macOS 的打包脚本、CI 配置已经准备好，但还没有在这两个系统上实际构建和验收，所以当前不提供已验证的 EXE/APP 安装包。

转场、视频片段/效果的通用高级关键帧、代理、复杂音频混音器、多机位、蒙版/跟踪、OCIO/HDR 和专业交换格式等都在 `ROADMAP.md`，目前没有把这些做成虚假的可点击按钮。下一步首先需要原生双平台验收，再逐步完善编辑和专业色彩能力。

调色示例：打开 artifacts/color-milestone/float-color.lumen。Inspector 的 Basic/Wheels/Curves 分页均连接实际渲染；Before 只影响监看，不会改变工程或导出。色轮/曲线拖动释放后只生成一步撤销。

## 字幕与本地识别

Captions 分页现在可以编辑字幕文字、起止帧和语言，导入/导出 SRT，并通过真实渲染烧录进视频。字幕与视频片段独立，移动视频不会自动移动字幕。中文需选择已安装、含中文字形的字体。

本地识别需自行准备可信 whisper.cpp 运行程序和多语言模型，在界面明确选择。程序不会下载模型或上传音频。模型真实测试识别了英语和中文，但中文有误识别，必须人工校对。字幕朗读已实现实验性本地 Kokoro 中英文生成，可保存新 WAV 并添加到独立音轨；过长和重叠会明确提示，不自动截断。需显式安装可选依赖 `python -m pip install -e ".[tts]"`，并导入现有完整模型包。运行库和模型不在源码包中，组件许可证与双平台打包仍待审核。详见 LOCAL_TTS.md。详情见 LOCAL_ASR.md。

## 本地特效与贴纸

Effects & stickers 分页可以添加原创贴纸或明确选择本地PNG/JSON帧包。贴纸单独占用时间线显示行，设置起始帧/时长/图层顺序。默认显示位置、缩放、透明度；勾选Advanced再编辑线性关键帧。Clip effects用于选中画面片段的高斯模糊与暗角，顺序、开关和数值都会改变真实渲染。修改后需要Build preview。

完整示例：`artifacts/effects-demo/project.lumen`。PNG格式/尺寸/资源上限和颜色边界见STICKER_ASSETS.md及OVERLAY_RENDER.md。当前不是全功能素材商城或实时GPU效果系统。
