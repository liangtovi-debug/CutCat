# 教程研究转化为工作流设计 / Tutorial-informed workflow design

**2026-10-15 · 仅规划，不是新功能交付。** 基线为独立 Lumen Edit 暂用名工程的 840-test 源码包；本次没有改代码、运行新的产品测试或授权仓库/CI。原始测试结果见 [VALIDATION](VALIDATION.md)。资料阅读方式、版本和可访问性见 [证据台账](TUTORIAL_EVIDENCE_LEDGER.md)。没有声称看完或播放了课程。

## 先校正对标认识

- 剪映中国官网已列出多种蒙版、关键帧、色轮/HSL/曲线、浮点调色、多机位和多时间线。不能把它概括为“只有滤镜”，也不能把我们的贴纸与基础效果当成全面对等。官网功能列表不证明具体版本的处理质量或离线能力。[剪映官网](https://www.capcut.cn/)
- 国际 CapCut 与中国剪映分开记录。CapCut 官方自动字幕帮助明确讨论云处理和网络依赖；这不是“所有剪映 AI 都联网”的证据，也不是客户本地方案可照搬的依据。[CapCut 自动字幕](https://www.capcut.com/help/auto-captions)
- Resolve 的优势启发我们先把可解释的控制、示波器、参考镜头与色彩管线做扎实，而不是先堆节点或轮子。浮点运算不等于完整色彩管理。[官方 Color 页](https://www.blackmagicdesign.com/products/davinciresolve/color)
- 以下验收条件和实现顺序是本项目的设计推论，不是教程逐句要求，也不是竞品逐项实测结论。

## 已有基础与实际缺口

| 范围 | 840-test 基线已实现 | 本文规划，尚未实现 |
|---|---|---|
| 模式/工程 | Simple/Professional 一键切换，共同工程与渲染；v6 保存与旧版迁移；撤销/重做 | 恢复日志、完整关系图、无限轨道、通用嵌套序列 |
| 剪辑/声音 | 整数帧剪切/移动/裁切、吸附、两画面轨与两音乐轨、增益/静音/淡入淡出 | 关系安全 ripple/roll/slip/slide、锚点/组、角色路由、峰值/响度计、多机位 |
| 字幕/本地 AI | 可编辑字幕/SRT、已验证的显式本地 ASR/TTS 适配器、校对差异确认、语义搜索 | 字幕修订与重生成冲突合并、逐词编辑、可靠段落级旁白重生成/重新对齐 |
| 效果/动画 | 原创 PNG/帧动画贴纸、位置/缩放/透明度线性键、blur/vignette 有序旁路 | 跨类型关键帧引擎、缓动/曲线编辑器、蒙版/跟踪、通用变换与转场 |
| 色彩 | 自有 float32 SDR 运算、三组 RGB LGG、曲线/LUT、固定处理顺序、Before/After、明确 SDR 输入转换、缓存图像 histogram/waveform | Resolve 同款算子、第四 Offset 轮、Log/HDR 轮、RGB parade/vectorscope、参考镜头擦除、局部调色、已验证相机 Log/gamut/显示管线 |

之前的真实 AI 推理证据仍保留；本次没有重新下载模型或重新推理。Windows/macOS 原生验收、物理音频与断网冷启动验收仍待完成。

## A. 时间线必须保护关系，而不仅移动矩形

Apple 文档描述主故事线的连接行为、锚点、位置工具与空隙；复合片段可共享父内容，独立新父副本和快照是不同语义。[主故事线](https://support.apple.com/en-ca/guide/final-cut-pro/verb8fcfc133/mac)、[锚点](https://support.apple.com/nl-nl/guide/final-cut-pro/ver7a77ef9e/mac)、[复合复制](https://support.apple.com/en-ie/guide/final-cut-pro/verca9e8d33/mac)、[快照](https://support.apple.com/en-is/guide/final-cut-pro/verbbd3496b/mac)

**设计目标：** 用稳定对象 ID 和显式关系表达画面/原音、B-roll 锚点、标题/字幕、生成旁白、组和复合片段。每次编辑先计算受影响对象，再一次性提交可撤销事务。

- 区分主时间线、连接片段锚点、固定绝对时间对象；定义移动、删除父片段、跨切点、变速和插入时各自如何跟随。不要把所有关系都等同于“同轨同起点”。
- Ripple 明示影响范围、轨锁、同步锁、选区及尾部对象；删除连接父对象时明确保留、重锚或删除策略。拒绝悄悄打破旁白与字幕同步。
- **选择组**只管理选择；**链接媒体**保留原音关系；**复合/嵌套序列**封装结构；**共享复合实例**与**独立复制**必须是不同命令。复制后编辑是否传导必须可见。
- 字幕/贴纸/旁白的时间归属不可随界面模式改变。Simple 可提供清晰默认值，Professional 展开策略，但保存同一语义。

**验收：** 带连接 B-roll、字幕和两句旁白的项目，逐一执行 ripple trim/delete、父片段删除、插入和跨轨移动；检查对象关系和精确帧区间。一次 undo/redo 恢复整个关系图。复制复合实例分别验证“共享更新”与“独立修改”，拒绝循环嵌套和悬空锚点。

## B. 字幕、校对、朗读是一条可修订的数据链

CapCut 的公开桌面指南展示“识别→时间线字幕→编辑”和“文字→生成旁白”工作流；本项目增加本地处理、版本和冲突语义，不推断竞品具有相同实现。[桌面指南](https://www.capcut.com/resource/how-to-use-capcut-on-pc)

- 保存音频来源/范围、语言、识别模型版本、字幕 ID、文本修订和时间修订；人工修改不能被再次 ASR 静默覆盖。
- ASR 重生成给出新增/删除/文字变化/时间变化差异，允许保留人工修订、选择接受或重新分段；批量纠错与样式应用明确作用域。
- TTS 以字幕或脚本修订为来源，保持音色/模型 hash/速率与音频资源的不可变来源记录。文本变更只标记旧音频过期；重生成先试听/比较，再替换选定句子。
- 新语音超长时列出碰撞、时长变化及可选改文/改速/延长策略；不静默截音或移动其他对白。旧媒体仍可撤销恢复。
- 语义搜索继续返回真实字幕与帧区间；未来“插入选中范围”必须建立来源关系，不能把检索分数当正确率或生成新内容。

**验收：** 手改两行字幕后重新识别，修改保留或显式冲突；换音色重生成一句只改变批准的音频；一次 undo 恢复文本、音频、时间和来源记录。断网冷启动走完整闭环。

## C. 共用动画/蒙版模型，明确效果顺序

剪映官网把关键帧、形状/文字/钢笔蒙版及丰富调色列为已有能力；本项目以自己的数据和离线资源实现，不复制受保护模板。[剪映官网](https://www.capcut.cn/)

- 一个参数轨模型服务变换、透明度、效果强度、蒙版与音频；定义帧/局部时间、插值、缓动、外推、复制/粘贴和重置。
- 局部调色从静态几何蒙版开始，明确坐标空间、羽化、反相、合并、参数范围和预乘 alpha；跟踪、变形点动画放在静态数学/边界测试之后。
- 命名、有序处理层先于节点界面；旁路、重置和预设作用域明确。改变效果/蒙版顺序必须产生预期结果并使相关缓存失效。
- 将来节点 UI 仍使用相同处理图与工程格式，不能另写一套只有预览生效的路径。

**验收：** 首帧/末帧/切点与负值边界、透明边缘、缩放后的羽化、乱序效果、旁路及撤销；同一动画在预览/导出/重开后一致。当前只有贴纸的有限线性关键帧，不能宣传通用动画已完成。

## D. 色轮要有真实定义，不能换标签冒充专业

[Resolve 官方 Color 页](https://www.blackmagicdesign.com/products/davinciresolve/color)与[Resolve 20 Colorist Guide](https://documents.blackmagicdesign.com/UserManuals/DaVinci-Resolve-20-Colorist-Guide.pdf)支持以下功能区分；PDF 仅有索引节选可读，访问范围详见台账：

- **Primary 四组：Lift / Gamma / Gain / Offset。** 前三者影响的明暗范围相互重叠，Offset 作用全图。通道条是另一种操作界面，不是额外一套色轮。
- **Log 四组：Shadow / Midtone / Highlight / Offset。** 明暗区间更集中，Low/High Range 调整边界。第四组标签在旧版官方参考手册中核实；不据此宣称新版所有 UI 布局完全相同。
- **HDR 六个默认区域 + Global：Black / Dark / Shadow / Light / Highlight / Specular，加 Global。** 区域可调整；界面分页显示几个轮，不改变默认区域总数。不能写成“五个固定色轮”，也不能把区域名称当成精确数学公式。

**我们的现状：** 三组自有 RGB Lift/Gamma/Gain，线性曝光后执行 `v = (L * 2^linear_exposure + lift) * gain`，再执行 `sign(v) * abs(v)^(1/gamma)`，然后编码回 Rec.709。这是已记录的自有算子，不是 Resolve Primary/Log 的复刻；Offset 未实现，不能把 Exposure 或 legacy code gain 改名成 Offset。详见 [当前运算定义](COLOR_PIPELINE.md)。

**规划约束：** 每个新算子先定义工作空间、数学公式、亮度权重、重叠区与边界、顺序、单位和重置值；若无法核实竞品专有方程，就明确为我们自己的算子，不宣称数值等价。

- 先补可核验的 waveform/RGB parade/vectorscope、单控件重置、参考静帧/擦除和镜头间复制 grade。复制 grade 默认独立；需要联动时单独定义共享 grade 语义。
- LUT 明确区分输入技术转换与创意 look，固定/可变插入点可见。测试“平衡→LUT”与“LUT→平衡”确实不同，禁止未标注的双重变换。
- 明确 source interpretation → working → display/output。Rec.709/sRGB/linear 输入转换已经有，不能扩写为已支持相机 Log、完整 gamut 或校准监看。
- 相机 Log/gamut 仅在厂商资料、合法变换资产、范围/白点/曝光约定及参考图验证后加入。HDR/RAW/时域降噪在高位深输出、色域/色调映射、监看与性能验收之后。

**验收：** 灰阶 ramp/中性 patch 展示每组算子的影响；负值/超范围/身份变换/重复归一化测试；参考镜头与本地 LUT 可重定位；复制后独立修改；预览与导出符合记录的量化/编码误差。

## E. 音频角色先于复杂多机位

Apple 的角色文档以音频组件和角色/子角色表达用途；角色本身不自动等于混音总线。[音频角色](https://support.apple.com/en-gb/guide/final-cut-pro/verb1466e0bb/mac) Ripple 的音频课程只取得描述与章节，因此不能据章节声称已测量其混音结果。

角色表达 Dialogue/Music/Effects 等用途，不等于轨道号或颜色。先做角色分配、可见性/监听、实际峰值与响度计、路由/导出语义，再做多机位。

Apple 文档区分实时切割/切换、已有片段切换以及视频/音频切换方式。[切换模式](https://support.apple.com/en-lk/guide/final-cut-pro/ver23c76c9c/mac)、[角度与切点](https://support.apple.com/en-gb/guide/final-cut-pro/ver23c76d65/mac) 真正读到的 [Ripple 4K Multicam Magic 逐字稿](https://www.rippletraining.com/blog/final-cut-pro-x/4k-multicam-magic/)展示复制源角度、命名近景、重构图和返回时间线切换；不能将这种重构图角度称为新增真实摄影机。

多机位同步后应能锁定一条主音频，只切视频角度；另有明确命令切音频或同时切二者。连接角度、断开同步、变速和展平都需保留源时间映射。当前增益/静音/淡变不等于角色混音器或多机位已实现。

## 推荐顺序（无交期承诺）

1. **仍先完成原生验证门：** Windows/macOS、清洁安装、项目迁移、离线依赖、性能/长路径/恢复；见 [原生验证](NATIVE_VALIDATION_GATE.md)。文档研究不授权远程 CI、仓库、付费资源或发布。
2. **关系安全编辑与修订基础：** 锚点/ripple/原子 undo、字幕和旁白修订；同时明确组/共享复合/独立副本的数据语义。
3. **共同参数系统与可核验声音/色彩：** 通用关键帧、静态蒙版、处理顺序；角色/计量；参考镜头/示波器/LUT 阶段。
4. **更大工程：** 嵌套序列、独立复制、多机位固定音频；经验证的相机 Log/gamut。
5. **后续专业 finishing：** 跟踪、HDR/RAW、时域处理、GPU 加速与专业交换，逐项有测试再宣称。

终局工作流验收样片可用原创 60–90 秒口播+B-roll：离线导入→剪辑→ASR→人工修订→TTS 替换→动画标注→局部调色→两种画幅导出→两平台重开。**这是一组未来验收要求；其中蒙版、关系安全 ripple、多机位等当前未完成。**
