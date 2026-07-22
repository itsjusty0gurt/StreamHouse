from __future__ import annotations

import os
import sys
from datetime import datetime
from threading import Thread
from time import monotonic

from PySide6.QtCore import QSettings, QSize, QTimer, Qt
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from config.version import VERSION
from core.resources import resource_path
from sally_companion.protocol import PROTOCOL_VERSION
from sally_companion.server import CompanionReasoningService, create_server
from sally_companion.settings import CompanionSettings, CompanionSettingsStore


NAV_STYLE = """
QPushButton {
    border: none;
    border-radius: 0px;
    padding: 0px;
    min-height: 40px;
}
QPushButton:hover { background-color: palette(midlight); }
QPushButton:checked {
    background-color: palette(highlight);
    color: palette(highlighted-text);
    font-weight: bold;
}
"""


class CompanionWindow(QMainWindow):
    """Full AI application; Sally Bot only controls it over localhost."""

    NAVIGATION = (
        "Dashboard",
        "Memories",
        "Reply Review",
        "Training",
        "Test Report",
        "Personality",
        "Settings",
    )

    def __init__(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        super().__init__()
        self.host = host
        self.port = port
        self.setWindowTitle("Sally AI Companion")
        self.setMinimumSize(620, 420)
        self.resize(1050, 680)
        self.window_settings = QSettings("Sally AI", "Sally AI Companion")
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
        self.host, self.port = self._server.server_address
        self._server_thread = Thread(
            target=self._server.serve_forever,
            name="sally-companion-http",
            daemon=True,
        )
        self._server_thread.start()

        self.pages: dict[str, QWidget] = {}
        self.navigation_buttons: dict[str, QPushButton] = {}
        self._build_shell()
        self._build_pages()
        self._sync_settings_controls()
        self._refresh_owned_data()
        geometry = self.window_settings.value("geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(2_000)
        self.refresh_timer.timeout.connect(self._refresh_owned_data)
        self.refresh_timer.start()
        self._show_page("Dashboard")
        self.statusBar().showMessage(
            f"Companion: listening on http://{self.host}:{self.port}"
        )

    def _build_shell(self) -> None:
        central = QWidget(self)
        shell = QHBoxLayout(central)
        shell.setContentsMargins(10, 10, 10, 10)
        shell.setSpacing(10)
        navigation = QFrame(central)
        navigation.setMinimumWidth(150)
        navigation.setMaximumWidth(180)
        navigation.setFrameShape(QFrame.Shape.StyledPanel)
        navigation.setStyleSheet(NAV_STYLE)
        nav_layout = QVBoxLayout(navigation)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(0)
        self.navigation_group = QButtonGroup(self)
        self.navigation_group.setExclusive(True)
        for name in self.NAVIGATION:
            button = QPushButton(name, navigation)
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, page=name: self._show_page(page))
            self.navigation_group.addButton(button)
            self.navigation_buttons[name] = button
            nav_layout.addWidget(button)
            if name == "Test Report":
                nav_layout.addStretch()
        self.page_stack = QStackedWidget(central)
        shell.addWidget(navigation)
        shell.addWidget(self.page_stack, 1)
        self.setCentralWidget(central)

    def _build_pages(self) -> None:
        self._build_dashboard_page()
        self._build_memories_page()
        self._build_reply_review_page()
        self._build_training_page()
        self._build_test_report_page()
        self._build_personality_page()
        self._build_settings_page()

    def _add_page(self, name: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget(self.page_stack)
        layout = QVBoxLayout(page)
        self.pages[name] = page
        self.page_stack.addWidget(page)
        return page, layout

    def _build_dashboard_page(self) -> None:
        _page, layout = self._add_page("Dashboard")
        connection = QGroupBox("Companion Service")
        form = QFormLayout(connection)
        self.dashboard_service_label = QLabel("Waiting for Sally Bot")
        self.dashboard_endpoint_label = QLabel(f"http://{self.host}:{self.port}")
        self.dashboard_protocol_label = QLabel(str(PROTOCOL_VERSION))
        self.dashboard_model_label = QLabel("--")
        form.addRow("Status", self.dashboard_service_label)
        form.addRow("Local endpoint", self.dashboard_endpoint_label)
        form.addRow("Protocol", self.dashboard_protocol_label)
        form.addRow("Model", self.dashboard_model_label)
        layout.addWidget(connection)
        health = QGroupBox("Local AI Health")
        health_layout = QVBoxLayout(health)
        self.ollama_health_label = QLabel("Not tested")
        self.ollama_health_label.setWordWrap(True)
        test_button = QPushButton("Test Ollama")
        test_button.clicked.connect(self._test_ollama)
        health_layout.addWidget(self.ollama_health_label)
        health_layout.addWidget(test_button)
        layout.addWidget(health)
        summary = QGroupBox("Current Run")
        summary_form = QFormLayout(summary)
        self.dashboard_decisions_label = QLabel("0")
        self.dashboard_memories_label = QLabel("0")
        self.dashboard_training_label = QLabel("0")
        summary_form.addRow("Reply decisions", self.dashboard_decisions_label)
        summary_form.addRow("Memory proposals", self.dashboard_memories_label)
        summary_form.addRow("Training samples", self.dashboard_training_label)
        layout.addWidget(summary)
        layout.addStretch()

    def _build_memories_page(self) -> None:
        _page, layout = self._add_page("Memories")
        notice = QLabel(
            "Memory proposals created by Companion reasoning. Viewer consent and "
            "approved viewer records remain enforced by Sally Bot."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.memories_table = QTableWidget(0, 4)
        self.memories_table.setHorizontalHeaderLabels(
            ("Viewer", "Category", "Memory proposal", "Confidence")
        )
        self.memories_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.memories_table, 1)

    def _build_reply_review_page(self) -> None:
        _page, layout = self._add_page("Reply Review")
        notice = QLabel(
            "Recent AI decisions received from Sally Bot. Sending and moderation "
            "remain Bot responsibilities."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.reply_table = QTableWidget(0, 5)
        self.reply_table.setHorizontalHeaderLabels(
            ("Viewer", "Message", "Decision", "Reply", "Reason")
        )
        header = self.reply_table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.reply_table, 1)
        clear = QPushButton("Clear Runtime History")
        clear.clicked.connect(self._clear_runtime_history)
        layout.addWidget(clear)

    def _build_training_page(self) -> None:
        _page, layout = self._add_page("Training")
        self.training_status_label = QLabel("")
        layout.addWidget(self.training_status_label)
        self.training_table = QTableWidget(0, 6)
        self.training_table.setHorizontalHeaderLabels(
            ("Message", "Model label", "Decision", "State", "Label", "Reviewed")
        )
        self.training_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.training_table, 1)
        controls = QHBoxLayout()
        self.training_label_combo = QComboBox()
        self.training_label_combo.addItems(self.reasoning_service.training_store.LABELS)
        save = QPushButton("Save Reviewed Label")
        delete = QPushButton("Delete Selected")
        clear = QPushButton("Delete All")
        save.clicked.connect(self._save_training_label)
        delete.clicked.connect(self._delete_training_sample)
        clear.clicked.connect(self._clear_training)
        controls.addWidget(self.training_label_combo)
        controls.addWidget(save)
        controls.addWidget(delete)
        controls.addStretch()
        controls.addWidget(clear)
        layout.addLayout(controls)

    def _build_test_report_page(self) -> None:
        _page, layout = self._add_page("Test Report")
        summary = QGroupBox("Results")
        summary_layout = QHBoxLayout(summary)
        self.report_summary_labels: dict[str, QLabel] = {}
        for key, title in (
            ("total", "Evaluated"),
            ("sent", "Sent"),
            ("ignored", "Ignored"),
            ("missed", "Missed"),
            ("failed", "Failed"),
            ("average_latency_ms", "Avg latency"),
        ):
            value = QLabel("0")
            value.setStyleSheet("font-size:18px; font-weight:600;")
            box = QVBoxLayout()
            box.addWidget(QLabel(title))
            box.addWidget(value)
            summary_layout.addLayout(box)
            self.report_summary_labels[key] = value
        layout.addWidget(summary)
        self.report_table = QTableWidget(0, 6)
        self.report_table.setHorizontalHeaderLabels(
            ("Time", "Outcome", "Expected", "Latency", "Confidence", "Reason")
        )
        self.report_table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.report_table, 1)
        controls = QHBoxLayout()
        new_test = QPushButton("Start New Test")
        clear = QPushButton("Clear Report")
        new_test.clicked.connect(self._new_test_session)
        clear.clicked.connect(self._clear_test_report)
        controls.addWidget(new_test)
        controls.addStretch()
        controls.addWidget(clear)
        layout.addLayout(controls)

    def _build_personality_page(self) -> None:
        _page, layout = self._add_page("Personality")
        description = QLabel(
            "Describe how Sally should sound and behave. Companion applies these "
            "rules to every live reply decision."
        )
        description.setWordWrap(True)
        self.personality_edit = QTextEdit()
        self.mild_profanity_check = QCheckBox("Allow occasional mild profanity")
        self.strong_profanity_check = QCheckBox("Allow strong profanity / foul language")
        self.strong_profanity_check.toggled.connect(
            lambda enabled: self.mild_profanity_check.setChecked(True) if enabled else None
        )
        self.personality_status_label = QLabel("")
        save = QPushButton("Save Personality")
        save.clicked.connect(self._save_companion_settings)
        layout.addWidget(description)
        layout.addWidget(self.personality_edit, 1)
        layout.addWidget(self.mild_profanity_check)
        layout.addWidget(self.strong_profanity_check)
        layout.addWidget(save)
        layout.addWidget(self.personality_status_label)

    def _build_settings_page(self) -> None:
        _page, layout = self._add_page("Settings")
        model_group = QGroupBox("Local Model")
        form = QFormLayout(model_group)
        self.ollama_endpoint_edit = QLineEdit()
        self.model_edit = QLineEdit()
        form.addRow("Ollama endpoint", self.ollama_endpoint_edit)
        form.addRow("Model", self.model_edit)
        layout.addWidget(model_group)
        service_group = QGroupBox("Companion Service")
        service_form = QFormLayout(service_group)
        service_form.addRow("Listen address", QLabel(f"{self.host}:{self.port}"))
        service_form.addRow("Protocol", QLabel(str(PROTOCOL_VERSION)))
        layout.addWidget(service_group)
        self.settings_status_label = QLabel("")
        save = QPushButton("Save Settings")
        save.clicked.connect(self._save_companion_settings)
        layout.addWidget(save)
        layout.addWidget(self.settings_status_label)
        layout.addStretch()

    def _show_page(self, name: str) -> None:
        page = self.pages[name]
        self.page_stack.setCurrentWidget(page)
        self.navigation_buttons[name].setChecked(True)
        if name in {"Personality", "Settings"}:
            self._sync_settings_controls()
        self._refresh_owned_data()

    def _sync_settings_controls(self) -> None:
        settings = self.reasoning_service.settings
        self.ollama_endpoint_edit.setText(settings.ollama_endpoint)
        self.model_edit.setText(settings.model)
        self.personality_edit.setPlainText(settings.personality)
        self.mild_profanity_check.setChecked(settings.allow_mild_profanity)
        self.strong_profanity_check.setChecked(settings.allow_strong_profanity)
        self.dashboard_model_label.setText(settings.model)

    def _save_companion_settings(self) -> None:
        settings = CompanionSettings.from_dict(
            {
                "ollama_endpoint": self.ollama_endpoint_edit.text(),
                "model": self.model_edit.text(),
                "personality": self.personality_edit.toPlainText(),
                "allow_mild_profanity": self.mild_profanity_check.isChecked(),
                "allow_strong_profanity": self.strong_profanity_check.isChecked(),
            }
        )
        try:
            self.settings_store.save(settings)
        except OSError as error:
            self.settings_status_label.setText(f"Could not save: {error}")
            self.personality_status_label.setText(f"Could not save: {error}")
            return
        self.reasoning_service.settings = settings
        self.dashboard_model_label.setText(settings.model)
        self.settings_status_label.setText("Companion settings saved.")
        self.personality_status_label.setText("Personality saved.")

    def _test_ollama(self) -> None:
        self.ollama_health_label.setText("Testing…")
        QApplication.processEvents()
        status = self.reasoning_service._provider(timeout=5.0).status()
        if status.available:
            selected = self.reasoning_service.settings.model
            model_state = "installed" if selected in status.models else "not installed"
            self.ollama_health_label.setText(
                f"Ollama connected; {selected} is {model_state}."
            )
        else:
            self.ollama_health_label.setText(f"Ollama unavailable: {status.error}")

    def _refresh_owned_data(self) -> None:
        bot_connected = (
            self.reasoning_service.last_bot_contact > 0
            and monotonic() - self.reasoning_service.last_bot_contact <= 10.0
        )
        self.dashboard_service_label.setText(
            "Connected to Sally Bot" if bot_connected else "Waiting for Sally Bot"
        )
        self.dashboard_service_label.setStyleSheet(
            "color:#00d084; font-weight:600;"
            if bot_connected
            else "color:#f5c542; font-weight:600;"
        )
        self.dashboard_model_label.setText(self.reasoning_service.settings.model)
        self.dashboard_decisions_label.setText(
            str(len(self.reasoning_service.decision_history))
        )
        self.dashboard_memories_label.setText(
            str(len(self.reasoning_service.memory_history))
        )
        self.dashboard_training_label.setText(
            str(len(self.reasoning_service.training_store.examples))
        )
        self._fill_memory_table()
        self._fill_reply_table()
        self._fill_training_table()
        self._fill_report_table()

    @staticmethod
    def _set_row(table: QTableWidget, values: tuple[str, ...]) -> None:
        row = table.rowCount()
        table.insertRow(row)
        for column, value in enumerate(values):
            table.setItem(row, column, QTableWidgetItem(value))

    def _fill_memory_table(self) -> None:
        self.memories_table.setRowCount(0)
        for item in reversed(self.reasoning_service.memory_history):
            self._set_row(
                self.memories_table,
                (
                    str(item.get("user_name", "")),
                    str(item.get("category", "")),
                    str(item.get("text", "")),
                    f"{float(item.get('confidence', 0.0)):.0%}",
                ),
            )

    def _fill_reply_table(self) -> None:
        self.reply_table.setRowCount(0)
        for item in reversed(self.reasoning_service.decision_history):
            self._set_row(
                self.reply_table,
                (
                    str(item.get("user_name", "")),
                    str(item.get("source_text", "")),
                    str(item.get("decision", "")),
                    str(item.get("reply", "")),
                    str(item.get("reason", "")),
                ),
            )

    def _fill_training_table(self) -> None:
        examples = self.reasoning_service.training_store.examples
        self.training_table.setRowCount(0)
        for item in reversed(examples):
            self._set_row(
                self.training_table,
                (
                    str(item.get("message", "")),
                    str(item.get("model_label", "")),
                    str(item.get("decision", "")),
                    str(item.get("conversation_state", "")),
                    str(item.get("label", "")) or "Pending",
                    "Yes" if bool(item.get("reviewed")) else "No",
                ),
            )
            self.training_table.item(self.training_table.rowCount() - 1, 0).setData(
                Qt.ItemDataRole.UserRole, str(item.get("id", ""))
            )
        reviewed = sum(bool(item.get("reviewed")) for item in examples)
        self.training_status_label.setText(
            f"{len(examples)} local sample(s); {reviewed} reviewed."
        )

    def _fill_report_table(self) -> None:
        store = self.reasoning_service.test_report_store
        summary = store.summary(False)
        for key, label in self.report_summary_labels.items():
            value = summary.get(key, 0)
            label.setText(
                f"{int(value) / 1000:.1f}s"
                if key == "average_latency_ms"
                else str(value)
            )
        self.report_table.setRowCount(0)
        for item in reversed(store.events[-500:]):
            recorded = str(item.get("recorded_at", ""))
            try:
                display_time = datetime.fromisoformat(
                    recorded.replace("Z", "+00:00")
                ).astimezone().strftime("%H:%M:%S")
            except ValueError:
                display_time = "--"
            self._set_row(
                self.report_table,
                (
                    display_time,
                    str(item.get("outcome", "")),
                    "Yes" if bool(item.get("response_expected")) else "No",
                    f"{int(item.get('latency_ms', 0)) / 1000:.1f}s",
                    f"{float(item.get('confidence', 0.0)):.0%}",
                    str(item.get("reason", "")),
                ),
            )

    def _selected_training_id(self) -> str:
        row = self.training_table.currentRow()
        item = self.training_table.item(row, 0) if row >= 0 else None
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else ""

    def _save_training_label(self) -> None:
        example_id = self._selected_training_id()
        if example_id:
            self.reasoning_service.training_store.label(
                example_id, self.training_label_combo.currentText()
            )
            self._fill_training_table()

    def _delete_training_sample(self) -> None:
        example_id = self._selected_training_id()
        if example_id:
            self.reasoning_service.training_store.delete(example_id)
            self._fill_training_table()

    def _clear_training(self) -> None:
        if not self.reasoning_service.training_store.examples:
            return
        if QMessageBox.question(
            self, "Delete Training Samples", "Delete every training sample?"
        ) is QMessageBox.StandardButton.Yes:
            self.reasoning_service.training_store.clear()
            self._fill_training_table()

    def _new_test_session(self) -> None:
        self.reasoning_service.test_report_store.start_new_session()
        self._fill_report_table()

    def _clear_test_report(self) -> None:
        self.reasoning_service.test_report_store.clear()
        self._fill_report_table()

    def _clear_runtime_history(self) -> None:
        self.reasoning_service.decision_history.clear()
        self.reasoning_service.memory_history.clear()
        self._refresh_owned_data()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.refresh_timer.stop()
        self.window_settings.setValue("geometry", self.saveGeometry())
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
