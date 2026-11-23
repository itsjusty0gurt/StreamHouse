"""Editors for Core scheduling and system triggers."""

from PySide6.QtCore import QTime
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFormLayout, QHBoxLayout, QKeySequenceEdit, QLabel, QLineEdit, QPushButton,
    QTimeEdit, QWidget,
)

from products.hub.automation.system_trigger_config import (
    FILE_EVENTS, SYSTEM_TRIGGER_TYPES, local_schedule_timezone, system_config,
)


class SystemTriggerDialog(QDialog):
    def __init__(self, event_type, trigger=None, parent=None):
        super().__init__(parent)
        self.event_type = event_type
        self.config = trigger.system_config if trigger else {}
        label = SYSTEM_TRIGGER_TYPES[event_type]
        self.setWindowTitle(f'{"Edit" if trigger else "Add"} {label}')
        self.setMinimumWidth(420)
        self.form = QFormLayout(self)
        if event_type == "scheduled_time":
            self._schedule_controls()
        elif event_type == "hotkey":
            self.shortcut = QKeySequenceEdit(
                QKeySequence.fromString(self.config.get("shortcut", ""), QKeySequence.PortableText)
            )
            self.shortcut.setMaximumSequenceLength(1)
            self.form.addRow("Shortcut", self.shortcut)
            self._hint("Press a combination such as Ctrl+Alt+B. Only registered shortcuts fire.")
        elif event_type.startswith("process_"):
            self.name = QLineEdit(self.config.get("name", ""))
            self.name.setPlaceholderText("notepad.exe")
            self.form.addRow("Executable name", self.name)
            self._hint("Each instance is tracked separately. Already-running apps form the baseline.")
        else:
            self._file_controls()
        self.enabled = QCheckBox("Enabled")
        self.enabled.setChecked(trigger is None or trigger.enabled)
        self.form.addRow(self.enabled)
        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.hide()
        self.form.addRow(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.form.addRow(buttons)

    def _schedule_controls(self):
        self.mode = QComboBox()
        self.mode.addItem("Simple Schedule", "simple")
        self.mode.addItem("Advanced Cron", "cron")
        self.mode.setCurrentIndex(self.mode.findData(self.config.get("mode", "simple")))
        self.form.addRow("Schedule type", self.mode)
        zone = local_schedule_timezone()
        zone_id = bytes(zone.id()).decode("utf-8", errors="replace")
        self.form.addRow("Timezone", QLabel(f"System local time ({zone_id})"))
        self.time = QTimeEdit(QTime(8, 0))
        self.time.setDisplayFormat("hh:mm AP")
        self.form.addRow("Time", self.time)
        self.weekday_row = QWidget()
        days_layout = QHBoxLayout(self.weekday_row)
        days_layout.setContentsMargins(0, 0, 0, 0)
        self.weekdays = {}
        for value, name in zip((1, 2, 3, 4, 5, 6, 0), ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")):
            checkbox = QCheckBox(name)
            days_layout.addWidget(checkbox)
            self.weekdays[value] = checkbox
        self.form.addRow("Days (none = daily)", self.weekday_row)
        expression = self.config.get("expression", "")
        self.expression = QLineEdit(expression)
        self.expression.setPlaceholderText("0 8 * * MON-FRI")
        self.form.addRow("Five-field cron", self.expression)
        if expression and self.mode.currentData() == "simple":
            minute, hour, _, _, weekdays = expression.split()
            self.time.setTime(QTime(int(hour), int(minute)))
            for day, checkbox in self.weekdays.items():
                checkbox.setChecked(str(day) in weekdays.split(","))
        self.mode.currentIndexChanged.connect(self._update_schedule_mode)
        self._update_schedule_mode()

    def _update_schedule_mode(self):
        simple = self.mode.currentData() == "simple"
        self.form.setRowVisible(self.time, simple)
        self.form.setRowVisible(self.weekday_row, simple)
        self.form.setRowVisible(self.expression, not simple)

    def _file_controls(self):
        self.kind = QComboBox()
        self.kind.addItem("File", "file")
        self.kind.addItem("Folder", "folder")
        self.kind.setCurrentIndex(self.kind.findData(self.config.get("kind", "file")))
        self.form.addRow("Watch", self.kind)
        self.path = QLineEdit(self.config.get("path", ""))
        path_row = QHBoxLayout()
        path_row.addWidget(self.path)
        select = QPushButton("Browse…")
        select.clicked.connect(self._browse)
        path_row.addWidget(select)
        self.form.addRow("Path", path_row)
        self.change = QComboBox()
        for value, label in FILE_EVENTS.items():
            self.change.addItem(label, value)
        self.change.setCurrentIndex(self.change.findData(self.config.get("event", "modified")))
        self.form.addRow("When it is", self.change)
        self._hint("You can enter a path that does not exist yet. Folders are not watched recursively.")

    def _hint(self, text):
        label = QLabel(text)
        label.setWordWrap(True)
        self.form.addRow(label)

    def _browse(self):
        if self.kind.currentData() == "folder":
            path = QFileDialog.getExistingDirectory(self, "Choose Folder", self.path.text())
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Choose File", self.path.text())
        if path:
            self.path.setText(path)

    def values(self):
        if self.event_type == "scheduled_time":
            mode = self.mode.currentData()
            expression = self.expression.text()
            if mode == "simple":
                time = self.time.time()
                days = ",".join(str(day) for day, check in self.weekdays.items() if check.isChecked())
                expression = f"{time.minute()} {time.hour()} * * {days or '*'}"
            config = {"mode": mode, "expression": expression}
        elif self.event_type == "hotkey":
            config = {"shortcut": self.shortcut.keySequence().toString(QKeySequence.PortableText)}
        elif self.event_type.startswith("process_"):
            config = {"name": self.name.text()}
        else:
            config = {"path": self.path.text(), "event": self.change.currentData(), "kind": self.kind.currentData()}
        return {"enabled": self.enabled.isChecked(), "system_config": system_config(self.event_type, config)}

    def accept(self):
        try:
            self.values()
        except ValueError as error:
            self.error.setText(str(error))
            self.error.show()
            return
        super().accept()
