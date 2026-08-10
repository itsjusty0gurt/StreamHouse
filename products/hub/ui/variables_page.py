from __future__ import annotations

from PySide6.QtCore import Qt, Signal
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
from products.hub.automation.variable_registry import VariableDataType, VariableRegistry
from products.hub.ui.variable_picker import VariablePickerDialog


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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.registry = registry
        self.store = store
        layout = QVBoxLayout(self)
        toolbar = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search variables...")
        self.source_combo = QComboBox()
        self.new_button = QPushButton("+ New Custom Variable")
        self.edit_button = QPushButton("Edit")
        self.delete_button = QPushButton("Delete")
        self.picker_button = QPushButton("{x} Variable Picker")
        toolbar.addWidget(self.search_edit, 1)
        toolbar.addWidget(self.source_combo)
        toolbar.addWidget(self.picker_button)
        toolbar.addWidget(self.new_button)
        layout.addLayout(toolbar)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ("Name", "Value", "Source", "Type", "Access")
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table, 1)
        self.details_label = QLabel("Select a variable to view its description.")
        self.details_label.setWordWrap(True)
        actions = QHBoxLayout()
        self.copy_placeholder_button = QPushButton("Copy Placeholder")
        self.copy_name_button = QPushButton("Copy Name")
        actions.addWidget(self.copy_placeholder_button)
        actions.addWidget(self.copy_name_button)
        actions.addWidget(self.edit_button)
        actions.addWidget(self.delete_button)
        actions.addStretch()
        layout.addWidget(self.details_label)
        layout.addLayout(actions)
        self.search_edit.textChanged.connect(self.refresh)
        self.source_combo.currentIndexChanged.connect(self.refresh)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.copy_placeholder_button.clicked.connect(self._copy_placeholder)
        self.copy_name_button.clicked.connect(self._copy_name)
        self.new_button.clicked.connect(self._create)
        self.edit_button.clicked.connect(self._edit)
        self.delete_button.clicked.connect(self._delete)
        self.picker_button.clicked.connect(self._open_picker)
        self.refresh()

    def refresh(self) -> None:
        current_source = str(self.source_combo.currentData() or "")
        sources = sorted({item.source for item in self.registry.definitions()})
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.addItem("All sources", "")
        for source in sources:
            self.source_combo.addItem(source, source)
        self.source_combo.setCurrentIndex(max(0, self.source_combo.findData(current_source)))
        self.source_combo.blockSignals(False)
        selected = self.selected_name()
        needle = self.search_edit.text().strip().casefold()
        source = str(self.source_combo.currentData() or "")
        self.table.setRowCount(0)
        for snapshot in self.registry.snapshots():
            definition = snapshot.definition
            if needle and needle not in (
                f"{definition.name} {definition.display_name} {definition.description}"
            ).casefold():
                continue
            if source and definition.source != source:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            item = QTableWidgetItem(definition.name)
            item.setData(Qt.ItemDataRole.UserRole, definition.name)
            self.table.setItem(row, 0, item)
            self.table.setItem(row, 1, QTableWidgetItem(snapshot.display_value))
            self.table.setItem(row, 2, QTableWidgetItem(definition.source))
            self.table.setItem(row, 3, QTableWidgetItem(definition.data_type.value.title()))
            self.table.setItem(row, 4, QTableWidgetItem("Read/write" if definition.writable else "Read-only"))
            if definition.name == selected:
                self.table.selectRow(row)
        if self.table.rowCount() and self.table.currentRow() < 0:
            self.table.selectRow(0)

    def selected_name(self) -> str:
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def _selection_changed(self) -> None:
        snapshot = self.registry.resolve(self.selected_name())
        if snapshot is None:
            return
        definition = snapshot.definition
        self.details_label.setText(
            f"{definition.description or 'No description.'}\n"
            f"{definition.availability.value.title()} - "
            f"{'Read/write' if definition.writable else 'Read-only'}"
        )
        custom = definition.name.startswith("custom.")
        self.edit_button.setEnabled(custom or definition.writable)
        self.delete_button.setEnabled(custom)

    def _create(self) -> None:
        dialog = CustomVariableDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, value, data_type, description = dialog.values()
        try:
            bare = self.store.validate_custom_name(name)
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

    def _copy_name(self) -> None:
        name = self.selected_name()
        if name:
            QApplication.clipboard().setText(name)

    def _open_picker(self) -> None:
        VariablePickerDialog(self.registry, parent=self).exec()
