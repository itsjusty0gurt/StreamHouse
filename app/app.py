import sys

from PySide6.QtWidgets import QApplication

from core.logger import Logger
from ui.main_window import MainWindow


def run() -> None:
    Logger.info("Creating Qt application.", source="UI")

    application = QApplication(sys.argv)
    application.setApplicationName("Sally AI Bot")
    application.setOrganizationName("Sally AI")

    Logger.info("Creating main window.", source="UI")

    window = MainWindow()
    window.show()

    Logger.info("Main window displayed.", source="UI")
    Logger.info("Qt event loop started.", source="UI")

    exit_code = application.exec()

    Logger.info(
        f"Qt event loop ended with exit code {exit_code}.",
        source="UI",
    )

    sys.exit(exit_code)