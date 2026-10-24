from __future__ import annotations

from PySide6.QtCore import QTimer, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from products.hub.obs_service.config import ObsConnectionConfig
from products.hub.obs_service.models import ObsConnectionState
from products.hub.ui.page_header import PageHeader


class ObsModulePage(QWidget):
    """OBS-owned status, connection, and Automation defaults surface."""

    def __init__(self, module, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.module = module

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(scroll)

        content = QWidget(scroll)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(14)
        layout.addWidget(
            PageHeader(
                "OBS",
                "Connect OBS Studio and manage OBS-specific Automation defaults.",
                content,
            )
        )

        connection_group = QGroupBox("OBS WebSocket", content)
        form = QFormLayout(connection_group)
        self.host_edit = QLineEdit(connection_group)
        self.port_spin = QSpinBox(connection_group)
        self.port_spin.setRange(1, 65535)
        self.password_edit = QLineEdit(connection_group)
        self.password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.auto_connect_check = QCheckBox(
            "Connect automatically when Hub opens",
            connection_group,
        )
        self.status_label = QLabel("Disconnected", connection_group)
        self.status_label.setWordWrap(True)
        self.connect_button = QPushButton("Connect", connection_group)
        self.disconnect_button = QPushButton("Disconnect", connection_group)
        actions = QHBoxLayout()
        actions.addWidget(self.connect_button)
        actions.addWidget(self.disconnect_button)
        actions.addStretch()
        form.addRow("Host", self.host_edit)
        form.addRow("Port", self.port_spin)
        form.addRow("WebSocket password", self.password_edit)
        form.addRow("", self.auto_connect_check)
        form.addRow("Status", self.status_label)
        form.addRow("", actions)
        layout.addWidget(connection_group)

        automation_group = QGroupBox("Automation Defaults", content)
        automation_form = QFormLayout(automation_group)
        self.default_audio_input_edit = QLineEdit(automation_group)
        self.default_audio_input_edit.setPlaceholderText(
            "Optional, for example: Mic/Aux"
        )
        self.default_audio_input_edit.setToolTip(
            "Default OBS input for mute controls when an action does not name one."
        )
        automation_form.addRow("Default audio input", self.default_audio_input_edit)
        layout.addWidget(automation_group)
        layout.addStretch()
        scroll.setWidget(content)

        config, password = module.connection_values()
        self.host_edit.setText(config.host)
        self.port_spin.setValue(config.port)
        self.password_edit.setText(password)
        self.auto_connect_check.setChecked(config.auto_connect)
        self.default_audio_input_edit.setText(config.default_mute_input)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(500)
        self._save_timer.timeout.connect(self._save)
        for signal in (
            self.host_edit.textChanged,
            self.port_spin.valueChanged,
            self.password_edit.textChanged,
            self.auto_connect_check.toggled,
            self.default_audio_input_edit.textChanged,
        ):
            signal.connect(self._schedule_save)
        self.connect_button.clicked.connect(self._connect)
        self.disconnect_button.clicked.connect(module.service.disconnect)
        module.service.state_changed.connect(self._status_changed)
        self._status_changed(module.service.state, module.service.state.value)

    def _schedule_save(self, *_args) -> None:
        self._save_timer.start()

    def _config(self) -> ObsConnectionConfig:
        return ObsConnectionConfig(
            host=self.host_edit.text().strip() or "127.0.0.1",
            port=self.port_spin.value(),
            auto_connect=self.auto_connect_check.isChecked(),
            default_mute_input=self.default_audio_input_edit.text().strip(),
        )

    def _save(self) -> bool:
        self._save_timer.stop()
        try:
            self.module.save_connection(self._config(), self.password_edit.text())
        except (OSError, ValueError) as error:
            self.status_label.setText(f"Could not save: {error}")
            return False
        return True

    def _connect(self) -> None:
        if self._save():
            self.module.service.connect()

    @Slot(object, object)
    def _status_changed(self, state: ObsConnectionState, detail: str) -> None:
        clean_detail = detail.strip()
        self.status_label.setText(
            state.value
            if not clean_detail or clean_detail.casefold() == state.value.casefold()
            else f"{state.value}: {clean_detail}"
        )
        self.connect_button.setEnabled(state is not ObsConnectionState.CONNECTED)
        self.connect_button.setText(
            "Reconnect"
            if state in {ObsConnectionState.CONNECTING, ObsConnectionState.ERROR}
            else "Connect"
        )
        self.disconnect_button.setEnabled(
            state
            in {
                ObsConnectionState.CONNECTING,
                ObsConnectionState.CONNECTED,
                ObsConnectionState.ERROR,
            }
        )
