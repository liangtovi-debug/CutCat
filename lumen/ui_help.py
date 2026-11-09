"""Scrollable build-support text; no project state or network navigation."""
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTextEdit, QVBoxLayout
from .branding import display_name
from .i18n import tr
from .ui_clip_groups import GROUP_HELP
from .ui_linked_av import LINK_HELP
from .ui_clip_copy_paste import COPY_HELP
from .ui_source_audio_separation import SEPARATION_SCOPE, SEPARATION_LIMITS, SEPARATION_CHECK
from .ui_timeline_markers import MARKER_HELP
from .i18n_catalog_source_overwrite import SOURCE_OVERWRITE_HELP
from .i18n_catalog_source_ranges import SOURCE_RANGES_HELP
from .i18n_catalog_ripple_delete import RIPPLE_DELETE_HELP
from .i18n_catalog_gap_close import GAP_CLOSE_HELP


def help_text():
    return tr('{brand} is an independent desktop editor prototype.\n\nSupported: local SDR video/audio, two picture tracks with embedded source audio plus two audio tracks, frame-based cuts/trims/moves, snapping, undo, color controls and LUT, gain/mute/fades, atomic projects, PNG/animated-pack stickers with transform keyframes, ordered blur/vignette effects, subtitle editing/SRT, optional local whisper.cpp transcription, explicit-local Kokoro voice generation from reviewed subtitles, and rendered export.\n\nCross Dissolve: video-only blending between two adjacent clips on the same video track, using verified real source handles. This version requires 1x speed and complete whole-frame ranges from CFR sources. Audio still cuts at the original boundary. Clip motion, animated color windows, non-default picture-in-picture compositing, active clip effects and active stabilization are not supported on transition endpoints.\n\nThe viewer plays a rendered cache from the same graph as export. Rebuild after edits. Scopes measure that decoded cache. This is not live GPU rendering or a calibrated HDR grading monitor.\n\nNot implemented yet: general transitions, titles, general clip keyframes, proxies, tracked color windows, OCIO/HDR, multicam and interchange.\n\nWindows/macOS source and packaging targets are provided. Platform release validation and signing remain separate gates.', brand=display_name()) + '\n\n' + tr('Timeline → Analyze timeline audio measures integrated LUFS, sample peaks and estimated true peaks (dBTP) of the final stereo mix after master output gain, before encoding. Overload counts and first overload time refer only to original samples. To adjust the whole mix, use Timeline → Master output gain; for one clip, edit clip gain in the Audio inspector. Apply, build preview and listen, then analyze again. Results are session-only. Gain does not prevent clipping. No automatic gain, limiter or encoded-file certification.') + "\n\n" + tr('Current bounded tools also include clip transform keyframes, manually animated color windows (not tracking), and H.264 8-bit or ProRes 422 HQ 10-bit SDR export. Preview and scopes remain 8-bit. Each export profile retains its own content restrictions and runtime checks. The audio report can measure a separate constant-gain proposal before you explicitly apply it; this is not dynamic normalization or limiting. Native Windows/macOS release packages and hardware behavior are not verified by local source tests. This build does not claim paid-editor feature parity.') + '\n\n' + tr('Media bins organize project entries only: nested bins, scoped name/path search, type/usage filters and explicit moves. Only empty bins and unused media entries can be removed; original files are never moved or deleted. This is not Smart Bins, semantic search or proxies.') + '\n\n' + tr(LINK_HELP) + '\n\n' + tr(GROUP_HELP) + '\n\n' + tr(COPY_HELP) + '\n\n' + tr('Edit → Separate source audio… creates independent clips after explicit preview and Apply.') + '\n\n' + tr(SEPARATION_SCOPE) + '\n\n' + tr(SEPARATION_LIMITS) + '\n\n' + tr(SEPARATION_CHECK) + '\n\n' + tr(SOURCE_OVERWRITE_HELP) + '\n\n' + tr(SOURCE_RANGES_HELP) + "\n\n" + tr(RIPPLE_DELETE_HELP) + "\n\n" + tr(GAP_CLOSE_HELP) + "\n\n" + tr(MARKER_HELP)


class HelpDialog(QDialog):
    """Keep the close action outside the scrollable, selectable plain text."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('buildSupportDialog')
        self.setWindowTitle(tr('{brand} · About', brand=display_name()))
        self.setModal(True)
        layout = QVBoxLayout(self)
        self.body = QTextEdit(self)
        self.body.setObjectName('buildSupportText')
        self.body.setReadOnly(True)
        self.body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.body.setAcceptRichText(False)
        self.body.setTabChangesFocus(True)
        self.body.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body.setMinimumSize(0, 0)
        self.body.setAccessibleName(tr('Build support and limitations'))
        self.body.setPlainText(help_text())
        self.body.installEventFilter(self)
        layout.addWidget(self.body, 1)
        self.buttons = QDialogButtonBox(self)
        self.close_button = self.buttons.addButton(tr('Close'), QDialogButtonBox.ButtonRole.AcceptRole)
        self.close_button.setDefault(True)
        self.buttons.accepted.connect(self.accept)
        layout.addWidget(self.buttons)
        self.resize(640, 640)
        self.close_button.setFocus()

    def eventFilter(self, watched, event):
        if (watched is self.body and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() == Qt.KeyboardModifier.NoModifier):
            self.accept()
            return True
        return super().eventFilter(watched, event)

    def showEvent(self, event):
        super().showEvent(event)
        self.fit_available_screen()

    def fit_available_screen(self):
        # Logical pixels, not device pixels. Reserve space for platform chrome
        # before layout so even large fonts never force the text to full height.
        available = self.screen().availableGeometry()
        width = max(1, available.width() - 32)
        height = max(1, available.height() - 64)
        self.setMaximumSize(width, height)
        self.resize(min(self.width(), width), min(self.height(), height))
        frame = self.frameGeometry()
        frame.moveCenter(available.center())
        self.move(frame.topLeft())
