import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QWidget

from products.hub.automation.quick_actions import QuickActionStore
from products.hub.modules.contributions import ModuleDockContribution
from products.hub.ui.live_workspace import LiveWorkspace, QuickActionsPanel


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _quick_panel(tmp_path: Path) -> QuickActionsPanel:
    routines = SimpleNamespace(
        routines=[SimpleNamespace(routine_id="routine-1", name="Safe Routine")]
    )
    return QuickActionsPanel(
        QuickActionStore(tmp_path / "quick_actions.json"), routines
    )


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
    first.host.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, chat)
    chat.hide()
    assert first.save_layout()

    second = LiveWorkspace(
        [_chat_contribution()], _quick_panel(tmp_path), settings
    )
    assert second.restore_layout()
    assert second.host.dockWidgetArea(second.docks["twitch.chat"]) == Qt.DockWidgetArea.RightDockWidgetArea
    assert second.docks["twitch.chat"].isHidden()

    second.reset_to_default()
    assert second.host.dockWidgetArea(second.docks["twitch.chat"]) == Qt.DockWidgetArea.LeftDockWidgetArea
    assert not second.docks["twitch.chat"].isHidden()


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
        == Qt.DockWidgetArea.BottomDockWidgetArea
    )
    assert chat.x() < activity.x() < chatters.x()
    assert chat.width() > activity.width() > chatters.width()
    assert workspace.host.tabifiedDockWidgets(activity) == []
    assert all(dock.isVisible() for dock in (chat, activity, chatters))
    assert stream_tools.y() < chat.y() < quick_actions.y()
    assert stream_tools.width() >= workspace.host.width() - 10
    assert quick_actions.width() >= workspace.host.width() - 10
    assert quick_actions.height() < chat.height()


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
    chatters.hide()
    workspace.host.addDockWidget(Qt.DockWidgetArea.TopDockWidgetArea, quick_actions)
    workspace.save_layout()

    workspace.reset_to_default()
    _app().processEvents()

    assert chat.x() < activity.x() < chatters.x()
    assert chat.width() > activity.width() > chatters.width()
    assert workspace.host.tabifiedDockWidgets(activity) == []
    assert not chatters.isHidden()
    assert (
        workspace.host.dockWidgetArea(quick_actions)
        == Qt.DockWidgetArea.BottomDockWidgetArea
    )
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
