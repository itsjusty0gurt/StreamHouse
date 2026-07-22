from __future__ import annotations

import os
import sys
from threading import Thread

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from config.version import VERSION
from core.logger import Logger
from core.resources import resource_path
from sally_companion.protocol import PROTOCOL_VERSION
from sally_companion.server import CompanionReasoningService, create_server
from sally_companion.settings import CompanionSettings, CompanionSettingsStore


class CompanionWindow(QMainWindow):
    def __init__(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        super().__init__()
        self.setWindowTitle("Sally AI Companion")
        self.setMinimumSize(420, 180)
        self.settings_store = CompanionSettingsStore()
        try:
            settings = self.settings_store.load()
        except (OSError, ValueError):
            settings = CompanionSettings()
        self.reasoning_service = CompanionReasoningService(
            companion_settings=settings,
            settings_store=self.settings_store,
        )
        self._server = create_server(host, port, self.reasoning_service)
        self._server_thread = Thread(
            target=self._server.serve_forever,
            name="sally-companion-http",
            daemon=True,
        )
        self._server_thread.start()

        body = QWidget(self)
        layout = QVBoxLayout(body)
        title = QLabel("Sally AI Companion")
        title.setStyleSheet("font-size: 22px; font-weight: 600;")
        layout.addWidget(title)
        layout.addWidget(QLabel(f"Connected locally at http://{host}:{port}"))
        layout.addWidget(QLabel(f"Protocol version {PROTOCOL_VERSION}"))
        layout.addWidget(
            QLabel(
                "Keep this app open when Sally Bot should reason, reply, or "
                "propose memories. Twitch, OBS, and automation remain in the Bot."
            )
        )
        form = QFormLayout()
        self.ollama_endpoint_edit = QLineEdit(settings.ollama_endpoint)
        self.model_edit = QLineEdit(settings.model)
        self.personality_edit = QTextEdit(settings.personality)
        self.personality_edit.setMinimumHeight(100)
        self.mild_profanity_check = QCheckBox("Allow occasional mild profanity")
        self.mild_profanity_check.setChecked(settings.allow_mild_profanity)
        self.strong_profanity_check = QCheckBox("Allow strong profanity")
        self.strong_profanity_check.setChecked(settings.allow_strong_profanity)
        form.addRow("Ollama endpoint", self.ollama_endpoint_edit)
        form.addRow("Model", self.model_edit)
        form.addRow("Personality", self.personality_edit)
        form.addRow("", self.mild_profanity_check)
        form.addRow("", self.strong_profanity_check)
        layout.addLayout(form)
        self.save_button = QPushButton("Save Companion Settings")
        self.status_label = QLabel("")
        self.save_button.clicked.connect(self._save_settings)
        layout.addWidget(self.save_button)
        layout.addWidget(self.status_label)
        layout.addStretch()
        self.setCentralWidget(body)

    def _save_settings(self) -> None:
        values = {
            "ollama_endpoint": self.ollama_endpoint_edit.text(),
            "model": self.model_edit.text(),
            "personality": self.personality_edit.toPlainText(),
            "allow_mild_profanity": self.mild_profanity_check.isChecked(),
            "allow_strong_profanity": self.strong_profanity_check.isChecked(),
        }
        settings = CompanionSettings.from_dict(values)
        try:
            self.settings_store.save(settings)
        except OSError as error:
            self.status_label.setText(f"Could not save: {error}")
            return
        self.reasoning_service.settings = settings
        self.status_label.setText("Companion settings saved.")

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._server.shutdown()
        self._server.server_close()
        self._server_thread.join(timeout=2.0)
        super().closeEvent(event)


def run() -> None:
    application = QApplication(sys.argv)
    application.setApplicationName("Sally AI Companion")
    application.setOrganizationName("Sally AI")
    application.setApplicationVersion(VERSION)
    application.setWindowIcon(QIcon(str(resource_path("assets/sally-icon.png"))))
    window = CompanionWindow()
    window.show()
    if os.environ.get("SALLY_SMOKE_TEST") == "1":
        QTimer.singleShot(750, window.close)
        QTimer.singleShot(900, application.quit)
    sys.exit(application.exec())
