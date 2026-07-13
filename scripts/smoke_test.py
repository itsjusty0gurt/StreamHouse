from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from core.logger import Logger
from ui.main_window import MainWindow


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        os.environ["SALLY_DATA_DIR"] = directory
        Logger.setup(clear_latest=True)
        application = QApplication([])
        window = MainWindow(auto_upgrade_permissions=False)
        window.show()
        QTimer.singleShot(250, window.close)
        QTimer.singleShot(300, application.quit)
        exit_code = application.exec()
        Logger.shutdown()
        return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
