from PySide6.QtCore import Slot
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("Sally AI")
        self.resize(900, 600)

        self.status_label = QLabel("Status: Offline")
        self.status_label.setStyleSheet("font-size: 20px;")

        self.connect_button = QPushButton("Connect")
        self.connect_button.setFixedWidth(140)
        self.connect_button.clicked.connect(self.toggle_connection)

        layout = QVBoxLayout()
        layout.setContentsMargins(36, 36, 36, 36)
        layout.setSpacing(18)
        layout.addWidget(QLabel("Sally AI"))
        layout.addWidget(self.status_label)
        layout.addWidget(self.connect_button)
        layout.addStretch()

        container = QWidget()
        container.setLayout(layout)

        self.setCentralWidget(container)

    @Slot()
    def toggle_connection(self) -> None:
        is_offline = self.status_label.text() == "Status: Offline"

        if is_offline:
            self.status_label.setText("Status: Online")
            self.connect_button.setText("Disconnect")
        else:
            self.status_label.setText("Status: Offline")
            self.connect_button.setText("Connect")