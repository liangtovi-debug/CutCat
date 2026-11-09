"""Chinese UI-boundary error translations; placeholder values remain untouched.

Keys are exact developer-authored messages or explicit format templates, never
substitutions applied to user captions, paths, model data, or diagnostics.
"""

ZH_CN = {
    'Nested editor transactions are not supported': '不支持嵌套的编辑器事务',
    'Clip IDs must be a collection of unique strings': '片段 ID 必须是不重复的字符串集合',
    'Clip groups must be a list of at most 10000 entries': '片段编组必须为最多含 10000 项的列表',
    'Clip groups require 2..20000 unique clips': '片段编组必须包含 2 至 20000 个互不重复的片段',
    'Clip groups must be a list': '片段编组必须为列表',
    'Invalid clip group': '片段编组无效',
    'Duplicate clip group id': '片段编组标识重复',
    'Clip groups require at least two unique clips': '片段编组至少需要两个互不重复的片段',
    'Clip groups may contain only existing timeline clips': '片段编组只能包含现有时间线片段',
    'Duplicate clip group member': '片段编组成员重复',
    'A clip may belong to only one clip group': '一个片段只能属于一个编组',
    'Version 36 projects require a clip groups list': '版本 36 项目必须包含片段编组列表',
    'Invalid clip group fields': '片段编组字段无效',
    'Legacy projects cannot contain clip groups': '旧版项目不能包含片段编组',
    'Group/Ungroup may change only clip group membership': '编组或取消编组只能改变片段的编组关系',
    'Select at least two ungrouped clips to group': '请至少选择两个尚未编组的片段进行编组',
    'Ungroup existing groups before creating a new group': '创建新编组前，请先取消已有编组',
    'Ungroup clips before deleting only part of a group': '删除编组中的部分片段前，请先取消编组',
    'Ungroup clips before changing their source, timing or track': '更改编组片段的源范围、时间或轨道前，请先取消编组',
    'Ungroup clips before moving only part of a group': '移动编组中的部分片段前，请先取消编组',
    'Use Group/Ungroup to change clip group membership': '请使用“片段编组”或“取消片段编组”更改编组关系',
    'Ungroup clips first, or select every member of the group': '请先取消编组，或选择该编组的所有成员',
    'Ungroup affected clips before ripple deletion': '波纹删除前，请先取消受影响片段的编组',

    'Invalid timeline duration': '时间线时长无效',
    'Animated windows require evaluate_grade at an explicit clip-relative timeline frame.': '动画窗口须在明确的片段内时间线帧位置计算调色。',
    'Animated windows require evaluate_window at an explicit clip-relative timeline frame': '动画窗口须在明确的片段内时间线帧位置计算形状',
    'Window evaluation frame must be an integer or Fraction': '窗口计算帧号必须是整数或精确分数',
    'Window evaluation requires a grade': '窗口计算需要有效调色设置',
    'Window rebasing requires a grade': '窗口时间重定位需要有效调色设置',
    'Window animation delta must be an integer': '窗口动画的帧偏移必须是整数',
    'Window keyframes require typed keyframe records': '窗口关键帧必须使用有效的关键帧记录',
    'Window keyframe frame must be a bounded signed integer': '窗口关键帧帧号必须是允许范围内的有符号整数',
    'Window keyframe {name} must be finite and in {low}..{high}': '窗口关键帧 {name} 必须为 {low} 至 {high} 范围内的有限数值',
    'Window interpolation must be hold or linear': '窗口插值必须是保持或线性',
    'Window keyframe requires exactly its documented fields': '窗口关键帧必须且只能包含规定字段',
    'Window keyframes must be a list of at most 128 entries': '窗口关键帧必须是最多含 128 项的列表',
    'Window keyframe frames must be strictly increasing and unique': '窗口关键帧帧号必须严格递增且不得重复',
    'Window keyframes must be a bounded list': '窗口关键帧必须是长度受限的列表',

    'No displayed preview frame is available.': '当前没有可用的预览帧。',
    'The preview frame is too large for a reference still.': '此预览帧超出参考帧大小上限。',
    'Unknown reference comparison mode.': '未知的参考帧对比模式。',
    'Wipe position must be between zero and one.': '划像位置必须介于零和一之间。',
    'Decoded video endpoint requires video': '解码视频终点记录仅适用于视频素材',
    'Invalid decoded video endpoint': '解码视频终点无效',
    'Legacy projects cannot contain decoded video endpoint': '旧版项目不能包含解码视频终点记录',
    'Source slip exceeds the available media range.': '素材滑移超出可用的源素材范围。',
    'caption safe horizontal margin must be an integer in 0..20': '字幕水平安全边距必须为 0 至 20 范围内的整数',
    'caption maximum lines must be an integer in 1..20': '字幕最大行数必须为 1 至 20 范围内的整数',
}

# Kept as paired lines to make audits against the English sources straightforward.
_PAIRS = r'''
Select a media file.|请选择媒体文件。
No supported audio or video streams.|未找到支持的音频或视频流。
Media must have a finite, positive duration.|媒体时长必须为有限正数。
Invalid audio timestamp offset.|音频时间戳偏移无效。
Cannot inspect media: {exc}|无法检查媒体：{exc}
Non-finite timeline or grade value.|时间轴或调色参数包含非有限数值。
H.264 4:2:0 export requires even width and height.|H.264 4:2:0 导出的宽度和高度必须为偶数。
The timeline is empty.|时间轴为空。
Export filename must end in .mp4 or .mov.|导出文件名必须以 .mp4 或 .mov 结尾。
Output already exists. Choose a new filename; overwriting is disabled.|输出文件已存在。请选择新文件名；不允许覆盖。
An input media file cannot be replaced.|不能覆盖输入媒体文件。
A LUT input file cannot be replaced.|不能覆盖输入 LUT 文件。
A sticker input file cannot be replaced.|不能覆盖输入贴纸文件。
Output folder does not exist.|输出文件夹不存在。
Render cancelled.|渲染已取消。
Install FFmpeg and ffprobe and add them to PATH.|请安装 FFmpeg 和 ffprobe，并将其加入 PATH。
Frame is outside the timeline.|该帧超出时间轴范围。
Preview width must be at least 2 pixels.|预览宽度必须至少为 2 像素。
Generated speech source has changed: {name}. Restore the original audio or generate speech again.|生成的语音源文件已更改：{name}。请恢复原始音频或重新生成语音。
Cannot verify sticker asset: {exc}|无法验证贴纸素材：{exc}
Cannot verify generated speech source: {name}|无法验证生成的语音源文件：{name}
Cannot verify sticker input paths: {exc}|无法验证贴纸输入路径：{exc}
Render timed out.|渲染超时。
Local AI task cancelled.|本地 AI 任务已取消。
A task name is required.|必须提供任务名称。
Another local AI task is running. Wait or cancel it before starting this task.|另一个本地 AI 任务正在运行。请等待任务完成或取消后再启动此任务。
Unsupported local model category.|不支持此本地模型类别。
Choose between 1 and32 explicit local files.|请选择 1 至 32 个明确的本地文件。
Model files changed since local import. Verify/import the pack again.|模型文件自本地导入后已更改。请重新验证或导入模型包。
Invalid model-file role.|模型文件角色无效。
Only explicit local model files are supported, not URLs or network paths.|仅支持明确的本地模型文件，不支持网址或网络路径。
Choose an actual local file rather than a symbolic link.|请选择实际的本地文件，而非符号链接。
Model inputs must be regular local files.|模型输入必须为普通本地文件。
Model inputs must be regular local files without symbolic links.|模型输入必须为普通本地文件，不能使用符号链接。
Model file exceeds the4GiB per-file /8GiB pack limit.|模型文件超过单个文件 4 GiB 或模型包 8 GiB 的上限。
A model input changed while being verified.|模型输入在验证期间发生更改。
Local model registry is too large.|本地模型注册表过大。
Invalid local model registry.|本地模型注册表无效。
A verified model record is required.|需要已验证的模型记录。
The local registry has64 packs; remove an unused record first.|本地注册表已有 64 个模型包；请先移除不使用的记录。
Model registry exceeds its1MiB limit.|模型注册表超过 1 MiB 上限。
Model input is not a regular file.|模型输入不是普通文件。
A model input changed while being opened.|模型输入在打开时发生更改。
Model input grew while being verified.|模型输入在验证期间增大。
Invalid speech provenance|语音来源记录无效
Speech source end exceeds supported frame range|语音源结束位置超出支持的帧范围
Speech creation time must be an ISO-8601 UTC timestamp|语音创建时间必须为 ISO-8601 格式的 UTC 时间戳
Clip effects must be a list of at most 16 entries|片段效果必须为列表，最多包含 16 项
Clip effects are supported on video tracks only|仅视频轨道支持片段效果
Invalid overlay entry|叠加素材条目无效
Overlay assets must be local PNG files or JSON frame packs|叠加素材必须为本地 PNG 文件或 JSON 帧包
Overlay asset_sha256 must be a SHA-256 hexadecimal digest|叠加素材的 asset_sha256 必须为 SHA-256 十六进制摘要
Overlay end exceeds supported frame range|叠加素材结束位置超出支持的帧范围
Overlay keyframes must be a list of 1..128 entries|叠加素材关键帧必须为包含 1 至 128 项的列表
Overlays must be a list of at most 256 entries|叠加素材必须为列表，最多包含 256 项
Caption text contains invalid characters or exceeds 10000 characters|字幕文本包含无效字符或超过 10000 个字符
Captions must be a list of at most 20000 entries|字幕必须为列表，最多包含 20000 项
Captions on the subtitle track may not overlap|字幕轨道上的字幕不能重叠
Invalid caption style|字幕样式无效
Invalid caption font family|字幕字体族无效
Unknown clip: {id}|未知片段：{id}
Unknown overlay: {id}|未知叠加素材：{id}
Unknown effect: {id}|未知效果：{id}
Unknown caption: {id}|未知字幕：{id}
Unknown media: {id}|未知媒体：{id}
{name} must be an integer in {low}..{high}|{name} 必须为 {low} 至 {high} 范围内的整数
{name} must be finite and in {low}..{high}|{name} 必须为 {low} 至 {high} 范围内的有限数值
{name} must be a boolean|{name} 必须为布尔值
{name} must contain three RGB numbers|{name} 必须包含三个 RGB 数值
{name} must contain 2..32 points|{name} 必须包含 2 至 32 个点
{name} x endpoints must be 0 and 1|{name} 的 x 端点必须为 0 和 1
{name} points must be [x, y] pairs|{name} 的点必须为 [x, y] 数值对
{name} x coordinates must be strictly increasing|{name} 的 x 坐标必须严格递增
{name} must be a SHA-256 hexadecimal digest|{name} 必须为 SHA-256 十六进制摘要
Invalid speech creation time|语音创建时间无效
Invalid clip effect|片段效果无效
Duplicate effect id within clip|片段内存在重复的效果 ID
Unsupported clip effect kind|不支持此片段效果类型
Invalid overlay keyframe|叠加素材关键帧无效
Overlay keyframe frames must be strictly increasing|叠加素材关键帧的帧位置必须严格递增
Duplicate overlay id|叠加素材 ID 重复
Invalid caption entry|字幕条目无效
Duplicate caption id|字幕 ID 重复
Caption end exceeds supported frame range|字幕结束位置超出支持的帧范围
Caption text must use normalized line breaks|字幕文本必须使用标准换行符
Caption language must be a language tag such as und or en-US|字幕语言必须为 und 或 en-US 等语言标签
Caption colors must be #RRGGBB|字幕颜色必须采用 #RRGGBB 格式
Unsupported project frame rate|不支持此项目帧率
media and clips must be lists|media 和 clips 必须为列表
Project must be a JSON object|项目必须为 JSON 对象
Unsupported project format or version|不支持此项目格式或版本
Project file exceeds 32 MiB|项目文件超过 32 MiB
Unknown or immutable clip property|片段属性未知或不可修改
Unknown or immutable overlay property|叠加素材属性未知或不可修改
Unknown or immutable effect property|效果属性未知或不可修改
Unknown or immutable caption property|字幕属性未知或不可修改
Unknown caption style property|未知字幕样式属性
Invalid media entry|媒体条目无效
Duplicate media id|媒体 ID 重复
Media must contain video or audio|媒体必须包含视频或音频
Invalid clip entry|片段条目无效
Duplicate clip id|片段 ID 重复
Clip references unknown media|片段引用了未知媒体
Clip exceeds source duration|片段超出源媒体时长
Video tracks require video media|视频轨道需要视频媒体
Audio tracks require audio media|音频轨道需要音频媒体
Invalid grade|调色参数无效
Input encoding must be rec709, srgb or linear_rec709|输入编码必须为 rec709、srgb 或 linear_rec709
Clips on the same track may not overlap|同一轨道上的片段不能重叠
Malformed project: {exc}|项目格式错误：{exc}
A project cannot overwrite a file in its caption font directory|项目不能覆盖字幕字体目录中的文件
A project cannot overwrite an input media, LUT, or overlay asset file|项目不能覆盖输入媒体、LUT 或叠加素材文件
Generated speech requires provenance|生成的语音必须包含来源记录
Speech provenance references a different caption|语音来源记录引用了其他字幕
Split must be strictly inside the clip|分割位置必须严格位于片段内部
Generated speech must be positive-duration, zero-offset audio-only media|生成的语音必须为时长为正、偏移为零的纯音频媒体
Duplicate JSON key: {key}|JSON 键重复：{key}
A project cannot overwrite a possible input of an unreadable overlay frame pack|项目不能覆盖无法读取的叠加帧包中可能使用的输入文件
Invalid grade: {exc}|调色参数无效：{exc}
Sticker composition supports at most 16,777,216 canvas pixels. Choose a smaller project resolution, such as 3840 x 2160 UHD.|贴纸合成最多支持 16,777,216 个画布像素。请选择较小的项目分辨率，例如 3840 x 2160 UHD。
Overlay evaluation requires an integer project frame.|叠加素材计算需要整数项目帧。
An overlay needs at least one keyframe.|叠加素材至少需要一个关键帧。
Cannot allocate the overlay frame.|无法分配叠加素材帧内存。
Sticker frame changed after verification. Import the sticker again to approve new content.|贴纸帧在验证后发生更改。请重新导入贴纸以确认新内容。
Cannot decode verified sticker frame: {name}|无法解码已验证的贴纸帧：{name}
Invalid clip effect kind or amount.|片段效果类型或强度无效。
Render timed out composing stickers.|渲染在合成贴纸时超时。
Sticker frame identity is missing. Verify the asset before rendering.|缺少贴纸帧标识。请在渲染前验证素材。
Sticker frame dimensions changed after verification.|贴纸帧尺寸在验证后发生更改。
Sticker composition failed: {exc}|贴纸合成失败：{exc}
Caption font family "{family}" was not found locally. Install a local font or choose its family and font directory.|本地未找到字幕字体族“{family}”。请安装本地字体，或选择字体族及字体目录。
Caption font directory does not exist.|字幕字体目录不存在。
Caption fonts and cache entries must be regular local files.|字幕字体和缓存条目必须是本地普通文件。
Caption font changed after discovery.|字幕字体在发现后发生了变化。
Caption font changed while opening.|字幕字体在打开时发生了变化。
Caption font changed while reading.|字幕字体在读取时发生了变化。
Caption font cache entry must be a regular local file.|字幕字体缓存条目必须是本地普通文件。
Caption font requires a valid SFNT face or collection.|字幕字体必须包含有效的 SFNT 字体或字体集合。
Caption font family changed after discovery.|字幕字体族在发现后发生了变化。
Caption font cache requires an owned directory.|字幕字体缓存必须使用由应用管理的目录。
Caption font "{family}" is missing glyphs ({sample}). Choose a local font covering this language; no fallback/tofu will be exported.|字幕字体“{family}”缺少字形（{sample}）。请选择支持此语言的本地字体；不会导出替代字体或缺字方框。
'''
ZH_CN.update(line.split('|', 1) for line in _PAIRS.strip().splitlines())
_PAIRS = r'''
Sticker manifest must use standard JSON values.|贴纸清单必须使用标准 JSON 值。
Sticker validation cancelled.|贴纸验证已取消。
Sticker file exceeds its {limit} KiB size limit: {name}.|贴纸文件超过 {limit} KiB 大小上限：{name}。
Sticker manifest requires exactly version, frames, frame_duration, and loop.|贴纸清单必须且只能包含 version、frames、frame_duration 和 loop 字段。
Only sticker manifest version 1 is supported.|仅支持版本 1 的贴纸清单。
Sticker frame_duration must be an integer from 1 to 300 project frames.|贴纸 frame_duration 必须为 1 至 300 个项目帧的整数。
Sticker loop must be a JSON boolean.|贴纸 loop 必须为 JSON 布尔值。
Sticker manifest must list between 1 and {limit} PNG frames.|贴纸清单必须列出 1 至 {limit} 个 PNG 帧。
Sticker frames must be relative PNG filenames.|贴纸帧必须为相对 PNG 文件名。
Sticker frames must be contained relative PNG paths, without traversal or URLs.|贴纸帧必须为目录内的相对 PNG 路径，不能包含上级目录跳转或网址。
Sticker frame escapes its manifest directory.|贴纸帧超出了清单目录范围。
Sticker file content is not PNG, regardless of its filename.|贴纸文件内容不是 PNG，与文件名无关。
PNG is missing its ending chunk.|PNG 缺少结束数据块。
Sticker PNG could not be decoded completely.|无法完整解码贴纸 PNG。
Sticker pack exceeds the 128 MiB decoded-pixel budget.|贴纸包解码后的像素数据超过 128 MiB 上限。
Sticker expected SHA-256 digest must contain 64 hexadecimal characters.|贴纸预期的 SHA-256 摘要必须包含 64 个十六进制字符。
Sticker assets changed since import. Import the sticker again to approve the new content.|贴纸素材自导入后已更改。请重新导入贴纸以确认新内容。
Sticker frame must be an absolute PNG file path.|贴纸帧必须使用 PNG 文件的绝对路径。
Choose an explicit local PNG or JSON sticker file.|请选择明确的本地 PNG 或 JSON 贴纸文件。
Sticker network paths and URLs are not supported.|贴纸不支持网络路径或网址。
Sticker alternate data streams are not supported.|贴纸不支持备用数据流。
Sticker paths must not contain parent traversal (..).|贴纸路径不能包含上级目录跳转（..）。
Choose a PNG image or a JSON frame-pack manifest; archives are not supported.|请选择 PNG 图像或 JSON 帧包清单；不支持压缩包。
Selected sticker file must not be a symbolic link or junction.|所选贴纸文件不能为符号链接或目录联接。
Choose a valid local PNG or JSON sticker file.|请选择有效的本地 PNG 或 JSON 贴纸文件。
Sticker input must be an ordinary local file.|贴纸输入必须为普通本地文件。
Sticker file cannot be read: {name}.|无法读取贴纸文件：{name}。
Sticker input changed while being read; import it again.|贴纸输入在读取期间发生更改；请重新导入。
Sticker file cannot be read safely: {name}.|无法安全读取贴纸文件：{name}。
Duplicate sticker manifest field: {key}.|贴纸清单字段重复：{key}。
Sticker manifest must be valid UTF-8 JSON.|贴纸清单必须为有效的 UTF-8 JSON。
PNG compressed pixels are incomplete or do not match the declared dimensions.|PNG 压缩像素数据不完整或与声明的尺寸不匹配。
PNG contains invalid compressed pixels.|PNG 包含无效的压缩像素数据。
PNG has a truncated chunk.|PNG 包含被截断的数据块。
PNG has too many chunks (limit 1024).|PNG 数据块过多（上限为 1024 个）。
PNG has an invalid or truncated chunk.|PNG 包含无效或被截断的数据块。
PNG chunk checksum is invalid.|PNG 数据块校验和无效。
PNG must begin with its image header.|PNG 必须以图像头开始。
All sticker frame-pack PNG images must have the same dimensions.|贴纸帧包中的所有 PNG 图像必须具有相同尺寸。
Sticker frame must be an ordinary local PNG path.|贴纸帧必须使用普通本地 PNG 路径。
Sticker files and their directories must not use symbolic links or junctions.|贴纸文件及其目录不能使用符号链接或目录联接。
Sticker parent paths must be ordinary directories.|贴纸的上级路径必须为普通目录。
Sticker input changed while being opened; import it again.|贴纸输入在打开时发生更改；请重新导入。
PNG has an invalid image header.|PNG 图像头无效。
Sticker PNG dimensions must be from 1 to {limit} pixels per side.|贴纸 PNG 的每边尺寸必须为 1 至 {limit} 像素。
Only 8-bit RGB or RGBA PNG stickers are supported; convert other formats locally.|仅支持 8 位 RGB 或 RGBA PNG 贴纸；请在本地转换其他格式。
PNG has unsupported or invalid image header settings.|PNG 图像头设置不受支持或无效。
PNG compressed text/profile metadata is not supported; re-save a plain PNG locally.|不支持 PNG 压缩文本或配置元数据；请在本地重新保存为普通 PNG。
PNG compressed pixels exceed their declared dimensions or contain trailing data.|PNG 压缩像素数据超出声明尺寸或包含尾随数据。
Animated PNG is not supported; use an explicit PNG frame-pack manifest.|不支持动态 PNG；请使用明确的 PNG 帧包清单。
PNG palettes are not supported; use plain 8-bit RGB or RGBA.|不支持 PNG 调色板；请使用普通 8 位 RGB 或 RGBA。
PNG has invalid transparency content or ordering.|PNG 透明度内容或顺序无效。
PNG pixel chunks must be consecutive.|PNG 像素数据块必须连续。
PNG has an invalid ending or trailing content.|PNG 结束内容无效或包含尾随内容。
PNG contains an unsupported chunk; use a plain 8-bit sRGB PNG.|PNG 包含不支持的数据块；请使用普通 8 位 sRGB PNG。
PNG contains duplicate metadata chunks.|PNG 包含重复的元数据块。
PNG metadata appears after its pixels.|PNG 元数据位于像素数据之后。
PNG metadata is invalid or declares unsupported color settings; use plain sRGB PNG.|PNG 元数据无效或声明了不支持的颜色设置；请使用普通 sRGB PNG。
Speech synthesis cancelled.|语音合成已取消。
Model/audio assets must be regular files, without symbolic links or special files.|模型和音频素材必须为普通文件，不能使用符号链接或特殊文件。
An asset exceeds the 2 GiB safety limit.|素材超过 2 GiB 安全上限。
An asset changed while being read; operation canceled.|素材在读取期间发生更改；操作已取消。
Enter nonempty text, at most 100,000 characters, without NUL characters.|请输入非空文本，最多 100,000 个字符，不能包含 NUL 字符。
A TTSConfig is required.|需要 TTSConfig 配置。
Speech speed must be between 0.5 and 2.0.|语速必须介于 0.5 和 2.0 之间。
Threads must be an integer from 1 to 4.|线程数必须为 1 至 4 的整数。
Model directory cannot contain commas (engine path-list separator).|模型目录不能包含逗号（引擎路径列表分隔符）。
Incomplete Kokoro {version} local model pack. See docs/LOCAL_TTS.md; no automatic download is performed.|Kokoro {version} 本地模型包不完整。请参阅 docs/LOCAL_TTS.md；不会自动下载。
Select a WAV filename in an existing local directory.|请选择现有本地目录中的 WAV 文件名。
Save synthesized speech outside the model pack directory.|请将合成语音保存到模型包目录之外。
Output already exists; choose a new WAV filename.|输出文件已存在；请选择新的 WAV 文件名。
The model and voice bank do not match the verified Kokoro {version} pack. Choose the matching version and complete official pack; no download is performed.|模型及音色库与已验证的 Kokoro {version} 模型包不匹配。请选择对应版本及完整的官方模型包；不会执行下载。
An asset changed while being opened; operation canceled.|素材在打开时发生更改；操作已取消。
Select existing local model and output directories.|请选择现有的本地模型目录和输出目录。
Model pack exceeds the 10,000-directory safety limit.|模型包超过 10,000 个目录的安全上限。
oversized response|响应过大
invalid response|响应无效
Local speech worker returned an invalid response.|本地语音工作进程返回了无效响应。
This adapter is verified with {package}=={runtime}; the installed version is {version}. See docs/LOCAL_TTS.md.|此适配器已通过 {package}=={runtime} 验证；当前安装的版本为 {version}。请参阅 docs/LOCAL_TTS.md。
An asset changed size while being read; operation canceled.|素材大小在读取期间发生更改；操作已取消。
Model pack must not contain symbolic links.|模型包不能包含符号链接。
Model pack must contain only regular files and directories, without special files.|模型包只能包含普通文件和目录，不能包含特殊文件。
Model pack exceeds the 10,000-file / 2 GiB safety limit.|模型包超过 10,000 个文件或 2 GiB 的安全上限。
Local speech worker returned no engine version.|本地语音工作进程未返回引擎版本。
Local speech output exceeded the 512 MiB safety limit; nothing was published.|本地语音输出超过 512 MiB 安全上限；未发布任何文件。
Model pack changed during speech generation; output was discarded.|模型包在语音生成期间发生更改；输出已丢弃。
Cannot publish synthesized WAV: {exc}|无法发布合成的 WAV：{exc}
Optional local TTS runtime is missing. Explicitly install the documented sherpa-onnx runtime; nothing is downloaded by the app.|缺少可选的本地 TTS 运行时。请手动安装文档指定的 sherpa-onnx 运行时；应用不会下载任何内容。
Cannot start local speech worker: {exc}|无法启动本地语音工作进程：{exc}
Local speech worker exceeded the diagnostic size safety limit.|本地语音工作进程的诊断信息超过安全大小上限。
Speech sample count must be a positive supported integer.|语音采样数必须是支持范围内的正整数。
The speech engine skipped unsupported text or sounds. No audio was saved. Revise the reading text or choose another model.|语音引擎跳过了不支持的文字或发音，未保存音频。请修改朗读文本或选择其他模型。
Engine returned invalid mono 24 kHz PCM audio.|引擎返回的单声道 24 kHz PCM 音频无效。
Local speech generation exceeded the 30-minute safety limit. Try a shorter passage.|本地语音生成超过 30 分钟安全时限。请尝试较短的文本。
Unverified optional runtime version|可选运行时版本未验证
Invalid local engine configuration|本地引擎配置无效
The actual voice bank does not match the explicit pack version|实际音色库与指定的模型包版本不匹配
No usable speech generated|未生成可用语音
Voice ID {voice_id} is not documented for Kokoro {version}.|Kokoro {version} 文档未列出音色 ID {voice_id}。
Voice ID must be a documented integer for the selected pack.|音色 ID 必须为所选模型包文档列出的整数。
Transcription cancelled.|转写已取消。
Select an existing local {label} file.|请选择现有的本地 {label} 文件。
Speech recognition JSON exceeds the 16 MiB safety limit.|语音识别 JSON 超过 16 MiB 安全上限。
The selected whisper.cpp runtime is not executable.|所选 whisper.cpp 运行时不可执行。
Threads must be an integer between 1 and 256.|线程数必须为 1 至 256 的整数。
Language must be auto or a lowercase Whisper language code (en, zh, etc.).|语言必须为 auto 或小写的 Whisper 语言代码（en、zh 等）。
Threads must be an integer between1 and4 (shared local AI resource limit).|线程数必须为 1 至 4 的整数（共享本地 AI 资源限制）。
nonfinite stream timestamp|流时间戳不是有限数值
Cannot determine source audio timing; ensure it contains audio.|无法确定源音频时间信息；请确认其中包含音频。
expected a transcription list|需要转写列表
\ntoo many segments (maximum 100000)|分段过多（最多 100000 个）
invalid language|语言无效
Invalid whisper.cpp JSON output: {exc}|whisper.cpp JSON 输出无效：{exc}
Cannot transcribe media: {exc}|无法转写媒体：{exc}
Cannot start {label}: {exc}|无法启动 {label}：{exc}
{label} failed (exit {code}): {detail}|{label} 失败（退出代码 {code}）：{detail}
non-numeric offsets|偏移量不是数值
invalid time range|时间范围无效
out-of-order segments|分段顺序错误
invalid text|文本无效
'''
ZH_CN.update(line.split('|', 1) for line in _PAIRS.strip().splitlines())
_PAIRS = r'''
Subtitle assistance cancelled.|字幕辅助已取消。
A selected local file is not a regular file within its size limit.|所选本地文件不是符合大小限制的普通文件。
An AssistantConfig is required.|需要 AssistantConfig 配置。
This version supports proofreading one caption only.|此版本仅支持校对一条字幕。
Select one nonempty caption, at most 1,000 characters.|请选择一条非空字幕，最多 1,000 个字符。
Caption exceeds 2,048 UTF-8 bytes or contains unsupported control characters.|字幕超过 2,048 个 UTF-8 字节或包含不支持的控制字符。
Threads must be an integer between 1 and 4.|线程数必须为 1 至 4 的整数。
Maximum output tokens must be an integer between 32 and 512.|最大输出词元数必须为 32 至 512 的整数。
The selected local runtime is not executable.|所选本地运行时不可执行。
The local runtime exceeds the 256 MiB executable limit.|本地运行时超过 256 MiB 可执行文件上限。
The subtitle model exceeds the 2 GiB limit; choose the documented small model.|字幕模型超过 2 GiB 上限；请选择文档指定的小型模型。
URLs and UNC paths are not local files|网址和 UNC 路径不是本地文件
not a nonempty regular file|不是非空普通文件
Select an existing local {label} file. No automatic download is performed.|请选择现有的本地 {label} 文件。不会自动下载。
A selected local file changed while being opened.|所选本地文件在打开时发生更改。
A selected local file changed while being read.|所选本地文件在读取期间发生更改。
Caption contains invalid Unicode.|字幕包含无效的 Unicode。
Select a GGUF model, not a pointer file or a different model format.|请选择 GGUF 模型，不能使用指针文件或其他模型格式。
output safety limit exceeded|超过输出安全上限
expected one text field|需要一个文本字段
empty, excessive or invalid text|文本为空、过长或无效
changed caption line structure|字幕行结构被更改
changed numeric facts|数字事实被更改
changed language|语言被更改
rewrite is too extensive for proofreading|改写幅度超出校对范围
Local model suggestion rejected: {exc}. Original caption is unchanged.|本地模型建议已拒绝：{exc}。原始字幕未更改。
Cannot read or prepare the selected local subtitle runtime/model files.|无法读取或准备所选本地字幕运行时或模型文件。
A selected local file grew beyond its safety limit.|所选本地文件增大并超过安全上限。
Cannot start the selected local llama-completion runtime.|无法启动所选本地 llama-completion 运行时。
Local subtitle runtime failed (exit {code}). Check the documented runtime and model versions.|本地字幕运行时失败（退出代码 {code}）。请检查文档指定的运行时和模型版本。
Local runtime diagnostics exceeded their safety limit.|本地运行时诊断信息超过安全上限。
Local runtime output exceeded its safety limit.|本地运行时输出超过安全上限。
Runtime or model changed during inference; suggestion discarded.|运行时或模型在推理期间发生更改；建议已丢弃。
Local subtitle assistance exceeded the 180-second time limit.|本地字幕辅助超过 180 秒时限。
duplicate JSON key|JSON 键重复
Semantic search cancelled.|语义搜索已取消。
Search supports at most 20,000 captions.|搜索最多支持 20,000 条字幕。
Search assets must be bounded regular local files.|搜索素材必须为符合大小限制的普通本地文件。
Search asset changed while being verified.|搜索素材在验证期间发生更改。
A SearchConfig is required.|需要 SearchConfig 配置。
Search CPU threads must be an integer from 1 to 8.|搜索 CPU 线程数必须为 1 至 8 的整数。
Local search worker returned invalid embedding vectors.|本地搜索工作进程返回了无效的嵌入向量。
Local search worker returned non-normalized embedding vectors.|本地搜索工作进程返回了未归一化的嵌入向量。
Search requires caption entries.|搜索需要字幕条目。
Invalid caption ID.|字幕 ID 无效。
Duplicate caption ID.|字幕 ID 重复。
Caption end exceeds supported frame range.|字幕结束位置超出支持的帧范围。
Search supports at most 4,000,000 total caption characters.|搜索支持的字幕字符总数最多为 4,000,000。
Search asset changed while being opened.|搜索素材在打开时发生更改。
The local {label} does not match the supported official model revision. See docs/LOCAL_SEARCH.md for the exact files and SHA-256 hashes.|本地 {label} 与支持的官方模型修订版本不匹配。有关具体文件和 SHA-256 哈希值，请参阅 docs/LOCAL_SEARCH.md。
Local search worker returned an invalid response.|本地搜索工作进程返回了无效响应。
Local search worker returned no runtime provenance.|本地搜索工作进程未返回运行时来源记录。
Local search worker returned invalid provenance.|本地搜索工作进程返回的来源记录无效。
Add captions before building a semantic search index.|请先添加字幕，再构建语义搜索索引。
Search model files changed while building the index; the result was discarded.|搜索模型文件在构建索引期间发生更改；结果已丢弃。
Search result limit must be an integer from 1 to 100.|搜索结果数量上限必须为 1 至 100 的整数。
Search model files changed; rebuild the index.|搜索模型文件已更改；请重新构建索引。
Shorten the search query to at most 128 model tokens, including special tokens. Its ending would otherwise be discarded.|请将搜索查询缩短至最多 128 个模型词元（含特殊词元），否则末尾内容会被丢弃。
Search runtime changed; rebuild the index.|搜索运行时已更改；请重新构建索引。
Search model files changed during the query; rebuild the index.|搜索模型文件在查询期间发生更改；请重新构建索引。
unnormalized line breaks|换行符未标准化
Invalid caption text.|字幕文本无效。
Invalid caption timing.|字幕时间信息无效。
Search asset exceeded its file size limit.|搜索素材超过文件大小上限。
Only local filesystem paths are accepted|仅接受本地文件系统路径
Select an actual local file rather than a link|请选择实际的本地文件，而非链接
invalid file size|文件大小无效
Select an existing local {label} file within the documented size limit. No download is performed.|请选择符合文档大小限制的现有本地 {label} 文件。不会执行下载。
Local search worker returned an invalid vector buffer size.|本地搜索工作进程返回的向量缓冲区大小无效。
Enter a nonempty search query of at most 10,000 characters.|请输入非空搜索查询，最多 10,000 个字符。
Cannot start the local search worker.|无法启动本地搜索工作进程。
Local search timed out; reduce the transcript size and try again.|本地搜索超时；请缩短转写文本后重试。
Expected batch x tokens x 384 token embeddings|需要批次 × 词元 × 384 维词元嵌入
Invalid pooled embeddings|池化嵌入无效
Oversized request|请求过大
Invalid text batch|文本批次无效
Invalid CPU thread count|CPU 线程数无效
Incompatible official tokenizer|官方分词器不兼容
Unsupported model inputs|不支持此模型输入
Tokenizer exceeded maximum sequence length|分词器超出最大序列长度
Render timed out inspecting source color format.|渲染在检查源颜色格式时超时。
Expected one video stream|需要一个视频流
Cannot interpret source color metadata: {exc}|无法解析源颜色元数据：{exc}
Float grading failed: {exc}|浮点调色失败：{exc}
Incomplete RGB48 frame from decoder.|解码器返回了不完整的 RGB48 帧。
Color values must be finite|颜色值必须为有限数值
Curve needs at least two (x, y) points|曲线至少需要两个 (x, y) 点
Curve x must strictly increase|曲线的 x 值必须严格递增
Only .cube 3D LUT files are supported|仅支持 .cube 格式的 3D LUT 文件
LUT exceeds 64 MiB|LUT 超过 64 MiB
Invalid LUT row count or domain|LUT 行数或定义域无效
RGB requires a final dimension of 3|RGB 的最后一维必须为 3
Grade input must be a float32 NumPy array|调色输入必须为 float32 NumPy 数组
Lift/gamma/gain require three channels and positive gamma|暗部偏移、伽马和增益需要三个通道，且伽马必须为正数
Grade produced non-finite values|调色产生了非有限数值
Invalid .cube line {number}: {exc}|.cube 第 {number} 行无效：{exc}
Duplicate or late LUT header|LUT 头重复或位置过晚
Expected 3D LUT header and RGB triples (1D LUTs unsupported)|需要 3D LUT 头和 RGB 三元组（不支持 1D LUT）
Too many LUT rows|LUT 行数过多
Invalid TITLE|TITLE 无效
Invalid LUT size|LUT 大小无效
LUT size must be 2..65|LUT 大小必须为 2 至 65
Domain must have three numbers|定义域必须包含三个数值
Only SDR Rec.709-primary inputs are supported; color_transfer={transfer}. Convert unsupported Log/HDR to SDR Rec.709 before importing.|仅支持 SDR Rec.709 原色输入；color_transfer={transfer}。请在导入前将不支持的 Log/HDR 转换为 SDR Rec.709。
Malformed source {name} metadata|源 {name} 元数据格式错误
Malformed video color metadata|视频颜色元数据格式错误
Malformed video side metadata|视频附加元数据格式错误
HDR metadata is unsupported. Convert to SDR Rec.709 before importing.|不支持 HDR 元数据。请在导入前转换为 SDR Rec.709。
Only SDR Rec.709 primaries are supported; color_primaries={primaries}. Convert this source before importing; an input encoding assignment cannot change its gamut.|仅支持 SDR Rec.709 原色；color_primaries={primaries}。请在导入前转换此源文件；指定输入编码不能改变其色域。
Only SDR Rec.709-primary sources with a supported matrix are allowed; color_space={matrix}. Convert this source before importing.|仅允许采用支持的矩阵的 SDR Rec.709 原色源；color_space={matrix}。请在导入前转换此源文件。
Unsupported source color_range={range_tag}|不支持源 color_range={range_tag}
Unsupported source pixel format: {pixel_format}|不支持的源像素格式：{pixel_format}
YUV source has contradictory RGB matrix metadata|YUV 源包含矛盾的 RGB 矩阵元数据
BT.601-matrix sources require explicitly tagged BT.709 primaries; no unknown or BT.601 gamut conversion is inferred.|BT.601 矩阵源必须明确标注 BT.709 原色；不会推断未知色域或 BT.601 色域转换。
Full-range yuvj pixel format conflicts with limited-range metadata|全范围 yuvj 像素格式与有限范围元数据冲突
Limited-range RGB input is unsupported. Convert to full-range RGB or correctly tagged SDR YUV before importing.|不支持有限范围 RGB 输入。请在导入前转换为全范围 RGB 或正确标注的 SDR YUV。
Source transfer is {known}, but the clip input is {selected}. Select {known} in Input encoding before preview or export.|源传递函数为 {known}，但片段输入为 {selected}。请在预览或导出前在输入编码中选择 {known}。
SRT input must be text|SRT 输入必须为文本
SRT contains more than 20000 captions|SRT 包含超过 20000 条字幕
SRT captions overlap in source timings; adjust cue timings|SRT 字幕的源时间存在重叠；请调整字幕时间
SRT file exceeds 32 MiB|SRT 文件超过 32 MiB
Frame rate must be finite and in 0.001..1000|帧率必须为 0.001 至 1000 范围内的有限数值
Caption time must be finite and nonnegative|字幕时间必须为非负有限数值
Malformed SRT cue {position}: expected index, timing, and text|SRT 第 {position} 条格式错误：需要序号、时间和文本
Malformed SRT time in cue {position}; expected HH:MM:SS,mmm --> HH:MM:SS,mmm|SRT 第 {position} 条时间格式错误；需要 HH:MM:SS,mmm --> HH:MM:SS,mmm
SRT cue {position} must end after its start|SRT 第 {position} 条的结束时间必须晚于开始时间
SRT cue {position} collapses to zero frames at this project frame rate|SRT 第 {position} 条在当前项目帧率下变为零帧
Caption is too short for millisecond SRT precision|字幕过短，无法使用毫秒级 SRT 精度表示
SRT caption text cannot contain empty lines|SRT 字幕文本不能包含空行
SRT file must be UTF-8 encoded|SRT 文件必须采用 UTF-8 编码
SRT captions overlap after nearest-frame quantization; adjust cue timings|SRT 字幕在取整到最近帧后重叠；请调整字幕时间
'''
ZH_CN.update(line.split('|', 1) for line in _PAIRS.strip().splitlines())
ZH_CN['too many segments (maximum 100000)'] = '分段过多（最多 100000 个）'
ZH_CN.pop(r'\ntoo many segments (maximum 100000)', None)
del _PAIRS
ZH_CN.update({
    'Technical error details:\n{detail}': '技术错误详情：\n{detail}',
    'Workspace mode must be simple or professional.': '工作区模式必须为简单或专业。',
    'Voice is not in the currently displayed verified catalog.': '音色不在当前显示的已验证目录中。',
    'Choose an existing local Kokoro model-pack folder. Nothing is downloaded.': '请选择现有的本地 Kokoro 模型包文件夹。不会下载任何内容。',
    'Verify the selected local pack and runtime first.': '请先验证所选本地模型包和运行时。',
    'Choose a documented voice in the browser.': '请在音色浏览器中选择文档列出的音色。',
    'Choose an existing whisper.cpp runtime and model file first. Nothing is downloaded automatically.': '请先选择现有的 whisper.cpp 运行时和模型文件。不会自动下载任何内容。',
    'This source has no audio stream to transcribe.': '此源文件没有可供转写的音频流。',
    'Source is shorter than one full project frame.': '源文件时长短于一个完整项目帧。',
    'Select a subtitle to read first.': '请先选择要朗读的字幕。',
    'Choose Music 1 or Music 2 as the destination audio lane.': '请选择音乐 1 或音乐 2 作为目标音频轨道。',
    'Choose a new filename ending in .wav.': '请选择以 .wav 结尾的新文件名。',
    'Voice output must be a new file. Existing files and project sources cannot be overwritten.': '语音输出必须使用新文件。不能覆盖现有文件和项目源文件。',
    'Choose a durable media folder outside the temporary preview cache.': '请选择临时预览缓存之外的长期媒体文件夹。',
    'Select a timeline clip to transcribe.': '请选择要转写的时间轴片段。',
    'Select a source in the media bin.': '请在媒体库中选择源媒体。',
    'Cannot replace source media or a LUT with a project file.': '不能用项目文件覆盖源媒体或 LUT。',
    'Replacement media must have matching video/audio stream types.': '替换媒体的视频和音频流类型必须匹配。',
})
# Worker-protocol and process fallback messages also surface at the UI boundary.
ZH_CN.update({
    'FFmpeg and ffprobe must be installed and available on PATH.': '必须安装 FFmpeg 和 ffprobe，并确保可通过 PATH 访问。',
    'Cannot inspect media.': '无法检查媒体。',
    'No preview frame.': '没有预览帧。',
    'Optional local search runtime is missing. Install the documented onnxruntime and tokenizers extras; no download is performed by the app.': '缺少可选的本地搜索运行时。请安装文档指定的 onnxruntime 和 tokenizers 扩展；应用不会下载任何内容。',
    'Local semantic search failed: incompatible runtime, model, tokenizer, or input. Use the exact documented official model files.': '本地语义搜索失败：运行时、模型、分词器或输入不兼容。请使用文档指定的官方模型文件。',
    'Optional local TTS runtime is missing. Install sherpa-onnx in this Python environment; no download is performed by the app.': '缺少可选的本地 TTS 运行时。请在此 Python 环境中安装 sherpa-onnx；应用不会下载任何内容。',
    'Local speech synthesis failed: incompatible runtime, model pack, or text. Use the complete documented pack matching the selected version.': '本地语音合成失败：运行时、模型包或文本不兼容。请使用与所选版本匹配的完整文档指定模型包。',
})
# The model's short "Invalid {name}" grammar is restricted to its known field
# labels. A broad match would misclassify unrelated third-party diagnostics.
ZH_CN.update({f'Invalid {name}': f'{label}无效' for name, label in {
    'engine': '引擎', 'engine_version': '引擎版本', 'voice_id': '音色 ID',
    'source_caption_id': '源字幕 ID', 'effect id': '效果 ID', 'effect kind': '效果类型',
    'overlay id': '叠加素材 ID', 'overlay asset path': '叠加素材路径',
    'caption text': '字幕文本', 'caption id': '字幕 ID', 'caption language': '字幕语言',
    'caption font family': '字幕字体族', 'caption font directory': '字幕字体目录',
    'project name': '项目名称', 'media id': '媒体 ID', 'media path': '媒体路径',
    'media name': '媒体名称', 'color_space': '颜色空间', 'color_transfer': '传递函数',
    'clip group id': '片段编组 ID', 'group member clip id': '编组成员片段 ID',
    'color_primaries': '原色', 'color_range': '颜色范围', 'clip id': '片段 ID',
    'clip media_id': '片段媒体 ID', 'lut_path': 'LUT 路径',
}.items()})
ZH_CN.update({
    'Cannot inspect source color format.': '无法检查源颜色格式。',
    'Sticker encoding failed.': '贴纸编码失败。',
})

ZH_CN.update({
    'Recognition cues overlap in source timings; review the transcript before importing.': '识别字幕在源时间上重叠；请先检查转写结果，再导入。',
    'Transcription source bounds must be exact nonnegative times with end after start.': '转写源范围必须为精确的非负时间，且结束时间必须晚于开始时间。',
    '{name} must be an exact integer, fraction or decimal string': '{name} 必须为精确整数、分数或小数字符串',
    'Source position exceeds supported rational bounds': '源位置超过支持的有理数范围',
    'Speed must be between 1/4 and 4': '速度必须在 1/4 至 4 之间',
    'Speed exceeds supported rational bounds': '速度超过支持的有理数范围',
    'Time map must contain exactly six rational numerator/denominator fields': '时间映射必须且只能包含六个分子与分母字段',
    'Time map contains an invalid or oversized rational pair': '时间映射包含无效或超出范围的分子分母数值对',
    'Time map rational pairs must be reduced': '时间映射中的分数必须已约分',
    'Invalid clip time map': '片段时间映射无效',
    'Source endpoint must be after source origin': '源结束位置必须晚于源起始位置',
    'Project frame rate must be positive': '项目帧率必须为正数',
    'Sample boundary frame must be a nonnegative integer': '采样边界帧必须为非负整数',
    'Sample rate must be a positive integer': '采样率必须为正整数',
})

ZH_CN.update({
    'Clip source origin does not match its time map': '片段源起点与其时间映射不一致',
    'Clip duration does not match its time map': '片段时长与其时间映射不一致',
    'Left trim must leave at least one output frame': '左侧裁剪必须至少保留一个输出帧',
    'Source position must be an exact integer or fraction': '源位置必须为精确整数或分数',
    'Version 7 clips require only canonical time_map timing': '版本 7 片段必须仅使用规范的 time_map 时间设置',
    'Legacy clips cannot contain a time map': '旧版片段不能包含时间映射',
    'Source ended before the requested retime frame.': '源媒体在请求的变速帧之前已结束。',
    'No picture frames were decoded for this clip.': '未能为此片段解码任何画面帧。',
})

ZH_CN.update({
    'Audio offset must be a finite number.': '音频偏移必须为有限数值。',
    'Project rate must be a positive rational number.': '项目帧率必须为正有理数。',
    'Rubber Band audio compatibility check timed out.': 'Rubber Band 音频兼容性检查超时。',
    'Cannot verify required Rubber Band audio support: {exc}': '无法验证所需的 Rubber Band 音频支持：{exc}',
    'Required Rubber Band audio compatibility check failed: {reason}': '所需的 Rubber Band 音频兼容性检查失败：{reason}',
    'Pitch-preserving speed requires FFmpeg with Rubber Band (librubberband). This FFmpeg does not provide it; no lower-quality fallback is used.': '保持音调的变速需要包含 Rubber Band（librubberband）的 FFmpeg。当前 FFmpeg 不提供此功能；不会使用较低质量的替代方案。',
    'Rubber Band {speed}x failed the exact PCM sample-budget check.': 'Rubber Band 在 {speed} 倍速下未通过精确 PCM 采样数检查。',
    'Rubber Band {speed}x produced no measurable tone.': 'Rubber Band 在 {speed} 倍速下未产生可测量的音调。',
    'Rubber Band {speed}x failed pitch preservation ({frequency} Hz).': 'Rubber Band 在 {speed} 倍速下未能保持音调（{frequency} Hz）。',
    'Rubber Band {speed}x lost a selected audio transient or source tail.': 'Rubber Band 在 {speed} 倍速下丢失了所选音频的瞬态或源尾部。',
    'Rubber Band {speed}x is incompatible with the fixed start-delay compensation ({error} ms marker error).': 'Rubber Band 在 {speed} 倍速下与固定起始延迟补偿不兼容（标记误差 {error} 毫秒）。',
    'Audio capability timeout must be positive and finite.': '音频功能检查超时时间必须为有限正数。',
    'Cannot find FFmpeg to verify required Rubber Band audio support.': '未找到 FFmpeg，无法验证所需的 Rubber Band 音频支持。',
    'Cannot inspect FFmpeg for required Rubber Band audio support: {exc}': '无法检查 FFmpeg 的所需 Rubber Band 音频支持：{exc}',
    'Cannot verify FFmpeg identity for audio support: {exc}': '无法验证用于音频支持的 FFmpeg 文件身份：{exc}',
})

# Kept alongside the feature's UI strings; no runtime catalog dependency.
from .i18n_catalog_stabilization import ERROR_ZH_CN as _STABILIZATION_ERRORS
ZH_CN.update(_STABILIZATION_ERRORS)
from .i18n_catalog_restoration import ERROR_ZH_CN as _RESTORATION_ERRORS
ZH_CN.update(_RESTORATION_ERRORS)

# The render wrapper may translate these known layout reasons, and nothing
# else inside its diagnostic field. Caption IDs and inserted values stay raw.
CAPTION_LAYOUT_ZH_CN = {
    'Caption {name} must be an integer from {minimum} to {maximum}.': '字幕的 {name} 必须为 {minimum} 至 {maximum} 范围内的整数。',
    'Caption {name} must be a positive finite number.': '字幕的 {name} 必须为有限正数。',
    'Caption font size must be an integer from 1 to 1000.': '字幕字号必须为 1 至 1000 范围内的整数。',
    'Caption outline width must be an integer from 0 to 100.': '字幕描边宽度必须为 0 至 100 范围内的整数。',
    'Caption line spacing must be an integer from 0 to 1000.': '字幕行间距必须为 0 至 1000 范围内的整数。',
    'Caption maximum lines must be an integer from 1 to 10001.': '字幕最大行数必须为 1 至 10001 范围内的整数。',
    'Caption safe width must be a positive finite number.': '字幕安全宽度必须为有限正数。',
    'Caption safe height must be a positive finite number.': '字幕安全高度必须为有限正数。',
    'Caption text must contain at most {maximum} characters.': '字幕文本最多可包含 {maximum} 个字符。',
    'Caption text must use normalized LF breaks and contain no invalid characters.': '字幕文本必须使用规范的 LF 换行，且不能包含无效字符。',
    'Caption layout could not load the selected local font.': '字幕排版无法加载所选的本地字体。',
    'Caption layout requires Pillow with RAQM text shaping support; reinstall a supported application build.': '字幕排版需要支持 RAQM 文字塑形的 Pillow；请重新安装受支持的应用版本。',
    'Caption has more than {max_lines} manual lines; reduce text or increase the line limit.': '字幕的手动换行超过 {max_lines} 行；请缩短字幕或提高行数上限。',
    'Caption font and outline are too tall for the safe height; reduce font size or margins.': '字幕字体与描边的高度超出安全区域；请减小字号或边距。',
    'Caption needs more than {max_lines} lines; reduce text/font size or increase the line limit.': '字幕需要超过 {max_lines} 行；请缩短字幕、减小字号或提高行数上限。',
    'Caption has too many lines for the safe height; reduce text/font size or margins.': '字幕行数过多，超出安全区域高度；请缩短字幕、减小字号或边距。',
    'Caption contains a grapheme too wide for the safe width; reduce font size, outline, or margins.': '字幕中有不可拆分的字符组合超出安全区域宽度；请减小字号、描边或边距。',
    'Caption cannot wrap punctuation within the safe width; reduce font size, outline, or margins.': '字幕无法在安全区域宽度内合理放置标点并换行；请减小字号、描边或边距。',
    'Caption line exceeds the safe width; reduce font size, outline, or margins.': '字幕行超出安全区域宽度；请减小字号、描边或边距。',
    'Caption ink and outline exceed the safe height; reduce text/font size or margins.': '字幕文字与描边超出安全区域高度；请缩短字幕、减小字号或边距。',
}
ZH_CN.update(CAPTION_LAYOUT_ZH_CN)
ZH_CN.update({
    'Caption {caption_id} cannot fit safely: {exc}': '字幕 {caption_id} 无法容纳于安全区域：{exc}',
})
ZH_CN.update({
 'Pause preservation must be finite and between 0.2 and 1.0.': '停顿保留比例必须是 0.2 至 1.0 之间的有限数值。',
 'speech pause preservation': '配音停顿保留比例',
})
ZH_CN['Invalid pause preservation'] = '停顿保留参数无效'

ZH_CN["speech sample count"] = "语音采样数"

ZH_CN["Generated speech duration does not match its recorded PCM sample count"] = "生成语音的时长与记录的 PCM 采样数不一致"

ZH_CN['Choose an existing local TTS model-pack folder. Nothing is downloaded.'] = '请选择已有的本地 TTS 模型包文件夹。不会下载任何文件。'

ZH_CN.update({
    'Choose an explicitly supported local speech pack version: {versions}.': '请明确选择受支持的本地语音包版本：{versions}。',
    'Melo Chinese-English accepts at most 300 characters per take. Split the reading text explicitly before generating.': 'Melo Chinese-English 每次配音最多支持 300 个字符。请在生成前手动拆分朗读文本。',
    'Melo Chinese-English requires 2 CPU threads.': 'Melo Chinese-English 需要 2 个 CPU 线程。',
    'Incomplete {pack_name} local model pack. See docs/LOCAL_TTS.md; no automatic download is performed.': '{pack_name} 本地模型包不完整。请参阅 docs/LOCAL_TTS.md；不会自动下载。',
    'The model and voice assets do not match the verified {pack_name} pack. Choose the matching version and complete official pack; no download is performed.': '模型和音色文件与已验证的 {pack_name} 模型包不符。请选择匹配版本的完整官方模型包；不会下载。',
    'The local assets do not match the verified {pack_name} pack. Choose the complete official pack; no download is performed.': '本地文件与已验证的 {pack_name} 模型包不符。请选择完整的官方模型包；不会下载。',
    'Engine returned invalid mono {sample_rate} Hz PCM audio.': '引擎返回了无效的单声道 {sample_rate} Hz PCM 音频。',
    'Voice ID {voice_id} is not documented for {pack_name}.': '{pack_name} 的文档中没有音色 ID {voice_id}。',
    'Local speech engine failed. Check the {pack_name} pack and optional sherpa-onnx runtime.': '本地语音引擎失败。请检查 {pack_name} 模型包和可选的 sherpa-onnx 运行环境。',
})
ZH_CN.update({
    'Backend does not match the selected pack': '语音后端与所选模型包不匹配',
    'Unsupported local speech backend': '不支持此本地语音后端',
})
ZH_CN['The speech engine reported unsupported characters (possibly punctuation) or sounds. No audio was saved. Revise the reading text or choose another model.'] = '语音引擎报告了不支持的字符（可能包括标点）或语音。未保存音频。请修改朗读文本或选择其他模型。'

ZH_CN.update({
    'Trim edge must be left or right.': '裁剪边缘必须为左侧或右侧。',
    'Trim must leave at least one timeline frame.': '裁剪后必须至少保留一帧时间线时长。',
    'Trim exceeds the available source media range.': '裁剪超出可用的源素材范围。',
    'Trim would shorten an existing fade. Reduce the fade first.': '裁剪会缩短已有淡入淡出，请先调整淡入淡出时长。',
    'Trim did not preserve the exact timeline boundary.': '裁剪未能保持精确的时间线边界。',
    'Roll requires one immediately adjacent right clip on the same track.': '滚动剪辑需要同轨右侧恰好紧邻一个片段。',
    'Roll must leave at least one timeline frame in each clip.': '滚动后每个片段必须至少保留一帧时间线时长。',
    'Roll exceeds the available source media range.': '滚动剪辑超出可用的源素材范围。',
    'Roll would shorten an existing fade. Reduce the fade first.': '滚动剪辑会缩短已有淡入淡出，请先调整淡入淡出时长。',
    'Roll did not preserve the exact timeline boundary.': '滚动剪辑未能保持精确的时间线边界。',
})

ZH_CN['This roll control supports timeline cuts up to 2147483647 frames.'] = '此滚动控件支持最大 2147483647 帧的时间线接点。'

# Sound-library model preconditions.
ZH_CN.update({
    'Invalid sound provenance': '音效来源记录无效',
    'Invalid sound origin': '音效来源类型无效',
    'Sound identity must be a SHA-256 digest': '音效身份必须是 SHA-256 摘要',
    'Sound insertion requires provenance': '插入音效必须保留来源记录',
    'Sound effects must be zero-offset audio-only media': '音效必须是无时间偏移的纯音频素材',
    'Sound duration does not match its recorded PCM sample count': '音效时长与记录的 PCM 采样数不一致',
    'Sound media identity conflict': '音效素材身份冲突',
    'Legacy projects cannot contain sound provenance': '旧版项目不能包含音效来源记录',
})

ZH_CN.update({'An existing sound asset conflicts with this import.': '已有音效资源与本次导入冲突。',
 'Audio metadata is invalid.': '音频元数据无效。',
 'Built-in sound assets are missing.': '内置音效资源缺失。',
 'Cannot decode this audio file. Check its format and integrity.': '无法解码此音频文件，请检查格式和完整性。',
 'Cannot safely import the local sound.': '无法安全导入本地音效。',
 'Cannot safely save the portable sound asset.': '无法安全保存便携音效资源。',
 'Cannot write decoded sound safely.': '无法安全写入解码后的音效。',
 'Choose a supported audio file, not a video, playlist, or URL.': '请选择支持的音频文件，不要选择视频、播放列表或网址。',
 'Choose a valid local path.': '请选择有效的本地路径。',
 'Choose an audio-only file; cover artwork is allowed.': '请选择纯音频文件，可包含封面图片。',
 'Choose an ordinary local sound file or folder.': '请选择普通的本地音效文件或文件夹。',
 'Decoded sound contains invalid samples.': '解码后的音效包含无效采样。',
 'Decoded sound is empty, invalid, or longer than 60 seconds.': '解码后的音效为空、无效或超过 60 秒。',
 'Existing sound metadata is invalid.': '已有音效元数据无效。',
 'FFmpeg and ffprobe must be installed to import audio.': '导入音频需要安装 FFmpeg 和 ffprobe。',
 'Library sounds must be PCM16, 48 kHz, mono/stereo, and at most 60 seconds.': '音效库仅支持 48 kHz、PCM16、单声道或双声道、最长 60 '
                                                                               '秒的音效。',
 'Local sound names must match their content hash.': '本地音效文件名必须与内容校验值一致。',
 'Local sound sidecar name does not match its hash.': '本地音效元数据文件名与校验值不一致。',
 'Network paths and URLs are not supported.': '不支持网络路径或网址。',
 'Only local sound version 1 is supported.': '仅支持第 1 版本地音效格式。',
 'Only sound catalog version 1 is supported.': '仅支持第 1 版音效目录。',
 'Portable sound destination must be a WAV file.': '便携音效的目标必须是 WAV 文件。',
 'Sound WAV exceeds its safe peak limit.': '音效 WAV 超过安全峰值限制。',
 'Sound WAV is truncated or has an invalid sample count.': '音效 WAV 不完整或采样数量无效。',
 'Sound catalog contains duplicate identifiers.': '音效目录包含重复标识。',
 'Sound category is not supported.': '不支持此音效分类。',
 'Sound changed since import; select or import it again.': '音效在导入后已更改，请重新选择或导入。',
 'Sound decoder exceeded its output limit.': '音效解码器输出超过限制。',
 'Sound decoding timed out.': '音效解码超时。',
 'Sound destination changed during import.': '导入过程中音效目标位置已更改。',
 'Sound file cannot be read: {name}.': '无法读取音效文件：{name}。',
 'Sound file exceeds its size limit.': '音效文件超过大小限制。',
 'Sound files must be contained WAV basenames.': '音效文件必须是库内的 WAV 文件名，不得包含目录。',
 'Sound folder cannot be accessed safely.': '无法安全访问音效文件夹。',
 'Sound folders must not use symbolic links or junctions.': '音效文件夹不能使用符号链接或目录联接。',
 'Sound import cancelled.': '已取消音效导入。',
 'Sound input changed while being opened.': '打开过程中音效输入已更改。',
 'Sound input changed while being read.': '读取过程中音效输入已更改。',
 'Sound input must be an ordinary file within the size limit.': '音效输入必须是大小限制内的普通文件。',
 'Sound metadata contains a duplicate field.': '音效元数据包含重复字段。',
 'Sound metadata contains an invalid label.': '音效元数据包含无效名称。',
 'Sound metadata does not match its WAV samples.': '音效元数据与 WAV 采样不一致。',
 'Sound metadata is incomplete.': '音效元数据不完整。',
 'Sound metadata must be an object.': '音效元数据必须是对象。',
 'Sound metadata must be valid UTF-8 JSON.': '音效元数据必须是有效的 UTF-8 JSON。',
 'Sound metadata must use finite JSON values.': '音效元数据必须使用有限的 JSON 数值。',
 'Sound must be a valid PCM16 WAV file.': '音效必须是有效的 PCM16 WAV 文件。',
 'Sound must have a finite duration of at most 60 seconds.': '音效时长必须是最长 60 秒的有限值。',
 'Sound peak metadata does not match its WAV samples.': '音效峰值元数据与 WAV 采样不一致。',
 'Sound provenance metadata is invalid.': '音效来源元数据无效。',
 'Sound requires a valid SHA-256 digest.': '音效需要有效的 SHA-256 校验值。',
 'Sound sample or peak metadata is invalid.': '音效采样或峰值元数据无效。',
 'Sound source changed during import; import it again.': '导入过程中音效源文件已更改，请重新导入。',
 'Sound source is empty.': '音效源文件为空。',
 'Sound tags are invalid.': '音效标签无效。',
 'URLs and parent traversal are not supported.': '不支持网址或上级目录跳转。'})

ZH_CN.update({
    'Only external CC0 sounds may carry third-party source fields.': '仅外部 CC0 音效可包含第三方来源字段。',
    'External sounds require a recorded CC0-1.0 license.': '外部音效必须记录 CC0-1.0 许可。',
    'External sound source metadata is incomplete or invalid.': '外部音效的来源信息不完整或无效。',
    'Sound source links must be public HTTPS URLs.': '音效来源链接必须是公开的 HTTPS 网址。',
    'External sounds require the official CC0 license link.': '外部音效必须提供官方 CC0 许可链接。',
    'Sound size metadata does not match its WAV file.': '音效大小信息与 WAV 文件不一致。',
    'Sound size metadata is invalid.': '音效大小信息无效。',
    'Legacy projects cannot contain external sound source fields': '旧版项目不能包含外部音效来源字段',
})

# Controlled-edit gate errors also use the application's standard error renderer.
from .i18n_catalog_controlled_assistant import ERROR_ZH_CN as _CONTROLLED_ERRORS
ZH_CN.update(_CONTROLLED_ERRORS)

# Static picture transform schema and video-only editing preconditions.
ZH_CN.update({
    'Transform requires a picture clip on a video track': '画面变换仅支持视频轨上的画面片段',
    'Version 16 clips require transform settings': '版本 16 的片段必须包含画面变换设置',
    'Legacy clips cannot contain transform settings': '旧版本片段不能包含画面变换设置',
    'Invalid clip transform': '画面变换设置无效',
    'Transform requires exactly x, y, scale, rotation, mirror_x and mirror_y': '画面变换必须包含完整的位置、缩放、旋转和镜像设置',
    'Transform canvas dimensions must be integers in 1..16384': '变换画布尺寸必须为 1 至 16384 的整数',
    'Transform mirror_x must be a boolean': '水平镜像必须为布尔值',
    'Transform mirror_y must be a boolean': '垂直镜像必须为布尔值',
})

# Explicit SDR delivery-policy and encoder validation errors.
from .i18n_catalog_color_foundation import ERROR_MESSAGES
ZH_CN.update(ERROR_MESSAGES)

from .i18n_catalog_grade_transfer import ERROR_ZH_CN as _GRADE_TRANSFER_ERRORS
ZH_CN.update(_GRADE_TRANSFER_ERRORS)

from .i18n_catalog_selective_color import ERROR_ZH_CN as _SELECTIVE_COLOR_ERRORS
ZH_CN.update(_SELECTIVE_COLOR_ERRORS)

from .i18n_catalog_qualifier import ERROR_ZH_CN as _QUALIFIER_ERRORS
ZH_CN.update(_QUALIFIER_ERRORS)

# Independent inclusion/exclusion sets and exact legacy schema validation.
ZH_CN.update({
    'Qualifier range requires exactly its documented fields': '限定器色彩范围必须包含完整且仅限已定义的字段',
    'Qualifier range sets must be bounded lists': '限定器色彩范围组必须为限定数量的列表',
    'Qualifier range sets must be lists of at most 8 entries': '限定器每组色彩范围必须为最多 8 项的列表',
    'Qualifier range sets require typed range records': '限定器色彩范围组必须包含有效的范围记录',
    'Qualifier exclusion mode must be a boolean': '限定器排除模式必须为布尔值',
    'Qualifier range set is full (maximum 8)': '这组色彩范围已满（最多 8 项），请先移除一项再取样',
    'Version {version} requires exactly the legacy qualifier fields': '版本 {version} 必须包含完整且仅限旧版限定器的字段',
})

ZH_CN['Render timed out during float grading.'] = '浮点调色渲染超时。'

from .i18n_catalog_contrast_pivot import ERROR_ZH_CN as _CONTRAST_PIVOT_ERRORS
ZH_CN.update(_CONTRAST_PIVOT_ERRORS)

ZH_CN.update({
    'Enable the selected channel before sampling its feather': '请先启用所选通道，再采样其柔边',
    'Qualifier hue_enabled must be a boolean': '限定器色相启用状态必须为布尔值',
    'Qualifier saturation_enabled must be a boolean': '限定器饱和度启用状态必须为布尔值',
    'Qualifier luma_enabled must be a boolean': '限定器亮度启用状态必须为布尔值',
})

from .i18n_catalog_white_balance import ERROR_ZH_CN as _WHITE_BALANCE_ERRORS
ZH_CN.update(_WHITE_BALANCE_ERRORS)

from .i18n_catalog_vignette import ERROR_ZH_CN as _VIGNETTE_ERRORS
ZH_CN.update(_VIGNETTE_ERRORS)

from .i18n_catalog_apple_log import ERROR_MESSAGES as _APPLE_LOG_ERRORS
ZH_CN.update(_APPLE_LOG_ERRORS)

ZH_CN["Selected clips cannot start before frame zero"] = "选中的片段不能移动到时间零点之前"

ZH_CN.update({
    "Clip IDs must be a nonempty collection of unique strings": "片段 ID 必须是非空且不重复的字符串集合",
    "Move delta must be an integer": "移动帧数必须是整数",
    "Duplicate selected clip: {clip_id}": "重复选择了片段：{clip_id}",
})

from .i18n_catalog_clip_composite import ERROR_ZH_CN as _CLIP_COMPOSITE_ERRORS
ZH_CN.update(_CLIP_COMPOSITE_ERRORS)

from .i18n_catalog_clip_motion import ERROR_ZH_CN as _CLIP_MOTION_ERRORS
ZH_CN.update(_CLIP_MOTION_ERRORS)

from .i18n_catalog_picture_animation import ZH_CN as _PICTURE_ANIMATION_ERRORS
ZH_CN.update(_PICTURE_ANIMATION_ERRORS)

from .i18n_catalog_video_transition import ERROR_ZH_CN as _VIDEO_TRANSITION_ERRORS
ZH_CN.update(_VIDEO_TRANSITION_ERRORS)

from .i18n_catalog_audio_analysis import ERROR_ZH_CN as _AUDIO_ANALYSIS_ERRORS
ZH_CN.update(_AUDIO_ANALYSIS_ERRORS)

ZH_CN.update({
    'master output gain must be finite and in -96..24': '主输出增益必须为 -96 至 24 dB 范围内的有限数值',
    'Version 34 projects require master output gain': '版本 34 项目必须包含主输出增益',
    'Legacy projects cannot contain master output gain': '旧版项目不能包含主输出增益',
})

from .i18n_catalog_loudness_proposal_errors import ZH_CN as _LOUDNESS_PROPOSAL_ERRORS
ZH_CN.update(_LOUDNESS_PROPOSAL_ERRORS)

# Nondestructive media bins (schema 35).
ZH_CN.update({
    'Cannot remove media used by timeline clips': '无法移除时间线上仍在使用的素材',
    'Duplicate media bin id': '素材箱标识重复',
    'Invalid media bin': '素材箱无效',
    'Invalid media bin fields': '素材箱字段无效',
    'Invalid prepared media entry': '待导入的素材条目无效',
    'Legacy projects cannot contain media bin fields': '旧版项目不能包含素材箱字段',
    'Media IDs must be a collection': '素材标识必须为集合',
    'Media bin depth exceeds 16': '素材箱层级不能超过 16 层',
    'Media bin membership must be an object': '素材箱归属必须为对象',
    'Media bin membership references unknown media or bin': '素材箱归属引用了未知素材或素材箱',
    'Media bin names must be at most 128 characters': '素材箱名称不能超过 128 个字符',
    'Media bin references unknown parent': '素材箱引用了未知的父素材箱',
    'Media bins cannot contain cycles': '素材箱不能循环嵌套',
    'Media bins must be a list of at most 1024 entries': '素材箱必须为最多包含 1024 项的列表',
    'Only empty media bins can be deleted': '只能删除空的素材箱',
    'Unknown media bin': '未知素材箱',
    'Unknown media in batch': '批量操作包含未知素材',
    'Version 35 projects require media bins and membership': '版本 35 项目必须包含素材箱及素材归属',
    'Invalid media bin id': '素材箱标识无效',
    'Invalid media bin name': '素材箱名称无效',
    'Invalid media bin parent id': '父素材箱标识无效',
    'Invalid media membership id': '素材归属标识无效',
    'Invalid media membership bin id': '素材归属的素材箱标识无效',
})

from .i18n_catalog_track_edit_locks import ERROR_ZH_CN as _TRACK_EDIT_LOCK_ERRORS
ZH_CN.update(_TRACK_EDIT_LOCK_ERRORS)

from .i18n_catalog_source_overwrite import ERROR_ZH_CN as _SOURCE_OVERWRITE_ERRORS
ZH_CN.update(_SOURCE_OVERWRITE_ERRORS)

from .i18n_catalog_source_ranges import ERROR_ZH_CN as _SOURCE_RANGE_ERRORS
ZH_CN.update(_SOURCE_RANGE_ERRORS)

from .i18n_catalog_ripple_delete import ERROR_ZH_CN as _RIPPLE_DELETE_ERRORS
ZH_CN.update(_RIPPLE_DELETE_ERRORS)

from .i18n_catalog_gap_close import ERROR_ZH_CN as _GAP_CLOSE_ERRORS
ZH_CN.update(_GAP_CLOSE_ERRORS)

from .i18n_catalog_timeline_markers import ERROR_ZH_CN as _TIMELINE_MARKER_ERRORS
ZH_CN.update(_TIMELINE_MARKER_ERRORS)

from .i18n_catalog_clip_copy_paste import ERROR_ZH_CN as _CLIP_COPY_PASTE_ERRORS
ZH_CN.update(_CLIP_COPY_PASTE_ERRORS)

from .i18n_catalog_source_audio_separation import ERROR_ZH_CN as _SOURCE_AUDIO_SEPARATION_ERRORS
ZH_CN.update(_SOURCE_AUDIO_SEPARATION_ERRORS)

from .i18n_catalog_source_audio_reattach import ERROR_ZH_CN as _SOURCE_AUDIO_REATTACH_ERRORS
ZH_CN.update(_SOURCE_AUDIO_REATTACH_ERRORS)

from .i18n_catalog_audio_gain import ERROR_ZH_CN as _AUDIO_GAIN_ERRORS
ZH_CN.update(_AUDIO_GAIN_ERRORS)

from .i18n_catalog_final_frame_preview import ERROR_ZH_CN as _FINAL_FRAME_ERRORS
ZH_CN.update(_FINAL_FRAME_ERRORS)

from .i18n_catalog_linked_av import ERROR_ZH_CN as _LINKED_AV_ERRORS
ZH_CN.update(_LINKED_AV_ERRORS)

from .i18n_catalog_linked_audio_roll import ERROR_ZH_CN as _LINKED_AUDIO_ROLL_ERRORS
ZH_CN.update(_LINKED_AUDIO_ROLL_ERRORS)

from .i18n_catalog_range_preview import ERROR_ZH_CN as _RANGE_PREVIEW_ERRORS
ZH_CN.update(_RANGE_PREVIEW_ERRORS)
