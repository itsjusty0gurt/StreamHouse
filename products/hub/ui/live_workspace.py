from __future__ import annotations

from collections.abc import Callable, Iterable

from PySide6.QtCore import (
    QByteArray,
    QMimeData,
    QPoint,
    QSettings,
    QSize,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QDrag, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDockWidget,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QProxyStyle,
    QSizePolicy,
    QStyle,
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


class _LiveDockStyle(QProxyStyle):
    """Keep Live dock splitters compact without changing floating frames."""

    SEPARATOR_EXTENT = 4

    def pixelMetric(self, metric, option=None, widget=None) -> int:
        if metric == QStyle.PixelMetric.PM_DockWidgetSeparatorExtent:
            return self.SEPARATOR_EXTENT
        return super().pixelMetric(metric, option, widget)


class QuickActionDialog(QDialog):
    ICON_CHOICES = (
        ("None", ""),
        ("Play", "play"),
        ("Confirm", "confirm"),
        ("Stop", "stop"),
        ("Info", "info"),
        ("Alert", "alert"),
    )

    def __init__(self, routines: Iterable[object], action: QuickAction | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit Quick Action" if action else "Add Quick Action")
        layout = QFormLayout(self)
        self.label_edit = QLineEdit(action.label if action else "", self)
        self.routine_combo = QComboBox(self)
        routines = list(routines)
        available_ids = {str(routine.routine_id) for routine in routines}
        if action is not None and action.routine_id not in available_ids:
            self.routine_combo.addItem("Unavailable routine", action.routine_id)
        for routine in routines:
            self.routine_combo.addItem(str(routine.name), str(routine.routine_id))
        if action is not None:
            index = self.routine_combo.findData(action.routine_id)
            if index >= 0:
                self.routine_combo.setCurrentIndex(index)
        self.icon_combo = QComboBox(self)
        for label, value in self.ICON_CHOICES:
            self.icon_combo.addItem(label, value)
        if action is not None:
            icon_index = self.icon_combo.findData(action.icon)
            if icon_index >= 0:
                self.icon_combo.setCurrentIndex(icon_index)
        self._color = action.color if action else ""
        color_row = QHBoxLayout()
        self.color_button = QPushButton(self)
        self.color_button.clicked.connect(self._choose_color)
        self.clear_color_button = QPushButton("Use Default", self)
        self.clear_color_button.clicked.connect(self._clear_color)
        color_row.addWidget(self.color_button, 1)
        color_row.addWidget(self.clear_color_button)
        self._update_color_button()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow("Label", self.label_edit)
        layout.addRow("Routine", self.routine_combo)
        layout.addRow("Icon", self.icon_combo)
        layout.addRow("Tile color", color_row)
        layout.addRow(buttons)

    def _choose_color(self) -> None:
        initial = QColor(self._color) if self._color else QColor("#6b4f9f")
        selected = QColorDialog.getColor(initial, self, "Quick Action Tile Color")
        if selected.isValid():
            self._color = selected.name(QColor.NameFormat.HexRgb)
            self._update_color_button()

    def _update_color_button(self) -> None:
        self.color_button.setText(self._color or "Use default")
        if self._color:
            self.color_button.setStyleSheet(f"background-color:{self._color};")
        else:
            self.color_button.setStyleSheet("")

    def _clear_color(self) -> None:
        self._color = ""
        self._update_color_button()

    def values(self) -> tuple[str, str, str, str]:
        return (
            self.label_edit.text(),
            str(self.routine_combo.currentData() or ""),
            self._color,
            str(self.icon_combo.currentData() or ""),
        )


class QuickActionTile(QToolButton):
    """Square routine shortcut with a clean drag representation."""

    MIME_TYPE = "application/x-streamhouse-quick-action"

    def __init__(self, action_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.action_id = action_id
        self._press_position = QPoint()
        self._drag_started = False
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_position = event.position().toPoint()
            self._drag_started = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        distance = (event.position().toPoint() - self._press_position).manhattanLength()
        if distance < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return
        self._drag_started = True
        mime = QMimeData()
        mime.setData(self.MIME_TYPE, self.action_id.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        pixmap = self.grab()
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
        drag.exec(Qt.DropAction.MoveAction)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._drag_started:
            self._drag_started = False
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _QuickActionGrid(QWidget):
    """Projects stable action order into horizontal or vertical wrapping geometry."""

    reorder_requested = Signal(str, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._orientation = Qt.Orientation.Horizontal
        self._tile_size = 72
        self._tiles: list[QuickActionTile] = []
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setHorizontalSpacing(6)
        self._layout.setVerticalSpacing(6)
        self._drop_line = QFrame(self)
        self._drop_line.setObjectName("quickActionDropIndicator")
        self._drop_line.setStyleSheet(
            "background:#bf94ff; border:none; border-radius:1px;"
        )
        self._drop_line.hide()

    @property
    def orientation(self) -> Qt.Orientation:
        return self._orientation

    def set_orientation(self, orientation: Qt.Orientation) -> None:
        if orientation == self._orientation:
            return
        self._orientation = orientation
        self._update_minimum_constraint()
        self._relayout()

    def set_tile_size(self, size: int) -> None:
        self._tile_size = int(size)
        self._update_minimum_constraint()
        for tile in self._tiles:
            tile.setFixedSize(self._tile_size, self._tile_size)
            tile.setIconSize(QSize(self._tile_size // 3, self._tile_size // 3))
        self._relayout()

    def set_tiles(self, tiles: list[QuickActionTile]) -> None:
        for tile in self._tiles:
            self._layout.removeWidget(tile)
        self._tiles = list(tiles)
        for tile in self._tiles:
            tile.setParent(self)
            tile.setFixedSize(self._tile_size, self._tile_size)
            tile.setIconSize(QSize(self._tile_size // 3, self._tile_size // 3))
            tile.show()
        self._relayout()

    def logical_order(self) -> list[str]:
        return [tile.action_id for tile in self._tiles]

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt API
        extent = self._tile_size + 8
        if self._orientation == Qt.Orientation.Horizontal:
            return QSize(0, extent)
        return QSize(extent, 0)

    def _update_minimum_constraint(self) -> None:
        extent = self._tile_size + 8
        if self._orientation == Qt.Orientation.Horizontal:
            self.setMinimumSize(0, extent)
        else:
            self.setMinimumSize(extent, 0)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        count = max(1, min(4, len(self._tiles)))
        step = self._tile_size + self._layout.horizontalSpacing()
        if self._orientation == Qt.Orientation.Horizontal:
            return QSize(count * step + 8, self._tile_size + 8)
        return QSize(self._tile_size + 8, count * step + 8)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self) -> None:
        while self._layout.count():
            self._layout.takeAt(0)
        if not self._tiles:
            self.updateGeometry()
            return
        step = self._tile_size + max(
            self._layout.horizontalSpacing(), self._layout.verticalSpacing()
        )
        if self._orientation == Qt.Orientation.Horizontal:
            columns = max(1, (max(self.width(), self._tile_size) - 8) // step)
            for index, tile in enumerate(self._tiles):
                self._layout.addWidget(tile, index // columns, index % columns)
        else:
            rows = max(1, (max(self.height(), self._tile_size) - 8) // step)
            for index, tile in enumerate(self._tiles):
                self._layout.addWidget(tile, index % rows, index // rows)
        self.updateGeometry()

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.mimeData().hasFormat(QuickActionTile.MIME_TYPE):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not event.mimeData().hasFormat(QuickActionTile.MIME_TYPE):
            self._clear_drop_indicator()
            event.ignore()
            return
        insertion_index = self._insertion_index(event.position().toPoint())
        self._show_drop_indicator(insertion_index)
        event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._clear_drop_indicator()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not event.mimeData().hasFormat(QuickActionTile.MIME_TYPE):
            self._clear_drop_indicator()
            event.ignore()
            return
        action_id = bytes(
            event.mimeData().data(QuickActionTile.MIME_TYPE)
        ).decode("utf-8")
        insertion_index = self._insertion_index(event.position().toPoint())
        self._clear_drop_indicator()
        self.reorder_requested.emit(action_id, insertion_index)
        event.acceptProposedAction()

    def _insertion_index(self, point: QPoint) -> int:
        if not self._tiles:
            return 0
        target_index = min(
            range(len(self._tiles)),
            key=lambda index: (
                self._tiles[index].geometry().center() - point
            ).manhattanLength(),
        )
        rect = self._tiles[target_index].geometry()
        if self._orientation == Qt.Orientation.Horizontal:
            after = point.x() >= rect.center().x()
        else:
            after = point.y() >= rect.center().y()
        return target_index + int(after)

    def _show_drop_indicator(self, insertion_index: int) -> None:
        if not self._tiles:
            self._drop_line.hide()
            return
        if insertion_index >= len(self._tiles):
            rect = self._tiles[-1].geometry()
            trailing = True
        else:
            rect = self._tiles[insertion_index].geometry()
            trailing = False
        if self._orientation == Qt.Orientation.Horizontal:
            x = rect.right() + 3 if trailing else rect.left() - 3
            self._drop_line.setGeometry(x, rect.top(), 3, rect.height())
        else:
            y = rect.bottom() + 3 if trailing else rect.top() - 3
            self._drop_line.setGeometry(rect.left(), y, rect.width(), 3)
        self._drop_line.raise_()
        self._drop_line.show()

    def _clear_drop_indicator(self) -> None:
        self._drop_line.hide()


class QuickActionsPanel(QWidget):
    routine_requested = Signal(str)
    unavailable_requested = Signal(str)
    preferred_extent_changed = Signal(int)

    TILE_SIZES = {
        "small": 56,
        "medium": 72,
        "large": 88,
        "extra_large": 104,
    }
    SIZE_LABELS = {
        "small": "Small",
        "medium": "Medium",
        "large": "Large",
        "extra_large": "Extra Large",
    }
    SIZE_ABBREVIATIONS = {
        "small": "S",
        "medium": "M",
        "large": "L",
        "extra_large": "XL",
    }
    ICON_PIXMAPS = {
        "play": QStyle.StandardPixmap.SP_MediaPlay,
        "confirm": QStyle.StandardPixmap.SP_DialogApplyButton,
        "stop": QStyle.StandardPixmap.SP_MediaStop,
        "info": QStyle.StandardPixmap.SP_MessageBoxInformation,
        "alert": QStyle.StandardPixmap.SP_MessageBoxWarning,
    }

    def __init__(self, store: QuickActionStore, routine_store: object, parent=None) -> None:
        super().__init__(parent)
        self.store = store
        self.routine_store = routine_store
        self._buttons: dict[str, QuickActionTile] = {}
        self._dock_area = Qt.DockWidgetArea.BottomDockWidgetArea
        self._layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._layout.setContentsMargins(4, 4, 4, 4)
        self._layout.setSpacing(4)
        self._header = QWidget(self)
        self._header_layout = QBoxLayout(
            QBoxLayout.Direction.TopToBottom,
            self._header,
        )
        header = self._header_layout
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)
        self.size_button = QToolButton(self)
        self.size_button.setToolTip("Quick Action tile size")
        self.size_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.size_menu = QMenu(self.size_button)
        self._size_actions = {}
        for value in self.store.BUTTON_SIZES:
            action = self.size_menu.addAction(self.SIZE_LABELS[value])
            action.setCheckable(True)
            action.triggered.connect(
                lambda _checked=False, selected=value: self.set_button_size(selected)
            )
            self._size_actions[value] = action
        self.size_button.setMenu(self.size_menu)
        header.addWidget(self.size_button)
        header.addStretch()
        self.add_button = QToolButton(self)
        self.add_button.setText("+")
        self.add_button.setToolTip("Add Quick Action")
        self.add_button.setAccessibleName("Add Quick Action")
        self.add_button.clicked.connect(self.add_action)
        header.addWidget(self.add_button)
        self._layout.addWidget(self._header)
        self.grid = _QuickActionGrid(self)
        self.grid.reorder_requested.connect(self._reorder_action)
        self._layout.addWidget(self.grid, 1)
        try:
            self.store.load()
        except (OSError, TypeError, ValueError):
            self.store.actions = []
            self.store.button_size = self.store.DEFAULT_BUTTON_SIZE
        self._set_size_selection(self.store.button_size)
        self.grid.set_tile_size(self.TILE_SIZES[self.store.button_size])
        self.refresh()

    def refresh(self) -> None:
        previous_buttons = dict(self._buttons)
        current_buttons: dict[str, QuickActionTile] = {}
        routine_ids = {
            str(routine.routine_id) for routine in self.routine_store.routines
        }
        tiles: list[QuickActionTile] = []
        for action in self.store.actions:
            available = action.routine_id in routine_ids
            button = previous_buttons.pop(action.action_id, None)
            if button is None:
                button = QuickActionTile(action.action_id, self.grid)
                button.setObjectName(f"quickAction_{action.action_id}")
                button.clicked.connect(
                    lambda _checked=False, action_id=action.action_id: self._activate_action(action_id)
                )
                button.customContextMenuRequested.connect(
                    lambda point, action_id=action.action_id, source=button: self._show_menu(action_id, source, point)
                )
            button.setText(action.label)
            self._apply_tile_presentation(button, action, available)
            current_buttons[action.action_id] = button
            tiles.append(button)
        for obsolete_button in previous_buttons.values():
            obsolete_button.deleteLater()
        self._buttons = current_buttons
        self.grid.set_tiles(tiles)
        self.add_button.setEnabled(len(self.store.actions) < self.store.MAX_ACTIONS)

    def set_dock_area(self, area: Qt.DockWidgetArea) -> None:
        if area == Qt.DockWidgetArea.NoDockWidgetArea:
            return
        self._dock_area = area
        orientation = (
            Qt.Orientation.Horizontal
            if area
            in {
                Qt.DockWidgetArea.TopDockWidgetArea,
                Qt.DockWidgetArea.BottomDockWidgetArea,
            }
            else Qt.Orientation.Vertical
        )
        direction = (
            QBoxLayout.Direction.LeftToRight
            if orientation == Qt.Orientation.Horizontal
            else QBoxLayout.Direction.TopToBottom
        )
        self._layout.setDirection(direction)
        if orientation == Qt.Orientation.Horizontal:
            self._header.setSizePolicy(
                QSizePolicy.Policy.Fixed,
                QSizePolicy.Policy.Preferred,
            )
        else:
            self._header.setSizePolicy(
                QSizePolicy.Policy.Preferred,
                QSizePolicy.Policy.Fixed,
            )
        self.grid.set_orientation(orientation)
        self._layout.invalidate()
        self.updateGeometry()
        parent = self.parentWidget()
        if parent is not None:
            parent.updateGeometry()
        self.preferred_extent_changed.emit(self.preferred_cross_extent())

    def _apply_tile_presentation(
        self,
        button: QuickActionTile,
        action: QuickAction,
        available: bool,
    ) -> None:
        icon = self.ICON_PIXMAPS.get(action.icon)
        button.setIcon(self.style().standardIcon(icon) if icon is not None else QIcon())
        if icon is None:
            button.setIconSize(QSize(0, 0))
        color = QColor(action.color) if action.color else QColor("#4d3c68")
        lightness = color.lightness()
        foreground = "#111111" if lightness > 150 else "#ffffff"
        border = "#dc6b6b" if not available else color.lighter(125).name()
        button.setStyleSheet(
            "QToolButton {"
            f"background:{color.name()}; color:{foreground}; border:2px solid {border};"
            "border-radius:8px; padding:4px; font-weight:600;"
            "} QToolButton:hover { border-color:#bf94ff; }"
            "QToolButton:pressed { background:#342947; }"
        )
        if available:
            button.setToolTip(action.label)
        else:
            button.setText(f"Unavailable\n{action.label}")
            button.setToolTip(
                "The linked routine is unavailable. Right-click to relink or remove this action."
            )

    def _activate_action(self, action_id: str) -> None:
        action = next(
            (item for item in self.store.actions if item.action_id == action_id),
            None,
        )
        if action is None:
            return
        routine_ids = {
            str(routine.routine_id) for routine in self.routine_store.routines
        }
        if action.routine_id not in routine_ids:
            self.unavailable_requested.emit(action.label)
            return
        self.routine_requested.emit(action.routine_id)

    def set_button_size(self, value: str) -> None:
        value = str(value)
        if value not in self.TILE_SIZES:
            return
        try:
            self.store.set_button_size(value)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Quick Actions", str(error))
            return
        self._set_size_selection(value)
        self.grid.set_tile_size(self.TILE_SIZES[value])
        self._layout.invalidate()
        self.updateGeometry()
        parent = self.parentWidget()
        if parent is not None:
            parent.updateGeometry()
        self.preferred_extent_changed.emit(self.preferred_cross_extent())

    def preferred_cross_extent(self) -> int:
        margins = self._layout.contentsMargins()
        minimum = self.grid.minimumSizeHint()
        if self.grid.orientation == Qt.Orientation.Horizontal:
            return minimum.height() + margins.top() + margins.bottom()
        return minimum.width() + margins.left() + margins.right()

    def _set_size_selection(self, value: str) -> None:
        for name, action in self._size_actions.items():
            action.setChecked(name == value)
        label = self.SIZE_LABELS[value]
        self.size_button.setText(self.SIZE_ABBREVIATIONS[value])
        self.size_button.setToolTip(f"Tile size: {label}")
        self.size_button.setAccessibleName(f"Quick Action tile size: {label}")

    def _reorder_action(self, action_id: str, insertion_index: int) -> None:
        try:
            self.store.reorder(action_id, insertion_index)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Quick Actions", str(error))
            return
        self.refresh()

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
        self._default_layout_pending = False
        # QMainWindow defaults to a top-level window type even with a parent.
        # Live embeds it as a page widget so its dock layout participates in
        # the surrounding QStackedWidget geometry instead of becoming a hidden
        # secondary window.
        self.host = QMainWindow()
        self.host.setParent(self, Qt.WindowType.Widget)
        self.host.setObjectName("liveWorkspaceHost")
        self.host.setDockOptions(
            QMainWindow.DockOption.AllowNestedDocks
        )
        self._dock_style = _LiveDockStyle()
        self._dock_style.setParent(self.host)
        self.host.setStyle(self._dock_style)
        # Live is entirely dock-backed. Leaving the host without a central
        # widget lets the primary Chat dock consume the usable middle area
        # instead of reserving an empty center pane.
        self.host.setCorner(
            Qt.Corner.TopLeftCorner,
            Qt.DockWidgetArea.TopDockWidgetArea,
        )
        self.host.setCorner(
            Qt.Corner.TopRightCorner,
            Qt.DockWidgetArea.TopDockWidgetArea,
        )
        self.host.setCorner(
            Qt.Corner.BottomLeftCorner,
            Qt.DockWidgetArea.BottomDockWidgetArea,
        )
        self.host.setCorner(
            Qt.Corner.BottomRightCorner,
            Qt.DockWidgetArea.BottomDockWidgetArea,
        )
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
        quick_actions_dock = self.docks.get("core.quick-actions")
        if quick_actions_dock is not None:
            quick_actions_dock.visibilityChanged.connect(
                self._quick_actions_visibility_changed
            )
            quick_actions.preferred_extent_changed.connect(
                self._resize_quick_actions_to_contents,
                Qt.ConnectionType.QueuedConnection,
            )

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
        dock_area_setter = getattr(content, "set_dock_area", None)
        if callable(dock_area_setter):
            dock.dockLocationChanged.connect(dock_area_setter)
            dock_area_setter(_DOCK_AREAS[contribution.default_area])
        self.docks[contribution.dock_id] = dock
        self.panels_menu.addAction(dock.toggleViewAction())

    def _apply_default_layout(self) -> None:
        for dock in self.docks.values():
            # On Windows, removing a floating dock before clearing its
            # top-level state can leave the native window detached even after
            # addDockWidget() is called again.  Reattach it while Qt still
            # knows its owning QMainWindow, then rebuild the default splits.
            if dock.isFloating():
                dock.setFloating(False)
            dock.hide()
            self.host.removeDockWidget(dock)

        stream_tools = self.docks.get("twitch.stream-tools")
        quick_actions = self.docks.get("core.quick-actions")
        chat = self.docks.get("twitch.chat")
        activity = self.docks.get("twitch.activity")
        chatters = self.docks.get("twitch.chatters")
        arranged = {
            dock
            for dock in (
                stream_tools,
                quick_actions,
                chat,
                activity,
                chatters,
            )
            if dock is not None
        }

        middle_layout = [
            (dock, size)
            for dock, size in (
                (chat, 700),
                (activity, 260),
                (chatters, 170),
            )
            if dock is not None
        ]
        middle_docks = [dock for dock, _size in middle_layout]
        middle_anchor = middle_docks[0] if middle_docks else None
        root = stream_tools or middle_anchor or quick_actions
        if root is not None:
            self.host.addDockWidget(
                Qt.DockWidgetArea.TopDockWidgetArea,
                root,
            )

        if stream_tools is not None and middle_anchor is not None:
            self.host.splitDockWidget(
                stream_tools,
                middle_anchor,
                Qt.Orientation.Vertical,
            )
        if quick_actions is not None and middle_anchor is not None:
            self.host.splitDockWidget(
                middle_anchor,
                quick_actions,
                Qt.Orientation.Vertical,
            )
        elif quick_actions is not None and stream_tools is not None:
            self.host.splitDockWidget(
                stream_tools,
                quick_actions,
                Qt.Orientation.Vertical,
            )

        for left, right in zip(middle_docks, middle_docks[1:]):
            self.host.splitDockWidget(left, right, Qt.Orientation.Horizontal)

        for dock in self.docks.values():
            if dock not in arranged:
                self.host.addDockWidget(
                    _DOCK_AREAS[str(dock.property("defaultArea"))],
                    dock,
                )
            dock.setVisible(bool(dock.property("visibleByDefault")))

        if middle_docks:
            self.host.resizeDocks(
                middle_docks,
                [size for _dock, size in middle_layout],
                Qt.Orientation.Horizontal,
            )

        vertical_layout = [
            (dock, size)
            for dock, size in (
                (stream_tools, 170),
                (middle_anchor, 520),
                (
                    quick_actions,
                    max(1, quick_actions.minimumSizeHint().height())
                    if quick_actions is not None
                    else 1,
                ),
            )
            if dock is not None
        ]
        vertical_docks = [dock for dock, _size in vertical_layout]
        if vertical_docks:
            self.host.resizeDocks(
                vertical_docks,
                [size for _dock, size in vertical_layout],
                Qt.Orientation.Vertical,
            )

    def reset_to_default(self) -> None:
        self.settings.remove(self.STATE_KEY)
        self.settings.remove(self.DOCKS_KEY)
        self._default_layout_pending = False
        self._apply_default_layout()

    def _quick_actions_visibility_changed(self, visible: bool) -> None:
        """Give freed Quick Actions space to the middle row, not the top row."""
        if not self.host.isVisible():
            return
        stream_tools = self.docks.get("twitch.stream-tools")
        middle = (
            self.docks.get("twitch.chat")
            or self.docks.get("twitch.activity")
            or self.docks.get("twitch.chatters")
        )
        quick_actions = self.docks.get("core.quick-actions")
        if middle is None or quick_actions is None:
            return
        if (
            middle.isFloating()
            or quick_actions.isFloating()
            or self.host.dockWidgetArea(middle)
            != self.host.dockWidgetArea(quick_actions)
        ):
            return
        if stream_tools is not None and (
            stream_tools.isFloating()
            or self.host.dockWidgetArea(stream_tools)
            != self.host.dockWidgetArea(middle)
        ):
            stream_tools = None

        vertical_docks = [
            dock for dock in (stream_tools, middle) if dock is not None
        ]
        vertical_sizes = [max(1, dock.height()) for dock in vertical_docks]
        if visible:
            vertical_docks.append(quick_actions)
            vertical_sizes.append(max(1, quick_actions.minimumSizeHint().height()))
        elif vertical_sizes:
            vertical_sizes[-1] += max(1, quick_actions.height())
        self.host.resizeDocks(
            vertical_docks,
            vertical_sizes,
            Qt.Orientation.Vertical,
        )

    def _resize_quick_actions_to_contents(self, content_extent: int) -> None:
        """Resize the dock's cross-axis to one tile at the selected preset."""
        dock = self.docks.get("core.quick-actions")
        if dock is None or not dock.isVisible() or dock.isFloating():
            return
        content = dock.widget()
        if content is not None and content.layout() is not None:
            content.layout().activate()
        dock.updateGeometry()
        self.host.layout().invalidate()
        self.host.layout().activate()
        area = self.host.dockWidgetArea(dock)
        horizontal_flow = area in {
            Qt.DockWidgetArea.TopDockWidgetArea,
            Qt.DockWidgetArea.BottomDockWidgetArea,
        }
        orientation = (
            Qt.Orientation.Vertical
            if horizontal_flow
            else Qt.Orientation.Horizontal
        )
        extent = (
            (lambda item: item.height())
            if horizontal_flow
            else (lambda item: item.width())
        )
        minimum_extent = (
            lambda item: item.minimumSizeHint().height()
            if horizontal_flow
            else item.minimumSizeHint().width()
        )
        content_widget = dock.widget()
        chrome = 0
        if content_widget is not None:
            chrome = max(0, extent(dock) - extent(content_widget))
        target = max(1, int(content_extent) + chrome)
        peers = [
            candidate
            for candidate in self.docks.values()
            if candidate is not dock
            and candidate.isVisible()
            and not candidate.isFloating()
            and self.host.dockWidgetArea(candidate) == area
        ]
        if not peers:
            if horizontal_flow:
                dock.setFixedHeight(target)
            else:
                dock.setFixedWidth(target)
            self.host.resizeDocks([dock], [target], orientation)
            QTimer.singleShot(
                0,
                self,
                lambda target_dock=dock, horizontal=horizontal_flow: (
                    self._release_quick_actions_cross_constraint(
                        target_dock,
                        horizontal,
                    )
                ),
            )
            return
        recipient = max(peers, key=extent)
        released = extent(dock) - target
        recipient_target = max(
            1,
            minimum_extent(recipient),
            extent(recipient) + released,
        )
        self.host.resizeDocks(
            [recipient, dock],
            [recipient_target, target],
            orientation,
        )

    @staticmethod
    def _release_quick_actions_cross_constraint(
        dock: QDockWidget,
        horizontal_flow: bool,
    ) -> None:
        if horizontal_flow:
            dock.setMinimumHeight(0)
            dock.setMaximumHeight(16_777_215)
        else:
            dock.setMinimumWidth(0)
            dock.setMaximumWidth(16_777_215)

    def restore_layout(self) -> bool:
        state = self.settings.value(self.STATE_KEY)
        if not isinstance(state, QByteArray):
            self._defer_default_layout_until_shown()
            return False
        restored = self.host.restoreState(state, self.STATE_VERSION)
        if not restored:
            self._defer_default_layout_until_shown()
        return restored

    def _defer_default_layout_until_shown(self) -> None:
        """Wait for real host geometry before realizing native dock windows."""
        self._default_layout_pending = True
        for dock in self.docks.values():
            dock.hide()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        if not self._default_layout_pending:
            return
        self._default_layout_pending = False
        self._apply_default_layout()

    def save_layout(self) -> bool:
        current = set(self.docks)
        saved = {str(item) for item in self.settings.value(self.DOCKS_KEY, []) or []}
        if saved - current:
            return False
        self.settings.setValue(self.STATE_KEY, self.host.saveState(self.STATE_VERSION))
        self.settings.setValue(self.DOCKS_KEY, sorted(current))
        return True
