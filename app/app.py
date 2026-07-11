import sys

from PySide6.QtWidgets import QApplication, QLabel, QMainWindow


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("Sally AI")
        self.resize(900, 600)

        label = QLabel("Sally AI\nStatus: Offline")
        label.setStyleSheet("font-size: 24px; padding: 30px;")

        self.setCentralWidget(label)


def run() -> None:
    application = QApplication(sys.argv)

    window = MainWindow()
    window.show()

    application.exec()