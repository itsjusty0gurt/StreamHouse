from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from products.hub.twitch.commands import (
    TwitchCommandPermission,
    TwitchCommandTrigger,
    TwitchCommandTriggerStore,
)


class TwitchCommandDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        command: TwitchCommandTrigger | None = None,
        response: str = "",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(
            "Edit Twitch Command" if command is not None else "Add Twitch Command"
        )
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(command.name if command else "")
        self.name_edit.setPlaceholderText("discord")
        self.aliases_edit = QLineEdit(
            ", ".join(command.aliases) if command else ""
        )
        self.aliases_edit.setPlaceholderText("dc, socials")
        self.response_edit = QTextEdit(response)
        self.response_edit.setAcceptRichText(False)
        self.response_edit.setPlaceholderText(
            "Optional — leave blank to only run the automation routine"
        )
        self.response_edit.setMaximumHeight(110)
        self.permission_combo = QComboBox()
        for permission in TwitchCommandPermission:
            self.permission_combo.addItem(
                permission.value.replace("_", " ").title(), permission.value
            )
        if command is not None:
            index = self.permission_combo.findData(command.permission)
            self.permission_combo.setCurrentIndex(max(index, 0))
        self.global_cooldown_spin = QSpinBox()
        self.global_cooldown_spin.setRange(0, 3600)
        self.global_cooldown_spin.setSuffix(" seconds")
        self.global_cooldown_spin.setValue(
            command.global_cooldown_seconds if command else 10
        )
        self.user_cooldown_spin = QSpinBox()
        self.user_cooldown_spin.setRange(0, 86400)
        self.user_cooldown_spin.setSuffix(" seconds")
        self.user_cooldown_spin.setValue(
            command.user_cooldown_seconds if command else 30
        )
        form.addRow("Command", self.name_edit)
        form.addRow("Alternate commands", self.aliases_edit)
        form.addRow("Chat response (optional)", self.response_edit)
        form.addRow("Permission", self.permission_combo)
        form.addRow("Global cooldown", self.global_cooldown_spin)
        form.addRow("Viewer cooldown", self.user_cooldown_spin)
        layout.addLayout(form)
        if command is not None and command.is_default:
            source = QLabel(
                "Streamhouse default command. Its routine and conditional response "
                "steps remain fully editable; Reset to Default restores the current built-in definition."
            )
            source.setWordWrap(True)
            layout.addWidget(source)
        variables = QLabel(
            "Use registry-backed values such as {user.display_name}, "
            "{stream.title}, and {command.target}."
        )
        variables.setWordWrap(True)
        layout.addWidget(variables)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict[str, object]:
        aliases = [
            alias.strip()
            for alias in self.aliases_edit.text().split(",")
            if alias.strip()
        ]
        return {
            "name": self.name_edit.text(),
            "response": self.response_edit.toPlainText(),
            "aliases": aliases,
            "permission": str(self.permission_combo.currentData()),
            "global_cooldown_seconds": self.global_cooldown_spin.value(),
            "user_cooldown_seconds": self.user_cooldown_spin.value(),
        }


class TwitchCommandManagerDialog(QDialog):
    """Browse all commands and manage the command for a selected routine."""

    def __init__(
        self,
        store: TwitchCommandTriggerStore,
        routine_id: str,
        parent: QWidget | None = None,
        commands_changed: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self.routine_id = routine_id
        self.commands_changed = commands_changed or (lambda: None)
        self.created_trigger_id = ""
        self.setWindowTitle("Twitch Chat Commands")
        self.setMinimumSize(620, 420)

        layout = QVBoxLayout(self)
        introduction = QLabel(
            "Commands are Twitch triggers attached to routines. Create New adds "
            "a command to the selected routine; Edit Selected can update any "
            "existing command. Chat response text is optional."
        )
        introduction.setWordWrap(True)
        layout.addWidget(introduction)

        self.command_list = QListWidget()
        self.command_list.setAlternatingRowColors(True)
        self.command_list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        layout.addWidget(self.command_list, 1)

        actions = QHBoxLayout()
        self.create_button = QPushButton("Create New")
        self.edit_button = QPushButton("Edit Selected")
        actions.addWidget(self.create_button)
        actions.addWidget(self.edit_button)
        actions.addStretch()
        layout.addLayout(actions)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        layout.addWidget(buttons)

        self.create_button.clicked.connect(self._create_command)
        self.edit_button.clicked.connect(self._edit_command)
        self.command_list.itemSelectionChanged.connect(self._update_actions)
        self.command_list.itemDoubleClicked.connect(lambda _item: self._edit_command())
        self._refresh()

    def _refresh(self, selected_trigger_id: str = "") -> None:
        self.command_list.clear()
        selected_item: QListWidgetItem | None = None
        for command in self.store.ordered_triggers():
            routine = self.store.routine_store.get(command.routine_id)
            routine_name = routine.name if routine is not None else "Missing routine"
            state = "Enabled" if command.enabled else "Disabled"
            source = "Default" if command.is_default else "Custom"
            item = QListWidgetItem(
                f"!{command.name}  —  {routine_name}  [{state}; {source}]"
            )
            item.setData(Qt.ItemDataRole.UserRole, command.trigger_id)
            self.command_list.addItem(item)
            if command.trigger_id == selected_trigger_id:
                selected_item = item
        if selected_item is not None:
            self.command_list.setCurrentItem(selected_item)
        self.status_label.setText(f"{self.command_list.count()} existing command(s).")
        self._update_actions()

    def _selected_command(self) -> TwitchCommandTrigger | None:
        item = self.command_list.currentItem()
        trigger_id = str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""
        return self.store.get(trigger_id) if trigger_id else None

    def _update_actions(self) -> None:
        existing = self.store.for_routine(self.routine_id)
        self.create_button.setEnabled(existing is None)
        self.create_button.setToolTip(
            ""
            if existing is None
            else "This routine already has a Twitch chat command trigger."
        )
        self.edit_button.setEnabled(self._selected_command() is not None)

    def _create_command(self) -> None:
        if self.store.for_routine(self.routine_id) is not None:
            self._update_actions()
            return
        dialog = TwitchCommandDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            command = self.store.attach_routine(self.routine_id, **dialog.values())
        except (OSError, TypeError, ValueError) as error:
            QMessageBox.critical(self, "Could Not Create Command", str(error))
            return
        self.created_trigger_id = command.trigger_id
        self.commands_changed()
        self._refresh(command.trigger_id)

    def _edit_command(self) -> None:
        command = self._selected_command()
        if command is None:
            return
        dialog = TwitchCommandDialog(
            self,
            command,
            self.store.response_for(command),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            updated = self.store.update(command.trigger_id, **dialog.values())
        except (OSError, TypeError, ValueError) as error:
            QMessageBox.critical(self, "Could Not Update Command", str(error))
            return
        self.commands_changed()
        self._refresh(updated.trigger_id)
