# RGB16 径向暗角：支持范围与验证

本轮让原有暗角进入 ProRes 422 HQ 10 位 SDR 导出，不新增第二套暗角，也不改写项目参数。schema27 不变。现有强度 0–1、开关和有序特效列表继续使用；0 和关闭是精确旁路。高斯模糊和暗角可任意排序或重复。未知效果与动画贴纸包仍不在验证范围内。

## 实际处理阶段

源解码／空间修复 → RGB16/float32 调色与 LUT → RGB16 项目画布适配 → 有序 RGB16 模糊／暗角 → 片段变换 → 明确 Rec.709 限幅 YUV422P10 转换 → 10 位视频合成 → 静态贴纸／基础字幕 → ProRes。

暗角以项目画布、方形像素为基准，位于变换前；缩放或移动片段会一并移动已有暗角，不能误称最终输出框固定暗角。贴纸和字幕不经过片段暗角。当前帧调色检查仍位于特效前，并非最终合成预览。

预览及 H.264 继续原版 8 位 FFmpeg vignette。高位深效果遵循相同径向几何，但 RGB16 编码值乘法和舍入不同，不保证与旧版 YUV/8 位画面逐像素一致。可用最终 ProRes 文件核对，不声称预览是校准后的高位深监视器。

## 数学与真实位深

FFmpeg n7.1 的原版 vignette 仅接受 8 位格式，不能通过放宽验证后先降 8 位、再扩 10 位冒充高位深。本轮使用支持原生 gbrp16le 的 geq，每通道以双精度计算系数并乘以原生 16 位样本，存储为 uint16。滤镜自动格式转换被禁止。

- angle = amount × π/2
- dx = trunc(x − W/2)，dy = trunc(y − H/2)
- gain = cos(angle × sqrt(dx² + dy²) / sqrt((W/2)² + (H/2)²))⁴
- 输出为 floor(输入 × gain)

带符号偏移向零截断是原版整数偏移行为，因此奇数画布与浮点半像素中心不同。使用 nearest 显式读取整数坐标，避免边缘插值差异。这里是在伽马编码 SDR RGB 数值上变暗，并非线性光运算、曝光补偿、RAW/Log、ICC、OCIO 或 HDR。

每次有效导出重新检查 FFmpeg：偶数、奇数和窄画布中的完整像素响应及低位精度。检查失败、超时、输出预算超限或取消均停止，不静默回退。模糊的保守 CPU 标志只在存在有效模糊时启用；仅暗角不引入该标志。实际导出仍明确拒绝奇数宽高，内部奇数数学测试不是放开封装尺寸限制。

## 性能与内存

geq 逐像素解释表达式，速度明显慢于原有 8 位暗角。Linux 本机单线程、单帧 1080p 样本约 1.18 秒（包含进程启动），无效果约 0.11 秒，原版 8 位暗角约 0.13 秒。数据是本机样本，不是 Windows/macOS 或所有素材保证，也不是实时效果。多帧实测和环境信息见同轮证据。

处理以 FFmpeg 帧流进行，不把整部视频加载到 Python 内存；原有缓存、导出超时、取消与临时文件清理机制继续生效。暗角参数不污染调色源缓存，也不复用未验证的能力结果。

## 竞品比较与下一步

- [Adobe Lumetri 官方说明](https://helpx.adobe.com/premiere/desktop/add-video-effects/commonly-used-effects/create-vignettes-using-lumetri-color.html)：Amount、Midpoint、Roundness、Feather 独立控制，暗角在同一 Lumetri 的其余校色后应用。
- [Resolve 20 官方调色指南](https://documents.blackmagicdesign.com/UserManuals/DaVinci-Resolve-20-Colorist-Guide.pdf)，印刷页 81–82：圆形 Power Window 可调整位置、大小、柔边和反选，再降低 Gamma，以避免显著压暗亮天空；也提供独立 Vignette FX。
- [Resolve Color 官方页面](https://www.blackmagicdesign.com/products/davinciresolve/color)：局部窗口与跟踪、节点和色彩管理远超本轮范围。

本轮只补齐已有暗角在高位深 SDR 出口的精度缺口，不等于竞品功能对等。下一步更有价值的控制是 midpoint/radius 与 feather，再考虑 roundness、中心位置及高光保护。当前没有新增独立形状或柔边控制、跟踪或通用特效关键帧。

## 验证边界

独立标量 oracle 验证全像素、最末行列、1×1、奇偶／窄画布、极小／最大强度、多次效果顺序和 RGB16 低位；刻意 8 位量化的负对照必须失败。进一步验证实际 ProRes 解码像素、调色与变换顺序、透明贴纸和字幕共存、失败早于缓存生成、取消／超时／预算清理、H.264 历史一致性和中英 UI 状态恢复。

最终全量结果与重新解压验收以同轮验收说明和终结日志为准。源码／Linux offscreen 验证不等于 Windows/macOS 原生构建、签名、公证或安装测试；未据此声明远程 main 已更新。
