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
    routine_choice,
    routine_id_from_choice,
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


def test_choice_carries_stable_id_across_rename() -> None:
    before = routine_choice({"id": "stable-123", "name": "Old", "group": ""})
    after = routine_choice({"id": "stable-123", "name": "New", "group": ""})

    assert before != after
    assert routine_id_from_choice(before) == "stable-123"
    assert routine_id_from_choice(after) == "stable-123"


def test_action_dispatches_stable_id_not_display_name() -> None:
    hub = _Hub()
    plugin = TouchPortalPlugin(hub)
    selected = routine_choice(hub.routines[0])

    plugin.handle_message(
        {
            "type": "action",
            "actionId": RUN_ROUTINE_ACTION_ID,
            "data": [{"id": ROUTINE_CHOICE_ID, "value": selected}],
        }
    )

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
        "value": ["Show / Go Live  [stable-123]"],
    }

    hub.available = False
    plugin.refresh_routines()
    second = json.loads(writer.write.call_args.args[0])
    assert second["value"] == [HUB_UNAVAILABLE_CHOICE]


def test_invalid_or_deleted_selection_does_not_fall_back_by_name() -> None:
    hub = _Hub()
    plugin = TouchPortalPlugin(hub)

    plugin.handle_message(
        {
            "type": "action",
            "actionId": RUN_ROUTINE_ACTION_ID,
            "data": [{"id": ROUTINE_CHOICE_ID, "value": "Go Live"}],
        }
    )

    assert hub.runs == []


def test_touch_portal_close_message_stops_instead_of_reconnecting() -> None:
    plugin = TouchPortalPlugin(_Hub())

    try:
        plugin.handle_message({"type": "closePlugin"})
    except PluginClosed:
        pass
    else:
        raise AssertionError("Touch Portal close request was ignored.")
