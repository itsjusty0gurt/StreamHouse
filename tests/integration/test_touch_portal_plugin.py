from __future__ import annotations

import json
from unittest.mock import Mock

from integrations.touch_portal.StreamhouseHub.plugin import (
    HUB_UNAVAILABLE_CHOICE,
    PluginClosed,
    ROUTINE_CHOICE_ID,
    RUN_ROUTINE_ACTION_ID,
    HubApiError,
    TouchPortalPlugin,
    routine_choices,
)


class _Hub:
    def __init__(self) -> None:
        self.routines = [{"id": "stable-123", "name": "Go Live", "group": "Show"}]
        self.runs: list[str] = []
        self.available = True

    def list_routines(self) -> list[dict[str, str]]:
        if not self.available:
            raise HubApiError("unavailable")
        return list(self.routines)

    def run_routine(self, routine_id: str) -> None:
        if not self.available:
            raise HubApiError("unavailable")
        self.runs.append(routine_id)


def _action(value: str) -> dict[str, object]:
    return {
        "type": "action",
        "actionId": RUN_ROUTINE_ACTION_ID,
        "data": [{"id": ROUTINE_CHOICE_ID, "value": value}],
    }


def test_choice_labels_hide_stable_ids_and_track_rename() -> None:
    before = routine_choices([{"id": "stable-123", "name": "Old", "group": ""}])
    after = routine_choices([{"id": "stable-123", "name": "New", "group": ""}])

    assert before == {"Old": "stable-123"}
    assert after == {"New": "stable-123"}
    assert "stable-123" not in next(iter(after))


def test_action_dispatches_stable_id_not_display_name() -> None:
    hub = _Hub()
    plugin = TouchPortalPlugin(hub)
    plugin.refresh_routines()

    plugin.handle_message(_action("Go Live"))

    assert hub.runs == ["stable-123"]


def test_refresh_sends_dynamic_choices_and_unavailable_state() -> None:
    hub = _Hub()
    plugin = TouchPortalPlugin(hub)
    writer = Mock()
    plugin._writer = writer

    plugin.refresh_routines()
    first = json.loads(writer.write.call_args.args[0])
    assert first == {
        "type": "choiceUpdate",
        "id": ROUTINE_CHOICE_ID,
        "value": ["Go Live"],
    }

    hub.available = False
    plugin.refresh_routines()
    second = json.loads(writer.write.call_args.args[0])
    assert second["value"] == [HUB_UNAVAILABLE_CHOICE]


def test_invalid_or_deleted_selection_does_not_fall_back_by_name() -> None:
    hub = _Hub()
    plugin = TouchPortalPlugin(hub)
    plugin.refresh_routines()
    hub.routines = []
    plugin.refresh_routines()

    plugin.handle_message(_action("Go Live"))

    assert hub.runs == []


def test_disabled_routine_selection_is_removed_before_dispatch() -> None:
    hub = _Hub()
    plugin = TouchPortalPlugin(hub)
    plugin.refresh_routines()
    hub.routines = []  # Hub lists enabled routines only.
    plugin.refresh_routines()

    plugin.handle_message(_action("Go Live"))

    assert hub.runs == []


def test_duplicate_names_use_group_then_stable_opaque_disambiguation() -> None:
    choices = routine_choices(
        [
            {"id": "routine-social", "name": "Discord Link", "group": "Social"},
            {"id": "routine-command", "name": "Discord Link", "group": "Commands"},
            {"id": "routine-command-2", "name": "Discord Link", "group": "Commands"},
        ]
    )

    assert choices["Discord Link — Social"] == "routine-social"
    command_labels = [
        label for label in choices if label.startswith("Discord Link — Commands")
    ]
    assert len(command_labels) == 2
    assert len(set(command_labels)) == 2
    assert all("routine-command" not in label for label in command_labels)
    assert set(choices.values()) == {
        "routine-social",
        "routine-command",
        "routine-command-2",
    }


def test_rename_refresh_replaces_label_without_name_execution_fallback() -> None:
    hub = _Hub()
    plugin = TouchPortalPlugin(hub)
    plugin.refresh_routines()
    hub.routines[0]["name"] = "Starting Soon"
    plugin.refresh_routines()

    plugin.handle_message(_action("Go Live"))
    assert hub.runs == []

    plugin.handle_message(_action("Starting Soon"))
    assert hub.runs == ["stable-123"]


def test_touch_portal_close_message_stops_instead_of_reconnecting() -> None:
    plugin = TouchPortalPlugin(_Hub())

    try:
        plugin.handle_message({"type": "closePlugin"})
    except PluginClosed:
        pass
    else:
        raise AssertionError("Touch Portal close request was ignored.")
