"""Window-private In/Out selection; never project markers or Undo records.

Out denotes the displayed final frame; renderer windows remain half-open.
Marks survive same-editor edits/Undo at fixed frames, but never another session.
"""
from .i18n import tr


def selection(window):
    owner = (window.editor, window.editor.session_id)
    if getattr(window, '_preview_selection_owner', None) != owner:
        window._preview_selection_owner = owner
        window._preview_marks = (None, None)
    return window._preview_marks


def selection_error(window):
    start, end = selection(window)
    if start is None or end is None:
        return tr('Set both preview In and Out on the timeline first.')
    if not 0 <= start < end <= window.project.duration_frames:
        return tr('Preview In must precede Out within the current timeline. Out includes its frame.')
    if end - start > 600 or end - start > 10 * window.project.fps:
        return tr('Range preview supports at most 10 seconds and 600 frames.')
    return None


def selection_text(window):
    start, end = selection(window)
    return tr('Session preview · In {start} · Out {end} (inclusive)',
              start='—' if start is None else start, end='—' if end is None else end - 1)


def refresh_selection(window):
    window.timeline.preview_selection = selection(window)
    window.timeline.update()


def set_mark(window, kind):
    if window.closing:
        return
    start, end = selection(window)
    if kind == 'clear':
        start = end = None
    elif window.project.duration_frames > 0:
        frame = min(max(0, window.timeline.playhead), window.project.duration_frames - 1)
        if kind == 'in':
            start = frame
        elif kind == 'out':
            end = frame + 1
    window._preview_marks = (start, end)
    refresh_selection(window)
    window.statusBar().showMessage(selection_text(window), 8000)
    dialog = getattr(window, 'range_preview_dialog', None)
    if dialog is not None:
        dialog.check_current()


def preview_selection(window):
    from .ui_range_preview import open_range_preview
    dialog = open_range_preview(window, selected=True)
    if dialog is not None:
        dialog.loop_checkbox.setChecked(True)
        dialog.build_range(autoplay=True)
    return dialog
