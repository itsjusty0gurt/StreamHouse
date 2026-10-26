from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from products.hub.automation.custom_variables import CustomVariableStore
from products.hub.automation.routines import RoutineStore
from products.hub.automation.variable_outputs import (
    ConfiguredOutputReference,
    configured_output_references,
)
from products.hub.automation.variable_registry import (
    VariableDataType,
    VariableRegistry,
    VariableSnapshot,
)
from products.hub.ui.variable_picker import VariablePickerDialog
from products.hub.ui.page_header import PageHeader
from products.hub.ui.table_sizing import (
    configure_semantic_columns,
    make_columns_interactive,
)


@dataclass(frozen=True, slots=True)
class VariablePageEntry:
    key: str
    snapshot: VariableSnapshot
    output_reference: ConfiguredOutputReference | None = None

    @property
    def context_label(self) -> str:
        if self.output_reference is not None:
            return self.output_reference.routine_name
        return self.snapshot.definition.context_label or "—"


class CustomVariableDialog(QDialog):
    def __init__(
        self,
        *,
        name: str = "",
        value: str = "",
        data_type: str = "text",
        description: str = "",
        editing: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Custom Variable" if editing else "New Custom Variable")
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name_edit = QLineEdit(name.removeprefix("custom."))
        self.name_edit.setPlaceholderText("game_mode")
        self.name_edit.setEnabled(not editing)
        self.value_edit = QLineEdit(value)
        self.type_combo = QComboBox()
        for item in VariableDataType:
            self.type_combo.addItem(item.value.title(), item.value)
        self.type_combo.setCurrentIndex(max(0, self.type_combo.findData(data_type)))
        self.description_edit = QLineEdit(description)
        form.addRow("Name", self.name_edit)
        form.addRow("Value", self.value_edit)
        form.addRow("Type", self.type_combo)
        form.addRow("Description", self.description_edit)
        layout.addLayout(form)
        hint = QLabel("Stored as custom.<name>. Built-in namespaces are reserved.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> tuple[str, str, str, str]:
        return (
            self.name_edit.text().strip(),
            self.value_edit.text(),
            str(self.type_combo.currentData()),
            self.description_edit.text().strip(),
        )


class VariablesPage(QWidget):
    variables_changed = Signal()

    def __init__(
        self,
        registry: VariableRegistry,
        store: CustomVariableStore,
        routine_store: RoutineStore | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.registry = registry
        self.store = store
        self.routine_store = routine_store
        self._entries_by_key: dict[str, VariablePageEntry] = {}
        self._row_items_by_key: dict[str, QTableWidgetItem] = {}
        self._rendered_structure: tuple[tuple[object, ...], ...] = ()
        layout = QVBoxLayout(self)
        self.page_header = PageHeader(
            "Variables",
            "Browse placeholders and values currently available to Automation.",
            self,
        )
        layout.addWidget(self.page_header)
        toolbar = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search variables...")
        self.source_combo = QComboBox()
        self.new_button = QPushButton("+ New Custom Variable")
        self.edit_button = QPushButton("Edit")
        self.delete_button = QPushButton("Delete")
        self.picker_button = QPushButton("{x} Variable Picker")
        self.page_header.add_action(self.picker_button)
        self.page_header.add_action(self.new_button)
        toolbar.addWidget(self.search_edit, 1)
        toolbar.addWidget(self.source_combo)
        layout.addLayout(toolbar)
        self.table = QTableWidget(0, 8)
        self.table.setObjectName("automationVariablesTable")
        self.table.setHorizontalHeaderLabels(
            (
                "Placeholders",
                "Current Value",
                "Source",
                "Type",
                "Status",
                "Context",
                "Lifetime",
                "Access",
            )
        )
        configure_semantic_columns(
            self.table,
            stretch=(0,),
            compact=(2, 3, 6, 7),
            header_compact=(4, 5),
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)
        self.details_label = QLabel("Select a variable to view its description.")
        self.details_label.setWordWrap(True)
        actions = QHBoxLayout()
        self.copy_placeholder_button = QPushButton("Copy Placeholder")
        actions.addWidget(self.copy_placeholder_button)
        actions.addWidget(self.edit_button)
        actions.addWidget(self.delete_button)
        actions.addStretch()
        layout.addWidget(self.details_label)
        layout.addLayout(actions)
        self.search_edit.textChanged.connect(self.refresh)
        self.source_combo.currentIndexChanged.connect(self.refresh)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.copy_placeholder_button.clicked.connect(self._copy_placeholder)
        self.new_button.clicked.connect(self._create)
        self.edit_button.clicked.connect(self._edit)
        self.delete_button.clicked.connect(self._delete)
        self.picker_button.clicked.connect(self._open_picker)
        self._columns_interactive = False
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._columns_interactive:
            QTimer.singleShot(0, self._enable_manual_column_resizing)

    def _enable_manual_column_resizing(self) -> None:
        if self._columns_interactive:
            return
        make_columns_interactive(self.table)
        self._columns_interactive = True

    def refresh(self) -> None:
        self.rebuild_catalog()

    def rebuild_catalog(self) -> None:
        """Rebuild rows only when the Variable catalog or view structure changes."""
        entries = self._entries()
        selected = self.selected_key()
        had_selection = bool(selected)
        vertical_scroll = self.table.verticalScrollBar().value()
        horizontal_scroll = self.table.horizontalScrollBar().value()
        header = self.table.horizontalHeader()
        sorting_enabled = self.table.isSortingEnabled()
        sort_column = header.sortIndicatorSection()
        sort_order = header.sortIndicatorOrder()
        current_source = str(self.source_combo.currentData() or "")
        sources = sorted(
            {entry.snapshot.definition.source for entry in entries}
        )
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.addItem("All sources", "")
        for source in sources:
            self.source_combo.addItem(source, source)
        self.source_combo.setCurrentIndex(max(0, self.source_combo.findData(current_source)))
        self.source_combo.blockSignals(False)
        visible_entries = self._visible_entries(entries)
        self._entries_by_key = {entry.key: entry for entry in entries}
        self._row_items_by_key = {}
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for entry in visible_entries:
            snapshot = entry.snapshot
            definition = snapshot.definition
            reference = entry.output_reference
            row = self.table.rowCount()
            self.table.insertRow(row)
            item = QTableWidgetItem(f"{{{definition.name}}}")
            item.setData(Qt.ItemDataRole.UserRole, entry.key)
            item.setData(Qt.ItemDataRole.UserRole + 1, definition.name)
            self.table.setItem(row, 0, item)
            self._row_items_by_key[entry.key] = item
            self.table.setItem(
                row,
                1,
                QTableWidgetItem(
                    snapshot.display_value
                    if snapshot.available
                    else "Not currently available"
                ),
            )
            self.table.setItem(row, 2, QTableWidgetItem(definition.source))
            self.table.setItem(row, 3, QTableWidgetItem(definition.data_type.value.title()))
            self.table.setItem(
                row,
                4,
                QTableWidgetItem(
                    "Available"
                    if snapshot.available
                    else snapshot.detail or "Not currently available"
                ),
            )
            self.table.setItem(row, 5, QTableWidgetItem(entry.context_label))
            self.table.setItem(
                row, 6, QTableWidgetItem(definition.lifetime_label)
            )
            self.table.setItem(
                row,
                7,
                QTableWidgetItem(
                    "Read/write" if definition.writable else "Read-only"
                ),
            )
        self._rendered_structure = tuple(
            self._entry_structure(entry) for entry in visible_entries
        )
        self.table.setSortingEnabled(sorting_enabled)
        if sorting_enabled and sort_column >= 0:
            self.table.sortItems(sort_column, sort_order)
        else:
            header.setSortIndicator(sort_column, sort_order)
        selected_item = self._row_items_by_key.get(selected)
        if selected_item is not None:
            self.table.selectRow(selected_item.row())
        elif not had_selection and self.table.rowCount():
            self.table.selectRow(0)
        else:
            self.table.clearSelection()
        self.table.verticalScrollBar().setValue(vertical_scroll)
        self.table.horizontalScrollBar().setValue(horizontal_scroll)
        self.table.blockSignals(False)
        self.table.setUpdatesEnabled(True)
        self._selection_changed()

    def refresh_values(self) -> None:
        """Refresh live values without rebuilding an unchanged Variable catalog."""
        entries = self._entries()
        visible_entries = self._visible_entries(entries)
        structure = tuple(self._entry_structure(entry) for entry in visible_entries)
        sources = sorted({entry.snapshot.definition.source for entry in entries})
        rendered_sources = [
            str(self.source_combo.itemData(index) or "")
            for index in range(1, self.source_combo.count())
        ]
        if structure != self._rendered_structure or sources != rendered_sources:
            self.rebuild_catalog()
            return

        selected = self.selected_key()
        vertical_scroll = self.table.verticalScrollBar().value()
        horizontal_scroll = self.table.horizontalScrollBar().value()
        self._entries_by_key = {entry.key: entry for entry in entries}
        self.table.setUpdatesEnabled(False)
        try:
            for entry in visible_entries:
                identity_item = self._row_items_by_key.get(entry.key)
                if identity_item is None:
                    self.rebuild_catalog()
                    return
                row = identity_item.row()
                snapshot = entry.snapshot
                value_item = self.table.item(row, 1)
                status_item = self.table.item(row, 4)
                if value_item is not None:
                    value_item.setText(
                        snapshot.display_value
                        if snapshot.available
                        else "Not currently available"
                    )
                if status_item is not None:
                    status_item.setText(
                        "Available"
                        if snapshot.available
                        else snapshot.detail or "Not currently available"
                    )
            selected_item = self._row_items_by_key.get(selected)
            if selected_item is not None and not selected_item.isSelected():
                self.table.selectRow(selected_item.row())
            self.table.verticalScrollBar().setValue(vertical_scroll)
            self.table.horizontalScrollBar().setValue(horizontal_scroll)
            self._selection_changed()
        finally:
            self.table.setUpdatesEnabled(True)

    def _visible_entries(
        self, entries: tuple[VariablePageEntry, ...]
    ) -> tuple[VariablePageEntry, ...]:
        needle = self.search_edit.text().strip().casefold()
        source = str(self.source_combo.currentData() or "")
        visible: list[VariablePageEntry] = []
        for entry in entries:
            definition = entry.snapshot.definition
            reference = entry.output_reference
            searchable = " ".join(
                (
                    definition.name,
                    definition.display_name,
                    definition.description,
                    definition.source,
                    definition.category,
                    definition.context_label,
                    reference.routine_name if reference else "",
                    reference.task_name if reference else "",
                )
            ).casefold()
            if needle and needle not in searchable:
                continue
            if source and definition.source != source:
                continue
            visible.append(entry)
        return tuple(visible)

    @staticmethod
    def _entry_structure(entry: VariablePageEntry) -> tuple[object, ...]:
        definition = entry.snapshot.definition
        reference = entry.output_reference
        return (
            entry.key,
            definition.name,
            definition.display_name,
            definition.description,
            definition.source,
            definition.category,
            definition.data_type,
            definition.context_label,
            definition.availability,
            definition.writable,
            entry.context_label,
            reference.routine_name if reference else "",
            reference.task_name if reference else "",
        )

    def _entries(self) -> tuple[VariablePageEntry, ...]:
        entries = [
            VariablePageEntry(f"registry:{snapshot.definition.name}", snapshot)
            for snapshot in self.registry.snapshots()
        ]
        if self.routine_store is not None:
            for reference in configured_output_references(
                self.routine_store.routines
            ):
                entries.append(
                    VariablePageEntry(
                        (
                            f"routine:{reference.routine_id}:"
                            f"{reference.task_id}:{reference.definition.name}"
                        ),
                        VariableSnapshot(
                            reference.definition,
                            None,
                            False,
                            "Available only after its producing task runs.",
                        ),
                        reference,
                    )
                )
        return tuple(entries)

    def selected_key(self) -> str:
        selected_rows = self.table.selectionModel().selectedRows()
        row = selected_rows[0].row() if selected_rows else -1
        item = self.table.item(row, 0) if row >= 0 else None
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def selected_name(self) -> str:
        entry = self._entries_by_key.get(self.selected_key())
        return entry.snapshot.definition.name if entry is not None else ""

    def _selection_changed(self) -> None:
        entry = self._entries_by_key.get(self.selected_key())
        if entry is None:
            self.details_label.setText("Select a variable to view its description.")
            return
        snapshot = entry.snapshot
        definition = snapshot.definition
        details = [
            definition.description or "No description.",
            f"Context: {entry.context_label}",
            f"Lifetime: {definition.lifetime_label}",
            f"Current value: {'Available' if snapshot.available else 'Not currently available'}",
        ]
        if not snapshot.available and snapshot.detail:
            details.append(f"Status: {snapshot.detail}")
        reference = entry.output_reference
        if reference is not None:
            details.extend(
                (
                    f"Generated by: {reference.task_name}",
                    f"Routine: {reference.routine_name}",
                    f"Available: After task {reference.task_number}",
                )
            )
            if not reference.routine_enabled or not reference.task_enabled:
                details.append("Configured producer is currently disabled.")
        elif definition.context_label and not snapshot.available:
            details.append(f"Requires: {definition.context_label}")
        self.details_label.setText("\n".join(details))
        custom = definition.name.startswith("custom.")
        self.edit_button.setEnabled(
            reference is None and (custom or definition.writable)
        )
        self.delete_button.setEnabled(custom)

    def _create(self) -> None:
        dialog = CustomVariableDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, value, data_type, description = dialog.values()
        try:
            bare = self.store.validate_custom_name(f"custom.{name}")
            if self.store.scope_of(bare):
                raise ValueError(f'Custom variable "custom.{bare}" already exists.')
            self.store.set("global", bare, value, data_type=data_type, description=description)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Could Not Create Variable", str(error))
            return
        self.variables_changed.emit()
        self.refresh()

    def _edit(self) -> None:
        name = self.selected_name()
        snapshot = self.registry.resolve(name)
        if snapshot is None or not snapshot.definition.writable:
            return
        if not name.startswith("custom."):
            value, accepted = QInputDialog.getText(self, "Set Variable", "Value")
            if accepted:
                try:
                    self.registry.set_value(name, value)
                except (OSError, TypeError, ValueError) as error:
                    QMessageBox.warning(self, "Could Not Update Variable", str(error))
                    return
                self.refresh()
            return
        bare = name.removeprefix("custom.")
        dialog = CustomVariableDialog(
            name=bare,
            value=snapshot.display_value,
            data_type=self.store.type_of(bare),
            description=self.store.description_of(bare),
            editing=True,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        _name, value, data_type, description = dialog.values()
        try:
            self.store.set(self.store.scope_of(bare) or "global", bare, value, data_type=data_type, description=description)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Could Not Update Variable", str(error))
            return
        self.variables_changed.emit()
        self.refresh()

    def _delete(self) -> None:
        name = self.selected_name()
        if not name.startswith("custom."):
            return
        if QMessageBox.question(self, "Delete Variable", f"Delete {{{name}}}?") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.delete(name)
        except OSError as error:
            QMessageBox.warning(self, "Could Not Delete Variable", str(error))
            return
        self.variables_changed.emit()
        self.refresh()

    def _copy_placeholder(self) -> None:
        name = self.selected_name()
        if name:
            QApplication.clipboard().setText(f"{{{name}}}")

    def _open_picker(self) -> None:
        VariablePickerDialog(self.registry, parent=self).exec()
