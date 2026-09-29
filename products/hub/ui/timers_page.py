from __future__ import annotations

from collections.abc import Callable
from math import ceil

from PySide6.QtCore import QEvent, QObject, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from products.hub.automation.core_triggers import (
    CoreAutomationTrigger,
    CoreTriggerStore,
)
from products.hub.automation.routines import RoutineDefinition, RoutineStore
from products.hub.automation.timer_scheduler import AutomationTimerScheduler
from products.hub.ui.automation_page import TimerTriggerDialog
from products.hub.ui.page_header import PageHeader
from shared.streamhouse_shared.responsive import responsive_grid_columns


class RoutineTargetDialog(QDialog):
    """Select the normal Routine that will own a new Timer trigger."""

    CREATE_NEW = "__create_new_routine__"

    def __init__(self, routine_store: RoutineStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Choose Routine")
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Which routine should this Timer start?"))
        self.routine_combo = QComboBox(self)
        for routine in sorted(routine_store.routines, key=lambda item: item.name.casefold()):
            group = routine_store.get_group(routine.group_id)
            suffix = f" - {group.name}" if group is not None else ""
            self.routine_combo.addItem(f"{routine.name}{suffix}", routine.routine_id)
        self.routine_combo.addItem("Create a new routine...", self.CREATE_NEW)
        layout.addWidget(self.routine_combo)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def target(self) -> str:
        return str(self.routine_combo.currentData())


class TimerCard(QFrame):
    """Stable Timer presentation widget; countdown changes never rebuild it."""

    edit_requested = Signal(str)
    toggle_requested = Signal(str)
    delete_requested = Signal(str)
    open_routine_requested = Signal(str)

    def __init__(self, trigger_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.trigger_id = trigger_id
        self.routine_id = ""
        self.setObjectName("timerCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(5)
        top = QHBoxLayout()
        self.title_label = QLabel("Timer", self)
        title_font = self.title_label.font()
        title_font.setBold(True)
        title_font.setPointSize(title_font.pointSize() + 2)
        self.title_label.setFont(title_font)
        self.state_label = QLabel(self)
        top.addWidget(self.title_label, 1)
        top.addWidget(self.state_label)
        layout.addLayout(top)
        self.description_label = QLabel(self)
        self.description_label.setObjectName("timerDescription")
        self.description_label.setStyleSheet("color:#d8d8de;")
        layout.addWidget(self.description_label)
        self.remaining_label = QLabel("Starting…", self)
        remaining_font = self.remaining_label.font()
        remaining_font.setBold(True)
        self.remaining_label.setFont(remaining_font)
        layout.addWidget(self.remaining_label)
        self.details_label = QLabel("Recurring", self)
        self.details_label.setStyleSheet("color:#b8b8c2;")
        layout.addWidget(self.details_label)
        self.used_by_label = QLabel(self)
        self.used_by_label.setWordWrap(True)
        layout.addWidget(self.used_by_label)
        actions = QHBoxLayout()
        self.open_button = QPushButton("Open Routine", self)
        self.edit_button = QPushButton("Edit", self)
        self.toggle_button = QPushButton(self)
        self.delete_button = QPushButton("Delete", self)
        actions.addWidget(self.open_button)
        actions.addStretch(1)
        actions.addWidget(self.edit_button)
        actions.addWidget(self.toggle_button)
        actions.addWidget(self.delete_button)
        layout.addLayout(actions)
        self.open_button.clicked.connect(
            lambda: self.open_routine_requested.emit(self.routine_id)
        )
        self.edit_button.clicked.connect(lambda: self.edit_requested.emit(self.trigger_id))
        self.toggle_button.clicked.connect(
            lambda: self.toggle_requested.emit(self.trigger_id)
        )
        self.delete_button.clicked.connect(
            lambda: self.delete_requested.emit(self.trigger_id)
        )

    def update_content(
        self,
        trigger: CoreAutomationTrigger,
        routine: RoutineDefinition | None,
        description: str,
    ) -> None:
        self.routine_id = trigger.routine_id
        self.title_label.setText(routine.name if routine is not None else "Timer")
        self.description_label.setText(description)
        self.state_label.setText("Enabled" if trigger.enabled else "Disabled")
        self.state_label.setStyleSheet(
            "color:#7acb8a; font-weight:600;"
            if trigger.enabled
            else "color:#adadb8; font-weight:600;"
        )
        self.toggle_button.setText("Disable" if trigger.enabled else "Enable")
        self.used_by_label.setText(
            f"Used by: {routine.name}"
            if routine is not None
            else "Used by: Missing routine"
        )
        self.open_button.setEnabled(routine is not None)

    def set_runtime_text(self, text: str) -> None:
        self.remaining_label.setText(text)


class TimersPage(QWidget):
    """Management/status view over existing Core Timer triggers and scheduler."""

    MIN_CARD_WIDTH = 330
    GRID_GAP = 10
    GRID_HYSTERESIS = 24
    structure_refresh_requested = Signal()
    timers_changed = Signal()
    open_routine_requested = Signal(str)

    def __init__(
        self,
        routine_store: RoutineStore,
        trigger_store: CoreTriggerStore,
        scheduler: AutomationTimerScheduler,
        *,
        create_routine: Callable[[], RoutineDefinition | None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.routine_store = routine_store
        self.trigger_store = trigger_store
        self.scheduler = scheduler
        self.create_routine = create_routine
        self.cards: dict[str, TimerCard] = {}
        self._shutting_down = False
        self._columns = 0
        self._visible_trigger_ids: tuple[str, ...] = ()

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(10)
        self.header = PageHeader(
            "Timers",
            "Create and manage the recurring Timer triggers used by your routines.",
            self,
        )
        self.add_button = QPushButton("+ Timer", self)
        self.header.add_action(self.add_button)
        root.addWidget(self.header)
        filters = QHBoxLayout()
        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText("Search timers or routines")
        self.filter_combo = QComboBox(self)
        self.filter_combo.addItem("All", "all")
        self.filter_combo.addItem("Enabled", "enabled")
        self.filter_combo.addItem("Disabled", "disabled")
        filters.addWidget(self.search_edit, 1)
        filters.addWidget(self.filter_combo)
        root.addLayout(filters)
        self.empty_label = QLabel(
            "No timers yet. Create one here or add a Timer trigger to a routine.",
            self,
        )
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_label.setWordWrap(True)
        root.addWidget(self.empty_label, 1)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.viewport().installEventFilter(self)
        self.scroll_content = QWidget(self.scroll_area)
        self.card_layout = QGridLayout(self.scroll_content)
        self.card_layout.setContentsMargins(0, 0, 0, 0)
        self.card_layout.setHorizontalSpacing(self.GRID_GAP)
        self.card_layout.setVerticalSpacing(self.GRID_GAP)
        self.card_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_area.setWidget(self.scroll_content)
        root.addWidget(self.scroll_area, 1)

        self.add_button.clicked.connect(self.add_timer)
        self.search_edit.textChanged.connect(self._apply_filter)
        self.filter_combo.currentIndexChanged.connect(self._apply_filter)
        self.structure_refresh_requested.connect(self.synchronize)
        self.trigger_store.subscribe_changes(self._store_changed)
        self.countdown_timer = QTimer(self)
        self.countdown_timer.setInterval(250)
        self.countdown_timer.timeout.connect(self.refresh_countdowns)
        self.countdown_timer.start()
        self.synchronize()

    def _store_changed(self) -> None:
        self.structure_refresh_requested.emit()

    @Slot()
    def synchronize(self) -> None:
        if self._shutting_down:
            return
        timers = {
            trigger.trigger_id: trigger
            for trigger in self.trigger_store.triggers
            if trigger.event_type == "timer"
        }
        for trigger_id in tuple(self.cards):
            if trigger_id in timers:
                continue
            card = self.cards.pop(trigger_id)
            self.card_layout.removeWidget(card)
            card.deleteLater()
        for trigger_id, trigger in timers.items():
            card = self.cards.get(trigger_id)
            if card is None:
                card = TimerCard(trigger_id, self.scroll_content)
                card.edit_requested.connect(self.edit_timer)
                card.toggle_requested.connect(self.toggle_timer)
                card.delete_requested.connect(self.delete_timer)
                card.open_routine_requested.connect(self.open_routine_requested.emit)
                self.cards[trigger_id] = card
            routine = self.routine_store.get(trigger.routine_id)
            card.update_content(
                trigger,
                routine,
                self.trigger_store.timer_description(trigger),
            )
        self._apply_filter()
        self.refresh_countdowns()

    @Slot()
    def refresh_countdowns(self) -> None:
        if self._shutting_down:
            return
        for trigger_id, card in tuple(self.cards.items()):
            trigger = self.trigger_store.get(trigger_id)
            if trigger is None:
                continue
            if not trigger.enabled:
                card.set_runtime_text("Disabled")
                continue
            status = self.scheduler.status(trigger_id)
            if not status.running:
                card.set_runtime_text("Starting…")
            elif not status.scheduled or status.remaining_seconds is None:
                card.set_runtime_text("Not scheduled")
            else:
                card.set_runtime_text(
                    f"Next run in {self._format_remaining(status.remaining_seconds)}"
                )

    @staticmethod
    def _format_remaining(seconds: float) -> str:
        total = max(ceil(seconds), 0)
        hours, remainder = divmod(total, 3600)
        minutes, seconds_value = divmod(remainder, 60)
        return (
            f"{hours}:{minutes:02d}:{seconds_value:02d}"
            if hours
            else f"{minutes:02d}:{seconds_value:02d}"
        )

    def _apply_filter(self, *_args: object) -> None:
        query = self.search_edit.text().strip().casefold()
        state = str(self.filter_combo.currentData())
        visible_ids: list[str] = []
        for trigger_id, card in self.cards.items():
            trigger = self.trigger_store.get(trigger_id)
            routine = self.routine_store.get(trigger.routine_id) if trigger else None
            matches_state = bool(
                trigger
                and (
                    state == "all"
                    or (state == "enabled" and trigger.enabled)
                    or (state == "disabled" and not trigger.enabled)
                )
            )
            haystack = " ".join(
                (
                    card.title_label.text(),
                    card.description_label.text(),
                    routine.name if routine is not None else "",
                )
            ).casefold()
            show = matches_state and (not query or query in haystack)
            if show:
                visible_ids.append(trigger_id)
        visible = len(visible_ids)
        self._visible_trigger_ids = tuple(visible_ids)
        self._reflow(force=True)
        visible_set = set(visible_ids)
        for trigger_id, card in self.cards.items():
            card.setVisible(trigger_id in visible_set)
        self.empty_label.setText(
            "No timers match your search."
            if self.cards and visible == 0
            else "No timers yet. Create one here or add a Timer trigger to a routine."
        )
        self.empty_label.setVisible(visible == 0)
        self.scroll_area.setVisible(visible > 0)

    @classmethod
    def columns_for_width(cls, width: int, *, current: int = 0) -> int:
        return responsive_grid_columns(
            width,
            cls.MIN_CARD_WIDTH,
            cls.GRID_GAP,
            current=current,
            hysteresis=cls.GRID_HYSTERESIS,
        )

    def _reflow(self, *, force: bool = False) -> None:
        columns = self.columns_for_width(
            self.scroll_area.viewport().width(),
            current=self._columns,
        )
        if not force and columns == self._columns:
            return
        previous_columns = self._columns
        self._columns = columns
        for card in self.cards.values():
            self.card_layout.removeWidget(card)
        for position, trigger_id in enumerate(self._visible_trigger_ids):
            self.card_layout.addWidget(
                self.cards[trigger_id],
                position // columns,
                position % columns,
            )
        for column in range(max(previous_columns, columns)):
            self.card_layout.setColumnStretch(column, 0)
        for column in range(columns):
            self.card_layout.setColumnStretch(column, 1)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        self._reflow()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if (
            watched is self.scroll_area.viewport()
            and event.type() == QEvent.Type.Resize
        ):
            self._reflow()
        return super().eventFilter(watched, event)

    @Slot()
    def add_timer(self) -> None:
        target_dialog = RoutineTargetDialog(self.routine_store, self)
        if target_dialog.exec() != QDialog.DialogCode.Accepted:
            return
        target = target_dialog.target()
        routine = None
        if target == RoutineTargetDialog.CREATE_NEW:
            if self.create_routine is None:
                return
            routine = self.create_routine()
        else:
            routine = self.routine_store.get(target)
        if routine is None:
            return
        dialog = TimerTriggerDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.trigger_store.add_timer(routine.routine_id, **dialog.values())
        except (OSError, TypeError, ValueError) as error:
            QMessageBox.warning(self, "Could Not Create Timer", str(error))
            return
        self.timers_changed.emit()

    @Slot(str)
    def edit_timer(self, trigger_id: str) -> None:
        trigger = self.trigger_store.get(trigger_id)
        if trigger is None:
            return
        dialog = TimerTriggerDialog(self, trigger)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.trigger_store.update_timer(trigger_id, **dialog.values())
        except (OSError, TypeError, ValueError) as error:
            QMessageBox.warning(self, "Could Not Update Timer", str(error))
            return
        self.timers_changed.emit()

    @Slot(str)
    def toggle_timer(self, trigger_id: str) -> None:
        trigger = self.trigger_store.get(trigger_id)
        if trigger is None:
            return
        try:
            self.trigger_store.update_timer(
                trigger_id,
                timer_mode=trigger.timer_mode,
                timer_minimum=trigger.timer_minimum,
                timer_minimum_unit=trigger.timer_minimum_unit,
                timer_maximum=trigger.timer_maximum,
                timer_maximum_unit=trigger.timer_maximum_unit,
                enabled=not trigger.enabled,
            )
        except (OSError, TypeError, ValueError) as error:
            QMessageBox.warning(self, "Could Not Update Timer", str(error))
            return
        self.timers_changed.emit()

    @Slot(str)
    def delete_timer(self, trigger_id: str) -> None:
        trigger = self.trigger_store.get(trigger_id)
        if trigger is None:
            return
        routine = self.routine_store.get(trigger.routine_id)
        used_by = routine.name if routine is not None else "a missing routine"
        answer = QMessageBox.question(
            self,
            "Delete Timer",
            f"Delete this Timer trigger used by {used_by}?",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.trigger_store.delete(trigger_id)
        except OSError as error:
            QMessageBox.warning(self, "Could Not Delete Timer", str(error))
            return
        self.timers_changed.emit()

    def refresh(self) -> None:
        self.synchronize()

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self.countdown_timer.stop()
        self.trigger_store.unsubscribe_changes(self._store_changed)

