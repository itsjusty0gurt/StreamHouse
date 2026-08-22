from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QDoubleSpinBox, QSpinBox, QSplitter, QTabWidget, QTableWidget, QTableWidgetItem,
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
        self.name.setPlaceholderText("Counter name")
        self.counter_id = QLineEdit(definition.counter_id if definition else "")
        self.counter_id.setReadOnly(definition is not None)
        self.singular = QLineEdit(definition.singular if definition else "")
        self.singular.setPlaceholderText("Optional, for example cup")
        self.plural = QLineEdit(definition.plural if definition else "")
        self.plural.setPlaceholderText("Optional, for example cups")
        self.numeric_type = QComboBox()
        self.numeric_type.addItem("Integer", "integer")
        self.numeric_type.addItem("Decimal / Number", "decimal")
        self.numeric_type.setCurrentIndex(
            self.numeric_type.findData(definition.numeric_type if definition else "integer")
        )
        self.reset_value = QDoubleSpinBox()
        self.reset_value.setRange(-1_000_000_000, 1_000_000_000)
        self.reset_value.setDecimals(6)
        self.reset_value.setValue(float(definition.reset_value if definition else 0))
        self.display_precision = QSpinBox()
        self.display_precision.setRange(0, 6)
        self.display_precision.setValue(definition.display_precision if definition else 0)
        self.enabled = QCheckBox("Enabled"); self.enabled.setChecked(definition.enabled if definition else True)
        self.channel = QCheckBox("Channel all-time total"); self.channel.setChecked(definition.track_channel_total if definition else True)
        self.stream = QCheckBox("Current stream total"); self.stream.setChecked(definition.track_stream_total if definition else True)
        self.viewer = QCheckBox("Per-viewer all-time totals"); self.viewer.setChecked(definition.track_viewer_total if definition else True)
        self.viewer_stream = QCheckBox("Per-viewer current-stream totals"); self.viewer_stream.setChecked(definition.track_viewer_stream_total if definition else False)
        self.exclude_bots = QCheckBox("Exclude reliably identified bots"); self.exclude_bots.setChecked(definition.exclude_known_bots if definition else True)
        self.negative = QCheckBox("Allow negative values"); self.negative.setChecked(definition.allow_negative if definition else False)
        self.minimum = QDoubleSpinBox(); self.minimum.setRange(-1_000_000_000, 1_000_000_000); self.minimum.setDecimals(6); self.minimum.setValue(float(definition.minimum if definition else 0))
        form.addRow("Counter name", self.name); form.addRow("Stable ID", self.counter_id)
        form.addRow("Unit (singular)", self.singular); form.addRow("Unit (plural)", self.plural)
        form.addRow("Number type", self.numeric_type)
        form.addRow("Reset / starting value", self.reset_value)
        form.addRow("Display precision", self.display_precision)
        form.addRow("", self.enabled); form.addRow("Track", self.channel); form.addRow("", self.stream); form.addRow("", self.viewer); form.addRow("", self.viewer_stream)
        form.addRow("Options", self.exclude_bots); form.addRow("", self.negative); form.addRow("Minimum", self.minimum)
        layout.addLayout(form)
        note = QLabel("The stable ID is used by routine outputs and the named storage file. It cannot be casually changed after creation. Disabling a scope preserves its existing values.")
        note.setWordWrap(True); layout.addWidget(note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
        self.numeric_type.currentIndexChanged.connect(self._numeric_type_changed)
        self._numeric_type_changed()
        if definition is None:
            self.name.textChanged.connect(self._suggest_id)
            self.counter_id_status = QLabel(""); layout.insertWidget(layout.count() - 1, self.counter_id_status)
            self.counter_id.textChanged.connect(self._validate_id_live)

    def _suggest_id(self, text: str) -> None:
        if not self.counter_id.isModified():
            self.counter_id.setText(counter_id_from_name(text))

    def _validate_id_live(self, value: str) -> None:
        try:
            definition = CounterDefinition(value, self.name.text() or "Counter name", self.singular.text(), self.plural.text())
            if self.service.get_counter(definition.counter_id) is not None:
                raise ValueError("That stable counter ID is already in use.")
        except (OSError, ValueError) as error:
            self.counter_id_status.setText(str(error))
        else:
            self.counter_id_status.setText("Stable counter ID is available.")

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

    def _numeric_type_changed(self, *_args) -> None:
        decimal = self.numeric_type.currentData() == "decimal"
        self.display_precision.setEnabled(decimal)
        if not decimal:
            self.display_precision.setValue(0)
        decimals = 6 if decimal else 0
        self.reset_value.setDecimals(decimals)
        self.minimum.setDecimals(decimals)

    def values(self) -> CounterDefinition:
        return CounterDefinition(
            counter_id=self.counter_id.text(),
            display_name=self.name.text(),
            singular=self.singular.text(),
            plural=self.plural.text(),
            enabled=self.enabled.isChecked(),
            track_channel_total=self.channel.isChecked(),
            track_stream_total=self.stream.isChecked(),
            track_viewer_total=self.viewer.isChecked(),
            track_viewer_stream_total=self.viewer_stream.isChecked(),
            exclude_known_bots=self.exclude_bots.isChecked(),
            allow_negative=self.negative.isChecked(),
            minimum=str(self.minimum.value()),
            numeric_type=str(self.numeric_type.currentData()),
            reset_value=str(self.reset_value.value()),
            display_precision=self.display_precision.value(),
        )


class CountersPage(QWidget):
    counters_changed = Signal()

    def __init__(self, service: CounterService, stream_id_provider: Callable[[], str], routine_store=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service; self.stream_id_provider = stream_id_provider; self.routine_store = routine_store
        root = QVBoxLayout(self); header = QHBoxLayout(); self.search = QLineEdit(); self.search.setPlaceholderText("Search counters…")
        self.new_button = QPushButton("+ New Counter"); header.addWidget(QLabel("COUNTERS")); header.addWidget(self.search, 1); header.addWidget(self.new_button); root.addLayout(header)
        self.empty_state = QWidget(); empty_layout = QVBoxLayout(self.empty_state)
        empty_message = QLabel("No counters yet.\nCreate a counter to use it in commands and automation routines.")
        empty_message.setAlignment(Qt.AlignmentFlag.AlignCenter); self.empty_create_button = QPushButton("Create Counter")
        empty_layout.addStretch(); empty_layout.addWidget(empty_message); empty_layout.addWidget(self.empty_create_button, alignment=Qt.AlignmentFlag.AlignCenter); empty_layout.addStretch()
        root.addWidget(self.empty_state)
        self.split = QSplitter(); self.list = QListWidget(); self.split.addWidget(self.list)
        detail = QWidget(); detail_layout = QVBoxLayout(detail); self.title = QLabel("Select or create a counter")
        detail_layout.addWidget(self.title); self.tabs = QTabWidget(); detail_layout.addWidget(self.tabs)
        self._build_overview(); self._build_viewers(); self._build_settings(); self.split.addWidget(detail); self.split.setStretchFactor(1, 1); root.addWidget(self.split)
        self.search.textChanged.connect(self.refresh); self.new_button.clicked.connect(self._create); self.empty_create_button.clicked.connect(self._create); self.list.currentItemChanged.connect(lambda *_: self._refresh_detail())
        self.refresh()

    def _build_overview(self) -> None:
        page = QWidget(); layout = QVBoxLayout(page); self.summary = QLabel(); self.summary.setWordWrap(True); layout.addWidget(self.summary)
        row = QHBoxLayout(); self.plus = QPushButton("Manual +1"); self.minus = QPushButton("Manual -1"); self.set_channel = QPushButton("Set channel total"); self.reset_channel = QPushButton("Reset channel total")
        for button in (self.plus, self.minus, self.set_channel, self.reset_channel): row.addWidget(button)
        stream_row = QHBoxLayout(); self.set_stream = QPushButton("Set current-stream total"); self.reset_stream = QPushButton("Reset current-stream total")
        self.reset_all_viewer_totals = QPushButton("Danger: Reset all viewer lifetime values")
        self.reset_all_viewer_stream_totals = QPushButton("Danger: Reset all viewer stream values")
        for button in (self.set_stream, self.reset_stream, self.reset_all_viewer_totals, self.reset_all_viewer_stream_totals): stream_row.addWidget(button)
        layout.addLayout(row); layout.addLayout(stream_row); layout.addStretch(); self.tabs.addTab(page, "Overview")
        self.plus.clicked.connect(lambda: self._adjust(1)); self.minus.clicked.connect(lambda: self._adjust(-1)); self.set_channel.clicked.connect(self._set_channel); self.reset_channel.clicked.connect(self._reset_channel)
        self.set_stream.clicked.connect(self._set_stream); self.reset_stream.clicked.connect(self._reset_stream)
        self.reset_all_viewer_totals.clicked.connect(lambda: self._reset_all_viewers("viewer_total"))
        self.reset_all_viewer_stream_totals.clicked.connect(lambda: self._reset_all_viewers("viewer_stream_total"))

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
        try:
            definitions = self.service.list_counters()
        except (OSError, TypeError, ValueError) as error:
            self.empty_state.setVisible(False); self.split.setVisible(True); self.search.setEnabled(False)
            self.title.setText("Counter configuration error"); self.summary.setText(str(error)); self.viewer_table.setRowCount(0)
            return
        self.empty_state.setVisible(not definitions)
        self.split.setVisible(bool(definitions))
        self.search.setEnabled(bool(definitions))
        for definition in definitions:
            if query and query not in definition.display_name.casefold() and query not in definition.counter_id: continue
            values = self.service.get_values(definition.counter_id, stream_id=self.stream_id_provider())
            state = "" if definition.enabled else " · Disabled"
            shared = self.service.format_value(definition.counter_id, values.channel_total)
            stream_text = self.service.format_value(definition.counter_id, values.stream_total) + " this stream" if self.stream_id_provider() else "Stream offline"
            item = QListWidgetItem(f"{definition.display_name}    {shared} · {stream_text}{state}"); item.setData(Qt.ItemDataRole.UserRole, definition.counter_id); self.list.addItem(item)
            if definition.counter_id == selected: self.list.setCurrentItem(item)
        if self.list.currentItem() is None and self.list.count(): self.list.setCurrentRow(0)
        self._refresh_detail()

    def _definition(self): return self.service.get_counter(self.selected_id()) if self.selected_id() else None

    def _refresh_detail(self) -> None:
        definition = self._definition()
        if not definition: self.title.setText("Select or create a counter"); self.summary.clear(); self.viewer_table.setRowCount(0); return
        stream_id = self.stream_id_provider()
        values = self.service.get_values(definition.counter_id, stream_id=stream_id); rows = self.service.viewer_rows(definition.counter_id, stream_id=stream_id); leaders = self.service.leaderboard(definition.counter_id, stream_id=stream_id, limit=1)
        referenced = 0
        if self.routine_store is not None: referenced = sum(1 for routine in self.routine_store.routines for task in routine.tasks if task.task_type.startswith("counter.") and task.config.get("counter_id") == definition.counter_id)
        top = (leaders[0].get("display_name") or leaders[0].get("login")) if leaders else "None"
        channel_text = self.service.format_value(definition.counter_id, values.channel_total) if definition.track_channel_total else "Not tracked"
        stream_text = self.service.format_value(definition.counter_id, values.stream_total) if definition.track_stream_total and stream_id else "Offline / unavailable" if definition.track_stream_total else "Not tracked"
        self.title.setText(definition.display_name); self.summary.setText(f"Channel lifetime: {channel_text}\nCurrent Twitch stream: {stream_text}\nViewers with a value: {len(rows):,}\nTop viewer: {top}\nReferenced by {referenced} routine task(s).")
        active_stream = bool(stream_id) and definition.track_stream_total
        self.set_stream.setEnabled(active_stream and definition.enabled)
        self.reset_stream.setEnabled(active_stream and definition.enabled)
        self.reset_all_viewer_stream_totals.setEnabled(bool(stream_id) and definition.enabled and definition.track_viewer_stream_total)
        self.reset_all_viewer_totals.setEnabled(definition.enabled and definition.track_viewer_total)
        channel_enabled = definition.enabled and definition.track_channel_total
        for button in (self.plus, self.minus, self.set_channel, self.reset_channel): button.setEnabled(channel_enabled)
        self._refresh_viewers()

    def _refresh_viewers(self) -> None:
        definition = self._definition(); self.viewer_table.setSortingEnabled(False); self.viewer_table.setRowCount(0)
        if definition:
            query = self.viewer_search.text().strip().casefold()
            for row in self.service.viewer_rows(definition.counter_id, stream_id=self.stream_id_provider()):
                name = row["display_name"] or row["login"] or row["user_id"]
                if query and query not in name.casefold() and query not in row["login"].casefold(): continue
                index = self.viewer_table.rowCount(); self.viewer_table.insertRow(index)
                lifetime = QTableWidgetItem(); lifetime.setData(Qt.ItemDataRole.EditRole, float(row["total"]))
                current = QTableWidgetItem(); current.setData(Qt.ItemDataRole.EditRole, float(row["stream_total"]))
                self.viewer_table.setItem(index, 0, QTableWidgetItem(name)); self.viewer_table.setItem(index, 1, lifetime); self.viewer_table.setItem(index, 2, current); self.viewer_table.setItem(index, 3, QTableWidgetItem(row["user_id"]))
        self.viewer_table.setSortingEnabled(True)

    def _create(self) -> None:
        dialog = CounterDefinitionDialog(self.service, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            try: definition = self.service.create_counter(dialog.values())
            except (OSError, ValueError) as error: QMessageBox.critical(self, "Could Not Create Counter", str(error)); return
            self.counters_changed.emit(); self.refresh(); self._select_counter(definition.counter_id)

    def _select_counter(self, counter_id: str) -> None:
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == counter_id:
                self.list.setCurrentItem(item)
                break

    def _accept_operation(self, operation) -> bool:
        if operation.status in {"success", "partial_success", "minimum_reached"}:
            return True
        QMessageBox.warning(self, "Counter Operation", operation.detail or operation.status.replace("_", " ").title())
        return False

    def _adjust(self, amount: int) -> None:
        definition = self._definition()
        if not definition: return
        operation = self.service.update_values(definition.counter_id, amount, ("channel_total",), stream_id=self.stream_id_provider())
        if self._accept_operation(operation): self.refresh()

    def _set_channel(self) -> None:
        definition = self._definition()
        if not definition: return
        value, ok = self._ask_value("Set Shared Counter", "Exact value", self.service.get_values(definition.counter_id).channel_total)
        if ok:
            operation = self.service.set_value(definition.counter_id, "channel_total", value)
            if self._accept_operation(operation): self.refresh()

    def _reset_channel(self) -> None:
        definition = self._definition()
        if definition and QMessageBox.question(self, "Reset Counter", "Reset this counter's channel lifetime total?") == QMessageBox.StandardButton.Yes:
            if self._accept_operation(self.service.reset(definition.counter_id, ("channel_total",))): self.refresh()

    def _set_stream(self) -> None:
        definition = self._definition(); stream_id = self.stream_id_provider()
        if not definition or not stream_id: return
        current = self.service.get_values(definition.counter_id, stream_id=stream_id).stream_total
        value, ok = self._ask_value("Set Current-Broadcast Counter", "Exact value", current)
        if ok:
            operation = self.service.set_value(definition.counter_id, "stream_total", value, stream_id=stream_id)
            if self._accept_operation(operation): self.refresh()

    def _reset_stream(self) -> None:
        definition = self._definition(); stream_id = self.stream_id_provider()
        if definition and stream_id and QMessageBox.question(self, "Reset Current Stream", "Reset this counter's value for the active Twitch broadcast?") == QMessageBox.StandardButton.Yes:
            if self._accept_operation(self.service.reset(definition.counter_id, ("stream_total",), stream_id=stream_id)): self.refresh()

    def _reset_all_viewers(self, scope: str) -> None:
        definition = self._definition()
        if not definition: return
        label = "lifetime" if scope == "viewer_total" else "current-stream"
        prompt = f"Reset every viewer {label} value for this counter? This does not remove general viewer profiles or values in other counters."
        if QMessageBox.warning(self, "Danger: Reset All Viewer Values", prompt, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel) == QMessageBox.StandardButton.Yes:
            if self._accept_operation(self.service.reset(definition.counter_id, (), stream_id=self.stream_id_provider(), all_viewer_scopes=(scope,))): self.refresh()

    def _edit_viewer(self) -> None:
        definition = self._definition(); row = self.viewer_table.currentRow()
        if not definition or row < 0: return
        user_id = self.viewer_table.item(row, 3).text(); lifetime = self.viewer_table.item(row, 1).text(); stream = self.viewer_table.item(row, 2).text()
        value, ok = self._ask_value("Edit Viewer Lifetime", "Lifetime value", lifetime)
        if ok and not self._accept_operation(self.service.set_value(definition.counter_id, "viewer_total", value, user_id=user_id)): return
        stream_id = self.stream_id_provider()
        if ok and stream_id:
            stream_value, accepted = self._ask_value("Edit Viewer Current Stream", "Current-stream value", stream)
            if accepted: self._accept_operation(self.service.set_value(definition.counter_id, "viewer_stream_total", stream_value, user_id=user_id, stream_id=stream_id))
        self.refresh()

    def _ask_value(self, title: str, label: str, current) -> tuple[float | int, bool]:
        definition = self._definition()
        if definition is not None and definition.numeric_type == "decimal":
            return QInputDialog.getDouble(
                self,
                title,
                label,
                float(current),
                -1_000_000_000,
                1_000_000_000,
                definition.display_precision or 6,
            )
        return QInputDialog.getInt(
            self,
            title,
            label,
            int(current),
            -1_000_000_000,
            1_000_000_000,
        )

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
