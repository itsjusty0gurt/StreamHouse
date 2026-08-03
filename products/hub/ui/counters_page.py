from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QSpinBox, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from products.hub.counters.models import CounterDefinition, counter_id_from_name
from products.hub.counters.service import CounterService


class CounterDefinitionDialog(QDialog):
    def __init__(self, service: CounterService, parent: QWidget | None = None, definition: CounterDefinition | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.definition = definition
        self.setWindowTitle("Counter Settings" if definition else "New Counter")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(definition.display_name if definition else "")
        self.counter_id = QLineEdit(definition.counter_id if definition else "")
        self.counter_id.setReadOnly(definition is not None)
        self.singular = QLineEdit(definition.singular if definition else "")
        self.plural = QLineEdit(definition.plural if definition else "")
        self.starting_total = QSpinBox(); self.starting_total.setRange(-1_000_000_000, 1_000_000_000)
        self.starting_total.setVisible(definition is None)
        self.enabled = QCheckBox("Enabled"); self.enabled.setChecked(definition.enabled if definition else True)
        self.channel = QCheckBox("Channel all-time total"); self.channel.setChecked(definition.track_channel_total if definition else True)
        self.stream = QCheckBox("Current stream total"); self.stream.setChecked(definition.track_stream_total if definition else True)
        self.viewer = QCheckBox("Per-viewer all-time totals"); self.viewer.setChecked(definition.track_viewer_total if definition else True)
        self.viewer_stream = QCheckBox("Per-viewer current-stream totals"); self.viewer_stream.setChecked(definition.track_viewer_stream_total if definition else False)
        self.exclude_bots = QCheckBox("Exclude reliably identified bots"); self.exclude_bots.setChecked(definition.exclude_known_bots if definition else True)
        self.negative = QCheckBox("Allow negative values"); self.negative.setChecked(definition.allow_negative if definition else False)
        self.minimum = QSpinBox(); self.minimum.setRange(-1_000_000_000, 1_000_000_000); self.minimum.setValue(definition.minimum if definition else 0)
        form.addRow("Counter name", self.name); form.addRow("Stable ID", self.counter_id)
        form.addRow("Singular", self.singular); form.addRow("Plural", self.plural)
        if definition is None: form.addRow("Starting channel total", self.starting_total)
        form.addRow("", self.enabled); form.addRow("Track", self.channel); form.addRow("", self.stream); form.addRow("", self.viewer); form.addRow("", self.viewer_stream)
        form.addRow("Options", self.exclude_bots); form.addRow("", self.negative); form.addRow("Minimum", self.minimum)
        layout.addLayout(form)
        note = QLabel("The stable ID is used by routine outputs and the named storage file. It cannot be casually changed after creation. Disabling a scope preserves its existing values.")
        note.setWordWrap(True); layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        if definition is None:
            self.name.textChanged.connect(self._suggest_id)

    def _suggest_id(self, text: str) -> None:
        if not self.counter_id.isModified():
            self.counter_id.setText(counter_id_from_name(text))

    def _accept(self) -> None:
        try:
            values = self.values()
            if not any((values.track_channel_total, values.track_stream_total, values.track_viewer_total, values.track_viewer_stream_total)):
                raise ValueError("Select at least one tracked scope.")
            existing = self.service.get_counter(values.counter_id)
            if self.definition is None and existing is not None:
                raise ValueError(f'Counter ID "{values.counter_id}" already exists.')
        except ValueError as error:
            QMessageBox.warning(self, "Counter Configuration", str(error)); return
        self.accept()

    def values(self) -> CounterDefinition:
        return CounterDefinition(
            self.counter_id.text(), self.name.text(), self.singular.text(), self.plural.text(),
            self.enabled.isChecked(), self.channel.isChecked(), self.stream.isChecked(), self.viewer.isChecked(),
            self.viewer_stream.isChecked(), self.exclude_bots.isChecked(), self.negative.isChecked(), self.minimum.value(),
        )


class CountersPage(QWidget):
    counters_changed = Signal()

    def __init__(self, service: CounterService, stream_id_provider: Callable[[], str], routine_store=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service; self.stream_id_provider = stream_id_provider; self.routine_store = routine_store
        root = QVBoxLayout(self); header = QHBoxLayout(); self.search = QLineEdit(); self.search.setPlaceholderText("Search counters…")
        self.new_button = QPushButton("+ New Counter"); header.addWidget(QLabel("COUNTERS")); header.addWidget(self.search, 1); header.addWidget(self.new_button); root.addLayout(header)
        split = QSplitter(); self.list = QListWidget(); split.addWidget(self.list)
        detail = QWidget(); detail_layout = QVBoxLayout(detail); self.title = QLabel("Select or create a counter")
        detail_layout.addWidget(self.title); self.tabs = QTabWidget(); detail_layout.addWidget(self.tabs)
        self._build_overview(); self._build_viewers(); self._build_settings(); split.addWidget(detail); split.setStretchFactor(1, 1); root.addWidget(split)
        self.search.textChanged.connect(self.refresh); self.new_button.clicked.connect(self._create); self.list.currentItemChanged.connect(lambda *_: self._refresh_detail())
        self.refresh()

    def _build_overview(self) -> None:
        page = QWidget(); layout = QVBoxLayout(page); self.summary = QLabel(); self.summary.setWordWrap(True); layout.addWidget(self.summary)
        row = QHBoxLayout(); self.plus = QPushButton("Manual +1"); self.minus = QPushButton("Manual -1"); self.set_channel = QPushButton("Set channel total"); self.reset_channel = QPushButton("Reset channel total")
        for button in (self.plus, self.minus, self.set_channel, self.reset_channel): row.addWidget(button)
        stream_row = QHBoxLayout(); self.set_stream = QPushButton("Set current-stream total"); self.reset_stream = QPushButton("Reset current-stream total"); self.reset_all_viewers = QPushButton("Danger: Reset all viewer values")
        for button in (self.set_stream, self.reset_stream, self.reset_all_viewers): stream_row.addWidget(button)
        layout.addLayout(row); layout.addLayout(stream_row); layout.addStretch(); self.tabs.addTab(page, "Overview")
        self.plus.clicked.connect(lambda: self._adjust(1)); self.minus.clicked.connect(lambda: self._adjust(-1)); self.set_channel.clicked.connect(self._set_channel); self.reset_channel.clicked.connect(self._reset_channel)
        self.set_stream.clicked.connect(self._set_stream); self.reset_stream.clicked.connect(self._reset_stream); self.reset_all_viewers.clicked.connect(self._reset_all_viewers)

    def _build_viewers(self) -> None:
        page = QWidget(); layout = QVBoxLayout(page); self.viewer_search = QLineEdit(); self.viewer_search.setPlaceholderText("Search viewer name or login…"); layout.addWidget(self.viewer_search)
        self.viewer_table = QTableWidget(0, 4); self.viewer_table.setHorizontalHeaderLabels(("Viewer", "Lifetime", "Current Stream", "Twitch User ID")); self.viewer_table.setSortingEnabled(True); layout.addWidget(self.viewer_table)
        row = QHBoxLayout(); self.edit_viewer = QPushButton("Edit selected values"); self.remove_viewer_button = QPushButton("Remove viewer from this counter"); row.addWidget(self.edit_viewer); row.addWidget(self.remove_viewer_button); row.addStretch(); layout.addLayout(row); self.tabs.addTab(page, "Viewers")
        self.viewer_search.textChanged.connect(self._refresh_viewers); self.edit_viewer.clicked.connect(self._edit_viewer); self.remove_viewer_button.clicked.connect(self._remove_viewer)

    def _build_settings(self) -> None:
        page = QWidget(); layout = QVBoxLayout(page); self.settings_button = QPushButton("Edit Counter Settings"); self.delete_button = QPushButton("Delete Counter…"); layout.addWidget(self.settings_button); layout.addWidget(self.delete_button); layout.addStretch(); self.tabs.addTab(page, "Settings")
        self.settings_button.clicked.connect(self._settings); self.delete_button.clicked.connect(self._delete)

    def selected_id(self) -> str:
        item = self.list.currentItem(); return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def refresh(self) -> None:
        selected = self.selected_id(); query = self.search.text().strip().casefold(); self.list.clear()
        for definition in self.service.list_counters():
            if query and query not in definition.display_name.casefold() and query not in definition.counter_id: continue
            values = self.service.get_values(definition.counter_id, stream_id=self.stream_id_provider())
            state = "" if definition.enabled else " · Disabled"
            item = QListWidgetItem(f"{definition.display_name}    {values.channel_total:,} total · {values.stream_total:,} this stream{state}"); item.setData(Qt.ItemDataRole.UserRole, definition.counter_id); self.list.addItem(item)
            if definition.counter_id == selected: self.list.setCurrentItem(item)
        if self.list.currentItem() is None and self.list.count(): self.list.setCurrentRow(0)
        self._refresh_detail()

    def _definition(self): return self.service.get_counter(self.selected_id()) if self.selected_id() else None

    def _refresh_detail(self) -> None:
        definition = self._definition()
        if not definition: self.title.setText("Select or create a counter"); self.summary.clear(); self.viewer_table.setRowCount(0); return
        values = self.service.get_values(definition.counter_id, stream_id=self.stream_id_provider()); rows = self.service.viewer_rows(definition.counter_id, stream_id=self.stream_id_provider()); leaders = self.service.leaderboard(definition.counter_id, stream_id=self.stream_id_provider(), limit=1)
        referenced = 0
        if self.routine_store is not None: referenced = sum(1 for routine in self.routine_store.routines for task in routine.tasks if task.task_type.startswith("counter.") and task.config.get("counter_id") == definition.counter_id)
        top = (leaders[0].get("display_name") or leaders[0].get("login")) if leaders else "None"
        self.title.setText(definition.display_name); self.summary.setText(f"Channel all-time: {values.channel_total:,}\nCurrent Twitch stream: {values.stream_total:,}\nViewers with a value: {len(rows):,}\nTop viewer: {top}\nReferenced by {referenced} routine task(s).")
        active_stream = bool(self.stream_id_provider())
        self.set_stream.setEnabled(active_stream)
        self.reset_stream.setEnabled(active_stream)
        self._refresh_viewers()

    def _refresh_viewers(self) -> None:
        definition = self._definition(); self.viewer_table.setSortingEnabled(False); self.viewer_table.setRowCount(0)
        if definition:
            query = self.viewer_search.text().strip().casefold()
            for row in self.service.viewer_rows(definition.counter_id, stream_id=self.stream_id_provider()):
                name = row["display_name"] or row["login"] or row["user_id"]
                if query and query not in name.casefold() and query not in row["login"].casefold(): continue
                index = self.viewer_table.rowCount(); self.viewer_table.insertRow(index)
                lifetime = QTableWidgetItem(); lifetime.setData(Qt.ItemDataRole.EditRole, row["total"])
                current = QTableWidgetItem(); current.setData(Qt.ItemDataRole.EditRole, row["stream_total"])
                self.viewer_table.setItem(index, 0, QTableWidgetItem(name)); self.viewer_table.setItem(index, 1, lifetime); self.viewer_table.setItem(index, 2, current); self.viewer_table.setItem(index, 3, QTableWidgetItem(row["user_id"]))
        self.viewer_table.setSortingEnabled(True)

    def _create(self) -> None:
        dialog = CounterDefinitionDialog(self.service, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try: self.service.create_counter(dialog.values(), dialog.starting_total.value())
            except (OSError, ValueError) as error: QMessageBox.critical(self, "Could Not Create Counter", str(error)); return
            self.counters_changed.emit(); self.refresh()

    def _adjust(self, amount: int) -> None:
        definition = self._definition()
        if not definition: return
        self.service.update_values(definition.counter_id, amount, ("channel_total",), stream_id=self.stream_id_provider()); self.refresh()

    def _set_channel(self) -> None:
        definition = self._definition()
        if not definition: return
        value, ok = QInputDialog.getInt(self, "Set Channel Total", "Exact value", self.service.get_values(definition.counter_id).channel_total, -1_000_000_000, 1_000_000_000)
        if ok:
            try: self.service.set_value(definition.counter_id, "channel_total", value)
            except ValueError as error: QMessageBox.warning(self, "Invalid Value", str(error)); return
            self.refresh()

    def _reset_channel(self) -> None:
        definition = self._definition()
        if definition and QMessageBox.question(self, "Reset Counter", "Reset this counter's channel all-time total?") == QMessageBox.StandardButton.Yes: self.service.reset(definition.counter_id, ("channel_total",)); self.refresh()

    def _set_stream(self) -> None:
        definition = self._definition(); stream_id = self.stream_id_provider()
        if not definition or not stream_id: return
        current = self.service.get_values(definition.counter_id, stream_id=stream_id).stream_total
        value, ok = QInputDialog.getInt(self, "Set Current-Stream Total", "Exact value", current, -1_000_000_000, 1_000_000_000)
        if ok:
            try: self.service.set_value(definition.counter_id, "stream_total", value, stream_id=stream_id)
            except ValueError as error: QMessageBox.warning(self, "Invalid Value", str(error)); return
            self.refresh()

    def _reset_stream(self) -> None:
        definition = self._definition(); stream_id = self.stream_id_provider()
        if definition and stream_id and QMessageBox.question(self, "Reset Current Stream", "Reset this counter's value for the active Twitch broadcast?") == QMessageBox.StandardButton.Yes:
            self.service.reset(definition.counter_id, ("stream_total",), stream_id=stream_id); self.refresh()

    def _reset_all_viewers(self) -> None:
        definition = self._definition()
        if not definition: return
        prompt = "Reset every viewer lifetime and current-stream value for this counter? This does not remove general viewer profiles or values in other counters."
        if QMessageBox.warning(self, "Danger: Reset All Viewer Values", prompt, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes:
            self.service.reset(definition.counter_id, (), stream_id=self.stream_id_provider(), all_viewers=True); self.refresh()

    def _edit_viewer(self) -> None:
        definition = self._definition(); row = self.viewer_table.currentRow()
        if not definition or row < 0: return
        user_id = self.viewer_table.item(row, 3).text(); lifetime = int(self.viewer_table.item(row, 1).text()); stream = int(self.viewer_table.item(row, 2).text())
        value, ok = QInputDialog.getInt(self, "Edit Viewer Lifetime", "Lifetime value", lifetime, -1_000_000_000, 1_000_000_000)
        if ok: self.service.set_value(definition.counter_id, "viewer_total", value, user_id=user_id)
        stream_id = self.stream_id_provider()
        if ok and stream_id:
            stream_value, accepted = QInputDialog.getInt(self, "Edit Viewer Current Stream", "Current-stream value", stream, -1_000_000_000, 1_000_000_000)
            if accepted: self.service.set_value(definition.counter_id, "viewer_stream_total", stream_value, user_id=user_id, stream_id=stream_id)
        self.refresh()

    def _remove_viewer(self) -> None:
        definition = self._definition(); row = self.viewer_table.currentRow()
        if definition and row >= 0 and QMessageBox.question(self, "Remove Viewer Entry", "Remove this viewer only from this counter? Their general Hub profile and other counters are unaffected.") == QMessageBox.StandardButton.Yes: self.service.remove_viewer(definition.counter_id, self.viewer_table.item(row, 3).text()); self.refresh()

    def _settings(self) -> None:
        definition = self._definition()
        if not definition: return
        dialog = CounterDefinitionDialog(self.service, self, definition)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            updated = dialog.values(); self.service.update_counter(definition.counter_id, **{key: value for key, value in updated.to_dict().items() if key != "counter_id"}); self.counters_changed.emit(); self.refresh()

    def _delete(self) -> None:
        definition = self._definition()
        if not definition: return
        text = "Delete this counter definition, its named JSON file, and all shared/per-viewer values? General viewer profiles, other counters, commands, Twitch data, and routines are not deleted. Referencing tasks will show Missing Counter."
        if QMessageBox.warning(self, "Delete Counter", text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes:
            self.service.delete_counter(definition.counter_id); self.counters_changed.emit(); self.refresh()
