import json
from dataclasses import asdict
from datetime import datetime, timezone

import pytest

from products.hub.automation.models import TaskDefinition, TriggerEvent
from products.hub.automation.routines import RoutineStore
from products.hub.twitch.commands import (
    TwitchCommandTriggerDispatcher,
    TwitchCommandTriggerOutcome,
    TwitchCommandTriggerStore,
)
from products.hub.twitch.models import TwitchMessage
from products.hub.twitch.tasks import BuildCommandListTask


def make_store(root):
    store = TwitchCommandTriggerStore(
        root / "commands.json", RoutineStore(root / "routines.json")
    )
    store.load_for_startup()
    return store


def test_external_listing_persists_without_touching_triggers_or_routines(tmp_path):
    store = make_store(tmp_path)
    managed = store.add("managed", "Hello", aliases=["greet"])
    original = (store.path.read_bytes(), store.routine_store.path.read_bytes())
    assert not store.external_path.exists()
    external = store.save_external_command(" !Fish ", "Player command")
    assert external.name == "fish"
    assert store.resolve("fish") is None
    assert store.get(external.command_id) is None
    dispatcher = TwitchCommandTriggerDispatcher(store)

    def evaluate(text):
        return dispatcher.evaluate(TwitchMessage(
            username="Viewer", text=text, user_id="1",
            received_at=datetime.now(timezone.utc),
        ))

    for enabled in (True, False):
        updated = store.save_external_command(
            "fish", "Third-party listing", enabled, command_id=external.command_id,
        )
        result = evaluate("!fish")
        assert result.outcome is TwitchCommandTriggerOutcome.NOT_FOUND
        assert not result.handled
        assert store.external_listing_names() == ({"fish"} if enabled else set())
    assert evaluate("!greet").trigger_id == managed.trigger_id
    updated = store.save_external_command(
        "!Fishing", "Renamed listing", False, command_id=updated.command_id,
    )
    assert updated.command_id == external.command_id
    assert (store.path.read_bytes(), store.routine_store.path.read_bytes()) == original
    reloaded = make_store(tmp_path)
    assert reloaded.external_commands == (updated,)
    assert reloaded.delete_external_command(updated.command_id)
    assert make_store(tmp_path).external_commands == ()


def test_external_name_conflicts_include_builtin_templates_and_aliases(tmp_path):
    store = make_store(tmp_path)
    store.add("custom", aliases=["other"])
    external = store.save_external_command("fish")
    for name in ("!CUSTOM", "other", " !fish ", "uptime", "discord", "sallymemory"):
        with pytest.raises(ValueError, match="already used or reserved"):
            store.save_external_command(name)
    with pytest.raises(ValueError, match="already used or reserved"):
        store.save_external_command("other", command_id=external.command_id)
    with pytest.raises(ValueError, match="External Command"):
        store.add("new", aliases=["fish"])
    assert store.external_commands == (external,)


def test_command_list_is_sorted_bounded_and_safe_with_saved_overlaps(tmp_path):
    store = make_store(tmp_path)
    store.add("managed", aliases=["alias"])
    fish = store.save_external_command("fish")
    store.save_external_command("hidden", enabled=False)
    saved = [asdict(item) for item in store.external_commands]
    saved += [
        dict(asdict(fish), command_id="dup", name="!FISH"),
        dict(asdict(fish), command_id="conflict", name="alias"),
        dict(asdict(fish), command_id="template", name="discord"),
    ]
    store.external_path.write_text(json.dumps({"version": 1, "commands": saved}), encoding="utf-8")
    store = make_store(tmp_path)
    assert len(store.external_commands) == len(saved)

    def build(limit):
        event = TriggerEvent("test", "twitch", "command", {})
        task = TaskDefinition("list", BuildCommandListTask.task_type, "List", {"maximum_characters": limit})
        assert BuildCommandListTask(lambda: store).execute(task, event).succeeded
        assert event.context["automation.command_list_status"] == "available"
        return event.context["automation.command_list"]

    names = build(440).split(", ")
    assert names == sorted(set(names))
    assert {"!commands", "!fish", "!managed", "!uptime"} <= set(names)
    assert not {"!hidden", "!alias", "!discord"} & set(names)
    short = build(50)
    assert len(short) <= 50
    assert short.split(", ") == names[:len(short.split(", "))]


def test_unrecognized_external_file_does_not_block_hub_commands(tmp_path):
    store = make_store(tmp_path)
    contents = '{"version": 999, "commands": []}'
    store.external_path.write_text(contents, encoding="utf-8")
    reloaded = make_store(tmp_path)
    assert reloaded.resolve("uptime") is not None
    assert not reloaded.external_listing_names()
    assert reloaded.external_load_error
    with pytest.raises(ValueError, match="could not be loaded"):
        reloaded.save_external_command("fish")
    assert reloaded.external_path.read_text(encoding="utf-8") == contents
