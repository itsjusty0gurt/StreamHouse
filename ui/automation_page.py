from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import json
from pathlib import Path
import re

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from automation.models import AutomationExecutionResult, TaskDefinition, TriggerEvent
from automation.custom_variables import CustomVariableStore
from automation.core_triggers import (
    CORE_TRIGGER_TYPES,
    CoreAutomationTrigger,
    CoreTriggerStore,
)
from automation.routines import RoutineStore
from automation.service import AutomationService
from automation.tasks import TaskRegistry
from automation.core_tasks import CORE_TASK_LABELS, PlayAudioTask
from automation.variable_tasks import (
    VARIABLE_MANAGEMENT_TASK_TYPES,
    VARIABLE_TASK_LABELS,
)
from automation.logic_tasks import (
    COMPARISON_CHOICES,
    LOGIC_TASK_LABELS,
    UNARY_OPERATORS,
)
from automation.file_tasks import FILE_TASK_TYPES
from automation.queues import (
    AutomationQueueDefinition,
    AutomationQueueManager,
    AutomationQueueStore,
)
from automation.transfer import export_routine, import_routine, validate_import
from automation.variables import (
    CORE_VARIABLES,
    OBS_VARIABLES,
    TWITCH_VARIABLES,
    VARIABLE_INFO,
    VARIABLE_SOURCE_INFO,
    TEMPLATE_PATTERN,
    render_preview,
    sample_context,
)
from obs_service.tasks import OBS_TASK_LABELS
from obs_service.service import ObsWebSocketService
from obs_service.triggers import (
    OBS_TRIGGER_TYPES,
    ObsAutomationTrigger,
    ObsTriggerStore,
)
from twitch.commands import (
    TwitchCommandPermission,
    TwitchCommandTriggerStore,
)
from twitch.tasks import SendTwitchChatMessageTask, TWITCH_TASK_LABELS
from twitch.automation_triggers import (
    TWITCH_AUTOMATION_EVENT_TYPES,
    TwitchEventAutomationTrigger,
    TwitchEventTriggerStore,
)
from ui.twitch_command_dialog import TwitchCommandDialog, TwitchCommandManagerDialog


def _event_display_name(event_type: str) -> str:
    if event_type == "channel.chat.first_message":
        return "First Message Of Stream"
    return event_type.replace("channel.", "").replace("_", " ").replace(".", " › ").title()


def _parse_event_filters(text: str) -> dict[str, str]:
    filters: dict[str, str] = {}
    for entry in text.split(","):
        if not entry.strip():
            continue
        path, separator, value = entry.partition("=")
        if not separator or not path.strip() or not value.strip():
            raise ValueError(
                "Event filters use field=value pairs separated by commas."
            )
        filters[path.strip()] = value.strip()
    return filters


class NewRoutineDialog(QDialog):
    def __init__(self, store: RoutineStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create Routine")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Welcome Chatters")
        self.group_combo = QComboBox()
        self.group_combo.setEditable(True)
        self.group_combo.addItem("Ungrouped", "")
        for group in store.groups:
            self.group_combo.addItem(group.name, group.group_id)
        self.group_combo.lineEdit().setPlaceholderText(
            "Choose an existing group or type a new one"
        )
        self.description_edit = QLineEdit()
        self.description_edit.setPlaceholderText("Optional description")
        self.enabled_check = QCheckBox("Enable this routine")
        self.enabled_check.setChecked(True)
        self.trigger_combo = QComboBox()
        self.trigger_combo.addItem("Manual only", "manual")
        self.trigger_combo.addItem("Twitch chat command", "twitch.command")
        self.trigger_combo.addItem("Twitch event", "twitch.eventsub")
        self.trigger_combo.addItem("Core program event", "core.lifecycle")
        self.trigger_combo.addItem("OBS WebSocket event", "obs.event")
        form.addRow("Name", self.name_edit)
        form.addRow("Group", self.group_combo)
        form.addRow("Description", self.description_edit)
        form.addRow("Starts from", self.trigger_combo)
        form.addRow("", self.enabled_check)
        layout.addLayout(form)

        self.command_group = QGroupBox("Twitch Command")
        command_form = QFormLayout(self.command_group)
        self.command_edit = QLineEdit()
        self.command_edit.setPlaceholderText("welcome")
        self.alternate_commands_edit = QLineEdit()
        self.alternate_commands_edit.setPlaceholderText("hello, hi")
        self.response_edit = QTextEdit()
        self.response_edit.setMaximumHeight(90)
        self.response_edit.setPlaceholderText("Welcome, {user}!")
        self.permission_combo = QComboBox()
        for permission in TwitchCommandPermission:
            self.permission_combo.addItem(
                permission.value.replace("_", " ").title(), permission.value
            )
        self.global_cooldown_spin = QSpinBox()
        self.global_cooldown_spin.setRange(0, 3600)
        self.global_cooldown_spin.setSuffix(" seconds")
        self.global_cooldown_spin.setValue(10)
        self.user_cooldown_spin = QSpinBox()
        self.user_cooldown_spin.setRange(0, 86400)
        self.user_cooldown_spin.setSuffix(" seconds")
        self.user_cooldown_spin.setValue(30)
        command_form.addRow("Command", self.command_edit)
        command_form.addRow(
            "Alternate commands", self.alternate_commands_edit
        )
        command_form.addRow("First response task", self.response_edit)
        command_form.addRow("Permission", self.permission_combo)
        command_form.addRow("Global cooldown", self.global_cooldown_spin)
        command_form.addRow("Viewer cooldown", self.user_cooldown_spin)
        layout.addWidget(self.command_group)
        self.command_group.hide()

        self.event_group = QGroupBox("Twitch Event")
        event_form = QFormLayout(self.event_group)
        self.event_type_combo = QComboBox()
        for event_type in TWITCH_AUTOMATION_EVENT_TYPES:
            self.event_type_combo.addItem(_event_display_name(event_type), event_type)
        self.event_filters_edit = QLineEdit()
        self.event_filters_edit.setPlaceholderText(
            "Optional: reward.id=abc123, tier=1000"
        )
        event_help = QLabel(
            "Leave filters empty to run for every event of this type. Nested "
            "fields use dots, such as reward.id or reward.title."
        )
        event_help.setWordWrap(True)
        self.event_reset_spin = QSpinBox()
        self.event_reset_spin.setRange(1, 180)
        self.event_reset_spin.setValue(15)
        self.event_reset_spin.setSuffix(" minutes offline")
        event_form.addRow("Event", self.event_type_combo)
        event_form.addRow("Field filters", self.event_filters_edit)
        event_form.addRow("Reset welcomes after", self.event_reset_spin)
        event_form.addRow("", event_help)
        layout.addWidget(self.event_group)
        self.event_group.hide()

        self.core_group = QGroupBox("Core Program Event")
        core_form = QFormLayout(self.core_group)
        self.core_event_combo = QComboBox()
        for event_type, label in CORE_TRIGGER_TYPES.items():
            self.core_event_combo.addItem(label, event_type)
        core_help = QLabel(
            "Application Started fires after Sally's window opens. Application "
            "Closing fires before services and automation are shut down."
        )
        core_help.setWordWrap(True)
        core_form.addRow("Event", self.core_event_combo)
        core_form.addRow("", core_help)
        layout.addWidget(self.core_group)
        self.core_group.hide()

        self.obs_group = QGroupBox("OBS Event")
        obs_form = QFormLayout(self.obs_group)
        self.obs_event_combo = QComboBox()
        for event_type, label in OBS_TRIGGER_TYPES.items():
            self.obs_event_combo.addItem(label, event_type)
        self.obs_filters_edit = QLineEdit()
        self.obs_filters_edit.setPlaceholderText("Optional: sceneName=Gameplay")
        obs_form.addRow("Event", self.obs_event_combo)
        obs_form.addRow("Field filters", self.obs_filters_edit)
        layout.addWidget(self.obs_group)
        self.obs_group.hide()
        self.trigger_combo.currentIndexChanged.connect(
            self._update_trigger_fields
        )
        self.event_type_combo.currentIndexChanged.connect(
            self._update_trigger_fields
        )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Create")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_trigger_fields(self, _index: int = 0) -> None:
        trigger_type = self.trigger_combo.currentData()
        self.command_group.setVisible(trigger_type == "twitch.command")
        self.event_group.setVisible(trigger_type == "twitch.eventsub")
        self.core_group.setVisible(trigger_type == "core.lifecycle")
        self.obs_group.setVisible(trigger_type == "obs.event")
        reset_visible = (
            trigger_type == "twitch.eventsub"
            and self.event_type_combo.currentData()
            == "channel.chat.first_message"
        )
        self.event_reset_spin.setVisible(reset_visible)
        reset_label = self.event_group.layout().labelForField(
            self.event_reset_spin
        )
        if reset_label is not None:
            reset_label.setVisible(reset_visible)

    def values(self) -> dict[str, object]:
        aliases = [
            value.strip()
            for value in self.alternate_commands_edit.text().split(",")
            if value.strip()
        ]
        return {
            "name": self.name_edit.text(),
            "group_name": self.group_combo.currentText(),
            "group_id": str(self.group_combo.currentData() or ""),
            "description": self.description_edit.text(),
            "enabled": self.enabled_check.isChecked(),
            "trigger_type": str(self.trigger_combo.currentData()),
            "command": self.command_edit.text(),
            "response": self.response_edit.toPlainText(),
            "aliases": aliases,
            "permission": str(self.permission_combo.currentData()),
            "global_cooldown_seconds": self.global_cooldown_spin.value(),
            "user_cooldown_seconds": self.user_cooldown_spin.value(),
            "event_type": str(self.event_type_combo.currentData()),
            "event_filters": _parse_event_filters(self.event_filters_edit.text()),
            "event_reset_minutes": self.event_reset_spin.value(),
            "core_event_type": str(self.core_event_combo.currentData()),
            "obs_event_type": str(self.obs_event_combo.currentData()),
            "obs_filters": _parse_event_filters(self.obs_filters_edit.text()),
        }


class TwitchEventTriggerDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        trigger: TwitchEventAutomationTrigger | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Twitch Trigger" if trigger else "Add Twitch Trigger")
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.event_type_combo = QComboBox()
        for event_type in TWITCH_AUTOMATION_EVENT_TYPES:
            self.event_type_combo.addItem(_event_display_name(event_type), event_type)
        if trigger:
            self.event_type_combo.setCurrentIndex(
                max(self.event_type_combo.findData(trigger.event_type), 0)
            )
        self.filters_edit = QLineEdit()
        self.filters_edit.setPlaceholderText("reward.id=abc123, tier=1000")
        if trigger:
            self.filters_edit.setText(
                ", ".join(f"{key}={value}" for key, value in trigger.filters.items())
            )
        self.enabled_check = QCheckBox("Enabled")
        self.enabled_check.setChecked(trigger.enabled if trigger else True)
        self.reset_spin = QSpinBox()
        self.reset_spin.setRange(1, 180)
        self.reset_spin.setValue(trigger.reset_minutes if trigger else 15)
        self.reset_spin.setSuffix(" minutes offline")
        form.addRow("Event", self.event_type_combo)
        form.addRow("Optional field filters", self.filters_edit)
        form.addRow("Reset welcomes after", self.reset_spin)
        form.addRow("", self.enabled_check)
        layout.addLayout(form)
        help_label = QLabel(
            "No filters means every event of the selected type. Use field=value "
            "pairs for specific events; nested payload fields use dots. Example: "
            "reward.title=Hydrate."
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        self.event_type_combo.currentIndexChanged.connect(
            self._update_reset_visibility
        )
        self._update_reset_visibility()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict[str, object]:
        return {
            "event_type": str(self.event_type_combo.currentData()),
            "filters": _parse_event_filters(self.filters_edit.text()),
            "enabled": self.enabled_check.isChecked(),
            "reset_minutes": self.reset_spin.value(),
        }

    def _update_reset_visibility(self) -> None:
        visible = (
            self.event_type_combo.currentData()
            == "channel.chat.first_message"
        )
        self.reset_spin.setVisible(visible)
        form = self.layout().itemAt(0).layout()
        label = form.labelForField(self.reset_spin)
        if label is not None:
            label.setVisible(visible)


class CoreTriggerDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        trigger: CoreAutomationTrigger | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Core Trigger" if trigger else "Add Core Trigger")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.event_type_combo = QComboBox()
        for event_type, label in CORE_TRIGGER_TYPES.items():
            self.event_type_combo.addItem(label, event_type)
        if trigger is not None:
            self.event_type_combo.setCurrentIndex(
                max(self.event_type_combo.findData(trigger.event_type), 0)
            )
        self.enabled_check = QCheckBox("Enabled")
        self.enabled_check.setChecked(trigger.enabled if trigger else True)
        form.addRow("Program event", self.event_type_combo)
        form.addRow("", self.enabled_check)
        layout.addLayout(form)
        help_label = QLabel(
            "Core triggers run locally. Closing routines finish before Sally "
            "disconnects Twitch and clears its services."
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict[str, object]:
        return {
            "event_type": str(self.event_type_combo.currentData()),
            "enabled": self.enabled_check.isChecked(),
        }


class ObsTriggerDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        trigger: ObsAutomationTrigger | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit OBS Trigger" if trigger else "Add OBS Trigger")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.event_combo = QComboBox()
        for event_type, label in OBS_TRIGGER_TYPES.items():
            self.event_combo.addItem(label, event_type)
        self.filters_edit = QLineEdit()
        self.filters_edit.setPlaceholderText("sceneName=Gameplay, inputName=Mic/Aux")
        self.enabled_check = QCheckBox("Enabled")
        self.enabled_check.setChecked(trigger.enabled if trigger else True)
        if trigger:
            self.event_combo.setCurrentIndex(max(self.event_combo.findData(trigger.event_type), 0))
            self.filters_edit.setText(", ".join(f"{k}={v}" for k, v in trigger.filters.items()))
        form.addRow("OBS event", self.event_combo)
        form.addRow("Optional field filters", self.filters_edit)
        form.addRow("", self.enabled_check)
        layout.addLayout(form)
        help_label = QLabel("Filters use the field names supplied by OBS WebSocket. Leave blank to match every event of this type.")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict[str, object]:
        return {
            "event_type": str(self.event_combo.currentData()),
            "filters": _parse_event_filters(self.filters_edit.text()),
            "enabled": self.enabled_check.isChecked(),
        }


class SwitchCasesEditor(QWidget):
    def __init__(
        self,
        routine_store: RoutineStore | None,
        cases: object = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.routine_store = routine_store
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(("Case value", "Routine"))
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        add_button = QPushButton("+ Case")
        remove_button = QPushButton("Remove Selected")
        buttons.addWidget(add_button)
        buttons.addWidget(remove_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        add_button.clicked.connect(lambda: self.add_case())
        remove_button.clicked.connect(self.remove_selected)
        if isinstance(cases, dict):
            for value, routine_id in cases.items():
                self.add_case(str(value), str(routine_id))

    def add_case(self, value: str = "", routine_id: str = "") -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(value))
        routine = QComboBox()
        routine.addItem("Choose a routine…", "")
        if self.routine_store is not None:
            for definition in self.routine_store.routines:
                routine.addItem(definition.name, definition.routine_id)
        routine.setCurrentIndex(max(routine.findData(routine_id), 0))
        self.table.setCellWidget(row, 1, routine)

    def remove_selected(self) -> None:
        rows = sorted(
            {index.row() for index in self.table.selectedIndexes()},
            reverse=True,
        )
        for row in rows:
            self.table.removeRow(row)

    def value(self) -> dict[str, str]:
        cases: dict[str, str] = {}
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            combo = self.table.cellWidget(row, 1)
            value = item.text().strip() if item is not None else ""
            routine_id = str(combo.currentData() or "") if isinstance(combo, QComboBox) else ""
            if value and routine_id:
                cases[value] = routine_id
        return cases


class RandomChoicesEditor(QWidget):
    def __init__(
        self,
        routine_store: RoutineStore | None,
        choices: object = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.routine_store = routine_store
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(("Label", "Weight", "Routine"))
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        add_button = QPushButton("+ Choice")
        remove_button = QPushButton("Remove Selected")
        buttons.addWidget(add_button)
        buttons.addWidget(remove_button)
        buttons.addStretch()
        layout.addLayout(buttons)
        add_button.clicked.connect(lambda: self.add_choice())
        remove_button.clicked.connect(self.remove_selected)
        if isinstance(choices, list):
            for entry in choices:
                if isinstance(entry, dict):
                    self.add_choice(
                        str(entry.get("label", "")),
                        float(entry.get("weight", 1)),
                        str(entry.get("routine_id", "")),
                    )

    def add_choice(
        self,
        label: str = "",
        weight: float = 1,
        routine_id: str = "",
    ) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(label))
        weight_input = QDoubleSpinBox()
        weight_input.setRange(0.01, 1000000)
        weight_input.setDecimals(2)
        weight_input.setValue(weight)
        self.table.setCellWidget(row, 1, weight_input)
        routine = QComboBox()
        routine.addItem("Choose a routine…", "")
        if self.routine_store is not None:
            for definition in self.routine_store.routines:
                routine.addItem(definition.name, definition.routine_id)
        routine.setCurrentIndex(max(routine.findData(routine_id), 0))
        self.table.setCellWidget(row, 2, routine)

    def remove_selected(self) -> None:
        rows = sorted(
            {index.row() for index in self.table.selectedIndexes()},
            reverse=True,
        )
        for row in rows:
            self.table.removeRow(row)

    def value(self) -> list[dict[str, object]]:
        choices: list[dict[str, object]] = []
        for row in range(self.table.rowCount()):
            label_item = self.table.item(row, 0)
            weight = self.table.cellWidget(row, 1)
            routine = self.table.cellWidget(row, 2)
            routine_id = (
                str(routine.currentData() or "")
                if isinstance(routine, QComboBox)
                else ""
            )
            if not routine_id:
                continue
            choices.append(
                {
                    "label": label_item.text().strip() if label_item else "",
                    "weight": weight.value() if isinstance(weight, QDoubleSpinBox) else 1,
                    "routine_id": routine_id,
                }
            )
        return choices


class TaskEditorDialog(QDialog):
    LABELS = {
        **TWITCH_TASK_LABELS,
        **CORE_TASK_LABELS,
        **OBS_TASK_LABELS,
    }
    SCHEMAS: dict[str, tuple[dict[str, object], ...]] = {
        "twitch.send_chat_message": (
            {"key": "message", "label": "Message", "kind": "multiline", "default": "", "required": True, "placeholder": "Hello {user}!"},
            {"key": "as_bot", "label": "", "kind": "bool", "default": True, "text": "Send through Sally's bot account"},
        ),
        "twitch.send_pinned_message": (
            {"key": "message", "label": "Message", "kind": "multiline", "default": "", "required": True, "placeholder": "Important message for {user}"},
        ),
        "twitch.run_commercial": (
            {"key": "length", "label": "Length", "kind": "choice", "default": 30, "choices": (("30 seconds", 30), ("60 seconds", 60), ("90 seconds", 90), ("120 seconds", 120), ("150 seconds", 150), ("180 seconds", 180))},
        ),
        "twitch.snooze_ad": (),
        "twitch.update_stream_title": (
            {"key": "title", "label": "Stream title", "kind": "text", "default": "", "required": True, "placeholder": "Playing {game} with {user}"},
        ),
        "twitch.update_stream_category": (
            {"key": "category", "label": "Category name", "kind": "text", "default": "", "required": True, "placeholder": "Science & Technology"},
        ),
        "twitch.moderate_user": (
            {"key": "action", "label": "Action", "kind": "choice", "default": "timeout", "choices": (("Timeout", "timeout"), ("Ban", "ban"), ("Unban", "unban"), ("Delete message", "delete_message"))},
            {"key": "user", "label": "User ID or login", "kind": "text", "default": "{user_id}", "required": True, "placeholder": "{user_id}, {target}, or a login"},
            {"key": "duration_seconds", "label": "Timeout duration", "kind": "number", "default": 600, "minimum": 1, "maximum": 1209600, "suffix": " seconds"},
            {"key": "reason", "label": "Reason", "kind": "text", "default": "", "placeholder": "Optional; up to 500 characters"},
            {"key": "message_id", "label": "Message ID", "kind": "text", "default": "{message_id}", "placeholder": "Used only by Delete message"},
        ),
        "twitch.update_redemption": (
            {"key": "reward_id", "label": "Reward ID", "kind": "text", "default": "{reward_id}", "required": True},
            {"key": "redemption_id", "label": "Redemption ID", "kind": "text", "default": "{redemption_id}", "required": True},
            {"key": "action", "label": "Result", "kind": "choice", "default": "fulfill", "choices": (("Fulfill", "fulfill"), ("Cancel and refund", "refund"))},
        ),
        "core.launch_application": (
            {"key": "executable", "label": "Application", "kind": "file", "default": "", "required": True},
            {"key": "arguments", "label": "Arguments", "kind": "text", "default": "", "placeholder": "Optional command-line arguments"},
            {"key": "working_directory", "label": "Working folder", "kind": "folder", "default": ""},
            {"key": "start_minimized", "label": "", "kind": "bool", "default": False, "text": "Start minimized"},
            {"key": "only_if_not_running", "label": "", "kind": "bool", "default": True, "text": "Only launch if it is not already running"},
        ),
        "core.close_application": (
            {"key": "process_name", "label": "Process name", "kind": "text", "default": "", "required": True, "placeholder": "obs64.exe"},
            {"key": "force", "label": "", "kind": "bool", "default": False, "text": "Force close if a normal close fails"},
        ),
        "core.delay": (
            {"key": "seconds", "label": "Duration", "kind": "number", "default": 1.0, "minimum": 0.0, "maximum": 86400.0, "suffix": " seconds"},
        ),
        "core.random_delay": (
            {"key": "minimum_seconds", "label": "Minimum", "kind": "number", "default": 1.0, "minimum": 0.0, "maximum": 86400.0, "suffix": " seconds"},
            {"key": "maximum_seconds", "label": "Maximum", "kind": "number", "default": 5.0, "minimum": 0.0, "maximum": 86400.0, "suffix": " seconds"},
        ),
        "core.wait_for_service": (
            {"key": "service", "label": "Service", "kind": "choice", "default": "obs", "choices": (("OBS Studio", "obs"), ("Twitch", "twitch"))},
            {"key": "timeout_seconds", "label": "Timeout", "kind": "number", "default": 15.0, "minimum": 0.1, "maximum": 3600.0, "suffix": " seconds"},
        ),
        "core.open_target": (
            {"key": "target", "label": "File, folder, or URL", "kind": "target", "default": "", "required": True, "placeholder": "https://twitch.tv or C:/path"},
        ),
        "core.show_notification": (
            {"key": "title", "label": "Title", "kind": "text", "default": "Sally", "required": True, "placeholder": "Stream reminder"},
            {"key": "message", "label": "Message", "kind": "multiline", "default": "", "required": True, "placeholder": "{user} triggered a reminder"},
            {"key": "icon", "label": "Icon", "kind": "choice", "default": "information", "choices": (("Information", "information"), ("Warning", "warning"), ("Critical", "critical"), ("No icon", "none"))},
            {"key": "duration_seconds", "label": "Display duration", "kind": "number", "default": 5, "minimum": 1, "maximum": 60, "suffix": " seconds"},
        ),
        "core.play_audio": (
            {"key": "file", "label": "Audio file", "kind": "file", "default": "", "required": True, "placeholder": "C:/path/to/sound.ogg, .mp3, or .wav"},
            {"key": "volume", "label": "Volume", "kind": "number", "default": 80, "minimum": 0, "maximum": 100, "suffix": "%"},
            {"key": "wait_for_completion", "label": "", "kind": "bool", "default": False, "text": "Wait for the audio to finish before continuing"},
            {"key": "timeout_seconds", "label": "Timeout", "kind": "number", "default": 30.0, "minimum": 0.1, "maximum": 86400.0, "suffix": " seconds"},
        ),
        "core.create_global_variable": (
            {"key": "name", "label": "Variable name", "kind": "text", "default": "", "required": True, "placeholder": "death_count"},
            {"key": "value", "label": "Value", "kind": "text", "default": "", "placeholder": "0 or a template such as {user}"},
        ),
        "core.create_session_variable": (
            {"key": "name", "label": "Variable name", "kind": "text", "default": "", "required": True, "placeholder": "current_song"},
            {"key": "value", "label": "Value", "kind": "text", "default": "", "placeholder": "Value is forgotten when Sally closes"},
        ),
        "core.create_routine_variable": (
            {"key": "name", "label": "Variable name", "kind": "text", "default": "", "required": True, "placeholder": "winner"},
            {"key": "value", "label": "Value", "kind": "text", "default": "", "placeholder": "Shared with nested routines during this run"},
        ),
        "core.delete_variable": (
            {"key": "name", "label": "Variable name", "kind": "text", "default": "", "required": True, "placeholder": "death_count"},
        ),
        "core.adjust_variable": (
            {"key": "name", "label": "Variable name", "kind": "text", "default": "", "required": True, "placeholder": "death_count"},
            {"key": "amount", "label": "Amount", "kind": "number", "default": 1.0, "minimum": -1000000.0, "maximum": 1000000.0},
        ),
        "core.toggle_variable": (
            {"key": "name", "label": "Variable name", "kind": "text", "default": "", "required": True, "placeholder": "feature_enabled"},
        ),
        "core.run_routine": (
            {"key": "routine_id", "label": "Routine", "kind": "routine", "default": "", "required": True},
            {"key": "stop_on_failure", "label": "", "kind": "bool", "default": True, "text": "Stop this routine if the nested routine fails"},
        ),
        "core.set_routine_state": (
            {"key": "routine_id", "label": "Routine", "kind": "routine", "default": "", "required": True},
            {"key": "action", "label": "Action", "kind": "choice", "default": "toggle", "choices": (("Toggle", "toggle"), ("Enable", "enable"), ("Disable", "disable"))},
        ),
        "core.set_task_state": (
            {"key": "routine_id", "label": "Routine", "kind": "routine", "default": "", "required": True},
            {"key": "task_id", "label": "Task", "kind": "routine_task", "default": "", "required": True},
            {"key": "action", "label": "Action", "kind": "choice", "default": "toggle", "choices": (("Toggle", "toggle"), ("Enable", "enable"), ("Disable", "disable"))},
        ),
        "core.set_queue_state": (
            {"key": "queue_id", "label": "Queue", "kind": "queue", "default": "", "required": True},
            {"key": "action", "label": "Action", "kind": "choice", "default": "toggle", "choices": (("Toggle pause", "toggle"), ("Pause", "pause"), ("Resume", "resume"))},
        ),
        "core.clear_queue": (
            {"key": "queue_id", "label": "Queue", "kind": "queue", "default": "", "required": True},
        ),
        "core.logic_break": (),
        "core.logic_get_input": (
            {"key": "name", "label": "Output variable", "kind": "text", "default": "input_result", "required": True},
            {"key": "title", "label": "Window title", "kind": "text", "default": "Sally Input", "required": True},
            {"key": "prompt", "label": "Prompt", "kind": "text", "default": "Enter a value:", "required": True},
            {"key": "default", "label": "Default text", "kind": "text", "default": ""},
            {"key": "break_on_cancel", "label": "", "kind": "bool", "default": False, "text": "Break the routine if the input is cancelled"},
        ),
        "core.logic_random_number": (
            {"key": "name", "label": "Output variable", "kind": "text", "default": "random_number", "required": True},
            {"key": "mode", "label": "Number type", "kind": "choice", "default": "integer", "choices": (("Integer between two values", "integer"), ("Decimal from 0 to 1", "decimal"))},
            {"key": "minimum", "label": "Minimum", "kind": "number", "default": 0, "minimum": -1000000000, "maximum": 1000000000},
            {"key": "maximum", "label": "Maximum", "kind": "number", "default": 100, "minimum": -1000000000, "maximum": 1000000000},
        ),
        "core.logic_random_choice": (
            {"key": "choices", "label": "Weighted choices", "kind": "random_choices", "default": []},
        ),
        "core.file_read": (
            {"key": "path", "label": "Text file", "kind": "file", "default": "", "required": True},
            {"key": "variable", "label": "Output variable", "kind": "text", "default": "file_text", "required": True},
            {"key": "trim", "label": "", "kind": "bool", "default": False, "text": "Trim whitespace from the beginning and end"},
            {"key": "stop_on_failure", "label": "", "kind": "bool", "default": True, "text": "Stop the routine if the file cannot be read"},
        ),
        "core.file_random_line": (
            {"key": "path", "label": "Text file", "kind": "file", "default": "", "required": True},
            {"key": "variable", "label": "Output variable", "kind": "text", "default": "random_line", "required": True},
            {"key": "ignore_blank_lines", "label": "", "kind": "bool", "default": True, "text": "Ignore blank lines"},
            {"key": "stop_on_failure", "label": "", "kind": "bool", "default": True, "text": "Stop the routine if the file cannot be read"},
        ),
        "core.file_specific_line": (
            {"key": "path", "label": "Text file", "kind": "file", "default": "", "required": True},
            {"key": "line_number", "label": "Line number", "kind": "text", "default": "1", "required": True, "placeholder": "1 or {line_number}"},
            {"key": "variable", "label": "Output variable", "kind": "text", "default": "file_line", "required": True},
            {"key": "ignore_blank_lines", "label": "", "kind": "bool", "default": False, "text": "Ignore blank lines when counting"},
            {"key": "stop_on_failure", "label": "", "kind": "bool", "default": True, "text": "Stop the routine if the line cannot be read"},
        ),
        "core.file_write": (
            {"key": "path", "label": "Text file", "kind": "file", "default": "", "required": True},
            {"key": "mode", "label": "Write mode", "kind": "choice", "default": "append", "choices": (("Append to the file", "append"), ("Overwrite the file", "overwrite"))},
            {"key": "text", "label": "Text", "kind": "multiline", "default": "", "placeholder": "{user} redeemed {reward}"},
            {"key": "add_newline", "label": "", "kind": "bool", "default": True, "text": "Add a new line after the text"},
            {"key": "create_folders", "label": "", "kind": "bool", "default": False, "text": "Create missing parent folders"},
            {"key": "stop_on_failure", "label": "", "kind": "bool", "default": True, "text": "Stop the routine if the file cannot be written"},
        ),
        "core.path_exists": (
            {"key": "path", "label": "Path", "kind": "target", "default": "", "required": True},
            {"key": "path_type", "label": "Expected type", "kind": "choice", "default": "either", "choices": (("File or folder", "either"), ("File", "file"), ("Folder", "folder"))},
            {"key": "variable", "label": "Output variable", "kind": "text", "default": "path_exists", "required": True},
        ),
        "core.file_count_lines": (
            {"key": "path", "label": "Text file", "kind": "file", "default": "", "required": True},
            {"key": "variable", "label": "Output variable", "kind": "text", "default": "line_count", "required": True},
            {"key": "ignore_blank_lines", "label": "", "kind": "bool", "default": False, "text": "Do not count blank lines"},
            {"key": "stop_on_failure", "label": "", "kind": "bool", "default": True, "text": "Stop the routine if the file cannot be read"},
        ),
        "core.logic_if_else": (
            {"key": "left", "label": "Input", "kind": "text", "default": "", "required": True, "placeholder": "{death_count}"},
            {"key": "operator", "label": "Comparison", "kind": "choice", "default": "equals", "choices": COMPARISON_CHOICES},
            {"key": "right", "label": "Value", "kind": "text", "default": ""},
            {"key": "true_routine_id", "label": "If true, run", "kind": "routine", "default": ""},
            {"key": "false_routine_id", "label": "If false, run", "kind": "routine", "default": ""},
            {"key": "break_if_false", "label": "", "kind": "bool", "default": False, "text": "Break this routine when the condition is false"},
        ),
        "core.logic_switch": (
            {"key": "input", "label": "Input", "kind": "text", "default": "", "required": True, "placeholder": "{reward}"},
            {"key": "cases", "label": "Cases", "kind": "switch_cases", "default": {}},
            {"key": "default_routine_id", "label": "Default routine", "kind": "routine", "default": ""},
            {"key": "ignore_case", "label": "", "kind": "bool", "default": True, "text": "Ignore uppercase and lowercase differences"},
        ),
        "core.logic_while": (
            {"key": "left", "label": "Input", "kind": "text", "default": "", "required": True, "placeholder": "{counter}"},
            {"key": "operator", "label": "Comparison", "kind": "choice", "default": "less_than", "choices": COMPARISON_CHOICES},
            {"key": "right", "label": "Value", "kind": "text", "default": "10"},
            {"key": "routine_id", "label": "Repeat routine", "kind": "routine", "default": "", "required": True},
            {"key": "max_iterations", "label": "Maximum iterations", "kind": "number", "default": 100, "minimum": 1, "maximum": 10000},
            {"key": "timeout_seconds", "label": "Time limit", "kind": "number", "default": 10, "minimum": 0.1, "maximum": 3600, "suffix": " seconds"},
        ),
        "core.run_python_script": (
            {"key": "script", "label": "Python script", "kind": "python_file", "default": "", "required": True, "placeholder": "C:/path/to/script.py"},
            {"key": "python_executable", "label": "Python executable", "kind": "file", "default": "", "placeholder": "Optional; automatically uses Sally's Python when available"},
            {"key": "arguments", "label": "Arguments", "kind": "text", "default": "", "placeholder": "Optional, for example: --user \"{user}\""},
            {"key": "working_directory", "label": "Working folder", "kind": "folder", "default": ""},
            {"key": "timeout_seconds", "label": "Timeout", "kind": "number", "default": 30.0, "minimum": 0.1, "maximum": 86400.0, "suffix": " seconds"},
            {"key": "wait_for_completion", "label": "", "kind": "bool", "default": True, "text": "Wait for the script to finish"},
            {"key": "capture_output", "label": "", "kind": "bool", "default": True, "text": "Capture script output in run history"},
            {"key": "stop_on_failure", "label": "", "kind": "bool", "default": True, "text": "Stop the routine if the script fails or times out"},
        ),
        "obs.set_program_scene": (
            {"key": "scene", "label": "Scene", "kind": "obs_scene", "default": "", "required": True},
        ),
        "obs.set_preview_scene": (
            {"key": "scene", "label": "Preview scene", "kind": "obs_scene", "default": "", "required": True},
        ),
        "obs.set_scene_item_enabled": (
            {"key": "scene", "label": "Scene", "kind": "obs_scene", "default": "", "required": True},
            {"key": "source", "label": "Source", "kind": "obs_source", "default": "", "required": True},
            {"key": "action", "label": "Action", "kind": "choice", "default": "toggle", "choices": (("Toggle", "toggle"), ("Show", "show"), ("Hide", "hide"))},
        ),
        "obs.set_input_mute": (
            {"key": "input", "label": "Audio input", "kind": "obs_input", "default": "", "required": True},
            {"key": "action", "label": "Action", "kind": "choice", "default": "toggle", "choices": (("Toggle mute", "toggle"), ("Mute", "mute"), ("Unmute", "unmute"))},
        ),
        "obs.set_input_volume": (
            {"key": "input", "label": "Audio input", "kind": "obs_input", "default": "", "required": True},
            {"key": "volume_db", "label": "Volume", "kind": "number", "default": 0.0, "minimum": -100.0, "maximum": 26.0, "suffix": " dB"},
        ),
        "obs.set_source_filter_state": (
            {"key": "source", "label": "Source", "kind": "obs_input", "default": "", "required": True},
            {"key": "filter", "label": "Filter", "kind": "obs_filter", "default": "", "required": True},
            {"key": "action", "label": "Action", "kind": "choice", "default": "toggle", "choices": (("Toggle", "toggle"), ("Enable", "enable"), ("Disable", "disable"))},
        ),
        "obs.set_scene_filter_state": (
            {"key": "scene", "label": "Scene", "kind": "obs_scene", "default": "", "required": True},
            {"key": "filter", "label": "Filter", "kind": "obs_filter", "default": "", "required": True},
            {"key": "action", "label": "Action", "kind": "choice", "default": "toggle", "choices": (("Toggle", "toggle"), ("Enable", "enable"), ("Disable", "disable"))},
        ),
        "obs.set_text_source": (
            {"key": "input", "label": "Text source", "kind": "obs_input", "default": "", "required": True},
            {"key": "text", "label": "Text", "kind": "multiline", "default": "", "placeholder": "Now playing: {game}"},
        ),
        "obs.set_image_source": (
            {"key": "input", "label": "Image source", "kind": "obs_input", "default": "", "required": True},
            {"key": "file", "label": "Image file", "kind": "file", "default": "", "required": True},
        ),
        "obs.stream_control": (
            {"key": "action", "label": "Action", "kind": "choice", "default": "start", "choices": (("Start streaming", "start"), ("Stop streaming", "stop"))},
        ),
        "obs.record_control": (
            {"key": "action", "label": "Action", "kind": "choice", "default": "start", "choices": (("Start recording", "start"), ("Stop recording", "stop"), ("Pause recording", "pause"), ("Resume recording", "resume"))},
        ),
        "obs.replay_buffer_control": (
            {"key": "action", "label": "Action", "kind": "choice", "default": "save", "choices": (("Save replay", "save"), ("Start replay buffer", "start"), ("Stop replay buffer", "stop"))},
        ),
        "obs.media_control": (
            {"key": "input", "label": "Media source", "kind": "obs_input", "default": "", "required": True},
            {"key": "action", "label": "Action", "kind": "choice", "default": "restart", "choices": (("Play", "play"), ("Pause", "pause"), ("Stop", "stop"), ("Restart", "restart"), ("Next", "next"), ("Previous", "previous"))},
        ),
        "obs.trigger_hotkey": (
            {"key": "hotkey", "label": "Hotkey", "kind": "obs_hotkey", "default": "", "required": True},
        ),
        "obs.set_studio_mode": (
            {"key": "enabled", "label": "Studio Mode", "kind": "choice", "default": True, "choices": (("Enable", True), ("Disable", False))},
        ),
        "obs.raw_request": (
            {"key": "request_type", "label": "Request type", "kind": "text", "default": "GetVersion", "required": True},
            {"key": "request_data", "label": "Request data", "kind": "json", "default": {}},
        ),
    }
    TEMPLATED_FIELDS: dict[str, tuple[str, ...]] = {
        "twitch.send_chat_message": ("message",),
        "twitch.send_pinned_message": ("message",),
        "twitch.update_stream_title": ("title",),
        "twitch.update_stream_category": ("category",),
        "twitch.moderate_user": ("user", "reason", "message_id"),
        "twitch.update_redemption": ("reward_id", "redemption_id"),
        "core.create_global_variable": ("value",),
        "core.create_session_variable": ("value",),
        "core.create_routine_variable": ("value",),
        "core.logic_get_input": ("title", "prompt", "default"),
        "core.logic_if_else": ("left", "right"),
        "core.logic_switch": ("input",),
        "core.logic_while": ("left", "right"),
        "core.run_python_script": ("script", "arguments", "working_directory"),
        "core.show_notification": ("title", "message"),
        "core.file_read": ("path",),
        "core.file_random_line": ("path",),
        "core.file_specific_line": ("path", "line_number"),
        "core.file_write": ("path", "text"),
        "core.path_exists": ("path",),
        "core.file_count_lines": ("path",),
        "obs.set_text_source": ("text",),
        "obs.set_image_source": ("file",),
    }

    def __init__(
        self,
        task_type: str,
        parent: QWidget | None = None,
        task: TaskDefinition | None = None,
        obs_service: ObsWebSocketService | None = None,
        variables: dict[str, str] | None = None,
        routine_store: RoutineStore | None = None,
        queue_store: AutomationQueueStore | None = None,
    ) -> None:
        super().__init__(parent)
        self.task = task
        self.task_type = task.task_type if task is not None else task_type
        self.obs_service = obs_service
        self.variables = dict(variables or {})
        self.routine_store = routine_store
        self.queue_store = queue_store
        self.field_widgets: dict[str, dict[str, QWidget]] = {}
        self.setWindowTitle("Edit Task" if task else "Add Task")
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        task_label = QLabel(self.LABELS.get(self.task_type, self.task_type))
        self.name_edit = QLineEdit(task.name if task else "")
        self.enabled_check = QCheckBox("Enabled")
        self.enabled_check.setChecked(task.enabled if task else True)
        form.addRow("Task", task_label)
        form.addRow("Name", self.name_edit)
        form.addRow("", self.enabled_check)
        layout.addLayout(form)

        obs_toolbar = QHBoxLayout()
        self.obs_choices_status = QLabel("")
        self.refresh_obs_choices_button = QPushButton("Refresh OBS Lists")
        self.refresh_obs_choices_button.clicked.connect(self._refresh_obs_choices)
        obs_toolbar.addWidget(self.obs_choices_status)
        obs_toolbar.addStretch()
        obs_toolbar.addWidget(self.refresh_obs_choices_button)
        layout.addLayout(obs_toolbar)

        layout.addWidget(self._build_page(self.task_type))
        if self.task_type == "core.play_audio":
            self._audio_preview = PlayAudioTask()
            preview_row = QHBoxLayout()
            self.test_audio_button = QPushButton("Test Audio")
            self.audio_test_status = QLabel("")
            self.audio_test_status.setWordWrap(True)
            preview_row.addWidget(self.test_audio_button)
            preview_row.addWidget(self.audio_test_status, 1)
            layout.addLayout(preview_row)
            self.test_audio_button.clicked.connect(self._test_audio)
            self.finished.connect(lambda _result: self._audio_preview.stop_all())
        if self.task_type == "core.run_python_script":
            warning = QLabel(
                "Trusted local code: this script runs outside Sally and has the "
                "same access to your computer as your user account. Only run "
                "scripts you trust. Trigger values are provided through arguments "
                "and SALLY_* environment variables."
            )
            warning.setObjectName("pythonScriptWarning")
            warning.setWordWrap(True)
            warning.setStyleSheet(
                "background-color:#332b18; border:1px solid #80651f; "
                "border-radius:4px; color:#f2d675; padding:8px;"
            )
            layout.addWidget(warning)
        self._build_variable_help(layout)

        if self.TEMPLATED_FIELDS.get(self.task_type):
            variables = QLabel(
                "Task templates support variables such as {user}, {channel}, "
                "{event_type}, {scene}, {source}, {input}, {message}, "
                "{viewers}, {reward}, {command}, and {args}."
            )
            variables.setWordWrap(True)
            layout.addWidget(variables)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_page()
        QTimer.singleShot(0, self._refresh_obs_choices)

    def _build_variable_help(self, layout: QVBoxLayout) -> None:
        field_keys = self.TEMPLATED_FIELDS.get(self.task_type, ())
        if not field_keys:
            return
        group = QGroupBox("Available Variables")
        group_layout = QVBoxLayout(group)
        help_label = QLabel(
            "Live trigger values are shown first. Other known variables use "
            "sample values in the preview and are filled from Twitch, OBS, or "
            "runtime context when the task runs."
        )
        help_label.setWordWrap(True)
        group_layout.addWidget(help_label)
        self.variable_table = QTableWidget(0, 4)
        self.variable_table.setHorizontalHeaderLabels(
            ("Variable", "Source", "Test value", "Meaning")
        )
        self.variable_table.horizontalHeader().setStretchLastSection(True)
        self.variable_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.variable_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.variable_table.setMaximumHeight(190)
        ordered_keys = list(self.variables)
        ordered_keys.extend(key for key in VARIABLE_INFO if key not in self.variables)
        self.variable_preview_context = {
            **sample_context(ordered_keys),
            **self.variables,
        }
        for row, key in enumerate(ordered_keys):
            value = self.variable_preview_context.get(key, "")
            self.variable_table.insertRow(row)
            description = VARIABLE_INFO.get(key, (value, "Trigger value"))[1]
            self.variable_table.setItem(row, 0, QTableWidgetItem(f"{{{key}}}"))
            self.variable_table.setItem(
                row,
                1,
                QTableWidgetItem(
                    VARIABLE_SOURCE_INFO.get(
                        key,
                        "Trigger context" if key in VARIABLE_INFO else "Custom variable",
                    )
                ),
            )
            self.variable_table.setItem(row, 2, QTableWidgetItem(value))
            self.variable_table.setItem(row, 3, QTableWidgetItem(description))
        if self.variable_table.rowCount():
            self.variable_table.selectRow(0)
        group_layout.addWidget(self.variable_table)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Insert into"))
        self.variable_field_combo = QComboBox()
        schema_by_key = {
            str(spec["key"]): str(spec.get("label", spec["key"]))
            for spec in self.SCHEMAS.get(self.task_type, ())
        }
        for key in field_keys:
            if key in self.field_widgets.get(self.task_type, {}):
                self.variable_field_combo.addItem(schema_by_key.get(key, key), key)
        self.insert_variable_button = QPushButton("Insert Selected Variable")
        controls.addWidget(self.variable_field_combo)
        controls.addWidget(self.insert_variable_button)
        controls.addStretch()
        group_layout.addLayout(controls)
        self.variable_preview_label = QLabel()
        self.variable_preview_label.setWordWrap(True)
        group_layout.addWidget(self.variable_preview_label)
        self.insert_variable_button.clicked.connect(self._insert_selected_variable)
        self.variable_field_combo.currentIndexChanged.connect(
            lambda _index: self._update_variable_preview()
        )
        for key in field_keys:
            widget = self.field_widgets.get(self.task_type, {}).get(key)
            if isinstance(widget, QLineEdit):
                widget.textChanged.connect(lambda _text: self._update_variable_preview())
            elif isinstance(widget, QTextEdit):
                widget.textChanged.connect(self._update_variable_preview)
        self._update_variable_preview()
        layout.addWidget(group)

    def _selected_variable(self) -> str:
        row = self.variable_table.currentRow() if hasattr(self, "variable_table") else -1
        item = self.variable_table.item(row, 0) if row >= 0 else None
        return item.text() if item is not None else ""

    def _template_widget(self) -> QWidget | None:
        key = str(self.variable_field_combo.currentData() or "")
        return self.field_widgets.get(self.task_type, {}).get(key)

    def _insert_selected_variable(self) -> None:
        variable = self._selected_variable()
        widget = self._template_widget()
        if not variable or widget is None:
            return
        if isinstance(widget, QLineEdit):
            widget.insert(variable)
        elif isinstance(widget, QTextEdit):
            widget.insertPlainText(variable)
        self._update_variable_preview()

    def _update_variable_preview(self) -> None:
        if not hasattr(self, "variable_preview_label"):
            return
        widget = self._template_widget()
        if isinstance(widget, QLineEdit):
            template = widget.text()
        elif isinstance(widget, QTextEdit):
            template = widget.toPlainText()
        else:
            template = ""
        self.variable_preview_label.setText(
            "Preview: "
            + (
                render_preview(
                    template,
                    getattr(self, "variable_preview_context", self.variables),
                )
                or "(empty)"
            )
        )

    def _build_page(self, task_type: str) -> QWidget:
        page = QWidget()
        form = QFormLayout(page)
        fields: dict[str, QWidget] = {}
        values = self.task.config if self.task and self.task.task_type == task_type else {}
        schema = self.SCHEMAS.get(task_type, ())
        if not schema:
            form.addRow(QLabel("This task provider has no editable settings."))
        for spec in schema:
            key, kind = str(spec["key"]), str(spec["kind"])
            value = values.get(key, spec.get("default"))
            widget, row_widget = self._create_field(kind, spec, value)
            fields[key] = widget
            form.addRow(str(spec.get("label", "")), row_widget)
        self.field_widgets[task_type] = fields
        return page

    def _create_field(
        self, kind: str, spec: dict[str, object], value: object
    ) -> tuple[QWidget, QWidget]:
        if kind in {"text", "file", "folder", "target", "python_file"}:
            edit = QLineEdit(str(value or ""))
            edit.setPlaceholderText(str(spec.get("placeholder", "")))
            if kind == "text":
                return edit, edit
            container = QWidget()
            row = QHBoxLayout(container)
            row.setContentsMargins(0, 0, 0, 0)
            row.addWidget(edit)
            if kind == "target":
                file_button = QPushButton("File…")
                folder_button = QPushButton("Folder…")
                file_button.clicked.connect(lambda: self._choose_file(edit))
                folder_button.clicked.connect(lambda: self._choose_folder(edit))
                row.addWidget(file_button)
                row.addWidget(folder_button)
            else:
                button = QPushButton("Browse…")
                button.clicked.connect(
                    (lambda: self._choose_file(edit))
                    if kind == "file"
                    else (
                        lambda: self._choose_file(
                            edit, "Python Scripts (*.py *.pyw);;All Files (*)"
                        )
                    )
                    if kind == "python_file"
                    else (lambda: self._choose_folder(edit))
                )
                row.addWidget(button)
            return edit, container
        if kind == "multiline":
            edit = QTextEdit()
            edit.setAcceptRichText(False)
            edit.setMaximumHeight(120)
            edit.setPlaceholderText(str(spec.get("placeholder", "")))
            edit.setPlainText(str(value or ""))
            return edit, edit
        if kind == "bool":
            check = QCheckBox(str(spec.get("text", "")))
            check.setChecked(bool(value))
            return check, check
        if kind == "number":
            spin = QDoubleSpinBox()
            spin.setRange(float(spec.get("minimum", -1_000_000)), float(spec.get("maximum", 1_000_000)))
            spin.setDecimals(2)
            spin.setSuffix(str(spec.get("suffix", "")))
            spin.setValue(float(value or 0))
            return spin, spin
        if kind == "choice":
            combo = QComboBox()
            for label, data in spec.get("choices", ()):
                combo.addItem(str(label), data)
            index = combo.findData(value)
            combo.setCurrentIndex(max(index, 0))
            return combo, combo
        if kind == "routine":
            combo = QComboBox()
            combo.addItem("Choose a routine…", "")
            if self.routine_store is not None:
                for routine in self.routine_store.routines:
                    combo.addItem(routine.name, routine.routine_id)
            index = combo.findData(str(value or ""))
            combo.setCurrentIndex(max(index, 0))
            return combo, combo
        if kind == "routine_task":
            combo = QComboBox()
            combo.addItem("Choose a task...", "")
            if self.routine_store is not None:
                for routine in self.routine_store.routines:
                    for routine_task in routine.tasks:
                        combo.addItem(
                            f"{routine.name} / {routine_task.name}",
                            routine_task.task_id,
                        )
            index = combo.findData(str(value or ""))
            combo.setCurrentIndex(max(index, 0))
            return combo, combo
        if kind == "queue":
            combo = QComboBox()
            combo.addItem("Choose a queue...", "")
            if self.queue_store is not None:
                for queue in self.queue_store.queues:
                    combo.addItem(queue.name, queue.queue_id)
            index = combo.findData(str(value or ""))
            combo.setCurrentIndex(max(index, 0))
            return combo, combo
        if kind == "switch_cases":
            editor = SwitchCasesEditor(self.routine_store, value)
            editor.setMinimumHeight(180)
            return editor, editor
        if kind == "random_choices":
            editor = RandomChoicesEditor(self.routine_store, value)
            editor.setMinimumHeight(200)
            return editor, editor
        if kind.startswith("obs_"):
            combo = QComboBox()
            combo.setEditable(True)
            combo.setCurrentText(str(value or ""))
            combo.setProperty("obs_choice_kind", kind)
            if kind == "obs_scene":
                combo.activated.connect(
                    lambda _index, field=combo: self._refresh_obs_sources(
                        field.currentText()
                    )
                )
                if combo.lineEdit() is not None:
                    combo.lineEdit().editingFinished.connect(
                        lambda field=combo: self._refresh_obs_sources(
                            field.currentText()
                        )
                    )
            return combo, combo
        if kind == "json":
            edit = QTextEdit()
            edit.setAcceptRichText(False)
            edit.setMaximumHeight(160)
            edit.setPlainText(json.dumps(value if isinstance(value, dict) else {}, indent=2))
            return edit, edit
        edit = QLineEdit(str(value or ""))
        return edit, edit

    def _update_page(self) -> None:
        is_obs = self.task_type.startswith("obs.")
        self.refresh_obs_choices_button.setVisible(is_obs)
        self.obs_choices_status.setVisible(is_obs)
        if self.task_type == "core.run_python_script":
            fields = self.field_widgets[self.task_type]
            wait = fields["wait_for_completion"]
            dependent = (
                fields["timeout_seconds"],
                fields["capture_output"],
                fields["stop_on_failure"],
            )

            def update_script_mode(checked: bool) -> None:
                for widget in dependent:
                    widget.setEnabled(checked)

            wait.toggled.connect(update_script_mode)
            update_script_mode(wait.isChecked())
        if self.task_type == "core.play_audio":
            fields = self.field_widgets[self.task_type]
            wait = fields["wait_for_completion"]

            def update_audio_mode(checked: bool) -> None:
                fields["timeout_seconds"].setEnabled(checked)

            wait.toggled.connect(update_audio_mode)
            update_audio_mode(wait.isChecked())
        if self.task_type == "core.logic_random_number":
            fields = self.field_widgets[self.task_type]
            mode = fields["mode"]

            def update_random_mode(_index: int = 0) -> None:
                integer_mode = mode.currentData() == "integer"
                fields["minimum"].setEnabled(integer_mode)
                fields["maximum"].setEnabled(integer_mode)

            mode.currentIndexChanged.connect(update_random_mode)
            update_random_mode()
        if self.task_type in {"core.logic_if_else", "core.logic_while"}:
            fields = self.field_widgets[self.task_type]
            operator = fields["operator"]

            def update_condition(_index: int = 0) -> None:
                fields["right"].setEnabled(
                    str(operator.currentData()) not in UNARY_OPERATORS
                )

            operator.currentIndexChanged.connect(update_condition)
            update_condition()
        if self.task is None:
            label = self.LABELS.get(self.task_type, self.task_type)
            self.name_edit.setText(label.partition("—")[2].strip() or label)

    def _test_audio(self) -> None:
        fields = self.field_widgets["core.play_audio"]
        config = {
            "file": fields["file"].text().strip(),
            "volume": fields["volume"].value(),
            "wait_for_completion": False,
        }
        self._audio_preview.stop_all()
        result = self._audio_preview.execute(
            TaskDefinition(
                task_id="audio-preview",
                task_type="core.play_audio",
                name="Audio preview",
                config=config,
            ),
            TriggerEvent(
                trigger_id="audio-preview",
                service="core",
                trigger_type="preview",
                context={},
            ),
        )
        self.audio_test_status.setText(result.detail)
        self.audio_test_status.setProperty(
            "state", "success" if result.succeeded else "error"
        )
        self.audio_test_status.style().unpolish(self.audio_test_status)
        self.audio_test_status.style().polish(self.audio_test_status)

    def _choose_file(
        self, edit: QLineEdit, file_filter: str = "All Files (*)"
    ) -> None:
        filename, _filter = QFileDialog.getOpenFileName(
            self, "Choose File", edit.text(), file_filter
        )
        if filename:
            edit.setText(filename)

    def _choose_folder(self, edit: QLineEdit) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose Folder", edit.text())
        if folder:
            edit.setText(folder)

    def _refresh_obs_choices(self) -> None:
        if self.obs_service is None or not self.obs_service.connected:
            self.obs_choices_status.setText("Connect OBS to load its names; fields remain editable.")
            return
        self.obs_choices_status.setText("Loading OBS scenes, inputs, and hotkeys…")
        pending = {"scenes", "inputs", "hotkeys"}

        def completed(kind: str, values: list[str]) -> None:
            self._populate_obs_choices(kind, values)
            pending.discard(kind)
            if not pending:
                self.obs_choices_status.setText("OBS lists loaded.")

        def scenes_completed(values: list[str]) -> None:
            completed("obs_scene", values)
            selected_scenes = {
                widget.currentText().strip()
                for fields in self.field_widgets.values()
                for widget in fields.values()
                if isinstance(widget, QComboBox)
                and widget.property("obs_choice_kind") == "obs_scene"
                and widget.currentText().strip()
            }
            for scene in selected_scenes:
                self._refresh_obs_sources(scene)

        try:
            self.obs_service.send_request(
                "GetSceneList",
                callback=lambda result: scenes_completed(
                    [str(item.get("sceneName", "")) for item in result.response_data.get("scenes", []) if isinstance(item, dict)],
                ),
            )
            self.obs_service.send_request(
                "GetInputList",
                callback=lambda result: completed(
                    "obs_input",
                    [str(item.get("inputName", "")) for item in result.response_data.get("inputs", []) if isinstance(item, dict)],
                ),
            )
            self.obs_service.send_request(
                "GetHotkeyList",
                callback=lambda result: completed(
                    "obs_hotkey",
                    [str(value) for value in result.response_data.get("hotkeys", [])],
                ),
            )
        except ValueError as error:
            self.obs_choices_status.setText(str(error))

    def _refresh_obs_sources(self, scene: str) -> None:
        clean_scene = scene.strip()
        if not clean_scene or self.obs_service is None or not self.obs_service.connected:
            return
        try:
            self.obs_service.send_request(
                "GetSceneItemList",
                {"sceneName": clean_scene},
                callback=lambda result: self._populate_obs_choices(
                    "obs_source",
                    [
                        str(item.get("sourceName", ""))
                        for item in result.response_data.get("sceneItems", [])
                        if isinstance(item, dict)
                    ],
                ),
            )
        except ValueError as error:
            self.obs_choices_status.setText(str(error))

    def _populate_obs_choices(self, kind: str, values: list[str]) -> None:
        clean_values = sorted({value for value in values if value}, key=str.casefold)
        for fields in self.field_widgets.values():
            for widget in fields.values():
                if isinstance(widget, QComboBox) and widget.property("obs_choice_kind") == kind:
                    current = widget.currentText()
                    widget.blockSignals(True)
                    widget.clear()
                    widget.addItems(clean_values)
                    widget.setCurrentText(current)
                    widget.blockSignals(False)

    def values(self) -> dict[str, object]:
        task_type = self.task_type
        config: dict[str, object] = {}
        fields = self.field_widgets.get(task_type, {})
        for spec in self.SCHEMAS.get(task_type, ()):
            key, kind = str(spec["key"]), str(spec["kind"])
            widget = fields[key]
            if isinstance(widget, QLineEdit):
                value: object = widget.text().strip()
            elif isinstance(widget, QTextEdit):
                text = widget.toPlainText().strip()
                if kind == "json":
                    value = json.loads(text or "{}")
                    if not isinstance(value, dict):
                        raise ValueError(f"{spec.get('label', key)} must be a JSON object.")
                else:
                    value = text
            elif isinstance(widget, QCheckBox):
                value = widget.isChecked()
            elif isinstance(widget, QDoubleSpinBox):
                value = widget.value()
            elif isinstance(widget, QComboBox):
                value = widget.currentText().strip() if kind.startswith("obs_") else widget.currentData()
            elif isinstance(widget, SwitchCasesEditor):
                value = widget.value()
            elif isinstance(widget, RandomChoicesEditor):
                value = widget.value()
            else:
                value = ""
            if spec.get("required") and not str(value).strip():
                raise ValueError(f"{spec.get('label', key)} is required.")
            config[key] = value
        if task_type == "core.logic_random_choice" and not config.get("choices"):
            raise ValueError("Add at least one weighted choice and select its routine.")
        if (
            task_type == "core.random_delay"
            and float(config.get("minimum_seconds", 0))
            > float(config.get("maximum_seconds", 0))
        ):
            raise ValueError("Minimum delay cannot be greater than maximum delay.")
        if task_type == SendTwitchChatMessageTask.task_type:
            SendTwitchChatMessageTask.validate_template(
                str(config.get("message", "")),
                self.variables,
            )
        if task_type in {
            "core.create_global_variable",
            "core.create_session_variable",
            "core.create_routine_variable",
            "core.delete_variable",
            "core.adjust_variable",
            "core.toggle_variable",
            "core.logic_get_input",
            "core.logic_random_number",
            "core.file_read",
            "core.file_random_line",
            "core.file_specific_line",
            "core.path_exists",
            "core.file_count_lines",
        }:
            variable_key = "variable" if task_type in FILE_TASK_TYPES else "name"
            config[variable_key] = CustomVariableStore.validate_name(
                str(config.get(variable_key, ""))
            )
        name = self.name_edit.text().strip()
        if not name:
            raise ValueError("Task name is required.")
        return {
            "task_type": task_type,
            "name": name,
            "config": config,
            "enabled": self.enabled_check.isChecked(),
        }


class TaskTestDialog(QDialog):
    def __init__(
        self,
        task: TaskDefinition,
        context: dict[str, str],
        external_effect: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Test Selected Task")
        self.setMinimumSize(620, 460)
        layout = QVBoxLayout(self)
        title = QLabel(f"Test: {task.name}")
        title_font = title.font()
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)
        notice = QLabel(
            f"External action: {external_effect}"
            if external_effect
            else "No external side effect was detected for this task."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)
        instructions = QLabel(
            "Edit the sample trigger values below. The selected task will run "
            "once using this context; other routine tasks will not run."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        self.context_table = QTableWidget(0, 2)
        self.context_table.setHorizontalHeaderLabels(("Variable", "Test value"))
        self.context_table.horizontalHeader().setStretchLastSection(True)
        for row, (key, value) in enumerate(context.items()):
            self.context_table.insertRow(row)
            key_item = QTableWidgetItem(key)
            key_item.setFlags(key_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.context_table.setItem(row, 0, key_item)
            self.context_table.setItem(row, 1, QTableWidgetItem(value))
        layout.addWidget(self.context_table, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Run Task")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict[str, str]:
        return {
            self.context_table.item(row, 0).text(): self.context_table.item(row, 1).text()
            for row in range(self.context_table.rowCount())
        }


class RoutineTreeWidget(QTreeWidget):
    """Routine-only drag and drop that never nests routines inside routines."""

    routine_dropped = Signal(str, str, int)
    KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 1

    def _schedule_routine_drop(
        self,
        routine_id: str,
        group_id: str,
        destination_index: int,
    ) -> None:
        """Persist the move after Qt has finished processing the active drop."""
        QTimer.singleShot(
            0,
            lambda: self.routine_dropped.emit(
                routine_id,
                group_id,
                destination_index,
            ),
        )

    def dropEvent(self, event) -> None:
        if not bool(self.property("routine_reorder_enabled")):
            event.ignore()
            return
        selected = self.selectedItems()
        source = selected[0] if selected else None
        target = self.itemAt(event.position().toPoint())
        if (
            source is None
            or target is None
            or source is target
            or source.data(0, self.KIND_ROLE) != "routine"
        ):
            event.ignore()
            return
        target_kind = target.data(0, self.KIND_ROLE)
        if target_kind == "group":
            destination = target
            destination_index = destination.childCount()
        elif target_kind == "routine" and target.parent() is not None:
            destination = target.parent()
            destination_index = destination.indexOfChild(target)
            if self.dropIndicatorPosition() != QAbstractItemView.DropIndicatorPosition.AboveItem:
                destination_index += 1
        else:
            event.ignore()
            return

        source_parent = source.parent()
        if source_parent is None:
            event.ignore()
            return
        source_index = source_parent.indexOfChild(source)
        source_parent.takeChild(source_index)
        if source_parent is destination and source_index < destination_index:
            destination_index -= 1
        destination_index = max(0, min(destination_index, destination.childCount()))
        destination.insertChild(destination_index, source)
        destination.setExpanded(True)
        self.setCurrentItem(source)
        self._schedule_routine_drop(
            str(source.data(0, Qt.ItemDataRole.UserRole) or ""),
            str(destination.data(0, Qt.ItemDataRole.UserRole) or ""),
            destination_index,
        )
        event.acceptProposedAction()


class QueueEditorDialog(QDialog):
    def __init__(
        self,
        queue: AutomationQueueDefinition | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Queue" if queue else "New Queue")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(queue.name if queue else "")
        self.name_edit.setPlaceholderText("Soundboard")
        self.max_length_spin = QSpinBox()
        self.max_length_spin.setRange(1, 10_000)
        self.max_length_spin.setValue(queue.max_length if queue else 100)
        self.duplicate_combo = QComboBox()
        self.duplicate_combo.addItem("Allow every trigger", "allow")
        self.duplicate_combo.addItem("Ignore if already queued or running", "ignore")
        self.duplicate_combo.addItem("Replace the older pending copy", "replace")
        if queue:
            self.duplicate_combo.setCurrentIndex(
                max(self.duplicate_combo.findData(queue.duplicate_policy), 0)
            )
        self.delay_spin = QDoubleSpinBox()
        self.delay_spin.setRange(0, 3600)
        self.delay_spin.setDecimals(2)
        self.delay_spin.setSuffix(" seconds")
        self.delay_spin.setValue(queue.delay_seconds if queue else 0)
        form.addRow("Name", self.name_edit)
        form.addRow("Maximum pending", self.max_length_spin)
        form.addRow("Duplicate triggers", self.duplicate_combo)
        form.addRow("Delay between routines", self.delay_spin)
        layout.addLayout(form)
        note = QLabel(
            "Queues execute one routine at a time. Nested routines remain part "
            "of their parent and do not create another queue item."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_values)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_values(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "Queue Name Required", "Enter a queue name.")
            return
        self.accept()

    def values(self) -> dict[str, object]:
        return {
            "name": self.name_edit.text().strip(),
            "max_length": self.max_length_spin.value(),
            "duplicate_policy": str(self.duplicate_combo.currentData()),
            "delay_seconds": self.delay_spin.value(),
        }


class AutomationPage(QWidget):
    KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 1

    def __init__(
        self,
        routine_store: RoutineStore,
        trigger_store: TwitchCommandTriggerStore,
        event_trigger_store: TwitchEventTriggerStore,
        core_trigger_store: CoreTriggerStore,
        obs_trigger_store: ObsTriggerStore,
        task_registry: TaskRegistry,
        automation_service: AutomationService,
        *,
        obs_service: ObsWebSocketService | None = None,
        commands_changed: Callable[[], None] | None = None,
        queue_store: AutomationQueueStore | None = None,
        queue_manager: AutomationQueueManager | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.routine_store = routine_store
        self.trigger_store = trigger_store
        self.event_trigger_store = event_trigger_store
        self.core_trigger_store = core_trigger_store
        self.obs_trigger_store = obs_trigger_store
        self.task_registry = task_registry
        self.automation_service = automation_service
        self.obs_service = obs_service
        self.commands_changed = commands_changed or (lambda: None)
        self.queue_store = queue_store or AutomationQueueStore(
            routine_store.path.with_name("queues.json")
        )
        self.queue_manager = queue_manager or AutomationQueueManager(self.queue_store)
        self.history: list[dict[str, object]] = []
        self._selected_routine_id = ""
        self.setObjectName("automationPage")
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("automationTabs")
        root.addWidget(self.tabs)
        self._build_routines_tab()
        self._build_queues_tab()
        self._build_task_library_tab()
        self._build_history_tab()
        self.queue_timer = QTimer(self)
        self.queue_timer.setInterval(100)
        self.queue_timer.timeout.connect(self._poll_queues)
        self.queue_timer.start()

    def set_responsive_orientation(self, portrait: bool) -> None:
        orientation = (
            Qt.Orientation.Vertical
            if portrait
            else Qt.Orientation.Horizontal
        )
        for splitter in self.findChildren(QSplitter):
            splitter.setOrientation(orientation)
            if splitter.count() == 2:
                splitter.setStretchFactor(0, 1)
                splitter.setStretchFactor(1, 1)
                splitter.setSizes([1, 1])

    def _build_routines_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.routines_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.routines_splitter.setObjectName("automationSplitter")
        self.routines_splitter.setChildrenCollapsible(False)
        layout.addWidget(self.routines_splitter)

        browser = QWidget()
        browser.setMinimumWidth(230)
        browser_layout = QVBoxLayout(browser)
        toolbar = QHBoxLayout()
        self.new_routine_button = QPushButton("+ New Routine")
        self.new_group_button = QPushButton("+ Group")
        self.import_routine_button = QPushButton("Import")
        self.export_routine_button = QPushButton("Export")
        self.sort_routines_button = QPushButton("Sort A-Z")
        self.sort_routines_button.setObjectName("automationRoutineSortButton")
        self.sort_routines_button.setCheckable(True)
        self.sort_routines_button.setToolTip(
            "Show groups and routines alphabetically. Ungrouped always stays first."
        )
        toolbar.addWidget(self.new_routine_button)
        toolbar.addWidget(self.new_group_button)
        browser_layout.addLayout(toolbar)
        transfer_toolbar = QHBoxLayout()
        transfer_toolbar.addWidget(self.import_routine_button)
        transfer_toolbar.addWidget(self.export_routine_button)
        transfer_toolbar.addStretch()
        transfer_toolbar.addWidget(self.sort_routines_button)
        browser_layout.addLayout(transfer_toolbar)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search routines…")
        self.search_edit.setClearButtonEnabled(True)
        browser_layout.addWidget(self.search_edit)
        self.routine_tree = RoutineTreeWidget()
        self.routine_tree.setHeaderHidden(True)
        self.routine_tree.setAlternatingRowColors(True)
        self.routine_tree.setDragEnabled(True)
        self.routine_tree.setAcceptDrops(True)
        self.routine_tree.setDropIndicatorShown(True)
        self.routine_tree.setAutoScroll(True)
        self.routine_tree.setAutoScrollMargin(40)
        self.routine_tree.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove
        )
        self.routine_tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.routine_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        browser_layout.addWidget(self.routine_tree, 1)
        self.routine_count_label = QLabel()
        browser_layout.addWidget(self.routine_count_label)
        self.routines_splitter.addWidget(browser)

        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        header = QHBoxLayout()
        self.routine_title_label = QLabel("Select a routine")
        title_font = self.routine_title_label.font()
        title_font.setPointSize(14)
        title_font.setBold(True)
        self.routine_title_label.setFont(title_font)
        self.routine_enabled_check = QCheckBox("Enabled")
        self.test_routine_button = QPushButton("Test Run")
        header.addWidget(self.routine_title_label)
        header.addStretch()
        header.addWidget(self.routine_enabled_check)
        header.addWidget(self.test_routine_button)
        editor_layout.addLayout(header)
        self.routine_summary_label = QLabel(
            "Choose a routine from the grouped list to edit it."
        )
        editor_layout.addWidget(self.routine_summary_label)
        self.editor_tabs = QTabWidget()
        self.editor_tabs.setObjectName("automationEditorTabs")
        editor_layout.addWidget(self.editor_tabs, 1)
        self._build_trigger_editor()
        self._build_task_editor()
        self._build_settings_editor()
        self._build_routine_history()
        self.editor_tabs.setCurrentIndex(1)
        self.routines_splitter.addWidget(editor)
        self.routines_splitter.setStretchFactor(0, 1)
        self.routines_splitter.setStretchFactor(1, 1)
        self.routines_splitter.setSizes([600, 600])
        self.tabs.addTab(page, "Routines")

        self.new_routine_button.clicked.connect(self._new_routine)
        self.new_group_button.clicked.connect(self._new_group)
        self.import_routine_button.clicked.connect(self._import_routine)
        self.export_routine_button.clicked.connect(self._export_routine)
        self.sort_routines_button.toggled.connect(lambda _checked: self.refresh())
        self.search_edit.textChanged.connect(lambda _text: self.refresh())
        self.routine_tree.currentItemChanged.connect(self._routine_selected)
        self.routine_tree.customContextMenuRequested.connect(
            self._routine_context_menu
        )
        self.routine_tree.itemCollapsed.connect(
            lambda item: self._set_group_collapsed(item, True)
        )
        self.routine_tree.itemExpanded.connect(
            lambda item: self._set_group_collapsed(item, False)
        )
        self.routine_tree.routine_dropped.connect(self._routine_dropped)
        self.routine_enabled_check.toggled.connect(self._toggle_selected_routine)
        self.test_routine_button.clicked.connect(self._test_selected_routine)
        self._install_routine_shortcuts()

    def _build_queues_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        intro = QLabel(
            "Assign routines to independent sequential queues. Immediate is the "
            "default and bypasses queueing."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        layout.addWidget(splitter, 1)

        browser = QWidget()
        browser_layout = QVBoxLayout(browser)
        queue_toolbar = QHBoxLayout()
        self.add_queue_button = QPushButton("+ New Queue")
        self.edit_queue_button = QPushButton("Edit")
        self.delete_queue_button = QPushButton("Delete")
        queue_toolbar.addWidget(self.add_queue_button)
        queue_toolbar.addWidget(self.edit_queue_button)
        queue_toolbar.addWidget(self.delete_queue_button)
        browser_layout.addLayout(queue_toolbar)
        self.queue_list = QListWidget()
        self.queue_list.setAlternatingRowColors(True)
        browser_layout.addWidget(self.queue_list, 1)
        splitter.addWidget(browser)

        details = QWidget()
        details_layout = QVBoxLayout(details)
        header = QHBoxLayout()
        self.queue_title_label = QLabel("Select a queue")
        title_font = self.queue_title_label.font()
        title_font.setBold(True)
        title_font.setPointSize(13)
        self.queue_title_label.setFont(title_font)
        self.pause_queue_button = QPushButton("Pause")
        self.clear_queue_button = QPushButton("Clear Pending")
        header.addWidget(self.queue_title_label)
        header.addStretch()
        header.addWidget(self.pause_queue_button)
        header.addWidget(self.clear_queue_button)
        details_layout.addLayout(header)
        self.queue_status_label = QLabel("Choose a custom queue to inspect it.")
        self.queue_status_label.setWordWrap(True)
        details_layout.addWidget(self.queue_status_label)
        details_layout.addWidget(QLabel("Pending routines — drag to reorder"))
        self.pending_queue_list = QListWidget()
        self.pending_queue_list.setAlternatingRowColors(True)
        self.pending_queue_list.setDragEnabled(True)
        self.pending_queue_list.setAcceptDrops(True)
        self.pending_queue_list.setDropIndicatorShown(True)
        self.pending_queue_list.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove
        )
        self.pending_queue_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        details_layout.addWidget(self.pending_queue_list, 1)
        self.remove_queue_item_button = QPushButton("Remove Selected")
        details_layout.addWidget(self.remove_queue_item_button)
        splitter.addWidget(details)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([300, 850])
        self.tabs.addTab(page, "Queues")

        self.add_queue_button.clicked.connect(self._add_queue)
        self.edit_queue_button.clicked.connect(self._edit_queue)
        self.delete_queue_button.clicked.connect(self._delete_queue)
        self.pause_queue_button.clicked.connect(self._toggle_queue_pause)
        self.clear_queue_button.clicked.connect(self._clear_queue)
        self.remove_queue_item_button.clicked.connect(self._remove_queue_item)
        self.queue_list.itemSelectionChanged.connect(self._queue_selected)
        self.pending_queue_list.model().rowsMoved.connect(
            lambda *_args: QTimer.singleShot(0, self._persist_queue_order)
        )
        self._queue_state_snapshot: tuple = ()

    def _selected_queue_id(self) -> str:
        item = self.queue_list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def _selected_queue(self) -> AutomationQueueDefinition | None:
        return self.queue_store.get(self._selected_queue_id())

    def _queue_snapshot(self) -> tuple:
        return tuple(
            (
                queue.queue_id,
                queue.name,
                queue.paused,
                queue.max_length,
                queue.duplicate_policy,
                queue.delay_seconds,
                getattr(self.queue_manager.current.get(queue.queue_id), "item_id", ""),
                tuple(item.item_id for item in self.queue_manager.pending.get(queue.queue_id, [])),
            )
            for queue in self.queue_store.queues
        )

    def _refresh_queues(self, selected_queue_id: str = "") -> None:
        selected_queue_id = selected_queue_id or self._selected_queue_id()
        self.queue_list.blockSignals(True)
        self.queue_list.clear()
        selected_item = None
        for queue in self.queue_store.queues:
            pending = self.queue_manager.count(queue.queue_id)
            state = "Paused" if queue.paused else "Running"
            item = QListWidgetItem(f"{queue.name}  —  {state} • {pending} pending")
            item.setData(Qt.ItemDataRole.UserRole, queue.queue_id)
            self.queue_list.addItem(item)
            if queue.queue_id == selected_queue_id:
                selected_item = item
        self.queue_list.blockSignals(False)
        if selected_item is not None:
            self.queue_list.setCurrentItem(selected_item)
        elif self.queue_list.count():
            self.queue_list.setCurrentRow(0)
        else:
            self._show_queue(None)
        self._queue_state_snapshot = self._queue_snapshot()

    def _queue_selected(self) -> None:
        self._show_queue(self._selected_queue())

    def _show_queue(self, queue: AutomationQueueDefinition | None) -> None:
        enabled = queue is not None
        self.edit_queue_button.setEnabled(enabled)
        self.delete_queue_button.setEnabled(enabled)
        self.pause_queue_button.setEnabled(enabled)
        self.clear_queue_button.setEnabled(enabled)
        self.remove_queue_item_button.setEnabled(enabled)
        self.pending_queue_list.clear()
        if queue is None:
            self.queue_title_label.setText("Select a queue")
            self.queue_status_label.setText(
                "Create a queue, then assign routines from their Settings tab."
            )
            return
        self.queue_title_label.setText(queue.name)
        self.pause_queue_button.setText("Resume" if queue.paused else "Pause")
        current = self.queue_manager.current.get(queue.queue_id)
        self.queue_status_label.setText(
            f"Current: {current.routine_name if current else 'None'}  •  "
            f"Limit: {queue.max_length}  •  Duplicates: {queue.duplicate_policy.title()}  •  "
            f"Delay: {queue.delay_seconds:g}s"
        )
        for item in self.queue_manager.pending.get(queue.queue_id, []):
            row = QListWidgetItem(item.routine_name)
            row.setData(Qt.ItemDataRole.UserRole, item.item_id)
            row.setToolTip(
                f"Trigger: {item.trigger.trigger_id}\nEvent: {item.trigger.event_id}"
            )
            self.pending_queue_list.addItem(row)

    def _add_queue(self) -> None:
        dialog = QueueEditorDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            queue = self.queue_store.add(**dialog.values())
        except (OSError, TypeError, ValueError) as error:
            self._error("Could Not Create Queue", error)
            return
        self._refresh_queues(queue.queue_id)
        self.refresh(self._selected_routine_id)

    def _edit_queue(self) -> None:
        queue = self._selected_queue()
        if queue is None:
            return
        dialog = QueueEditorDialog(queue, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.queue_store.update(queue.queue_id, **dialog.values())
        except (OSError, TypeError, ValueError) as error:
            self._error("Could Not Update Queue", error)
            return
        self._refresh_queues(queue.queue_id)
        self.refresh(self._selected_routine_id)

    def _delete_queue(self) -> None:
        queue = self._selected_queue()
        if queue is None:
            return
        if QMessageBox.question(
            self,
            "Delete Queue",
            f'Delete "{queue.name}"? Assigned routines will become Immediate.',
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            for routine in tuple(self.routine_store.routines):
                if routine.queue_id == queue.queue_id:
                    self.routine_store.update(routine.routine_id, queue_id="")
            self.queue_manager.clear(queue.queue_id)
            self.queue_store.delete(queue.queue_id)
        except (OSError, ValueError) as error:
            self._error("Could Not Delete Queue", error)
            return
        self._refresh_queues()
        self.refresh(self._selected_routine_id)

    def _toggle_queue_pause(self) -> None:
        queue = self._selected_queue()
        if queue is None:
            return
        try:
            self.queue_store.update(queue.queue_id, paused=not queue.paused)
        except (OSError, ValueError) as error:
            self._error("Could Not Update Queue", error)
            return
        self._refresh_queues(queue.queue_id)

    def _clear_queue(self) -> None:
        queue = self._selected_queue()
        if queue is None:
            return
        self.queue_manager.clear(queue.queue_id)
        self._refresh_queues(queue.queue_id)

    def _remove_queue_item(self) -> None:
        queue = self._selected_queue()
        item = self.pending_queue_list.currentItem()
        if queue is None or item is None:
            return
        self.queue_manager.remove(
            queue.queue_id,
            str(item.data(Qt.ItemDataRole.UserRole) or ""),
        )
        self._refresh_queues(queue.queue_id)

    def _persist_queue_order(self) -> None:
        queue = self._selected_queue()
        if queue is None:
            return
        try:
            self.queue_manager.reorder(
                queue.queue_id,
                [
                    str(self.pending_queue_list.item(index).data(Qt.ItemDataRole.UserRole) or "")
                    for index in range(self.pending_queue_list.count())
                ],
            )
        except ValueError as error:
            self._error("Could Not Reorder Queue", error)
        self._refresh_queues(queue.queue_id)

    def _poll_queues(self) -> None:
        for execution in self.automation_service.process_queues():
            routine = (
                self.routine_store.get(execution.routine_results[0].routine_id)
                if execution.routine_results
                else None
            )
            self.record_execution(
                execution,
                f"Queued — {routine.name if routine else execution.trigger_id}",
            )
        if self._queue_snapshot() != self._queue_state_snapshot:
            self._refresh_queues(self._selected_queue_id())

    def _add_shortcut(self, sequence: str, parent: QWidget, callback: Callable) -> None:
        shortcut = QShortcut(QKeySequence(sequence), parent)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(callback)
        if not hasattr(self, "_shortcuts"):
            self._shortcuts = []
        self._shortcuts.append(shortcut)

    def _install_routine_shortcuts(self) -> None:
        self._add_shortcut("Return", self.routine_tree, self._edit_selected_routine)
        self._add_shortcut("Ctrl+D", self.routine_tree, self._duplicate_routine)
        self._add_shortcut("Space", self.routine_tree, lambda: self._toggle_selected_routine(None))
        self._add_shortcut("Delete", self.routine_tree, self._delete_routine)
        self._add_shortcut("Ctrl+Up", self.routine_tree, lambda: self._move_selected_routine(-1))
        self._add_shortcut("Ctrl+Down", self.routine_tree, lambda: self._move_selected_routine(1))

    def _install_task_shortcuts(self) -> None:
        self._add_shortcut("Return", self.task_list, self._edit_task)
        self._add_shortcut("Ctrl+C", self.task_list, self._copy_task)
        self._add_shortcut("Ctrl+V", self.task_list, self._paste_task)
        self._add_shortcut("Ctrl+D", self.task_list, self._duplicate_task)
        self._add_shortcut("Space", self.task_list, self._toggle_task)
        self._add_shortcut("Delete", self.task_list, self._delete_task)
        self._add_shortcut("Ctrl+Up", self.task_list, lambda: self._move_task(-1))
        self._add_shortcut("Ctrl+Down", self.task_list, lambda: self._move_task(1))

    def _build_trigger_editor(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.trigger_detail_label = QLabel()
        self.trigger_detail_label.setWordWrap(True)
        layout.addWidget(self.trigger_detail_label)
        self.trigger_list = QListWidget()
        self.trigger_list.setAlternatingRowColors(True)
        self.trigger_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        layout.addWidget(self.trigger_list, 1)
        actions = QHBoxLayout()
        self.add_trigger_button = QPushButton("+ Add Trigger")
        self.edit_trigger_button = QPushButton("Edit Trigger")
        self.remove_trigger_button = QPushButton("Remove Trigger")
        actions.addWidget(self.add_trigger_button)
        actions.addWidget(self.edit_trigger_button)
        actions.addWidget(self.remove_trigger_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.editor_tabs.addTab(tab, "Triggers")
        self.add_trigger_button.clicked.connect(self._show_add_trigger_menu)
        self.edit_trigger_button.clicked.connect(self._edit_trigger)
        self.remove_trigger_button.clicked.connect(self._remove_trigger)
        self.trigger_list.itemSelectionChanged.connect(
            self._trigger_selection_changed
        )
        self.trigger_list.customContextMenuRequested.connect(
            self._trigger_context_menu
        )

    def _build_task_editor(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Tasks run from top to bottom. Drag to reorder."))
        toolbar.addStretch()
        self.test_task_button = QPushButton("Test Selected")
        self.test_task_button.setEnabled(False)
        self.add_task_button = QPushButton("+ Add Task")
        toolbar.addWidget(self.test_task_button)
        toolbar.addWidget(self.add_task_button)
        layout.addLayout(toolbar)
        self.task_list = QListWidget()
        self.task_list.setAlternatingRowColors(True)
        self.task_list.setDragEnabled(True)
        self.task_list.setAcceptDrops(True)
        self.task_list.setDropIndicatorShown(True)
        self.task_list.setDragDropOverwriteMode(False)
        self.task_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.task_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.task_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        layout.addWidget(self.task_list, 1)
        self.task_hint_label = QLabel(
            "Right-click a task or empty space to add, edit, duplicate, move, "
            "enable, or delete tasks."
        )
        self.task_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.task_hint_label)
        self.editor_tabs.addTab(tab, "Tasks")
        self.add_task_button.clicked.connect(self._show_add_task_button_menu)
        self.test_task_button.clicked.connect(self._test_selected_task)
        self.task_list.itemDoubleClicked.connect(lambda _item: self._edit_task())
        self.task_list.itemSelectionChanged.connect(self._task_selection_changed)
        self.task_list.customContextMenuRequested.connect(self._task_context_menu)
        self.task_list.model().rowsMoved.connect(
            lambda *_args: QTimer.singleShot(0, self._persist_task_order)
        )
        self._install_task_shortcuts()

    def _task_selection_changed(self) -> None:
        self.test_task_button.setEnabled(self._selected_task() is not None)

    def _build_settings_editor(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        group = QGroupBox("Routine")
        form = QFormLayout(group)
        self.settings_name_edit = QLineEdit()
        self.settings_group_combo = QComboBox()
        self.settings_queue_combo = QComboBox()
        self.settings_description_edit = QLineEdit()
        self.settings_enabled_check = QCheckBox("Enabled")
        form.addRow("Name", self.settings_name_edit)
        form.addRow("Group", self.settings_group_combo)
        form.addRow("Execution queue", self.settings_queue_combo)
        form.addRow("Description", self.settings_description_edit)
        form.addRow("", self.settings_enabled_check)
        layout.addWidget(group)
        self.save_settings_button = QPushButton("Save Routine Settings")
        layout.addWidget(self.save_settings_button)
        layout.addStretch()
        self.editor_tabs.addTab(tab, "Settings")
        self.save_settings_button.clicked.connect(self._save_routine_settings)

    def _build_routine_history(self) -> None:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.routine_history_table = QTableWidget(0, 4)
        self.routine_history_table.setHorizontalHeaderLabels(
            ("When", "Trigger", "Result", "Tasks")
        )
        self.routine_history_table.horizontalHeader().setStretchLastSection(True)
        self.routine_history_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        layout.addWidget(self.routine_history_table)
        self.routine_history_details = QTextEdit()
        self.routine_history_details.setReadOnly(True)
        self.routine_history_details.setMaximumHeight(150)
        self.routine_history_details.setPlaceholderText(
            "Select a run to inspect each task result."
        )
        layout.addWidget(self.routine_history_details)
        self.routine_history_table.itemSelectionChanged.connect(
            lambda: self._show_history_details(
                self.routine_history_table,
                self.routine_history_details,
            )
        )
        self.editor_tabs.addTab(tab, "History")

    def _build_task_library_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        intro = QLabel(
            "Available task providers. More services will appear here as they "
            "are implemented."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.task_library_tree = QTreeWidget()
        self.task_library_tree.setHeaderLabels(("Service / task", "Status"))
        for service_name, tasks in (
            ("Twitch", TWITCH_TASK_LABELS),
            ("Core", CORE_TASK_LABELS),
            ("OBS", OBS_TASK_LABELS),
        ):
            service = QTreeWidgetItem((service_name, ""))
            service.setExpanded(True)
            scripts = None
            variables = None
            logic = None
            files = None
            if service_name == "Core":
                scripts = QTreeWidgetItem(("Scripts", ""))
                scripts.setExpanded(True)
                service.addChild(scripts)
                variables = QTreeWidgetItem(("Variables", ""))
                variables.setExpanded(True)
                service.addChild(variables)
                logic = QTreeWidgetItem(("Logic", ""))
                logic.setExpanded(True)
                service.addChild(logic)
                files = QTreeWidgetItem(("Files", ""))
                files.setExpanded(True)
                service.addChild(files)
            for task_type, label in tasks.items():
                task = QTreeWidgetItem((label.split("—")[-1].strip(), "Available"))
                task.setData(0, Qt.ItemDataRole.UserRole, task_type)
                if task_type == "core.run_python_script" and scripts is not None:
                    scripts.addChild(task)
                elif (
                    task_type in VARIABLE_MANAGEMENT_TASK_TYPES
                    and variables is not None
                ):
                    variables.addChild(task)
                elif task_type in LOGIC_TASK_LABELS and logic is not None:
                    logic.addChild(task)
                elif task_type in FILE_TASK_TYPES and files is not None:
                    files.addChild(task)
                else:
                    service.addChild(task)
            self.task_library_tree.addTopLevelItem(service)
        for service in ("Voice", "Timer", "AI", "Vision"):
            self.task_library_tree.addTopLevelItem(
                QTreeWidgetItem((service, "Future provider"))
            )
        layout.addWidget(self.task_library_tree)
        self.tabs.addTab(page, "Task Library")
        self.task_library_tree.itemDoubleClicked.connect(
            self._add_library_task
        )

    def _build_history_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels(
            ("When", "Routine", "Trigger", "Result", "Tasks")
        )
        self.history_table.horizontalHeader().setStretchLastSection(True)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.history_table)
        self.history_details = QTextEdit()
        self.history_details.setReadOnly(True)
        self.history_details.setMaximumHeight(180)
        self.history_details.setPlaceholderText(
            "Select a run to inspect each task result."
        )
        layout.addWidget(self.history_details)
        self.history_table.itemSelectionChanged.connect(
            lambda: self._show_history_details(
                self.history_table,
                self.history_details,
            )
        )
        self.tabs.addTab(page, "Run History")

    def refresh(self, selected_routine_id: str = "") -> None:
        selected_routine_id = selected_routine_id or self._selected_routine_id
        query = self.search_edit.text().strip().casefold()
        alphabetical = self.sort_routines_button.isChecked()
        reorder_enabled = not query and not alphabetical
        self.routine_tree.setProperty("routine_reorder_enabled", reorder_enabled)
        self.routine_tree.setDragEnabled(reorder_enabled)
        self.routine_tree.setAcceptDrops(reorder_enabled)
        self.routine_tree.blockSignals(True)
        self.routine_tree.clear()
        selected_item = None
        # Ungrouped is the inbox for new/manual routines, so keep it visible
        # above the user's explicitly ordered custom groups.
        custom_groups = list(self.routine_store.groups)
        if alphabetical:
            custom_groups.sort(key=lambda group: group.name.casefold())
        groups = [None, *custom_groups]
        visible_count = 0
        for group in groups:
            group_id = group.group_id if group else ""
            routines = [
                routine
                for routine in self.routine_store.grouped(group_id)
                if not query
                or query in routine.name.casefold()
                or query in routine.description.casefold()
            ]
            if alphabetical:
                routines.sort(key=lambda routine: routine.name.casefold())
            if query and not routines:
                continue
            title = group.name if group else "Ungrouped"
            group_item = QTreeWidgetItem((f"{title} ({len(routines)})",))
            group_item.setData(0, Qt.ItemDataRole.UserRole, group_id)
            group_item.setData(0, self.KIND_ROLE, "group")
            group_item.setFlags(
                (group_item.flags() | Qt.ItemFlag.ItemIsDropEnabled)
                & ~Qt.ItemFlag.ItemIsDragEnabled
            )
            group_item.setExpanded(not group.collapsed if group else True)
            self.routine_tree.addTopLevelItem(group_item)
            for routine in routines:
                trigger_count = self._routine_trigger_count(routine.routine_id)
                issues = self._routine_issues(routine, trigger_count)
                prefix = "[!] " if issues else ("[Off] " if not routine.enabled else "")
                trigger_text = (
                    f"{trigger_count} trigger{'s' if trigger_count != 1 else ''}"
                    if trigger_count
                    else "Manual"
                )
                item = QTreeWidgetItem(
                    (f"{prefix}{routine.name}  —  {trigger_text} • {len(routine.tasks)} tasks",)
                )
                item.setData(0, Qt.ItemDataRole.UserRole, routine.routine_id)
                item.setData(0, self.KIND_ROLE, "routine")
                item.setToolTip(
                    0,
                    "\n".join(issues)
                    if issues
                    else "Ready. Drag to reorder or move between groups.",
                )
                item.setFlags(
                    item.flags()
                    | Qt.ItemFlag.ItemIsDragEnabled
                    | Qt.ItemFlag.ItemIsDropEnabled
                )
                group_item.addChild(item)
                visible_count += 1
                if routine.routine_id == selected_routine_id:
                    selected_item = item
        self.routine_tree.blockSignals(False)
        if alphabetical:
            order_hint = "alphabetical view • turn off sorting to drag"
        elif query:
            order_hint = "clear search to drag or regroup"
        else:
            order_hint = "drag to reorder or regroup"
        self.routine_count_label.setText(f"{visible_count} routine(s) • {order_hint}")
        if selected_item is not None:
            self.routine_tree.setCurrentItem(selected_item)
        elif selected_routine_id:
            self._selected_routine_id = ""
            self.setProperty("selectedRoutineId", "")
            self._show_routine(None)
        self._refresh_task_library()
        self._refresh_queues(self._selected_queue_id())

    def select_routine(self, routine_id: str) -> None:
        self.tabs.setCurrentIndex(0)
        self.search_edit.clear()
        self._selected_routine_id = routine_id
        self.refresh(routine_id)

    def _routine_selected(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        routine_id = ""
        if current is not None and current.data(0, self.KIND_ROLE) == "routine":
            routine_id = str(current.data(0, Qt.ItemDataRole.UserRole) or "")
        self._selected_routine_id = routine_id
        self.setProperty("selectedRoutineId", routine_id)
        self._show_routine(self.routine_store.get(routine_id) if routine_id else None)

    def _routine_trigger_count(self, routine_id: str) -> int:
        return (
            (1 if self.trigger_store.for_routine(routine_id) else 0)
            + len(self.event_trigger_store.for_routine(routine_id))
            + len(self.core_trigger_store.for_routine(routine_id))
            + len(self.obs_trigger_store.for_routine(routine_id))
        )

    def _task_issues(self, task: TaskDefinition) -> list[str]:
        issues: list[str] = []
        if task.task_type not in self.task_registry.registered_types():
            issues.append(f'Provider "{task.task_type}" is unavailable')
        for spec in TaskEditorDialog.SCHEMAS.get(task.task_type, ()):
            if not spec.get("required"):
                continue
            key = str(spec["key"])
            if not str(task.config.get(key, "")).strip():
                issues.append(f'{spec.get("label", key)} is required')
        if (
            task.task_type == "core.run_routine"
            and str(task.config.get("routine_id", "")).strip()
            and self.routine_store.get(str(task.config["routine_id"])) is None
        ):
            issues.append("Nested routine no longer exists")
        routine_reference_keys = {
            "core.logic_if_else": ("true_routine_id", "false_routine_id"),
            "core.logic_switch": ("default_routine_id",),
            "core.logic_while": ("routine_id",),
        }
        for key in routine_reference_keys.get(task.task_type, ()):
            routine_id = str(task.config.get(key, "")).strip()
            if routine_id and self.routine_store.get(routine_id) is None:
                issues.append(f"Referenced routine for {key.replace('_', ' ')} no longer exists")
        if task.task_type == "core.logic_switch":
            cases = task.config.get("cases", {})
            if isinstance(cases, dict):
                for case, routine_id in cases.items():
                    if routine_id and self.routine_store.get(str(routine_id)) is None:
                        issues.append(f'Switch case "{case}" references a missing routine')
        if task.task_type == "core.logic_random_choice":
            choices = task.config.get("choices", [])
            if not isinstance(choices, list) or not choices:
                issues.append("Random choice has no choices")
            else:
                for index, choice in enumerate(choices, start=1):
                    if not isinstance(choice, dict):
                        issues.append(f"Random choice {index} is invalid")
                        continue
                    routine_id = str(choice.get("routine_id", "")).strip()
                    if not routine_id:
                        issues.append(f"Random choice {index} has no routine")
                    elif self.routine_store.get(routine_id) is None:
                        issues.append(f"Random choice {index} references a missing routine")
                    try:
                        if float(choice.get("weight", 0)) <= 0:
                            issues.append(f"Random choice {index} needs a positive weight")
                    except (TypeError, ValueError):
                        issues.append(f"Random choice {index} has an invalid weight")
        return issues

    def _routine_issues(self, routine, trigger_count: int | None = None) -> list[str]:
        issues: list[str] = []
        if routine.queue_id and self.queue_store.get(routine.queue_id) is None:
            issues.append("Assigned automation queue no longer exists")
        if not routine.tasks:
            issues.append("Routine has no tasks")
        elif not any(task.enabled for task in routine.tasks):
            issues.append("Every task is disabled")
        for task in routine.tasks:
            issues.extend(f"{task.name}: {issue}" for issue in self._task_issues(task))
        return issues

    def _routine_dropped(
        self,
        routine_id: str,
        group_id: str,
        index: int,
    ) -> None:
        try:
            moved = self.routine_store.move_routine(routine_id, group_id, index)
        except (OSError, ValueError) as error:
            self._error("Could Not Move Routine", error)
            self.refresh(routine_id)
            return
        self.select_routine(moved.routine_id)

    def _show_routine(self, routine) -> None:
        enabled = routine is not None
        self.editor_tabs.setEnabled(enabled)
        self.routine_enabled_check.setEnabled(enabled)
        self.export_routine_button.setEnabled(enabled)
        self.test_routine_button.setEnabled(enabled and bool(routine.tasks if routine else []))
        if routine is None:
            self.routine_title_label.setText("Select a routine")
            self.routine_summary_label.setText(
                "Choose a routine from the grouped list to edit it."
            )
            self.task_list.clear()
            self.test_task_button.setEnabled(False)
            return
        command = self.trigger_store.for_routine(routine.routine_id)
        event_triggers = self.event_trigger_store.for_routine(routine.routine_id)
        core_triggers = self.core_trigger_store.for_routine(routine.routine_id)
        obs_triggers = self.obs_trigger_store.for_routine(routine.routine_id)
        self.routine_title_label.setText(routine.name)
        trigger_count = self._routine_trigger_count(routine.routine_id)
        group = self.routine_store.get_group(routine.group_id)
        queue = self.queue_store.get(routine.queue_id)
        issues = self._routine_issues(routine, trigger_count)
        self.routine_summary_label.setText(
            f"{group.name if group else 'Ungrouped'}  •  {trigger_count} trigger(s)"
            f"  •  {len(routine.tasks)} task(s)"
            f"  •  {queue.name if queue else 'Immediate'}"
            + (f"  •  Warning: {issues[0]}" if issues else "  •  Ready")
        )
        self.routine_summary_label.setToolTip("\n".join(issues))
        self.routine_enabled_check.blockSignals(True)
        self.routine_enabled_check.setChecked(routine.enabled)
        self.routine_enabled_check.blockSignals(False)
        self._refresh_trigger(routine, command, event_triggers, core_triggers, obs_triggers)
        self._refresh_tasks(routine)
        self._refresh_settings(routine)
        self._refresh_routine_history()

    def _refresh_trigger(
        self, routine, command, event_triggers, core_triggers, obs_triggers
    ) -> None:
        self.trigger_list.blockSignals(True)
        self.trigger_list.clear()
        if command is not None:
            state = "Enabled" if command.enabled else "Disabled"
            item = QListWidgetItem(f"Twitch command — !{command.name} [{state}]")
            item.setData(Qt.ItemDataRole.UserRole, command.trigger_id)
            item.setData(Qt.ItemDataRole.UserRole + 1, "command")
            self.trigger_list.addItem(item)
        for trigger in event_triggers:
            state = "Enabled" if trigger.enabled else "Disabled"
            filtered = f" • {len(trigger.filters)} filter(s)" if trigger.filters else ""
            item = QListWidgetItem(
                f"Twitch event — {_event_display_name(trigger.event_type)} "
                f"[{state}{filtered}]"
            )
            item.setData(Qt.ItemDataRole.UserRole, trigger.trigger_id)
            item.setData(Qt.ItemDataRole.UserRole + 1, "event")
            self.trigger_list.addItem(item)
        for trigger in core_triggers:
            state = "Enabled" if trigger.enabled else "Disabled"
            label = CORE_TRIGGER_TYPES.get(trigger.event_type, trigger.event_type)
            item = QListWidgetItem(f"Core program — {label} [{state}]")
            item.setData(Qt.ItemDataRole.UserRole, trigger.trigger_id)
            item.setData(Qt.ItemDataRole.UserRole + 1, "core")
            self.trigger_list.addItem(item)
        for trigger in obs_triggers:
            state = "Enabled" if trigger.enabled else "Disabled"
            filtered = f" • {len(trigger.filters)} filter(s)" if trigger.filters else ""
            label = OBS_TRIGGER_TYPES.get(trigger.event_type, trigger.event_type)
            item = QListWidgetItem(f"OBS — {label} [{state}{filtered}]")
            item.setData(Qt.ItemDataRole.UserRole, trigger.trigger_id)
            item.setData(Qt.ItemDataRole.UserRole + 1, "obs")
            self.trigger_list.addItem(item)
        self.trigger_list.blockSignals(False)
        self.editor_tabs.setTabText(0, f"Triggers ({self.trigger_list.count()})")
        if self.trigger_list.count() == 0:
            self.trigger_detail_label.setText(
                "No service trigger is attached. This routine can still be run "
                "manually with Test Run."
            )
            self.edit_trigger_button.setEnabled(False)
            self.remove_trigger_button.setEnabled(False)
            return
        self.trigger_list.setCurrentRow(0)

    def _refresh_tasks(self, routine) -> None:
        self.task_list.blockSignals(True)
        self.task_list.clear()
        for index, task in enumerate(routine.tasks, start=1):
            provider = TaskEditorDialog.LABELS.get(task.task_type, task.task_type)
            state = "Enabled" if task.enabled else "Disabled"
            managed = " • trigger task" if task.managed_key else ""
            issues = self._task_issues(task)
            warning = " • needs attention" if issues else ""
            item = QListWidgetItem(
                f"{index}   {provider} — {task.name}   [{state}{managed}{warning}]"
            )
            item.setData(Qt.ItemDataRole.UserRole, task.task_id)
            item.setToolTip("\n".join(issues) if issues else "Ready")
            self.task_list.addItem(item)
        self.task_list.blockSignals(False)
        self.test_task_button.setEnabled(False)
        self.editor_tabs.setTabText(1, f"Tasks ({len(routine.tasks)})")
        self.task_hint_label.setText(
            "No tasks yet. Right-click here or use + Add Task to build the routine."
            if not routine.tasks
            else "Tasks run from top to bottom. Drag to reorder; right-click for more options."
        )

    def _refresh_settings(self, routine) -> None:
        self.settings_name_edit.setText(routine.name)
        self.settings_description_edit.setText(routine.description)
        self.settings_enabled_check.setChecked(routine.enabled)
        self.settings_group_combo.clear()
        self.settings_group_combo.addItem("Ungrouped", "")
        for group in self.routine_store.groups:
            self.settings_group_combo.addItem(group.name, group.group_id)
        index = self.settings_group_combo.findData(routine.group_id)
        self.settings_group_combo.setCurrentIndex(max(index, 0))
        self.settings_queue_combo.clear()
        self.settings_queue_combo.addItem("Immediate (no queue)", "")
        for queue in self.queue_store.queues:
            self.settings_queue_combo.addItem(queue.name, queue.queue_id)
        queue_index = self.settings_queue_combo.findData(routine.queue_id)
        self.settings_queue_combo.setCurrentIndex(max(queue_index, 0))

    def _resolve_group(self, name: str, group_id: str = "") -> str:
        clean = name.strip()
        if not clean or clean.casefold() == "ungrouped":
            return ""
        if group_id and self.routine_store.get_group(group_id):
            return group_id
        existing = next(
            (
                group
                for group in self.routine_store.groups
                if group.name.casefold() == clean.casefold()
            ),
            None,
        )
        return existing.group_id if existing else self.routine_store.add_group(clean).group_id

    def _new_routine(self) -> None:
        dialog = NewRoutineDialog(self.routine_store, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        try:
            group_id = self._resolve_group(
                str(values["group_name"]), str(values["group_id"])
            )
            if values["trigger_type"] == "twitch.command":
                command = self.trigger_store.add(
                    str(values["command"]),
                    str(values["response"]),
                    aliases=values["aliases"],
                    permission=str(values["permission"]),
                    global_cooldown_seconds=int(values["global_cooldown_seconds"]),
                    user_cooldown_seconds=int(values["user_cooldown_seconds"]),
                )
                routine = self.routine_store.update(
                    command.routine_id,
                    name=str(values["name"]),
                    group_id=group_id,
                    description=str(values["description"]),
                    enabled=bool(values["enabled"]),
                )
                self.trigger_store.set_enabled(command.trigger_id, bool(values["enabled"]))
                self.commands_changed()
            else:
                routine = self.routine_store.add(
                    str(values["name"]),
                    group_id=group_id,
                    description=str(values["description"]),
                    enabled=bool(values["enabled"]),
                )
                if values["trigger_type"] == "twitch.eventsub":
                    self.event_trigger_store.add(
                        routine.routine_id,
                        str(values["event_type"]),
                        filters=values["event_filters"],
                        enabled=bool(values["enabled"]),
                        reset_minutes=int(values["event_reset_minutes"]),
                    )
                elif values["trigger_type"] == "core.lifecycle":
                    self.core_trigger_store.add(
                        routine.routine_id,
                        str(values["core_event_type"]),
                        enabled=bool(values["enabled"]),
                    )
                elif values["trigger_type"] == "obs.event":
                    self.obs_trigger_store.add(
                        routine.routine_id,
                        str(values["obs_event_type"]),
                        filters=values["obs_filters"],
                        enabled=bool(values["enabled"]),
                    )
        except (OSError, TypeError, ValueError) as error:
            self._error("Could Not Create Routine", error)
            return
        self.select_routine(routine.routine_id)

    def _new_group(self) -> None:
        name, accepted = QInputDialog.getText(self, "New Group", "Group name")
        if not accepted:
            return
        try:
            self.routine_store.add_group(name)
        except (OSError, ValueError) as error:
            self._error("Could Not Create Group", error)
            return
        self.refresh()

    def _set_group_collapsed(self, item: QTreeWidgetItem, collapsed: bool) -> None:
        if item.data(0, self.KIND_ROLE) != "group":
            return
        group_id = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        if not group_id:
            return
        try:
            self.routine_store.update_group(group_id, collapsed=collapsed)
        except (OSError, ValueError):
            pass

    def _routine_context_menu(self, position) -> None:
        item = self.routine_tree.itemAt(position)
        menu = QMenu(self)
        if item is None:
            menu.addAction("New Routine", self._new_routine)
            menu.addAction("New Group", self._new_group)
        elif item.data(0, self.KIND_ROLE) == "group":
            group_id = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
            menu.addAction("New Routine", self._new_routine)
            if group_id:
                menu.addAction("Rename Group", lambda: self._rename_group(group_id))
                menu.addAction("Move Group Up", lambda: self._move_group(group_id, -1))
                menu.addAction(
                    "Move Group Down", lambda: self._move_group(group_id, 1)
                )
                menu.addAction("Delete Group", lambda: self._delete_group(group_id))
        else:
            self.routine_tree.setCurrentItem(item)
            routine_id = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
            routine = self.routine_store.get(routine_id)
            menu.addAction("Edit Routine", self._edit_selected_routine)
            menu.addAction("Duplicate Routine", self._duplicate_routine)
            menu.addAction("Export Routine…", self._export_routine)
            menu.addAction(
                "Disable Routine" if routine and routine.enabled else "Enable Routine",
                lambda: self._toggle_selected_routine(None),
            )
            menu.addSeparator()
            menu.addAction("Move Up", lambda: self._move_selected_routine(-1))
            menu.addAction("Move Down", lambda: self._move_selected_routine(1))
            move_menu = menu.addMenu("Move to Group")
            move_menu.addAction(
                "Ungrouped", lambda: self._move_routine_to_group(routine_id, "")
            )
            for group in self.routine_store.groups:
                move_menu.addAction(
                    group.name,
                    lambda checked=False, gid=group.group_id: self._move_routine_to_group(
                        routine_id, gid
                    ),
                )
            menu.addSeparator()
            menu.addAction("Delete Routine", self._delete_routine)
        menu.exec(self.routine_tree.viewport().mapToGlobal(position))

    def _export_routine(self) -> None:
        routine = self.routine_store.get(self._selected_routine_id)
        if routine is None:
            return
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", routine.name).strip("-")
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Routine",
            f"{safe_name or 'sally-routine'}.sally-routine.json",
            "Sally routine files (*.sally-routine.json);;JSON files (*.json)",
        )
        if not filename:
            return
        payload = export_routine(
            routine,
            routine_store=self.routine_store,
            command_store=self.trigger_store,
            event_store=self.event_trigger_store,
            core_store=self.core_trigger_store,
            obs_store=self.obs_trigger_store,
        )
        try:
            Path(filename).write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
        except OSError as error:
            self._error("Could Not Export Routine", error)

    def _import_routine(self) -> None:
        filename, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Import Routine",
            "",
            "Sally routine files (*.sally-routine.json);;JSON files (*.json)",
        )
        if not filename:
            return
        try:
            payload = json.loads(Path(filename).read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("The selected file does not contain a routine object.")
            routine_values = payload.get("routine", {})
            validate_import(
                payload,
                task_registry=self.task_registry,
                command_store=self.trigger_store,
            )
            group_name = (
                str(routine_values.get("group", ""))
                if isinstance(routine_values, dict)
                else ""
            )
            group_id = self._resolve_group(group_name)
            routine = import_routine(
                payload,
                group_id=group_id,
                routine_store=self.routine_store,
                task_registry=self.task_registry,
                command_store=self.trigger_store,
                event_store=self.event_trigger_store,
                core_store=self.core_trigger_store,
                obs_store=self.obs_trigger_store,
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
            self._error("Could Not Import Routine", error)
            return
        self.commands_changed()
        self.select_routine(routine.routine_id)

    def _edit_selected_routine(self) -> None:
        if self.routine_store.get(self._selected_routine_id) is not None:
            self.editor_tabs.setCurrentIndex(2)

    def _move_selected_routine(self, offset: int) -> None:
        routine = self.routine_store.get(self._selected_routine_id)
        if routine is None:
            return
        siblings = list(self.routine_store.grouped(routine.group_id))
        index = siblings.index(routine)
        destination = max(0, min(index + offset, len(siblings) - 1))
        if destination == index:
            return
        try:
            moved = self.routine_store.move_routine(
                routine.routine_id,
                routine.group_id,
                destination,
            )
        except (OSError, ValueError) as error:
            self._error("Could Not Move Routine", error)
            return
        self.select_routine(moved.routine_id)

    def _rename_group(self, group_id: str) -> None:
        group = self.routine_store.get_group(group_id)
        if group is None:
            return
        name, accepted = QInputDialog.getText(
            self, "Rename Group", "Group name", text=group.name
        )
        if not accepted:
            return
        try:
            self.routine_store.update_group(group_id, name=name)
        except (OSError, ValueError) as error:
            self._error("Could Not Rename Group", error)
            return
        self.refresh()

    def _delete_group(self, group_id: str) -> None:
        group = self.routine_store.get_group(group_id)
        if group is None:
            return
        if QMessageBox.question(
            self,
            "Delete Routine Group",
            f'Delete "{group.name}"? Its routines will move to Ungrouped.',
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.routine_store.delete_group(group_id)
        except OSError as error:
            self._error("Could Not Delete Group", error)
            return
        self.refresh()

    def _move_group(self, group_id: str, offset: int) -> None:
        group = self.routine_store.get_group(group_id)
        if group is None:
            return
        index = self.routine_store.groups.index(group)
        try:
            self.routine_store.reorder_group(group_id, index + offset)
        except (OSError, ValueError) as error:
            self._error("Could Not Move Group", error)
            return
        self.refresh()

    def _duplicate_routine(self) -> None:
        if not self._selected_routine_id:
            return
        try:
            routine = self.routine_store.duplicate(self._selected_routine_id)
        except (OSError, ValueError) as error:
            self._error("Could Not Duplicate Routine", error)
            return
        self.select_routine(routine.routine_id)

    def _delete_routine(self) -> None:
        routine = self.routine_store.get(self._selected_routine_id)
        if routine is None:
            return
        command = self.trigger_store.for_routine(routine.routine_id)
        event_triggers = self.event_trigger_store.for_routine(routine.routine_id)
        core_triggers = self.core_trigger_store.for_routine(routine.routine_id)
        obs_triggers = self.obs_trigger_store.for_routine(routine.routine_id)
        detail = (
            "Its Twitch command trigger and all tasks will also be deleted."
            if command
            else "All tasks in the routine will also be deleted."
        )
        if QMessageBox.question(
            self,
            "Delete Routine",
            f'Delete "{routine.name}"?\n\n{detail}',
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            for event_trigger in event_triggers:
                self.event_trigger_store.delete(event_trigger.trigger_id)
            for core_trigger in core_triggers:
                self.core_trigger_store.delete(core_trigger.trigger_id)
            for obs_trigger in obs_triggers:
                self.obs_trigger_store.delete(obs_trigger.trigger_id)
            if command:
                self.trigger_store.delete(command.trigger_id)
                self.commands_changed()
            else:
                self.routine_store.delete(routine.routine_id)
        except (OSError, ValueError) as error:
            self._error("Could Not Delete Routine", error)
            return
        self._selected_routine_id = ""
        self.refresh()
        self._show_routine(None)

    def _move_routine_to_group(self, routine_id: str, group_id: str) -> None:
        try:
            self.routine_store.update(routine_id, group_id=group_id)
        except (OSError, ValueError) as error:
            self._error("Could Not Move Routine", error)
            return
        self.select_routine(routine_id)

    def _toggle_selected_routine(self, checked: bool | None = None) -> None:
        routine = self.routine_store.get(self._selected_routine_id)
        if routine is None:
            return
        enabled = bool(checked) if isinstance(checked, bool) else not routine.enabled
        try:
            self.routine_store.update(routine.routine_id, enabled=enabled)
        except (OSError, ValueError) as error:
            self._error("Could Not Update Routine", error)
            return
        self.select_routine(routine.routine_id)

    def _save_routine_settings(self) -> None:
        routine = self.routine_store.get(self._selected_routine_id)
        if routine is None:
            return
        try:
            updated = self.routine_store.update(
                routine.routine_id,
                name=self.settings_name_edit.text(),
                group_id=str(self.settings_group_combo.currentData() or ""),
                description=self.settings_description_edit.text(),
                enabled=self.settings_enabled_check.isChecked(),
                queue_id=str(self.settings_queue_combo.currentData() or ""),
            )
        except (OSError, ValueError) as error:
            self._error("Could Not Save Routine", error)
            return
        self.select_routine(updated.routine_id)

    def _show_add_trigger_menu(self) -> None:
        routine = self.routine_store.get(self._selected_routine_id)
        if routine is None:
            return
        menu = QMenu(self)
        self._add_trigger_submenu(menu, direct_services=True)
        menu.exec(
            self.add_trigger_button.mapToGlobal(
                self.add_trigger_button.rect().bottomLeft()
            )
        )

    def _trigger_context_menu(self, position) -> None:
        item = self.trigger_list.itemAt(position)
        if item is not None:
            self.trigger_list.setCurrentItem(item)
        kind, trigger_id = self._selected_trigger() if item is not None else ("", "")
        menu = QMenu(self)
        self._add_trigger_submenu(menu)
        if trigger_id:
            menu.addSeparator()
            menu.addAction("Edit", self._edit_trigger)
            menu.addAction("Remove", self._remove_trigger)
        menu.exec(self.trigger_list.viewport().mapToGlobal(position))

    def _add_trigger_submenu(
        self, menu: QMenu, *, direct_services: bool = False
    ) -> QMenu:
        add_menu = menu if direct_services else menu.addMenu("Add")
        add_menu._sally_trigger_submenus = []
        routine = self.routine_store.get(self._selected_routine_id)

        core_menu = QMenu("Core", add_menu)
        program_event_menu = QMenu("Program Event", core_menu)
        for event_type, label in CORE_TRIGGER_TYPES.items():
            program_event_menu.addAction(
                label,
                lambda checked=False, value=event_type: self._add_core_trigger(value),
            )
        core_menu.addMenu(program_event_menu)
        core_menu._sally_trigger_submenus = [program_event_menu]
        add_menu.addMenu(core_menu)
        add_menu._sally_trigger_submenus.append(core_menu)

        obs_menu = QMenu("OBS", add_menu)
        for event_type, label in OBS_TRIGGER_TYPES.items():
            obs_menu.addAction(
                label,
                lambda checked=False, value=event_type: self._add_obs_trigger(value),
            )
        add_menu.addMenu(obs_menu)
        add_menu._sally_trigger_submenus.append(obs_menu)

        twitch_menu = QMenu("Twitch", add_menu)
        command_action = twitch_menu.addAction(
            "Chat Command…", self._open_command_manager
        )
        command_action.setEnabled(routine is not None)
        for event_type in TWITCH_AUTOMATION_EVENT_TYPES:
            twitch_menu.addAction(
                _event_display_name(event_type),
                lambda checked=False, value=event_type: self._add_event_trigger(value),
            )
        add_menu.addMenu(twitch_menu)
        add_menu._sally_trigger_submenus.append(twitch_menu)
        return add_menu

    def _open_command_manager(self) -> None:
        routine = self.routine_store.get(self._selected_routine_id)
        if routine is None:
            return
        dialog = TwitchCommandManagerDialog(
            self.trigger_store,
            routine.routine_id,
            self,
            self.commands_changed,
        )
        dialog.exec()
        self.select_routine(routine.routine_id)
        if dialog.created_trigger_id:
            self._select_trigger("command", dialog.created_trigger_id)

    def _add_event_trigger(self, event_type: str | None = None) -> None:
        routine = self.routine_store.get(self._selected_routine_id)
        if routine is None:
            return
        if event_type is None:
            dialog = TwitchEventTriggerDialog(self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            values = dialog.values()
        else:
            values = {"event_type": event_type, "filters": {}, "enabled": True}
        try:
            trigger = self.event_trigger_store.add(
                routine.routine_id, **values
            )
        except (OSError, TypeError, ValueError) as error:
            self._error("Could Not Add Trigger", error)
            return
        self.select_routine(routine.routine_id)
        self._select_trigger("event", trigger.trigger_id)

    def _add_core_trigger(self, event_type: str | None = None) -> None:
        routine = self.routine_store.get(self._selected_routine_id)
        if routine is None:
            return
        if event_type is None:
            dialog = CoreTriggerDialog(self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            values = dialog.values()
        else:
            values = {"event_type": event_type, "enabled": True}
        try:
            trigger = self.core_trigger_store.add(
                routine.routine_id, **values
            )
        except (OSError, TypeError, ValueError) as error:
            self._error("Could Not Add Trigger", error)
            return
        self.select_routine(routine.routine_id)
        self._select_trigger("core", trigger.trigger_id)

    def _add_obs_trigger(self, event_type: str | None = None) -> None:
        routine = self.routine_store.get(self._selected_routine_id)
        if routine is None:
            return
        if event_type is None:
            dialog = ObsTriggerDialog(self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            values = dialog.values()
        else:
            values = {"event_type": event_type, "filters": {}, "enabled": True}
        try:
            trigger = self.obs_trigger_store.add(routine.routine_id, **values)
        except (OSError, TypeError, ValueError) as error:
            self._error("Could Not Add Trigger", error)
            return
        self.select_routine(routine.routine_id)
        self._select_trigger("obs", trigger.trigger_id)

    def _selected_trigger(self) -> tuple[str, str]:
        item = self.trigger_list.currentItem()
        if item is None:
            return "", ""
        return (
            str(item.data(Qt.ItemDataRole.UserRole + 1) or ""),
            str(item.data(Qt.ItemDataRole.UserRole) or ""),
        )

    def _select_trigger(self, kind: str, trigger_id: str) -> None:
        for index in range(self.trigger_list.count()):
            item = self.trigger_list.item(index)
            if (
                item.data(Qt.ItemDataRole.UserRole + 1) == kind
                and item.data(Qt.ItemDataRole.UserRole) == trigger_id
            ):
                self.trigger_list.setCurrentItem(item)
                break

    def _trigger_selection_changed(self) -> None:
        kind, trigger_id = self._selected_trigger()
        self.edit_trigger_button.setEnabled(bool(trigger_id))
        self.remove_trigger_button.setEnabled(bool(trigger_id))
        if kind == "command":
            command = self.trigger_store.get(trigger_id)
            if command is None:
                return
            alternates = ", ".join(f"!{value}" for value in command.aliases) or "None"
            self.trigger_detail_label.setText(
                f"Twitch chat command: !{command.name}\n"
                f"Alternate commands: {alternates}\n"
                f"Permission: {command.permission.title()}\n"
                f"Cooldowns: {command.global_cooldown_seconds}s global, "
                f"{command.user_cooldown_seconds}s per viewer"
            )
        elif kind == "event":
            trigger = self.event_trigger_store.get(trigger_id)
            if trigger is None:
                return
            filters = ", ".join(
                f"{key}={value}" for key, value in trigger.filters.items()
            ) or "None — every event of this type"
            self.trigger_detail_label.setText(
                f"Twitch EventSub event: {trigger.event_type}\n"
                f"Field filters: {filters}\n"
                f"State: {'Enabled' if trigger.enabled else 'Disabled'}"
            )
        elif kind == "core":
            trigger = self.core_trigger_store.get(trigger_id)
            if trigger is None:
                return
            self.trigger_detail_label.setText(
                f"Core program event: "
                f"{CORE_TRIGGER_TYPES.get(trigger.event_type, trigger.event_type)}\n"
                f"Event key: {trigger.event_type}\n"
                f"State: {'Enabled' if trigger.enabled else 'Disabled'}"
            )
        elif kind == "obs":
            trigger = self.obs_trigger_store.get(trigger_id)
            if trigger is None:
                return
            filters = ", ".join(f"{k}={v}" for k, v in trigger.filters.items()) or "None — every event of this type"
            self.trigger_detail_label.setText(
                f"OBS event: {OBS_TRIGGER_TYPES.get(trigger.event_type, trigger.event_type)}\n"
                f"Field filters: {filters}\n"
                f"State: {'Enabled' if trigger.enabled else 'Disabled'}"
            )

    def _edit_trigger(self) -> None:
        routine = self.routine_store.get(self._selected_routine_id)
        kind, trigger_id = self._selected_trigger()
        if routine is None or not trigger_id:
            return
        if kind == "event":
            trigger = self.event_trigger_store.get(trigger_id)
            if trigger is not None:
                self._edit_event_trigger(routine.routine_id, trigger)
            return
        if kind == "core":
            trigger = self.core_trigger_store.get(trigger_id)
            if trigger is not None:
                self._edit_core_trigger(routine.routine_id, trigger)
            return
        if kind == "obs":
            trigger = self.obs_trigger_store.get(trigger_id)
            if trigger is not None:
                self._edit_obs_trigger(routine.routine_id, trigger)
            return
        command = self.trigger_store.get(trigger_id)
        if command is None:
            return
        dialog = TwitchCommandDialog(self, command, self.trigger_store.response_for(command))
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.trigger_store.update(command.trigger_id, **dialog.values())
        except (OSError, TypeError, ValueError) as error:
            self._error("Could Not Update Trigger", error)
            return
        self.commands_changed()
        self.select_routine(routine.routine_id)
        self._select_trigger("command", command.trigger_id)

    def _edit_event_trigger(
        self, routine_id: str, trigger: TwitchEventAutomationTrigger
    ) -> None:
        dialog = TwitchEventTriggerDialog(self, trigger)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            updated = self.event_trigger_store.update(
                trigger.trigger_id, **dialog.values()
            )
        except (OSError, TypeError, ValueError) as error:
            self._error("Could Not Update Trigger", error)
            return
        self.select_routine(routine_id)
        self._select_trigger("event", updated.trigger_id)

    def _edit_core_trigger(
        self, routine_id: str, trigger: CoreAutomationTrigger
    ) -> None:
        dialog = CoreTriggerDialog(self, trigger)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            updated = self.core_trigger_store.update(
                trigger.trigger_id, **dialog.values()
            )
        except (OSError, TypeError, ValueError) as error:
            self._error("Could Not Update Trigger", error)
            return
        self.select_routine(routine_id)
        self._select_trigger("core", updated.trigger_id)

    def _edit_obs_trigger(
        self, routine_id: str, trigger: ObsAutomationTrigger
    ) -> None:
        dialog = ObsTriggerDialog(self, trigger)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            updated = self.obs_trigger_store.update(trigger.trigger_id, **dialog.values())
        except (OSError, TypeError, ValueError) as error:
            self._error("Could Not Update Trigger", error)
            return
        self.select_routine(routine_id)
        self._select_trigger("obs", updated.trigger_id)

    def _remove_trigger(self) -> None:
        routine = self.routine_store.get(self._selected_routine_id)
        kind, trigger_id = self._selected_trigger()
        if routine is None or not trigger_id:
            return
        if kind == "event":
            trigger = self.event_trigger_store.get(trigger_id)
            detail = trigger.event_type if trigger else "this event"
            prompt = f"Remove the {detail} trigger but keep the routine?"
        elif kind == "core":
            trigger = self.core_trigger_store.get(trigger_id)
            detail = (
                CORE_TRIGGER_TYPES.get(trigger.event_type, trigger.event_type)
                if trigger
                else "Core program event"
            )
            prompt = f"Remove the {detail} trigger but keep the routine?"
        elif kind == "obs":
            trigger = self.obs_trigger_store.get(trigger_id)
            detail = OBS_TRIGGER_TYPES.get(trigger.event_type, trigger.event_type) if trigger else "OBS event"
            prompt = f"Remove the {detail} trigger but keep the routine?"
        else:
            command = self.trigger_store.get(trigger_id)
            if command is None:
                return
            prompt = f"Remove !{command.name} but keep this routine and all of its tasks?"
        if QMessageBox.question(
            self,
            "Remove Trigger",
            prompt,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            if kind == "event":
                self.event_trigger_store.delete(trigger_id)
            elif kind == "core":
                self.core_trigger_store.delete(trigger_id)
            elif kind == "obs":
                self.obs_trigger_store.delete(trigger_id)
            else:
                self.trigger_store.delete(trigger_id, delete_routine=False)
        except (OSError, ValueError) as error:
            self._error("Could Not Remove Trigger", error)
            return
        if kind == "command":
            self.commands_changed()
        self.select_routine(routine.routine_id)

    def _selected_task(self) -> TaskDefinition | None:
        routine = self.routine_store.get(self._selected_routine_id)
        item = self.task_list.currentItem()
        task_id = str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""
        if routine is None or not task_id:
            return None
        return next((task for task in routine.tasks if task.task_id == task_id), None)

    def _add_task(self, task_type: str = "") -> None:
        routine = self.routine_store.get(self._selected_routine_id)
        if routine is None or not task_type:
            return
        dialog = TaskEditorDialog(
            task_type,
            self,
            obs_service=self.obs_service,
            variables=self._sample_context_for_routine(routine),
            routine_store=self.routine_store,
            queue_store=self.queue_store,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            task = self.routine_store.add_task(routine.routine_id, **dialog.values())
        except (OSError, TypeError, ValueError) as error:
            self._error("Could Not Add Task", error)
            return
        self.select_routine(routine.routine_id)
        self._select_task(task.task_id)

    def _add_configured_task(
        self,
        task_type: str,
        name: str,
        config: dict[str, object],
    ) -> None:
        routine = self.routine_store.get(self._selected_routine_id)
        if routine is None:
            return
        try:
            task = self.routine_store.add_task(
                routine.routine_id,
                task_type=task_type,
                name=name,
                config=config,
            )
        except (OSError, TypeError, ValueError) as error:
            self._error("Could Not Add Task", error)
            return
        self.select_routine(routine.routine_id)
        self._select_task(task.task_id)

    def _edit_task(self) -> None:
        routine = self.routine_store.get(self._selected_routine_id)
        task = self._selected_task()
        if routine is None or task is None:
            return
        dialog = TaskEditorDialog(
            task.task_type,
            self,
            task,
            self.obs_service,
            self._sample_context_for_routine(routine),
            self.routine_store,
            self.queue_store,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            updated = self.routine_store.update_task(
                routine.routine_id, task.task_id, **dialog.values()
            )
        except (OSError, TypeError, ValueError) as error:
            self._error("Could Not Edit Task", error)
            return
        self.select_routine(routine.routine_id)
        self._select_task(updated.task_id)

    def _duplicate_task(self) -> None:
        routine = self.routine_store.get(self._selected_routine_id)
        task = self._selected_task()
        if routine is None or task is None:
            return
        try:
            copied = self.routine_store.duplicate_task(routine.routine_id, task.task_id)
        except (OSError, ValueError) as error:
            self._error("Could Not Duplicate Task", error)
            return
        self.select_routine(routine.routine_id)
        self._select_task(copied.task_id)

    def _toggle_task(self) -> None:
        routine = self.routine_store.get(self._selected_routine_id)
        task = self._selected_task()
        if routine is None or task is None:
            return
        try:
            self.routine_store.update_task(
                routine.routine_id, task.task_id, enabled=not task.enabled
            )
        except (OSError, ValueError) as error:
            self._error("Could Not Update Task", error)
            return
        self.select_routine(routine.routine_id)

    def _delete_task(self) -> None:
        routine = self.routine_store.get(self._selected_routine_id)
        task = self._selected_task()
        if routine is None or task is None:
            return
        if QMessageBox.question(
            self, "Delete Task", f'Delete "{task.name}" from this routine?'
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.routine_store.delete_task(routine.routine_id, task.task_id)
        except (OSError, ValueError) as error:
            self._error("Could Not Delete Task", error)
            return
        self.select_routine(routine.routine_id)

    def _move_task(self, offset: int) -> None:
        routine = self.routine_store.get(self._selected_routine_id)
        task = self._selected_task()
        if routine is None or task is None:
            return
        index = routine.tasks.index(task)
        try:
            self.routine_store.move_task(
                routine.routine_id, task.task_id, index + offset
            )
        except (OSError, ValueError) as error:
            self._error("Could Not Move Task", error)
            return
        self.select_routine(routine.routine_id)
        self._select_task(task.task_id)

    def _persist_task_order(self) -> None:
        routine = self.routine_store.get(self._selected_routine_id)
        if routine is None or self.task_list.count() != len(routine.tasks):
            return
        task_ids = [
            str(self.task_list.item(index).data(Qt.ItemDataRole.UserRole) or "")
            for index in range(self.task_list.count())
        ]
        if task_ids == [task.task_id for task in routine.tasks]:
            return
        try:
            self.routine_store.reorder_tasks(routine.routine_id, task_ids)
        except (OSError, ValueError) as error:
            self._error("Could Not Reorder Tasks", error)
        self.select_routine(routine.routine_id)

    def _task_context_menu(self, position) -> None:
        item = self.task_list.itemAt(position)
        if item is not None:
            self.task_list.setCurrentItem(item)
        task = self._selected_task() if item is not None else None
        menu = QMenu(self)
        self._add_task_submenu(menu)
        if task is not None:
            menu.addSeparator()
            menu.addAction("Edit", self._edit_task)
            menu.addAction("Test Task…", self._test_selected_task)
            menu.addAction("Duplicate", self._duplicate_task)
            menu.addAction("Copy", self._copy_task)
            paste_action = menu.addAction("Paste Task", self._paste_task)
            paste_action.setEnabled(self._clipboard_task_payload() is not None)
            menu.addAction("Disable Task" if task.enabled else "Enable Task", self._toggle_task)
            menu.addSeparator()
            menu.addAction("Move Up", lambda: self._move_task(-1))
            menu.addAction("Move Down", lambda: self._move_task(1))
            menu.addSeparator()
            menu.addAction("Remove", self._delete_task)
        else:
            menu.addSeparator()
            paste_action = menu.addAction("Paste Task", self._paste_task)
            paste_action.setEnabled(self._clipboard_task_payload() is not None)
        menu.exec(self.task_list.viewport().mapToGlobal(position))

    @staticmethod
    def _clipboard_task_payload() -> dict[str, object] | None:
        try:
            payload = json.loads(QApplication.clipboard().text())
        except (TypeError, json.JSONDecodeError):
            return None
        if (
            not isinstance(payload, dict)
            or payload.get("format") != "sally.automation.task"
            or int(payload.get("version", 0)) != 1
            or not isinstance(payload.get("task"), dict)
        ):
            return None
        return payload

    def _copy_task(self) -> None:
        task = self._selected_task()
        if task is None:
            return
        QApplication.clipboard().setText(
            json.dumps(
                {
                    "format": "sally.automation.task",
                    "version": 1,
                    "task": {
                        "task_type": task.task_type,
                        "name": task.name,
                        "config": task.config,
                        "enabled": task.enabled,
                    },
                },
                indent=2,
            )
        )

    def _paste_task(self) -> None:
        routine = self.routine_store.get(self._selected_routine_id)
        payload = self._clipboard_task_payload()
        if routine is None or payload is None:
            return
        values = payload["task"]
        task_type = str(values.get("task_type", ""))
        if task_type not in self.task_registry.registered_types():
            self._error(
                "Could Not Paste Task",
                ValueError(f"Task provider is unavailable: {task_type}"),
            )
            return
        config = values.get("config", {})
        if not isinstance(config, dict):
            self._error("Could Not Paste Task", ValueError("Task configuration is invalid."))
            return
        try:
            copied = self.routine_store.add_task(
                routine.routine_id,
                task_type=task_type,
                name=str(values.get("name", "Copied task")),
                config=config,
                enabled=bool(values.get("enabled", True)),
            )
        except (OSError, TypeError, ValueError) as error:
            self._error("Could Not Paste Task", error)
            return
        self.select_routine(routine.routine_id)
        self._select_task(copied.task_id)

    def _show_add_task_button_menu(self) -> None:
        menu = QMenu(self)
        self._add_task_submenu(menu, direct_services=True)
        menu.exec(
            self.add_task_button.mapToGlobal(
                self.add_task_button.rect().bottomLeft()
            )
        )

    def _add_task_submenu(
        self, menu: QMenu, *, direct_services: bool = False
    ) -> QMenu:
        add_menu = menu if direct_services else menu.addMenu("Add")
        available = set(self.task_registry.registered_types())
        services = (
            ("Core", CORE_TASK_LABELS),
            ("OBS", OBS_TASK_LABELS),
            (
                "Twitch",
                TWITCH_TASK_LABELS,
            ),
        )
        # Keep Python wrappers alive for the lifetime of the cascading menu.
        # PySide may otherwise collect a submenu before QMenu.exec() opens it.
        add_menu._sally_task_submenus = []
        for service_name, task_labels in services:
            service_menu = QMenu(service_name, add_menu)
            add_menu.addMenu(service_menu)
            add_menu._sally_task_submenus.append(service_menu)
            service_menu._sally_task_submenus = []
            scripts_menu = None
            variables_menu = None
            logic_menu = None
            files_menu = None
            if service_name == "Core":
                scripts_menu = QMenu("Scripts", service_menu)
                service_menu.addMenu(scripts_menu)
                service_menu._sally_task_submenus.append(scripts_menu)
                variables_menu = QMenu("Variables", service_menu)
                service_menu.addMenu(variables_menu)
                service_menu._sally_task_submenus.append(variables_menu)
                logic_menu = QMenu("Logic", service_menu)
                service_menu.addMenu(logic_menu)
                service_menu._sally_task_submenus.append(logic_menu)
                files_menu = QMenu("Files", service_menu)
                service_menu.addMenu(files_menu)
                service_menu._sally_task_submenus.append(files_menu)
            for task_type, full_label in task_labels.items():
                label = full_label.partition("—")[2].strip() or full_label
                target_menu = (
                    scripts_menu
                    if task_type == "core.run_python_script"
                    and scripts_menu is not None
                    else (
                        variables_menu
                        if task_type in VARIABLE_MANAGEMENT_TASK_TYPES
                        and variables_menu is not None
                        else (
                            logic_menu
                            if task_type in LOGIC_TASK_LABELS
                            and logic_menu is not None
                            else (
                                files_menu
                                if task_type in FILE_TASK_TYPES
                                and files_menu is not None
                                else service_menu
                            )
                        )
                    )
                )
                if task_type == "twitch.run_commercial":
                    duration_menu = QMenu(label, service_menu)
                    for seconds in (30, 60, 90, 180):
                        action = duration_menu.addAction(
                            f"{seconds} seconds",
                            lambda _checked=False, value=seconds: self._add_configured_task(
                                "twitch.run_commercial",
                                f"Run {value} second commercial",
                                {"length": value},
                            ),
                        )
                        action.setEnabled(task_type in available)
                    duration_menu.addSeparator()
                    customize_action = duration_menu.addAction(
                        "Customize…",
                        lambda _checked=False: self._add_task(
                            "twitch.run_commercial"
                        ),
                    )
                    customize_action.setEnabled(task_type in available)
                    service_menu.addMenu(duration_menu)
                    service_menu._sally_task_submenus.append(duration_menu)
                    continue
                action = target_menu.addAction(
                    label,
                    lambda _checked=False, selected=task_type: self._add_task(selected),
                )
                action.setEnabled(task_type in available)
        return add_menu

    def _select_task(self, task_id: str) -> None:
        for index in range(self.task_list.count()):
            item = self.task_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == task_id:
                self.task_list.setCurrentItem(item)
                break

    def _add_library_task(self, item: QTreeWidgetItem, _column: int) -> None:
        task_type = str(item.data(0, Qt.ItemDataRole.UserRole) or "")
        if not task_type:
            return
        self.tabs.setCurrentIndex(0)
        self.editor_tabs.setCurrentIndex(1)
        self._add_task(task_type)

    def _refresh_task_library(self) -> None:
        # Provider availability is static for this process, but this keeps the
        # task library honest if services register later in startup.
        available = set(self.task_registry.registered_types())
        pending = [
            self.task_library_tree.topLevelItem(index)
            for index in range(self.task_library_tree.topLevelItemCount())
        ]
        while pending:
            item = pending.pop()
            pending.extend(item.child(index) for index in range(item.childCount()))
            task_type = item.data(0, Qt.ItemDataRole.UserRole)
            if task_type:
                item.setText(
                    1,
                    "Available" if task_type in available else "Unavailable",
                )

    def _sample_context_for_routine(self, routine) -> dict[str, str]:
        keys: set[str] = set()
        if (
            self.trigger_store.for_routine(routine.routine_id) is not None
            or self.event_trigger_store.for_routine(routine.routine_id)
        ):
            keys.update(TWITCH_VARIABLES)
        if self.obs_trigger_store.for_routine(routine.routine_id):
            keys.update(OBS_VARIABLES)
        if self.core_trigger_store.for_routine(routine.routine_id):
            keys.update(CORE_VARIABLES)
        for task in routine.tasks:
            for value in task.config.values():
                if isinstance(value, str):
                    keys.update(TEMPLATE_PATTERN.findall(value))
        context = sample_context(keys)
        context.update(self.automation_service.variable_store.values())
        for task in routine.tasks:
            for name in CustomVariableStore.generated_names(
                task.task_type,
                task.config,
            ):
                context.setdefault(name, "CustomValue")
        for definition in self.routine_store.routines:
            for task in definition.tasks:
                if task.task_type not in {
                    "core.create_global_variable",
                    "core.create_session_variable",
                    "core.create_routine_variable",
                }:
                    continue
                name = str(task.config.get("name", "")).strip().casefold()
                if name and name not in VARIABLE_INFO:
                    context.setdefault(
                        name,
                        str(task.config.get("value", "CustomValue")),
                    )
        for key in keys:
            if key not in VARIABLE_INFO:
                context.setdefault(key, "CustomValue")
        return context

    def _test_selected_task(self) -> None:
        routine = self.routine_store.get(self._selected_routine_id)
        task = self._selected_task()
        if routine is None or task is None:
            return
        dialog = TaskTestDialog(
            task,
            self._sample_context_for_routine(routine),
            self._task_external_effect(task),
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            execution = self.automation_service.run_task(
                routine.routine_id,
                task.task_id,
                dialog.values(),
            )
        except (OSError, ValueError) as error:
            self._error("Task Test Failed", error)
            return
        self.record_execution(execution, f"Task test: {task.name}")
        result = execution.routine_results[0].task_results[0]
        QMessageBox.information(
            self,
            "Task Test",
            f"{task.name} completed successfully.\n\n{result.detail}"
            if result.succeeded
            else f"{task.name} failed.\n\n{result.detail}",
        )

    def _test_selected_routine(self) -> None:
        routine = self.routine_store.get(self._selected_routine_id)
        if routine is None or not routine.tasks:
            return
        enabled_tasks = [task for task in routine.tasks if task.enabled]
        risks = [
            self._task_external_effect(task)
            for task in enabled_tasks
            if self._task_external_effect(task)
        ]
        preview = "\n".join(
            f"{index}. {task.name}"
            for index, task in enumerate(enabled_tasks, start=1)
        ) or "No enabled tasks"
        warning = (
            "\n\nExternal actions:\n- " + "\n- ".join(dict.fromkeys(risks))
            if risks
            else "\n\nNo external side effects were detected."
        )
        if QMessageBox.question(
            self,
            "Test Routine",
            f'Run "{routine.name}" now?\n\nTasks:\n{preview}{warning}\n\n'
            "This is a live test; enabled tasks perform their real actions.",
        ) != QMessageBox.StandardButton.Yes:
            return
        context = {
            "user": "TestViewer",
            "channel": "test-channel",
            "uptime": "00:00:00",
            "followers": "--",
            "game": "--",
            "title": "Automation test",
            "command": "manual",
            "args": "",
            "target": "--",
            "uses": "1",
        }
        try:
            execution = self.automation_service.run_routine(
                routine.routine_id, context
            )
        except (OSError, ValueError) as error:
            self._error("Routine Test Failed", error)
            return
        self.record_execution(execution, "Manual test")
        result = execution.routine_results[0]
        QMessageBox.information(
            self,
            "Routine Test",
            "Routine completed successfully."
            if result.succeeded
            else f"Routine failed: {result.detail}",
        )

    @staticmethod
    def _task_external_effect(task: TaskDefinition) -> str:
        effects = {
            "twitch.send_chat_message": "Sends a Twitch chat message",
            "twitch.send_pinned_message": "Sends and pins a Twitch chat message",
            "twitch.run_commercial": "Starts a Twitch commercial",
            "twitch.snooze_ad": "Changes the Twitch ad schedule",
            "twitch.update_stream_title": "Changes the Twitch stream title",
            "twitch.update_stream_category": "Changes the Twitch stream category",
            "twitch.moderate_user": "Performs a Twitch moderation action",
            "twitch.update_redemption": "Fulfills or refunds a redemption",
            "core.launch_application": "Launches an application",
            "core.close_application": "Closes an application",
            "core.open_target": "Opens a file, folder, or URL",
            "core.show_notification": "Shows a desktop notification",
            "core.run_python_script": "Runs trusted Python code on this computer",
            "core.play_audio": "Plays an audio file",
            "core.run_routine": "Runs another routine and all its enabled tasks",
            "core.create_global_variable": "Changes saved automation data",
            "core.file_write": "Writes data to a local text file",
            "core.logic_get_input": "Opens an interactive input window",
            "core.logic_random_choice": "Runs one randomly selected routine",
            "core.logic_if_else": "May run another routine",
            "core.logic_switch": "May run another routine",
            "core.logic_while": "May run another routine repeatedly",
        }
        if task.task_type.startswith("obs."):
            return "Controls OBS Studio"
        return effects.get(task.task_type, "")

    def record_execution(
        self, execution: AutomationExecutionResult, trigger_label: str = ""
    ) -> None:
        for result in execution.routine_results:
            routine = self.routine_store.get(result.routine_id)
            details = self._format_execution_details(routine, result)
            self.history.insert(
                0,
                {
                    "when": datetime.now().astimezone().strftime("%H:%M:%S"),
                    "routine_id": result.routine_id,
                    "routine": routine.name if routine else result.routine_id,
                    "trigger": trigger_label or execution.trigger_id,
                    "result": "Completed" if result.succeeded else "Failed",
                    "tasks": str(len(result.task_results)),
                    "details": details,
                },
            )
        del self.history[200:]
        self._refresh_history_table()
        self._refresh_routine_history()
        if self.history_table.rowCount():
            self.history_table.selectRow(0)

    @staticmethod
    def _format_execution_details(routine, result) -> str:
        tasks_by_id = {
            task.task_id: task
            for task in (routine.tasks if routine is not None else [])
        }
        lines = [result.detail] if result.detail else []
        for index, task_result in enumerate(result.task_results, start=1):
            task = tasks_by_id.get(task_result.task_id)
            name = task.name if task is not None else task_result.task_type
            state = "Completed" if task_result.succeeded else "Failed"
            lines.append(
                f"{index}. {name} — {state} ({task_result.duration_ms} ms)"
            )
            if task_result.detail:
                lines.append(f"   {task_result.detail}")
        return "\n".join(lines) or "No task results were recorded."

    def _refresh_history_table(self) -> None:
        self.history_table.setRowCount(len(self.history))
        for row, entry in enumerate(self.history):
            for column, key in enumerate(
                ("when", "routine", "trigger", "result", "tasks")
            ):
                item = QTableWidgetItem(str(entry[key]))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, entry)
                self.history_table.setItem(row, column, item)

    def _refresh_routine_history(self) -> None:
        entries = [
            entry
            for entry in self.history
            if entry["routine_id"] == self._selected_routine_id
        ]
        self.routine_history_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            for column, key in enumerate(("when", "trigger", "result", "tasks")):
                item = QTableWidgetItem(str(entry[key]))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, entry)
                self.routine_history_table.setItem(row, column, item)
        if entries:
            self.routine_history_table.selectRow(0)
        else:
            self.routine_history_details.clear()

    @staticmethod
    def _show_history_details(table: QTableWidget, output: QTextEdit) -> None:
        row = table.currentRow()
        item = table.item(row, 0) if row >= 0 else None
        entry = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        output.setPlainText(
            str(entry.get("details", "")) if isinstance(entry, dict) else ""
        )

    @staticmethod
    def _error_title(error: Exception) -> str:
        return str(error) or error.__class__.__name__

    def _error(self, title: str, error: Exception) -> None:
        QMessageBox.warning(self, title, self._error_title(error))
