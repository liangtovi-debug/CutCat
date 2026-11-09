
## 新增的可选本地 AI

Local AI 页可显式导入本机的 Qwen GGUF/llama.cpp 和 MiniLM ONNX/tokenizer 文件。校验后，字幕助手给出校对差异，需要你确认 Apply 才改文字；字幕 ID 和时间不变。文字搜索返回工程中真实字幕，点击结果跳到对应帧。模型和运行库不随源码包分发，也不会在应用中自动下载。具体安装、文件校验和资源限制见 `LOCAL_AI_MODELS.md`。

Captions 页现在支持校验已安装的 Kokoro v1.0/v1.1 包，按实际版本显示音色、筛选、收藏和试听。v1.1 有 100 个中文来源音色和 3 个英语来源音色；旧 v1.0 保留 53 个条目。版本条目数不等于经听评确认的独特声线数。见 `LOCAL_TTS.md`。本次真实字幕助手/搜索演示在 `artifacts/local-ai-demo/`，完整回归证据见 `VALIDATION.md`。

## 一键切换简易 / 专业

顶栏 Simple / Professional 两个按钮直接切换，同一工程不丢参数、不改变画面。新用户默认 Simple；高级调色、效果和贴纸动画若已存在，会继续生效并显示提示。切到 Professional 可检查全部已实现的高级设置。专业页可明确指定 Rec.709、sRGB 或线性 Rec.709 输入；不支持的 HDR/色域和冲突标签会报错。相机 Log、局部蒙版与 HDR 并未在这一阶段实现。

演示工程：`artifacts/workspace-color-milestone/shared-project.lumen`；sRGB 归一化：同目录 `srgb-normalized.lumen`。详细保存规则见 `workspace-modes.md`。
