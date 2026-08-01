from __future__ import annotations

import secrets

from PySide6.QtCore import QUrl, Qt, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from products.hub.automation.routines import RoutineStore
from products.hub.automation.service import AutomationService
from products.hub.config.twitch_extension import TWITCH_EXTENSION_CLIENT_ID
from products.hub.soundboard.models import SoundboardButton, SoundboardPage
from products.hub.soundboard.server import SoundboardLocalServer
from products.hub.soundboard.store import SoundboardStore
from products.hub.soundboard.relay import (
    SoundboardRelayClient,
    SoundboardRelayConfig,
    SoundboardRelayConfigStore,
)


class SoundboardPageWidget(QWidget):
    """Broadcaster editor and native preview for the viewer soundboard."""

    def __init__(
        self,
        store: SoundboardStore,
        routine_store: RoutineStore,
        automation_service: AutomationService,
        server: SoundboardLocalServer,
        relay_client: SoundboardRelayClient,
        relay_config_store: SoundboardRelayConfigStore,
        relay_config: SoundboardRelayConfig,
        relay_key: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.routine_store = routine_store
        self.automation_service = automation_service
        self.server = server
        self.relay_client = relay_client
        self.relay_config_store = relay_config_store
        self.selected_button_id = ""
        self.preview_buttons: dict[str, QPushButton] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.scroll_content = QWidget(self.scroll_area)
        content_layout = QVBoxLayout(self.scroll_content)
        self.scroll_area.setWidget(self.scroll_content)
        root.addWidget(self.scroll_area)
        header = QHBoxLayout()
        introduction = QLabel(
            "Build the soundboard viewers will see. Each sound runs an automation "
            "routine, so it can play audio and perform other Hub tasks."
        )
        introduction.setWordWrap(True)
        header.addWidget(introduction, 1)
        content_layout.addLayout(header)

        self.editor_area = QWidget(self.scroll_content)
        self.editor_grid = QGridLayout(self.editor_area)
        self.editor_grid.setContentsMargins(0, 0, 0, 0)
        self.editor_grid.setSpacing(8)
        self.page_panel = self._build_page_panel()
        self.preview_panel = self._build_preview_panel()
        self.sound_editor_panel = self._build_editor_panel()
        self.editor_grid.addWidget(self.page_panel, 0, 0)
        self.editor_grid.addWidget(self.preview_panel, 0, 1)
        self.editor_grid.addWidget(self.sound_editor_panel, 0, 2)
        self.editor_grid.setColumnStretch(0, 0)
        self.editor_grid.setColumnStretch(1, 1)
        self.editor_grid.setColumnStretch(2, 0)
        self.editor_area.setMinimumHeight(430)
        content_layout.addWidget(self.editor_area, 1)

        local = QGroupBox("Local Viewer Test", self)
        local_layout = QHBoxLayout(local)
        self.viewer_url_edit = QLineEdit(local)
        self.viewer_url_edit.setReadOnly(True)
        self.viewer_url_edit.setPlaceholderText(
            "Open this tab to start the local viewer preview."
        )
        self.copy_url_button = QPushButton("Copy URL", local)
        self.open_preview_button = QPushButton("Open Viewer", local)
        local_layout.addWidget(self.viewer_url_edit, 1)
        local_layout.addWidget(self.copy_url_button)
        local_layout.addWidget(self.open_preview_button)
        content_layout.addWidget(local)
        local_note = QLabel(
            "Local testing only: this address works on the Hub PC. The hosted "
            "Twitch relay will replace it when the Extension is published."
        )
        local_note.setWordWrap(True)
        content_layout.addWidget(local_note)

        hosted = QGroupBox("Hosted Twitch Extension Relay", self)
        hosted_layout = QFormLayout(hosted)
        self.extension_client_id_edit = QLineEdit(
            TWITCH_EXTENSION_CLIENT_ID,
            hosted,
        )
        self.extension_client_id_edit.setReadOnly(True)
        self.relay_url_edit = QLineEdit(relay_config.url, hosted)
        self.relay_url_edit.setPlaceholderText("https://relay.example.com")
        self.relay_channel_edit = QLineEdit(relay_config.channel_id, hosted)
        self.relay_channel_edit.setPlaceholderText("Numeric Twitch channel ID")
        self.relay_key_edit = QLineEdit(relay_key, hosted)
        self.relay_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.relay_key_edit.setPlaceholderText("Private Streamhouse relay key")
        self.relay_auto_connect_check = QCheckBox(
            "Connect automatically when Streamhouse Hub opens", hosted
        )
        self.relay_auto_connect_check.setChecked(relay_config.auto_connect)
        hosted_layout.addRow("Extension Client ID", self.extension_client_id_edit)
        hosted_layout.addRow("Relay URL", self.relay_url_edit)
        hosted_layout.addRow("Channel ID", self.relay_channel_edit)
        hosted_layout.addRow("Relay key", self.relay_key_edit)
        hosted_layout.addRow("", self.relay_auto_connect_check)
        relay_actions = QHBoxLayout()
        self.generate_relay_key_button = QPushButton("Generate Key", hosted)
        self.connect_relay_button = QPushButton("Save & Connect", hosted)
        self.disconnect_relay_button = QPushButton("Disconnect", hosted)
        relay_actions.addWidget(self.generate_relay_key_button)
        relay_actions.addStretch()
        relay_actions.addWidget(self.connect_relay_button)
        relay_actions.addWidget(self.disconnect_relay_button)
        hosted_layout.addRow("", relay_actions)
        self.relay_status_label = QLabel(relay_client.status, hosted)
        hosted_layout.addRow("Status", self.relay_status_label)
        content_layout.addWidget(hosted)

        self.status_label = QLabel(
            "Add a sound, choose a routine, then test it here or in the local viewer."
        )
        self.status_label.setWordWrap(True)
        content_layout.addWidget(self.status_label)

        self.page_list.currentItemChanged.connect(self._page_changed)
        self.add_page_button.clicked.connect(self._add_page)
        self.rename_page_button.clicked.connect(self._rename_page)
        self.delete_page_button.clicked.connect(self._delete_page)
        self.page_up_button.clicked.connect(lambda: self._move_page(-1))
        self.page_down_button.clicked.connect(lambda: self._move_page(1))
        self.add_sound_button.clicked.connect(self._add_sound)
        self.save_sound_button.clicked.connect(self._save_sound)
        self.remove_sound_button.clicked.connect(self._remove_sound)
        self.sound_up_button.clicked.connect(lambda: self._move_sound(-1))
        self.sound_down_button.clicked.connect(lambda: self._move_sound(1))
        self.test_sound_button.clicked.connect(self._test_selected_sound)
        self.copy_url_button.clicked.connect(self._copy_viewer_url)
        self.open_preview_button.clicked.connect(self._open_viewer)
        self.generate_relay_key_button.clicked.connect(self._generate_relay_key)
        self.connect_relay_button.clicked.connect(self._connect_relay)
        self.disconnect_relay_button.clicked.connect(
            self.relay_client.disconnect_relay
        )
        self.relay_client.status_changed.connect(self.relay_status_label.setText)
        self.refresh()

    def set_responsive_orientation(self, portrait: bool) -> None:
        for panel in (
            self.page_panel,
            self.preview_panel,
            self.sound_editor_panel,
        ):
            self.editor_grid.removeWidget(panel)
        if portrait:
            self.editor_grid.addWidget(self.preview_panel, 0, 0, 1, 2)
            self.editor_grid.addWidget(self.page_panel, 1, 0)
            self.editor_grid.addWidget(self.sound_editor_panel, 1, 1)
            self.editor_grid.setColumnStretch(0, 1)
            self.editor_grid.setColumnStretch(1, 1)
            self.editor_grid.setColumnStretch(2, 0)
            self.preview_panel.setMinimumHeight(300)
            self.editor_area.setMinimumHeight(650)
        else:
            self.editor_grid.addWidget(self.page_panel, 0, 0)
            self.editor_grid.addWidget(self.preview_panel, 0, 1)
            self.editor_grid.addWidget(self.sound_editor_panel, 0, 2)
            self.editor_grid.setColumnStretch(0, 0)
            self.editor_grid.setColumnStretch(1, 1)
            self.editor_grid.setColumnStretch(2, 0)
            self.preview_panel.setMinimumHeight(0)
            self.editor_area.setMinimumHeight(430)
        self.editor_grid.invalidate()

    def _build_page_panel(self) -> QWidget:
        panel = QGroupBox("Pages", self)
        panel.setMinimumWidth(170)
        layout = QVBoxLayout(panel)
        self.page_list = QListWidget(panel)
        layout.addWidget(self.page_list, 1)
        row = QGridLayout()
        self.add_page_button = QPushButton("Add", panel)
        self.rename_page_button = QPushButton("Rename", panel)
        self.delete_page_button = QPushButton("Remove", panel)
        self.page_up_button = QPushButton("Up", panel)
        self.page_down_button = QPushButton("Down", panel)
        row.addWidget(self.add_page_button, 0, 0)
        row.addWidget(self.rename_page_button, 0, 1)
        row.addWidget(self.delete_page_button, 1, 0)
        row.addWidget(self.page_up_button, 2, 0)
        row.addWidget(self.page_down_button, 2, 1)
        layout.addLayout(row)
        return panel

    def _build_preview_panel(self) -> QWidget:
        panel = QGroupBox("Viewer Layout", self)
        layout = QVBoxLayout(panel)
        self.preview_frame = QFrame(panel)
        self.preview_frame.setObjectName("soundboardPreview")
        self.preview_frame.setStyleSheet(
            "QFrame#soundboardPreview { background:#18181b; border:1px solid #3b3b40; "
            "border-radius:6px; }"
        )
        self.preview_layout = QGridLayout(self.preview_frame)
        self.preview_layout.setContentsMargins(8, 8, 8, 8)
        self.preview_layout.setSpacing(8)
        layout.addWidget(self.preview_frame, 1)
        self.preview_pages = QWidget(panel)
        self.preview_pages_layout = QHBoxLayout(self.preview_pages)
        self.preview_pages_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.preview_pages)
        return panel

    def _build_editor_panel(self) -> QWidget:
        panel = QGroupBox("Sound Button", self)
        panel.setMinimumWidth(240)
        layout = QVBoxLayout(panel)
        form = QFormLayout()
        self.sound_label_edit = QLineEdit(panel)
        self.sound_label_edit.setPlaceholderText("Air horn")
        self.sound_routine_combo = QComboBox(panel)
        self.sound_routine_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.sound_enabled_check = QCheckBox("Available to viewers", panel)
        form.addRow("Label", self.sound_label_edit)
        form.addRow("Routine", self.sound_routine_combo)
        form.addRow("", self.sound_enabled_check)
        layout.addLayout(form)
        self.add_sound_button = QPushButton("Add Sound", panel)
        self.save_sound_button = QPushButton("Save Selected", panel)
        self.test_sound_button = QPushButton("Test Selected", panel)
        self.remove_sound_button = QPushButton("Remove Selected", panel)
        reorder = QHBoxLayout()
        self.sound_up_button = QPushButton("Move Left/Up", panel)
        self.sound_down_button = QPushButton("Move Right/Down", panel)
        reorder.addWidget(self.sound_up_button)
        reorder.addWidget(self.sound_down_button)
        layout.addWidget(self.add_sound_button)
        layout.addWidget(self.save_sound_button)
        layout.addWidget(self.test_sound_button)
        layout.addWidget(self.remove_sound_button)
        layout.addLayout(reorder)
        help_label = QLabel(
            "A page holds up to nine sounds. The viewer grid grows from one large "
            "button to a 3 x 3 layout automatically."
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        layout.addStretch()
        return panel

    @staticmethod
    def grid_dimensions(count: int) -> tuple[int, int]:
        if count <= 1:
            return 1, 1
        if count == 2:
            return 1, 2
        if count <= 4:
            return 2, 2
        if count <= 6:
            return 2, 3
        return 3, 3

    def activate(self) -> None:
        self._refresh_routines()
        if not self.server.running:
            try:
                self.server.start()
            except OSError as error:
                self.status_label.setText(
                    f"Could not start the local soundboard viewer: {error}"
                )
        self.viewer_url_edit.setText(self.server.url)
        self.refresh(self.current_page_id, self.selected_button_id)

    def shutdown(self) -> None:
        self.server.stop()
        self.relay_client.disconnect_relay()

    def refresh(self, page_id: str = "", button_id: str = "") -> None:
        pages = self.store.snapshot()
        target_page = page_id or self.current_page_id
        self.page_list.blockSignals(True)
        self.page_list.clear()
        selected_row = 0
        for row, page in enumerate(pages):
            item = QListWidgetItem(page.name)
            item.setData(Qt.ItemDataRole.UserRole, page.page_id)
            self.page_list.addItem(item)
            if page.page_id == target_page:
                selected_row = row
        if pages:
            self.page_list.setCurrentRow(selected_row)
        self.page_list.blockSignals(False)
        self.selected_button_id = button_id
        self._refresh_routines()
        self._rebuild_preview()
        self._load_selected_button()

    @property
    def current_page_id(self) -> str:
        item = self.page_list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else ""

    def _current_page(self) -> SoundboardPage | None:
        return self.store.get_page(self.current_page_id)

    def _selected_button(self) -> SoundboardButton | None:
        found = self.store.get_button(self.selected_button_id)
        return found[1] if found else None

    @Slot()
    def _page_changed(self, _current=None, _previous=None) -> None:
        self.selected_button_id = ""
        self._rebuild_preview()
        self._load_selected_button()

    def _rebuild_preview(self) -> None:
        self._clear_layout(self.preview_layout)
        self._clear_layout(self.preview_pages_layout)
        self.preview_buttons.clear()
        page = self._current_page()
        buttons = page.buttons if page else []
        rows, columns = self.grid_dimensions(len(buttons))
        for row in range(3):
            self.preview_layout.setRowStretch(row, 1 if row < rows else 0)
        for column in range(3):
            self.preview_layout.setColumnStretch(column, 1 if column < columns else 0)
        if not buttons:
            empty = QLabel("This page has no sounds yet.", self.preview_frame)
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color:#adadb8; border:none;")
            self.preview_layout.addWidget(empty, 0, 0)
        for index, sound in enumerate(buttons):
            button = QPushButton(sound.label, self.preview_frame)
            button.setMinimumHeight(55)
            button.setCheckable(True)
            button.setChecked(sound.button_id == self.selected_button_id)
            button.setProperty("configured", bool(sound.routine_id))
            button.setStyleSheet(
                "QPushButton { font-size:16px; font-weight:600; padding:10px; "
                "border:1px solid #515158; border-radius:6px; background:#29292e; }"
                "QPushButton:checked { border:2px solid #00d47b; background:#075d3c; }"
                "QPushButton[configured='false'] { color:#96969f; border-style:dashed; }"
            )
            button.clicked.connect(
                lambda _checked=False, value=sound.button_id: self._select_sound(value)
            )
            self.preview_layout.addWidget(button, index // columns, index % columns)
            self.preview_buttons[sound.button_id] = button
        pages = self.store.snapshot()
        self.preview_pages.setVisible(len(pages) > 1)
        if len(pages) > 1:
            for index, sound_page in enumerate(pages):
                button = QPushButton(sound_page.name, self.preview_pages)
                button.setCheckable(True)
                button.setChecked(sound_page.page_id == self.current_page_id)
                button.clicked.connect(
                    lambda _checked=False, row=index: self.page_list.setCurrentRow(row)
                )
                self.preview_pages_layout.addWidget(button)

    def _refresh_routines(self) -> None:
        selected = self.sound_routine_combo.currentData()
        self.sound_routine_combo.blockSignals(True)
        self.sound_routine_combo.clear()
        self.sound_routine_combo.addItem("Choose a routine...", "")
        for routine in self.routine_store.routines:
            label = routine.name + (" (disabled)" if not routine.enabled else "")
            self.sound_routine_combo.addItem(label, routine.routine_id)
        index = self.sound_routine_combo.findData(selected)
        self.sound_routine_combo.setCurrentIndex(max(index, 0))
        self.sound_routine_combo.blockSignals(False)

    def _load_selected_button(self) -> None:
        sound = self._selected_button()
        active = sound is not None
        self.sound_label_edit.setText(sound.label if sound else "")
        index = self.sound_routine_combo.findData(sound.routine_id if sound else "")
        self.sound_routine_combo.setCurrentIndex(max(index, 0))
        self.sound_enabled_check.setChecked(sound.enabled if sound else True)
        for widget in (
            self.save_sound_button,
            self.test_sound_button,
            self.remove_sound_button,
            self.sound_up_button,
            self.sound_down_button,
        ):
            widget.setEnabled(active)
        page = self._current_page()
        self.add_sound_button.setEnabled(
            page is not None and len(page.buttons) < self.store.MAX_BUTTONS_PER_PAGE
        )
        self.rename_page_button.setEnabled(page is not None)
        self.delete_page_button.setEnabled(page is not None)

    def _select_sound(self, button_id: str) -> None:
        self.selected_button_id = button_id
        self._rebuild_preview()
        self._load_selected_button()

    def _add_page(self) -> None:
        name, accepted = QInputDialog.getText(self, "Add Soundboard Page", "Page name")
        if not accepted:
            return
        try:
            page = self.store.add_page(name)
            self.refresh(page.page_id)
        except (OSError, ValueError) as error:
            self.status_label.setText(str(error))

    def _rename_page(self) -> None:
        page = self._current_page()
        if page is None:
            return
        name, accepted = QInputDialog.getText(
            self, "Rename Soundboard Page", "Page name", text=page.name
        )
        if not accepted:
            return
        try:
            self.store.rename_page(page.page_id, name)
            self.refresh(page.page_id, self.selected_button_id)
        except (OSError, ValueError) as error:
            self.status_label.setText(str(error))

    def _delete_page(self) -> None:
        page = self._current_page()
        if page is None:
            return
        if QMessageBox.question(
            self,
            "Remove Soundboard Page",
            f'Remove "{page.name}" and its {len(page.buttons)} sound(s)?',
        ) is not QMessageBox.StandardButton.Yes:
            return
        self.store.delete_page(page.page_id)
        self.refresh()

    def _move_page(self, offset: int) -> None:
        if not self.current_page_id:
            return
        page = self.store.move_page(self.current_page_id, offset)
        self.refresh(page.page_id, self.selected_button_id)

    def _add_sound(self) -> None:
        page = self._current_page()
        if page is None:
            return
        try:
            sound = self.store.add_button(page.page_id, "New Sound")
            self.refresh(page.page_id, sound.button_id)
            self.sound_label_edit.selectAll()
            self.sound_label_edit.setFocus()
        except (OSError, ValueError) as error:
            self.status_label.setText(str(error))

    def _save_sound(self) -> None:
        if not self.selected_button_id:
            return
        try:
            sound = self.store.update_button(
                self.selected_button_id,
                label=self.sound_label_edit.text(),
                routine_id=str(self.sound_routine_combo.currentData() or ""),
                enabled=self.sound_enabled_check.isChecked(),
            )
            self.refresh(self.current_page_id, sound.button_id)
            self.status_label.setText(
                "Sound saved. Reload an open local viewer to see the change."
            )
        except (OSError, ValueError) as error:
            self.status_label.setText(str(error))

    def _remove_sound(self) -> None:
        sound = self._selected_button()
        if sound is None:
            return
        if QMessageBox.question(
            self, "Remove Sound", f'Remove "{sound.label}" from this page?'
        ) is not QMessageBox.StandardButton.Yes:
            return
        page_id = self.current_page_id
        self.store.delete_button(sound.button_id)
        self.refresh(page_id)

    def _move_sound(self, offset: int) -> None:
        if not self.selected_button_id:
            return
        sound = self.store.move_button(self.selected_button_id, offset)
        self.refresh(self.current_page_id, sound.button_id)

    def _test_selected_sound(self) -> None:
        sound = self._selected_button()
        if sound is None:
            return
        if not sound.routine_id:
            self.status_label.setText("Choose and save a routine before testing this sound.")
            return
        if not sound.enabled:
            self.status_label.setText("Enable this sound before testing it.")
            return
        try:
            result = self.automation_service.run_routine(
                sound.routine_id,
                {
                    "user": "Local Viewer",
                    "soundboard_button": sound.label,
                    "soundboard_button_id": sound.button_id,
                },
            )
            self.status_label.setText(
                f'Ran "{sound.label}" successfully.'
                if result.succeeded
                else f'Routine for "{sound.label}" did not complete successfully.'
            )
        except ValueError as error:
            self.status_label.setText(str(error))

    def _copy_viewer_url(self) -> None:
        if self.server.url:
            QApplication.clipboard().setText(self.server.url)
            self.status_label.setText("Local viewer URL copied.")

    def _open_viewer(self) -> None:
        self.activate()
        if self.server.url:
            QDesktopServices.openUrl(QUrl(self.server.url))

    def _generate_relay_key(self) -> None:
        self.relay_key_edit.setText(secrets.token_urlsafe(32))
        self.status_label.setText(
            "Generated a relay key. Add this same key to the hosted relay settings."
        )

    def _relay_settings(self) -> tuple[SoundboardRelayConfig, str]:
        config = SoundboardRelayConfig(
            url=self.relay_url_edit.text().strip().rstrip("/"),
            channel_id=self.relay_channel_edit.text().strip(),
            auto_connect=self.relay_auto_connect_check.isChecked(),
        )
        config.validate()
        key = self.relay_key_edit.text().strip()
        if not key:
            raise ValueError("Enter or generate a Streamhouse relay key.")
        return config, key

    def _connect_relay(self) -> None:
        try:
            config, key = self._relay_settings()
            self.relay_config_store.save(config, key)
            self.relay_client.connect_relay(config, key)
            self.status_label.setText("Soundboard relay settings saved.")
        except (OSError, ValueError) as error:
            self.relay_status_label.setText(str(error))

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
