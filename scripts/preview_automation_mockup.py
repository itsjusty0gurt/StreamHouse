"""Open the editable Automation UI mockup without starting Sally."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QFile, QIODevice
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import QApplication


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Sally Automation UI Mockup")

    ui_path = (
        Path(__file__).resolve().parents[1]
        / "ui"
        / "mockups"
        / "automation_routines_mockup.ui"
    )
    ui_file = QFile(str(ui_path))
    if not ui_file.open(QIODevice.OpenModeFlag.ReadOnly):
        raise RuntimeError(f"Could not open mockup: {ui_path}")

    try:
        window = QUiLoader().load(ui_file)
    finally:
        ui_file.close()
    if window is None:
        raise RuntimeError(f"Could not load mockup: {ui_path}")

    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
