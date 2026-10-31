from __future__ import annotations

from collections.abc import Callable, Iterable

from PySide6.QtCore import QByteArray, QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from products.hub.automation.quick_actions import QuickAction, QuickActionStore
from products.hub.modules.contributions import ModuleDockContribution


_DOCK_AREAS = {
    "left": Qt.DockWidgetArea.LeftDockWidgetArea,
    "right": Qt.DockWidgetArea.RightDockWidgetArea,
    "top": Qt.DockWidgetArea.TopDockWidgetArea,
    "bottom": Qt.DockWidgetArea.BottomDockWidgetArea,
}


class QuickActionDialog(QDialog):
    def __init__(self, routines: Iterable[object], action: QuickAction | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Quick Action" if action else "Add Quick Action")
        layout = QFormLayout(self)
        self.label_edit = QLineEdit(action.label if action else "", self)
        self.routine_combo = QComboBox(self)
        for routine in routines:
            self.routine_combo.addItem(str(routine.name), str(routine.routine_id))
        if action is not None:
            index = self.routine_combo.findData(action.routine_id)
            if index >= 0:
                self.routine_combo.setCurrentIndex(index)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow("Label", self.label_edit)
        layout.addRow("Routine", self.routine_combo)
        layout.addRow(buttons)

    def values(self) -> tuple[str, str]:
        return self.label_edit.text(), str(self.routine_combo.currentData() or "")


class QuickActionsPanel(QWidget):
    routine_requested = Signal(str)

    def __init__(self, store: QuickActionStore, routine_store: object, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.routine_store = routine_store
        self._buttons: dict[str, QPushButton] = {}
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        header.addWidget(QLabel("Run frequently used routines without leaving Live.", self), 1)
        self.add_button = QPushButton("Add", self)
        self.add_button.clicked.connect(self.add_action)
        header.addWidget(self.add_button)
        layout.addLayout(header)
        self.grid = QGridLayout()
        layout.addLayout(self.grid)
        layout.addStretch()
        try:
            self.store.load()
        except (OSError, TypeError, ValueError):
            self.store.actions = []
        self.refresh()

    def refresh(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._buttons.clear()
        for index, action in enumerate(self.store.actions):
            button = QPushButton(action.label, self)
            button.setObjectName(f"quickAction_{action.action_id}")
            button.clicked.connect(
                lambda _checked=False, routine_id=action.routine_id: self.routine_requested.emit(routine_id)
            )
            button.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            button.customContextMenuRequested.connect(
                lambda point, action_id=action.action_id, source=button: self._show_menu(action_id, source, point)
            )
            self.grid.addWidget(button, index // 2, index % 2)
            self._buttons[action.action_id] = button
        self.add_button.setEnabled(len(self.store.actions) < self.store.MAX_ACTIONS)

    def add_action(self) -> None:
        dialog = QuickActionDialog(self.routine_store.routines, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.store.add(*dialog.values())
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Quick Actions", str(error))
            return
        self.refresh()

    def edit_action(self, action_id: str) -> None:
        action = next((item for item in self.store.actions if item.action_id == action_id), None)
        if action is None:
            return
        dialog = QuickActionDialog(self.routine_store.routines, action, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.store.update(action_id, *dialog.values())
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Quick Actions", str(error))
            return
        self.refresh()

    def _show_menu(self, action_id: str, source: QWidget, point) -> None:
        menu = QMenu(self)
        menu.addAction("Edit", lambda: self.edit_action(action_id))
        menu.addAction("Remove", lambda: self._remove_action(action_id))
        menu.exec(source.mapToGlobal(point))

    def _remove_action(self, action_id: str) -> None:
        try:
            self.store.delete(action_id)
        except OSError as error:
            QMessageBox.warning(self, "Quick Actions", str(error))
            return
        self.refresh()


class LiveWorkspace(QWidget):
    """Core-owned host for provider-contributed live-use docks."""

    STATE_VERSION = 1
    STATE_KEY = "window/live_workspace_state"
    DOCKS_KEY = "window/live_workspace_docks"

    def __init__(
        self,
        contributions: Iterable[ModuleDockContribution],
        quick_actions: QuickActionsPanel,
        settings: QSettings,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("liveWorkspace")
        self.settings = settings
        # QMainWindow defaults to a top-level window type even with a parent.
        # Live embeds it as a page widget so its dock layout participates in
        # the surrounding QStackedWidget geometry instead of becoming a hidden
        # secondary window.
        self.host = QMainWindow()
        self.host.setParent(self, Qt.WindowType.Widget)
        self.host.setObjectName("liveWorkspaceHost")
        self.host.setDockNestingEnabled(True)
        self.host.setCentralWidget(QWidget(self.host))
        self.docks: dict[str, QDockWidget] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Live Workspace", self))
        controls.addStretch()
        self.panels_button = QToolButton(self)
        self.panels_button.setText("Panels")
        self.panels_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.panels_menu = QMenu(self.panels_button)
        self.panels_button.setMenu(self.panels_menu)
        self.reset_button = QPushButton("Reset to Default Layout", self)
        self.reset_button.clicked.connect(self.reset_to_default)
        controls.addWidget(self.panels_button)
        controls.addWidget(self.reset_button)
        layout.addLayout(controls)
        layout.addWidget(self.host, 1)

        all_contributions = list(contributions) + [
            ModuleDockContribution(
                dock_id="core.quick-actions",
                owner_module_id="streamhouse.core",
                title="Quick Actions",
                factory=lambda _parent: quick_actions,
                default_area="bottom",
                order=900,
            )
        ]
        for contribution in all_contributions:
            self._add_contribution(contribution)
        self._apply_default_layout()

    def _add_contribution(self, contribution: ModuleDockContribution) -> None:
        content = contribution.factory(self.host)
        if not isinstance(content, QWidget):
            raise TypeError(f'Dock "{contribution.dock_id}" did not create a QWidget.')
        dock = QDockWidget(contribution.title, self.host)
        dock.setObjectName(contribution.dock_id)
        features = QDockWidget.DockWidgetFeature.DockWidgetClosable | QDockWidget.DockWidgetFeature.DockWidgetMovable
        if contribution.floatable:
            features |= QDockWidget.DockWidgetFeature.DockWidgetFloatable
        dock.setFeatures(features)
        allowed = Qt.DockWidgetArea.NoDockWidgetArea
        for name in contribution.allowed_areas:
            allowed |= _DOCK_AREAS[name]
        dock.setAllowedAreas(allowed)
        dock.setProperty("defaultArea", contribution.default_area)
        dock.setProperty("visibleByDefault", contribution.visible_by_default)
        dock.setWidget(content)
        self.host.addDockWidget(_DOCK_AREAS[contribution.default_area], dock)
        self.docks[contribution.dock_id] = dock
        self.panels_menu.addAction(dock.toggleViewAction())

    def _apply_default_layout(self) -> None:
        for dock in self.docks.values():
            self.host.removeDockWidget(dock)
            self.host.addDockWidget(_DOCK_AREAS[str(dock.property("defaultArea"))], dock)
            dock.setFloating(False)
            dock.setVisible(bool(dock.property("visibleByDefault")))
        activity = self.docks.get("twitch.activity")
        chatters = self.docks.get("twitch.chatters")
        if activity is not None and chatters is not None:
            self.host.tabifyDockWidget(activity, chatters)
            activity.raise_()
        chat = self.docks.get("twitch.chat")
        if chat is not None:
            self.host.resizeDocks([chat], [700], Qt.Orientation.Horizontal)

    def reset_to_default(self) -> None:
        self.settings.remove(self.STATE_KEY)
        self.settings.remove(self.DOCKS_KEY)
        self._apply_default_layout()

    def restore_layout(self) -> bool:
        state = self.settings.value(self.STATE_KEY)
        if not isinstance(state, QByteArray):
            return False
        return self.host.restoreState(state, self.STATE_VERSION)

    def save_layout(self) -> bool:
        current = set(self.docks)
        saved = {str(item) for item in self.settings.value(self.DOCKS_KEY, []) or []}
        if saved - current:
            return False
        self.settings.setValue(self.STATE_KEY, self.host.saveState(self.STATE_VERSION))
        self.settings.setValue(self.DOCKS_KEY, sorted(current))
        return True
