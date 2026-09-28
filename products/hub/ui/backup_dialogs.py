from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QLabel,
    QVBoxLayout,
)

from products.hub.core.backup import (
    COMPONENT_LABELS,
    BackupComponent,
    BackupInspection,
    BackupManager,
    BackupPreset,
)


class BackupJobSignals(QObject):
    completed = Signal(object)
    failed = Signal(str)


class BackupJob(QRunnable):
    """Run archive IO outside the Qt UI thread."""

    def __init__(self, operation: Callable[[], object]) -> None:
        super().__init__()
        self.operation = operation
        self.signals = BackupJobSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.operation()
        except Exception as error:
            self.signals.failed.emit(str(error) or type(error).__name__)
            return
        self.signals.completed.emit(result)


class BackupSelectionDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create Streamhouse Backup")
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        explanation = QLabel(
            "Choose a preset or Custom components. Credentials, chat history, "
            "logs, crash reports, Support Bundles, and machine window geometry "
            "are never eligible."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.preset_combo = QComboBox(self)
        for label, preset in (
            ("Recommended", BackupPreset.RECOMMENDED),
            ("Configuration Only", BackupPreset.CONFIGURATION_ONLY),
            ("Everything Eligible", BackupPreset.EVERYTHING_ELIGIBLE),
            ("Custom", BackupPreset.CUSTOM),
        ):
            self.preset_combo.addItem(label, preset.value)
        layout.addWidget(self.preset_combo)
        component_group = QGroupBox("Components", self)
        component_layout = QVBoxLayout(component_group)
        self.component_checks: dict[BackupComponent, QCheckBox] = {}
        for component in BackupComponent:
            check = QCheckBox(COMPONENT_LABELS[component], component_group)
            self.component_checks[component] = check
            component_layout.addWidget(check)
        layout.addWidget(component_group)
        self.summary_label = QLabel(self)
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Save,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Create Backup")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.preset_combo.currentIndexChanged.connect(self._preset_changed)
        for check in self.component_checks.values():
            check.toggled.connect(self._selection_changed)
        self._preset_changed()

    def preset(self) -> BackupPreset:
        return BackupPreset(str(self.preset_combo.currentData()))

    def selected_components(self) -> tuple[BackupComponent, ...]:
        return tuple(
            component
            for component, check in self.component_checks.items()
            if check.isChecked()
        )

    def _preset_changed(self) -> None:
        preset = self.preset()
        custom = preset is BackupPreset.CUSTOM
        selected = (
            set(self.selected_components())
            if custom
            else set(BackupManager.components_for(preset))
        )
        for component, check in self.component_checks.items():
            check.blockSignals(True)
            check.setChecked(component in selected)
            check.setEnabled(custom)
            check.blockSignals(False)
        self._selection_changed()

    def _selection_changed(self) -> None:
        if self.preset() is BackupPreset.CUSTOM:
            values = set(self.selected_components())
            if BackupComponent.COUNTER_VALUES in values:
                definition = self.component_checks[
                    BackupComponent.COUNTER_DEFINITIONS
                ]
                definition.blockSignals(True)
                definition.setChecked(True)
                definition.blockSignals(False)
                values.add(BackupComponent.COUNTER_DEFINITIONS)
        else:
            values = set(BackupManager.components_for(self.preset()))
        labels = [COMPONENT_LABELS[item] for item in BackupComponent if item in values]
        self.summary_label.setText(
            "This backup will include:\n• " + "\n• ".join(labels)
            if labels
            else "Select at least one component."
        )

    def accept(self) -> None:
        if self.preset() is BackupPreset.CUSTOM and not self.selected_components():
            self.summary_label.setText("Select at least one component.")
            return
        super().accept()


class RestoreSelectionDialog(QDialog):
    def __init__(self, inspection: BackupInspection, parent=None) -> None:
        super().__init__(parent)
        self.inspection = inspection
        self.setWindowTitle("Restore Streamhouse Backup")
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        details = QLabel(
            f"Created: {inspection.created_at}\n"
            f"Hub version: {inspection.hub_version}\n"
            f"Backup type: {inspection.backup_type}\n\n"
            "Selected components replace current component data. A private "
            "safety backup is required before any changes are committed."
        )
        details.setWordWrap(True)
        layout.addWidget(details)
        group = QGroupBox("Restore components", self)
        group_layout = QVBoxLayout(group)
        self.component_checks: dict[BackupComponent, QCheckBox] = {}
        for component in inspection.components:
            schema = inspection.schemas[component.value]
            check = QCheckBox(
                f"{COMPONENT_LABELS[component]} (schema v{schema})", group
            )
            check.setChecked(True)
            check.toggled.connect(self._selection_changed)
            self.component_checks[component] = check
            group_layout.addWidget(check)
        layout.addWidget(group)
        self.warning_label = QLabel(
            "Required dependencies are selected automatically. Hub reloads the "
            "restored stores after completion; restart if prompted."
        )
        self.warning_label.setWordWrap(True)
        layout.addWidget(self.warning_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel
            | QDialogButtonBox.StandardButton.Ok,
            parent=self,
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Restore Selected")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._selection_changed()

    def selected_components(self) -> tuple[BackupComponent, ...]:
        return tuple(
            component
            for component, check in self.component_checks.items()
            if check.isChecked()
        )

    def _selection_changed(self) -> None:
        values = set(self.selected_components())
        if BackupComponent.COUNTER_VALUES in values:
            definition = self.component_checks.get(
                BackupComponent.COUNTER_DEFINITIONS
            )
            if definition is not None:
                definition.blockSignals(True)
                definition.setChecked(True)
                definition.setEnabled(False)
                definition.blockSignals(False)
                return
        definition = self.component_checks.get(BackupComponent.COUNTER_DEFINITIONS)
        if definition is not None:
            definition.setEnabled(True)

    def accept(self) -> None:
        if not self.selected_components():
            self.warning_label.setText("Select at least one component to restore.")
            return
        super().accept()
