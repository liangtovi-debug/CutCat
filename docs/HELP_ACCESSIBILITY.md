# 可滚动的版本帮助 / Accessible build help

修复继承的 QMessageBox 长文本在小屏幕上溢出问题。正文可选择、复制、键盘滚动、自动换行；关闭按钮固定在滚动区之外。按可用屏幕逻辑像素限制尺寸，并为窗口边框预留空间；不缩小字体，不删除警告。Tab/Shift-Tab 切换正文与关闭按钮，Enter/Escape 或点击关闭均可退出。

Preserves the original complete prototype, transition endpoint, rendered-preview, missing-feature, native-release and audio safety text. Adds narrowly bounded clarification for existing clip transform keyframes, manual animated color windows (not tracking), ProRes 10-bit SDR export versus 8-bit preview, and explicitly confirmed measured constant gain (not dynamic normalization/limiting). No network navigation or links are required to read any warning.

Current source audit: the accepted previous About text no longer says all animation is absent or exports are 8-bit only. “General clip keyframes” and “tracked color windows” remain unimplemented general capabilities, not denials of the existing bounded animation tools. Existing audio language says no automatic gain; the optional explicit-Apply proposal is not automatic gain. Preserve these distinctions rather than claiming broad parity.

Implementation touches only help presentation, translations and documentation. Existing tests that intercepted QMessageBox now intercept HelpDialog.exec; their content assertions are unchanged. No model/schema, render, audio, dependency, menu/shortcut definitions or toolbar changes.

Validation scope: real Linux Qt offscreen geometry and input events, English/Chinese, DPR 1/1.5/2, fonts 10/16/20 points, available logical screens 800×800, 360×480 and 320×360. Checks include full text retention, actual CJK glyph shaping, readonly selection, end-of-document reachability, persistent Close, Tab/Shift-Tab, Enter, Escape and repeated reopening. Independent review, full repository regression and a fresh archive extraction are separate required receipts, not implied by this document.

Limitations: not native Windows/macOS release testing, OS shortcut delivery, screen-reader certification or monitor/hardware verification. Extremely tiny displays unable to fit a single standard control are outside the tested range. No native packages or paid-editor parity claimed. Competitor feature expansion is intentionally not the purpose of this accessibility repair.
