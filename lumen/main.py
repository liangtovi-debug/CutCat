from __future__ import annotations
import os
import sys
from pathlib import Path

from .branding import display_name

def main(argv=None):
    args = list(argv or sys.argv)
    if len(args) == 4 and args[1] == '--lumen-tts-worker':
        from .tts_worker import main as worker_main
        return worker_main(args[2:])
    if len(args) == 4 and args[1] == '--lumen-search-worker':
        from .semantic_search_worker import main as worker_main
        return worker_main(args[2:])
    if len(args) == 3 and args[1] == '--lumen-self-test':
        from .self_test import run
        return run(args[2])
    from PySide6.QtWidgets import QApplication, QMessageBox
    from PySide6.QtGui import QIcon
    app = QApplication(argv or sys.argv)
    from .caption_runtime import CaptionRuntimeError, require_capability
    try:
        require_capability()
    except CaptionRuntimeError as error:
        QMessageBox.critical(None, display_name(), str(error))
        return 2
    from .ui import MainWindow
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parent / 'assets' / 'app' / 'cutcat.png')))
    app.setApplicationName('Lumen Edit')
    app.setApplicationDisplayName(display_name())
    app.setOrganizationName('LumenEditPrototype')
    window = MainWindow()
    app.setApplicationDisplayName(display_name())
    window.show()
    return app.exec()

if __name__ == '__main__':
    raise SystemExit(main())
