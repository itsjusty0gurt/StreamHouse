import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QPoint, QPointF, QRect, QSettings, Qt
from PySide6.QtWidgets import QApplication, QDialog, QMainWindow, QStyle, QWidget

from products.hub.automation.quick_actions import QuickActionStore
from products.hub.modules.contributions import ModuleDockContribution
from products.hub.ui.live_workspace import (
    LiveWorkspace,
    QuickActionDialog,
    QuickActionsPanel,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _quick_panel(tmp_path: Path) -> QuickActionsPanel:
    routines = SimpleNamespace(
        routines=[SimpleNamespace(routine_id="routine-1", name="Safe Routine")]
    )
    return QuickActionsPanel(
        QuickActionStore(tmp_path / "quick_actions.json"), routines
    )


def _populated_quick_panel(
    tmp_path: Path,
    count: int = 5,
) -> QuickActionsPanel:
    routines = SimpleNamespace(
        routines=[
            SimpleNamespace(routine_id=f"routine-{index}", name=f"Routine {index}")
            for index in range(count)
        ]
    )
    store = QuickActionStore(tmp_path / "quick_actions.json")
    for index in range(count):
        store.add(f"Action {index}", f"routine-{index}")
    return QuickActionsPanel(store, routines)


def _chat_contribution() -> ModuleDockContribution:
    return ModuleDockContribution(
        dock_id="twitch.chat",
        owner_module_id="streamhouse.twitch",
        title="Twitch Chat",
        factory=lambda parent: QWidget(parent),
        default_area="left",
    )


def _contribution(
    dock_id: str,
    title: str,
    default_area: str,
) -> ModuleDockContribution:
    return ModuleDockContribution(
        dock_id=dock_id,
        owner_module_id="streamhouse.twitch",
        title=title,
        factory=lambda parent: QWidget(parent),
        default_area=default_area,
    )


def _live_contributions() -> list[ModuleDockContribution]:
    return [
        _chat_contribution(),
        _contribution("twitch.activity", "Twitch Activity", "right"),
        _contribution("twitch.chatters", "Twitch Chatters", "right"),
        _contribution(
            "twitch.stream-tools",
            "Twitch Stream Status",
            "top",
        ),
    ]


def _show_workspace(workspace: LiveWorkspace) -> None:
    workspace.resize(1200, 800)
    workspace.show()
    _app().processEvents()


def _assert_docked_panels_do_not_overlap(workspace: LiveWorkspace) -> None:
    visible_docks = [
        dock
        for dock in workspace.docks.values()
        if dock.isVisible() and not dock.isFloating()
    ]
    for index, first in enumerate(visible_docks):
        first_geometry = QRect(first.geometry())
        for second in visible_docks[index + 1 :]:
            overlap = first_geometry.intersected(second.geometry())
            assert overlap.width() <= 0 or overlap.height() <= 0, (
                f"{first.objectName()} overlaps {second.objectName()}: {overlap}"
            )


def test_quick_action_store_round_trip_and_panel_runs_canonical_routine(tmp_path: Path) -> None:
    _app()
    store = QuickActionStore(tmp_path / "quick_actions.json")
    action = store.add("Welcome", "routine-1")
    loaded = QuickActionStore(store.path)
    assert loaded.load() == [action]

    routines = SimpleNamespace(
        routines=[SimpleNamespace(routine_id="routine-1", name="Safe Routine")]
    )
    panel = QuickActionsPanel(loaded, routines)
    requested: list[str] = []
    panel.routine_requested.connect(requested.append)
    panel._buttons[action.action_id].click()

    assert requested == ["routine-1"]


def test_quick_action_v1_data_migrates_without_changing_identity_or_order(
    tmp_path: Path,
) -> None:
    path = tmp_path / "quick_actions.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "actions": [
                    {
                        "action_id": "first-id",
                        "label": "First",
                        "routine_id": "routine-1",
                    },
                    {
                        "action_id": "second-id",
                        "label": "Second",
                        "routine_id": "routine-2",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    store = QuickActionStore(path)
    actions = store.load()

    assert [action.action_id for action in actions] == ["first-id", "second-id"]
    assert all(action.color == "" and action.icon == "" for action in actions)
    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated["version"] == 2
    assert migrated["button_size"] == "medium"


def test_quick_action_size_presets_remain_square_and_persist(tmp_path: Path) -> None:
    _app()
    panel = _populated_quick_panel(tmp_path, 1)
    action_id = panel.store.actions[0].action_id

    for value, expected_size in panel.TILE_SIZES.items():
        panel.set_button_size(value)
        button = panel._buttons[action_id]
        assert button.width() == expected_size
        assert button.height() == expected_size
        assert panel._size_actions[value].isChecked()

    loaded = QuickActionStore(panel.store.path)
    loaded.load()
    assert loaded.button_size == "extra_large"


def test_quick_action_grid_wraps_by_dock_orientation_without_reordering(
    tmp_path: Path,
) -> None:
    _app()
    panel = _populated_quick_panel(tmp_path)
    original_order = panel.grid.logical_order()

    panel.set_dock_area(Qt.DockWidgetArea.BottomDockWidgetArea)
    panel.resize(250, 320)
    panel.show()
    _app().processEvents()
    horizontal_tiles = list(panel._buttons.values())
    assert panel.grid.orientation == Qt.Orientation.Horizontal
    assert horizontal_tiles[0].y() == horizontal_tiles[1].y()
    assert horizontal_tiles[-1].y() > horizontal_tiles[0].y()

    panel.set_dock_area(Qt.DockWidgetArea.RightDockWidgetArea)
    panel.resize(320, 250)
    _app().processEvents()
    vertical_tiles = list(panel._buttons.values())
    assert panel.grid.orientation == Qt.Orientation.Vertical
    assert vertical_tiles[0].x() == vertical_tiles[1].x()
    assert vertical_tiles[-1].x() > vertical_tiles[0].x()
    assert panel.grid.logical_order() == original_order


def test_quick_action_reorder_persists_across_orientation_and_resize(
    tmp_path: Path,
) -> None:
    _app()
    panel = _populated_quick_panel(tmp_path, 4)
    original = panel.grid.logical_order()

    panel.resize(400, 220)
    panel.show()
    _app().processEvents()
    mime = QMimeData()
    mime.setData(
        panel._buttons[original[-1]].MIME_TYPE,
        original[-1].encode("utf-8"),
    )

    class DropEvent:
        accepted = False

        def mimeData(self):
            return mime

        def position(self):
            first = panel._buttons[original[0]].geometry()
            return QPointF(first.topLeft())

        def acceptProposedAction(self):
            self.accepted = True

        def ignore(self):
            self.accepted = False

    event = DropEvent()
    panel.grid.dragMoveEvent(event)
    assert panel.grid._drop_line.isVisible()
    panel.grid.dropEvent(event)
    assert event.accepted
    assert not panel.grid._drop_line.isVisible()
    expected = [original[-1], *original[:-1]]
    assert panel.grid.logical_order() == expected

    panel.set_dock_area(Qt.DockWidgetArea.LeftDockWidgetArea)
    panel.resize(180, 500)
    _app().processEvents()
    panel.resize(300, 240)
    _app().processEvents()
    assert panel.grid.logical_order() == expected

    loaded = QuickActionStore(panel.store.path)
    assert [action.action_id for action in loaded.load()] == expected


def test_quick_action_drop_destination_uses_primary_flow_axis(tmp_path: Path) -> None:
    _app()
    panel = _populated_quick_panel(tmp_path, 3)
    panel.resize(400, 220)
    panel.show()
    _app().processEvents()
    first = list(panel._buttons.values())[0]

    panel.set_dock_area(Qt.DockWidgetArea.BottomDockWidgetArea)
    assert panel.grid._insertion_index(first.geometry().topLeft()) == 0
    assert panel.grid._insertion_index(first.geometry().bottomRight()) == 1

    panel.set_dock_area(Qt.DockWidgetArea.RightDockWidgetArea)
    _app().processEvents()
    first = list(panel._buttons.values())[0]
    assert panel.grid._insertion_index(QPoint(first.x(), first.y())) == 0
    assert panel.grid._insertion_index(first.geometry().bottomRight()) == 1


def test_quick_action_customization_and_crud_preserve_stable_id(tmp_path: Path) -> None:
    store = QuickActionStore(tmp_path / "quick_actions.json")
    action = store.add("Welcome", "routine-1", "#123456", "play")
    updated = store.update(
        action.action_id,
        "Welcome Back",
        "routine-2",
        "#abcdef",
        "info",
    )
    assert updated.action_id == action.action_id
    assert (updated.label, updated.routine_id, updated.color, updated.icon) == (
        "Welcome Back",
        "routine-2",
        "#abcdef",
        "info",
    )
    assert store.delete(action.action_id)
    assert store.actions == []


def test_quick_action_panel_add_edit_and_delete_use_shared_store_paths(
    tmp_path: Path,
) -> None:
    _app()
    routines = SimpleNamespace(
        routines=[
            SimpleNamespace(routine_id="routine-1", name="One"),
            SimpleNamespace(routine_id="routine-2", name="Two"),
        ]
    )
    panel = QuickActionsPanel(
        QuickActionStore(tmp_path / "quick_actions.json"), routines
    )
    with (
        patch.object(QuickActionDialog, "exec", return_value=QDialog.DialogCode.Accepted),
        patch.object(
            QuickActionDialog,
            "values",
            return_value=("Added", "routine-1", "#112233", "play"),
        ),
    ):
        panel.add_action()
    action = panel.store.actions[0]
    assert action.label == "Added"

    with (
        patch.object(QuickActionDialog, "exec", return_value=QDialog.DialogCode.Accepted),
        patch.object(
            QuickActionDialog,
            "values",
            return_value=("Edited", "routine-2", "#445566", "info"),
        ),
    ):
        panel.edit_action(action.action_id)
    assert panel.store.actions[0].action_id == action.action_id
    assert panel.store.actions[0].label == "Edited"
    assert panel.store.actions[0].routine_id == "routine-2"

    panel._remove_action(action.action_id)
    assert panel.store.actions == []


def test_quick_action_edit_dialog_preserves_missing_routine_without_showing_id(
    tmp_path: Path,
) -> None:
    _app()
    store = QuickActionStore(tmp_path / "quick_actions.json")
    action = store.add("Old", "private-stable-id")
    dialog = QuickActionDialog([], action)

    assert dialog.routine_combo.currentText() == "Unavailable routine"
    assert "private-stable-id" not in dialog.routine_combo.currentText()
    assert dialog.values()[1] == "private-stable-id"
    dialog._color = "#123456"
    dialog._clear_color()
    assert dialog.values()[2] == ""


def test_missing_routine_tile_is_visible_editable_and_does_not_execute(
    tmp_path: Path,
) -> None:
    _app()
    store = QuickActionStore(tmp_path / "quick_actions.json")
    action = store.add("Old Routine", "missing-routine")
    panel = QuickActionsPanel(store, SimpleNamespace(routines=[]))
    requested: list[str] = []
    unavailable: list[str] = []
    panel.routine_requested.connect(requested.append)
    panel.unavailable_requested.connect(unavailable.append)
    button = panel._buttons[action.action_id]

    assert "Unavailable" in button.text()
    assert button.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
    button.click()
    assert requested == []
    assert unavailable == ["Old Routine"]


def test_quick_action_tile_color_and_builtin_icon_render(tmp_path: Path) -> None:
    _app()
    routines = SimpleNamespace(
        routines=[SimpleNamespace(routine_id="routine-1", name="Routine")]
    )
    store = QuickActionStore(tmp_path / "quick_actions.json")
    action = store.add("Styled", "routine-1", "#123456", "play")
    panel = QuickActionsPanel(store, routines)
    button = panel._buttons[action.action_id]

    assert "#123456" in button.styleSheet()
    assert not button.icon().isNull()


def test_customized_tiles_survive_live_workspace_reparenting(tmp_path: Path) -> None:
    _app()
    routines = SimpleNamespace(
        routines=[
            SimpleNamespace(routine_id=f"routine-{index}", name=f"Routine {index}")
            for index in range(6)
        ]
    )
    store = QuickActionStore(tmp_path / "quick_actions.json")
    icons = ("play", "confirm", "stop", "info", "alert", "play")
    for index, icon in enumerate(icons):
        store.add(
            f"Action {index}",
            f"routine-{index}",
            f"#{index + 1:02x}3456",
            icon,
        )
    panel = QuickActionsPanel(store, routines)
    workspace = LiveWorkspace(
        _live_contributions(),
        panel,
        QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
    )

    _show_workspace(workspace)

    assert workspace.docks["core.quick-actions"].widget() is panel
    assert all(not button.icon().isNull() for button in panel._buttons.values())
    assert all(button.width() == button.height() for button in panel._buttons.values())


def test_quick_actions_minimum_size_allows_compact_single_tile_dock(
    tmp_path: Path,
) -> None:
    _app()
    settings = QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat)
    panel = _populated_quick_panel(tmp_path, 1)
    workspace = LiveWorkspace([], panel, settings)
    _show_workspace(workspace)
    dock = workspace.docks["core.quick-actions"]

    assert panel.grid.minimumSizeHint().width() <= 80
    assert panel.grid.minimumSizeHint().height() <= 80
    assert dock.minimumSizeHint().width() <= 150
    assert dock.minimumSizeHint().height() < 150


def test_quick_action_minimum_tracks_tile_size_and_dock_orientation(
    tmp_path: Path,
) -> None:
    _app()
    panel = _populated_quick_panel(tmp_path, 8)
    workspace = LiveWorkspace(
        _live_contributions(),
        panel,
        QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
    )
    _show_workspace(workspace)
    dock = workspace.docks["core.quick-actions"]

    horizontal_extents = []
    horizontal_geometry = []
    for name, tile_size in panel.TILE_SIZES.items():
        panel.set_button_size(name)
        _app().processEvents()
        _app().processEvents()
        minimum = panel.grid.minimumSizeHint()
        assert minimum.width() == tile_size + 8
        assert minimum.height() == tile_size + 8
        horizontal_extents.append(dock.minimumSizeHint().height())
        horizontal_geometry.append(dock.height())
        # This is also the constraint Qt uses for a left/right docking preview
        # before dockLocationChanged switches the panel to vertical flow.
        assert dock.minimumWidth() >= panel.preferred_cross_extent()
        assert all(
            button.width() == button.height() == tile_size
            for button in panel._buttons.values()
        )

    assert horizontal_extents == sorted(horizontal_extents)
    assert len(set(horizontal_extents)) == len(horizontal_extents)
    assert horizontal_geometry == sorted(horizontal_geometry)
    assert horizontal_geometry == horizontal_extents

    panel.set_button_size("small")
    _app().processEvents()
    _app().processEvents()
    assert dock.height() == horizontal_geometry[0]

    workspace.host.splitDockWidget(
        workspace.docks["twitch.chatters"],
        dock,
        Qt.Orientation.Horizontal,
    )
    _app().processEvents()
    _app().processEvents()
    assert workspace.host.dockWidgetArea(dock) == Qt.DockWidgetArea.TopDockWidgetArea
    assert panel.grid.orientation == Qt.Orientation.Vertical
    vertical_extents = []
    vertical_geometry = []
    for name, tile_size in panel.TILE_SIZES.items():
        panel.set_button_size(name)
        _app().processEvents()
        _app().processEvents()
        minimum = panel.grid.minimumSizeHint()
        assert minimum.width() == tile_size + 8
        assert minimum.height() == tile_size + 8
        vertical_extents.append(dock.minimumSizeHint().width())
        vertical_geometry.append(dock.width())
        assert all(
            button.width() <= panel.grid.width()
            for button in panel._buttons.values()
        )
        first_button = next(iter(panel._buttons.values()))
        assert first_button.geometry().right() < panel.grid.width()
        assert dock.minimumWidth() == vertical_geometry[-1]

        # A stale/saved splitter size must not shrink the side dock below one
        # complete tile after the temporary auto-fit constraint is released.
        workspace.host.resizeDocks(
            [dock],
            [1],
            Qt.Orientation.Horizontal,
        )
        _app().processEvents()
        assert dock.width() >= dock.minimumWidth()
        assert first_button.geometry().right() < panel.grid.width()

    assert vertical_extents == sorted(vertical_extents)
    assert len(set(vertical_extents)) == len(vertical_extents)
    assert all(
        abs(actual - expected) <= 1
        for actual, expected in zip(vertical_geometry, vertical_extents)
    )


def test_quick_action_primary_minimum_does_not_grow_with_action_count(
    tmp_path: Path,
) -> None:
    _app()
    one = _populated_quick_panel(tmp_path / "one", 1)
    many = _populated_quick_panel(tmp_path / "many", 12)

    for panel in (one, many):
        panel.set_button_size("small")
        panel.set_dock_area(Qt.DockWidgetArea.BottomDockWidgetArea)
    assert (
        one.grid.minimumSizeHint().height()
        == many.grid.minimumSizeHint().height()
    )
    assert one.grid.minimumSizeHint().width() == many.grid.minimumSizeHint().width()

    for panel in (one, many):
        panel.set_dock_area(Qt.DockWidgetArea.LeftDockWidgetArea)
    assert (
        one.grid.minimumSizeHint().width()
        == many.grid.minimumSizeHint().width()
    )
    assert one.grid.minimumSizeHint().height() == many.grid.minimumSizeHint().height()


def test_live_workspace_persists_dock_area_visibility_and_resets(tmp_path: Path) -> None:
    _app()
    settings = QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat)
    first = LiveWorkspace(
        [_chat_contribution()], _quick_panel(tmp_path), settings
    )
    first.resize(900, 600)
    first.show()
    _app().processEvents()
    assert first.host.isVisible()
    assert first.host.height() > 300
    assert all(dock.toggleViewAction().isEnabled() for dock in first.docks.values())
    chat = first.docks["twitch.chat"]
    quick_actions = first.docks["core.quick-actions"]
    first.host.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, chat)
    first.host.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, quick_actions)
    chat.hide()
    assert first.save_layout()

    second = LiveWorkspace(
        [_chat_contribution()], _quick_panel(tmp_path), settings
    )
    assert second.restore_layout()
    assert second.host.dockWidgetArea(second.docks["twitch.chat"]) == Qt.DockWidgetArea.RightDockWidgetArea
    restored_quick_panel = second.docks["core.quick-actions"].widget()
    assert isinstance(restored_quick_panel, QuickActionsPanel)
    assert restored_quick_panel.grid.orientation == Qt.Orientation.Vertical
    assert second.docks["twitch.chat"].isHidden()

    second.reset_to_default()
    reset_chat = second.docks["twitch.chat"]
    reset_quick = second.docks["core.quick-actions"]
    assert not reset_chat.isHidden()
    assert not reset_chat.isFloating()
    assert (
        second.host.dockWidgetArea(reset_quick)
        == Qt.DockWidgetArea.TopDockWidgetArea
    )
    assert restored_quick_panel.grid.orientation == Qt.Orientation.Horizontal


def test_missing_saved_state_defers_default_until_workspace_is_shown(
    tmp_path: Path,
) -> None:
    _app()
    settings = QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat)
    workspace = LiveWorkspace(
        _live_contributions(),
        _quick_panel(tmp_path),
        settings,
    )
    chat = workspace.docks["twitch.chat"]

    assert not workspace.restore_layout()
    assert chat.isHidden()

    _show_workspace(workspace)

    assert not chat.isFloating()
    assert not chat.isHidden()
    assert chat.y() < workspace.docks["core.quick-actions"].y()


def test_saved_custom_floating_chat_layout_is_not_replaced_by_default(
    tmp_path: Path,
) -> None:
    _app()
    settings = QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat)
    first = LiveWorkspace(
        _live_contributions(),
        _quick_panel(tmp_path),
        settings,
    )
    _show_workspace(first)
    first.docks["twitch.chat"].setFloating(True)
    assert first.docks["twitch.chat"].isFloating()
    assert first.save_layout()

    restored = LiveWorkspace(
        _live_contributions(),
        _quick_panel(tmp_path),
        settings,
    )
    assert restored.restore_layout()
    _show_workspace(restored)

    assert restored.docks["twitch.chat"].isFloating()


def test_default_layout_uses_full_width_rows_and_descending_middle_columns(
    tmp_path: Path,
) -> None:
    _app()
    settings = QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat)
    workspace = LiveWorkspace(
        _live_contributions(),
        _quick_panel(tmp_path),
        settings,
    )
    _show_workspace(workspace)

    stream_tools = workspace.docks["twitch.stream-tools"]
    chat = workspace.docks["twitch.chat"]
    activity = workspace.docks["twitch.activity"]
    chatters = workspace.docks["twitch.chatters"]
    quick_actions = workspace.docks["core.quick-actions"]

    assert (
        workspace.host.dockWidgetArea(stream_tools)
        == Qt.DockWidgetArea.TopDockWidgetArea
    )
    assert (
        workspace.host.dockWidgetArea(quick_actions)
        == Qt.DockWidgetArea.TopDockWidgetArea
    )
    assert chat.x() < activity.x() < chatters.x()
    assert chat.width() > activity.width() > chatters.width()
    assert workspace.host.tabifiedDockWidgets(activity) == []
    assert all(dock.isVisible() for dock in (chat, activity, chatters))
    assert stream_tools.y() < chat.y() < quick_actions.y()
    assert stream_tools.width() >= workspace.host.width() - 10
    assert quick_actions.width() >= workspace.host.width() - 10
    assert quick_actions.height() < chat.height()
    assert quick_actions.height() <= quick_actions.minimumSizeHint().height() + 40
    _assert_docked_panels_do_not_overlap(workspace)


def test_dock_moves_reflow_without_tab_stacking_or_overlap(tmp_path: Path) -> None:
    _app()
    settings = QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat)
    workspace = LiveWorkspace(
        _live_contributions(),
        _quick_panel(tmp_path),
        settings,
    )
    _show_workspace(workspace)

    assert not (
        workspace.host.dockOptions()
        & workspace.host.DockOption.AllowTabbedDocks
    )
    for dock_id, area in (
        ("twitch.chat", Qt.DockWidgetArea.TopDockWidgetArea),
        ("twitch.chat", Qt.DockWidgetArea.BottomDockWidgetArea),
        ("twitch.chat", Qt.DockWidgetArea.LeftDockWidgetArea),
        ("twitch.chat", Qt.DockWidgetArea.RightDockWidgetArea),
        ("twitch.stream-tools", Qt.DockWidgetArea.LeftDockWidgetArea),
        ("twitch.activity", Qt.DockWidgetArea.TopDockWidgetArea),
        ("twitch.chatters", Qt.DockWidgetArea.BottomDockWidgetArea),
        ("core.quick-actions", Qt.DockWidgetArea.RightDockWidgetArea),
    ):
        dock = workspace.docks[dock_id]
        workspace.host.addDockWidget(area, dock)
        _app().processEvents()
        assert workspace.host.dockWidgetArea(dock) == area
        _assert_docked_panels_do_not_overlap(workspace)
    quick_panel = workspace.docks["core.quick-actions"].widget()
    assert isinstance(quick_panel, QuickActionsPanel)
    assert quick_panel.grid.orientation == Qt.Orientation.Vertical

    workspace.host.addDockWidget(
        Qt.DockWidgetArea.TopDockWidgetArea,
        workspace.docks["core.quick-actions"],
    )
    _app().processEvents()
    _app().processEvents()
    assert quick_panel.grid.orientation == Qt.Orientation.Horizontal


def test_horizontal_quick_actions_drop_normalizes_to_full_workspace_width(
    tmp_path: Path,
) -> None:
    _app()
    workspace = LiveWorkspace(
        _live_contributions(),
        _populated_quick_panel(tmp_path, 2),
        QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
    )
    _show_workspace(workspace)
    chat = workspace.docks["twitch.chat"]
    quick_actions = workspace.docks["core.quick-actions"]

    # Reproduce dropping the horizontal bar beneath Chat after the middle row
    # has already been split into Chat / Activity / Chatters columns.
    workspace.host.splitDockWidget(
        chat,
        quick_actions,
        Qt.Orientation.Vertical,
    )
    for _ in range(4):
        _app().processEvents()

    panel = quick_actions.widget()
    assert isinstance(panel, QuickActionsPanel)
    assert panel.grid.orientation == Qt.Orientation.Horizontal
    assert (
        workspace.host.dockWidgetArea(quick_actions)
        == Qt.DockWidgetArea.BottomDockWidgetArea
    )
    assert quick_actions.width() >= workspace.host.width() - 10
    _assert_docked_panels_do_not_overlap(workspace)


def test_native_quick_actions_redock_triggers_full_width_normalization(
    tmp_path: Path,
) -> None:
    _app()
    workspace = LiveWorkspace(
        _live_contributions(),
        _populated_quick_panel(tmp_path, 2),
        QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat),
    )
    _show_workspace(workspace)
    chat = workspace.docks["twitch.chat"]
    quick_actions = workspace.docks["core.quick-actions"]

    quick_actions.setFloating(True)
    _app().processEvents()
    workspace.host.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, quick_actions)
    workspace.host.splitDockWidget(
        chat,
        quick_actions,
        Qt.Orientation.Vertical,
    )
    quick_actions.setFloating(False)
    for _ in range(6):
        _app().processEvents()

    assert not quick_actions.isFloating()
    assert (
        workspace.host.dockWidgetArea(quick_actions)
        == Qt.DockWidgetArea.BottomDockWidgetArea
    )
    assert quick_actions.width() >= workspace.host.width() - 10
    _assert_docked_panels_do_not_overlap(workspace)


def test_hiding_quick_actions_reflows_immediately_without_persistence_loop(
    tmp_path: Path,
) -> None:
    _app()
    settings = QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat)
    workspace = LiveWorkspace(
        _live_contributions(),
        _populated_quick_panel(tmp_path, 8),
        settings,
    )
    _show_workspace(workspace)
    quick_actions = workspace.docks["core.quick-actions"]
    chat = workspace.docks["twitch.chat"]
    stream_tools = workspace.docks["twitch.stream-tools"]
    initial_chat_y = chat.y()
    initial_chat_height = chat.height()

    assert not (
        workspace.host.dockOptions()
        & QMainWindow.DockOption.AnimatedDocks
    )
    quick_actions.hide()
    _app().processEvents()

    assert quick_actions.isHidden()
    assert abs(chat.y() - initial_chat_y) <= 1
    assert chat.height() > initial_chat_height
    assert chat.y() - stream_tools.geometry().bottom() - 1 <= 4
    assert chat.geometry().bottom() >= workspace.host.height() - 1
    assert not settings.contains(LiveWorkspace.STATE_KEY)
    _assert_docked_panels_do_not_overlap(workspace)

    for _ in range(4):
        quick_actions.show()
        _app().processEvents()
        quick_actions.hide()
        _app().processEvents()
        assert chat.y() - stream_tools.geometry().bottom() - 1 <= 4
        assert chat.geometry().bottom() >= workspace.host.height() - 1
        _assert_docked_panels_do_not_overlap(workspace)

    quick_actions.show()
    _app().processEvents()
    assert not quick_actions.isHidden()
    assert not settings.contains(LiveWorkspace.STATE_KEY)


def test_live_dock_separator_is_compact_but_resizable(tmp_path: Path) -> None:
    _app()
    settings = QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat)
    workspace = LiveWorkspace(
        _live_contributions(),
        _quick_panel(tmp_path),
        settings,
    )

    separator_extent = workspace.host.style().pixelMetric(
        QStyle.PixelMetric.PM_DockWidgetSeparatorExtent,
        None,
        workspace.host,
    )
    assert separator_extent == 4


def test_reset_restores_default_geometry_after_dock_changes(tmp_path: Path) -> None:
    _app()
    settings = QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat)
    workspace = LiveWorkspace(
        _live_contributions(),
        _quick_panel(tmp_path),
        settings,
    )
    _show_workspace(workspace)
    chat = workspace.docks["twitch.chat"]
    activity = workspace.docks["twitch.activity"]
    chatters = workspace.docks["twitch.chatters"]
    quick_actions = workspace.docks["core.quick-actions"]

    workspace.host.tabifyDockWidget(chat, activity)
    workspace.host.tabifyDockWidget(activity, chatters)
    chat.setFloating(True)
    assert chat.isFloating()
    chatters.hide()
    workspace.host.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, quick_actions)
    workspace.save_layout()

    workspace.reset_to_default()
    _app().processEvents()

    assert chat.x() < activity.x() < chatters.x()
    assert chat.width() > activity.width() > chatters.width()
    assert workspace.host.tabifiedDockWidgets(activity) == []
    assert not chat.isFloating()
    assert not chatters.isHidden()
    assert chat.y() < quick_actions.y()
    assert quick_actions.width() >= workspace.host.width() - 10
    assert not settings.contains(LiveWorkspace.STATE_KEY)
    assert not settings.contains(LiveWorkspace.DOCKS_KEY)


def test_missing_module_dock_does_not_break_or_overwrite_saved_layout(tmp_path: Path) -> None:
    _app()
    settings = QSettings(str(tmp_path / "window.ini"), QSettings.Format.IniFormat)
    first = LiveWorkspace(
        [_chat_contribution()], _quick_panel(tmp_path), settings
    )
    assert first.save_layout()
    original = settings.value(LiveWorkspace.STATE_KEY)

    disabled = LiveWorkspace([], _quick_panel(tmp_path), settings)
    assert disabled.restore_layout()
    assert not disabled.save_layout()
    assert settings.value(LiveWorkspace.STATE_KEY) == original
