from __future__ import annotations

from collections.abc import Callable, Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from products.hub.automation.conditions import (
    CONDITION_JOIN_ALL,
    ConditionDefinition,
    condition_definitions,
    condition_join_mode,
)
from products.hub.automation.logic_tasks import (
    IF_COMPARISON_CHOICES,
    IF_UNARY_OPERATORS,
)
from products.hub.automation.variable_registry import VariableDefinition, VariableRegistry
from products.hub.ui.variable_picker import VariablePickerButton


class ConditionRowWidget(QFrame):
    """One streamer-facing condition row with canonical Variable insertion."""

    remove_requested = Signal(object)

    def __init__(
        self,
        condition: ConditionDefinition,
        *,
        registry: VariableRegistry | None = None,
        context: Mapping[str, object] | None = None,
        extra_definitions: tuple[VariableDefinition, ...] = (),
        user_groups=None,
        fixed_subject: str = "",
        allow_user_groups: bool = True,
        variable_filter: Callable[[VariableDefinition], bool] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.user_groups = user_groups
        self.allow_user_groups = allow_user_groups
        self.setObjectName("conditionRow")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setStyleSheet(
            "QFrame#conditionRow { border: 1px solid palette(mid); border-radius: 6px; }"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 10)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)

        self.join_label = QLabel("", self)
        self.join_label.setStyleSheet("color:#bf94ff; font-weight:700;")
        header.addWidget(self.join_label)
        header.addStretch(1)

        self.remove_button = QToolButton(self)
        self.remove_button.setText("×")
        self.remove_button.setToolTip("Remove this condition")
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(self))
        header.addWidget(self.remove_button)
        layout.addLayout(header)

        self.left_label = QLabel("Value / Variable", self)
        layout.addWidget(self.left_label)
        left_controls = QHBoxLayout()
        left_controls.setContentsMargins(0, 0, 0, 0)
        left_controls.setSpacing(6)

        self.left_edit = QLineEdit(condition.left or fixed_subject, self)
        self.left_edit.setPlaceholderText("Value or {variable.name}")
        self.left_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        if fixed_subject:
            self.left_edit.setText(fixed_subject)
            self.left_edit.setReadOnly(True)
            self.left_edit.setToolTip("ELSE IF uses the same subject as IF.")
        left_controls.addWidget(self.left_edit, 1)
        self.left_picker = VariablePickerButton(
            registry,
            self.left_edit,
            context,
            self,
            extra_definitions=extra_definitions,
            definition_filter=variable_filter,
        )
        self.left_picker.setText("{x}")
        self.left_picker.setVisible(not bool(fixed_subject))
        left_controls.addWidget(self.left_picker)
        layout.addLayout(left_controls)

        self.operator_label = QLabel("Comparison", self)
        layout.addWidget(self.operator_label)
        self.operator_combo = QComboBox(self)
        for label, value in IF_COMPARISON_CHOICES:
            if value == "user_in_group" and not allow_user_groups:
                continue
            self.operator_combo.addItem(label, value)
        self.operator_combo.setCurrentIndex(
            max(self.operator_combo.findData(condition.operator), 0)
        )
        self.operator_combo.setMinimumContentsLength(28)
        self.operator_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.operator_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        layout.addWidget(self.operator_combo)

        self.right_label = QLabel("Compare To", self)
        layout.addWidget(self.right_label)
        right_controls = QHBoxLayout()
        right_controls.setContentsMargins(0, 0, 0, 0)
        right_controls.setSpacing(6)
        self.right_edit = QLineEdit(condition.right, self)
        self.right_edit.setPlaceholderText("Value or {variable.name}")
        self.right_edit.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        right_controls.addWidget(self.right_edit, 1)
        self.right_picker = VariablePickerButton(
            registry,
            self.right_edit,
            context,
            self,
            extra_definitions=extra_definitions,
            definition_filter=variable_filter,
        )
        self.right_picker.setText("{x}")
        right_controls.addWidget(self.right_picker)
        layout.addLayout(right_controls)

        self.group_label = QLabel("User Group", self)
        layout.addWidget(self.group_label)
        self.group_combo = QComboBox(self)
        self.group_combo.addItem("Choose a user group…", "")
        if user_groups is not None:
            for group in user_groups.list_groups():
                self.group_combo.addItem(group.name, group.group_id)
        self.group_combo.setCurrentIndex(
            max(self.group_combo.findData(condition.group_id), 0)
        )
        self.group_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        layout.addWidget(self.group_combo)

        self.ignore_case_check = QCheckBox("Ignore case", self)
        self.ignore_case_check.setChecked(condition.ignore_case)
        layout.addWidget(self.ignore_case_check)

        self.operator_combo.currentIndexChanged.connect(self._update_operator)
        self._update_operator()

    def _update_operator(self, _index: int = -1) -> None:
        operation = str(self.operator_combo.currentData() or "equals")
        membership = operation == "user_in_group"
        self.left_label.setVisible(not membership)
        self.left_edit.setVisible(not membership)
        self.left_picker.setVisible(not membership and not self.left_edit.isReadOnly())
        self.right_label.setVisible(
            not membership and operation not in IF_UNARY_OPERATORS
        )
        self.right_edit.setVisible(not membership and operation not in IF_UNARY_OPERATORS)
        self.right_edit.setEnabled(not membership and operation not in IF_UNARY_OPERATORS)
        self.right_picker.setVisible(
            not membership and operation not in IF_UNARY_OPERATORS
        )
        self.right_picker.setEnabled(
            not membership and operation not in IF_UNARY_OPERATORS
        )
        self.group_label.setVisible(membership)
        self.group_combo.setVisible(membership)
        self.ignore_case_check.setVisible(
            not membership and operation not in IF_UNARY_OPERATORS
        )

    def value(self) -> ConditionDefinition:
        return ConditionDefinition(
            left=self.left_edit.text().strip(),
            operator=str(self.operator_combo.currentData() or "equals"),
            right=self.right_edit.text().strip(),
            ignore_case=self.ignore_case_check.isChecked(),
            group_id=str(self.group_combo.currentData() or ""),
        )


class ConditionBuilderWidget(QWidget):
    """Reusable flat AND/OR condition builder for tasks and future triggers."""

    def __init__(
        self,
        config: Mapping[str, object] | None = None,
        *,
        registry: VariableRegistry | None = None,
        context: Mapping[str, object] | None = None,
        extra_definitions: tuple[VariableDefinition, ...] = (),
        user_groups=None,
        fixed_subject: str = "",
        allow_user_groups: bool = True,
        variable_filter: Callable[[VariableDefinition], bool] | None = None,
        maximum_conditions: int = 12,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.registry = registry
        self.context = dict(context or {})
        self.extra_definitions = extra_definitions
        self.user_groups = user_groups
        self.fixed_subject = fixed_subject
        self.allow_user_groups = allow_user_groups
        self.variable_filter = variable_filter
        self.maximum_conditions = maximum_conditions
        self.rows: list[ConditionRowWidget] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Match", self))
        self.join_combo = QComboBox(self)
        self.join_combo.addItem("All conditions (AND)", "and")
        self.join_combo.addItem("Any condition (OR)", "or")
        self.join_combo.setMinimumContentsLength(20)
        controls.addWidget(self.join_combo)
        controls.addStretch(1)
        self.add_button = QPushButton("+ Add Condition", self)
        controls.addWidget(self.add_button)
        layout.addLayout(controls)

        self.rows_scroll = QScrollArea(self)
        self.rows_scroll.setObjectName("conditionRowsScroll")
        self.rows_scroll.setWidgetResizable(True)
        self.rows_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.rows_scroll.setMinimumHeight(280)
        self.rows_container = QWidget(self.rows_scroll)
        self.rows_layout = QVBoxLayout(self.rows_container)
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(8)
        self.rows_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.rows_scroll.setWidget(self.rows_container)
        layout.addWidget(self.rows_scroll, 1)

        values = dict(config or {})
        self.join_combo.setCurrentIndex(
            max(self.join_combo.findData(condition_join_mode(values)), 0)
        )
        for condition in condition_definitions(values):
            self.add_condition(condition)
        if not self.rows:
            self.add_condition(ConditionDefinition(left=fixed_subject))
        self.add_button.clicked.connect(
            lambda: self.add_condition(ConditionDefinition(left=fixed_subject))
        )
        self.join_combo.currentIndexChanged.connect(self._refresh_join_labels)
        self._refresh_join_labels()

    def add_condition(self, condition: ConditionDefinition) -> None:
        if len(self.rows) >= self.maximum_conditions:
            return
        row = ConditionRowWidget(
            condition,
            registry=self.registry,
            context=self.context,
            extra_definitions=self.extra_definitions,
            user_groups=self.user_groups,
            fixed_subject=self.fixed_subject,
            allow_user_groups=self.allow_user_groups,
            variable_filter=self.variable_filter,
            parent=self,
        )
        row.remove_requested.connect(self.remove_condition)
        self.rows.append(row)
        self.rows_layout.addWidget(row)
        self._refresh_join_labels()
        self.add_button.setEnabled(len(self.rows) < self.maximum_conditions)

    def remove_condition(self, row: ConditionRowWidget) -> None:
        if len(self.rows) <= 1:
            return
        self.rows.remove(row)
        row.deleteLater()
        self._refresh_join_labels()
        self.add_button.setEnabled(True)

    def _refresh_join_labels(self, _index: int = -1) -> None:
        label = "AND" if self.join_mode() == CONDITION_JOIN_ALL else "OR"
        for index, row in enumerate(self.rows):
            row.join_label.setText("IF" if index == 0 else label)
            row.remove_button.setEnabled(len(self.rows) > 1)

    def join_mode(self) -> str:
        return str(self.join_combo.currentData() or CONDITION_JOIN_ALL)

    def value(self) -> dict[str, object]:
        conditions = [row.value() for row in self.rows]
        for condition in conditions:
            if condition.operator == "user_in_group":
                if not condition.group_id:
                    raise ValueError("Choose a user group for every group condition.")
            elif not condition.left:
                raise ValueError("Every condition needs a left value or Variable.")
        return {
            "condition_join": self.join_mode(),
            "conditions": [condition.to_dict() for condition in conditions],
        }

    def first_row_widgets(self) -> dict[str, QWidget]:
        row = self.rows[0]
        return {
            "left": row.left_edit,
            "operator": row.operator_combo,
            "right": row.right_edit,
            "group_id": row.group_combo,
        }
