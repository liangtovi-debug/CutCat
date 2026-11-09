"""Static mathematical vignette shape controls and honest processing limits."""
ERROR_ZH_CN = {
    'Invalid vignette capability probe dimensions.': '暗角能力检查的画面尺寸无效。',
    'Invalid clip effect radius or feather; expected 0.25 to 2.': '片段效果的半径或羽化无效；应为 0.25 至 2。',
    'Invalid clip effect: blur radius and feather must be 1.': '片段效果无效：模糊效果的暗角半径和羽化必须为 1。',
    'Version 28 effects require vignette radius and feather': '版本 28 的效果必须包含暗角半径和羽化',
    'Legacy projects cannot contain vignette radius or feather': '旧版项目不能包含暗角半径或羽化',
    'Blur effects require default vignette radius and feather': '模糊效果必须保持默认暗角半径和羽化',
    'vignette radius must be finite and in 0.25..2': '暗角半径必须为 0.25 至 2 之间的有限数值',
    'vignette feather must be finite and in 0.25..2': '暗角羽化必须为 0.25 至 2 之间的有限数值',
}
ZH_CN = {
    **ERROR_ZH_CN,
    'Vignette radius': '暗角半径',
    'Vignette feather': '暗角羽化',
    'Larger radius protects a wider center; smaller radius darkens more of the image. 1.000 preserves the original radius.': '半径越大，受保护的中心区域越宽；半径越小，更多画面变暗。1.000 保持原有半径。',
    'Higher feather spreads darkening inward; lower feather keeps more of the center clear. 1.000 preserves the original falloff.': '羽化越高，暗部过渡越向内扩散；羽化越低，中心保留得越多。1.000 保持原有衰减。',
    'Stage settings, then Apply and build preview.': '调整后请应用设置，再生成预览。',
    'Centered mathematical vignette, not DaVinci Resolve parity or lens simulation. Shape changes use per-pixel CPU processing and can slow preview/export. No live preview or animated controls. Default shape preserves legacy rendering; changing shape switches preview/H.264 to RGB processing and can change color.': '居中数学暗角，不保证与 DaVinci Resolve 一致，也不是镜头模拟。形状调整使用逐像素 CPU 运算，可能减慢预览／导出。不提供实时预览或动画控制。默认形状保留旧版渲染；调整形状会将预览／H.264 切换到 RGB 处理，可能改变颜色。',
    'Vignette radius / feather: 0.25–2; defaults 1 / 1 keep the original shape. Larger radius protects a wider center; higher feather spreads darkening inward. Preview / H.264 keep the legacy 8-bit path at defaults and use RGB8 for changed shapes; ProRes uses RGB16. These paths are not bit-identical. Shape processing is CPU-based and can be slow.': '暗角半径／羽化：0.25–2；默认 1／1 保持原有形状。半径越大，中心保护范围越宽；羽化越高，暗部过渡越向内扩散。预览／H.264 在默认值下保持旧版 8 位路径，调整形状后使用 RGB8；ProRes 使用 RGB16。不同路径并非逐位一致。形状处理依赖 CPU，可能较慢。',
}
EN = {source: source for source in ZH_CN}
