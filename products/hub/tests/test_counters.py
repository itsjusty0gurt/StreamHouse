from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from products.hub.automation.models import TaskDefinition, TriggerEvent
from products.hub.automation.routines import RoutineStore
from products.hub.automation.tasks import TaskRegistry
from products.hub.counters.models import CounterDefinition, counter_id_from_name, validate_counter_id
from products.hub.counters.service import CounterService
from products.hub.counters.store import CounterStore
from products.hub.counters.tasks import register_counter_tasks
from products.hub.core.backup import BackupManager
from products.hub.twitch.commands import TwitchCommandTriggerStore


def definition(counter_id: str = "farts", **changes) -> CounterDefinition:
    values = {"counter_id": counter_id, "display_name": "Farts", "singular": "fart", "plural": "farts"}
    values.update(changes)
    return CounterDefinition(**values)


class CounterStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.root = Path(self.temp.name) / "counters"
        self.store = CounterStore(self.root)
        self.service = CounterService(self.store)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_empty_first_load_create_reload_and_named_format(self) -> None:
        self.assertEqual(self.service.list_counters(), ())
        self.assertFalse(self.root.exists())
        self.service.create_counter(definition(), 4)
        reloaded = CounterService(CounterStore(self.root))
        self.assertEqual(reloaded.get_counter("farts").display_name, "Farts")
        self.assertEqual(reloaded.get_values("farts").channel_total, 4)
        payload = json.loads((self.root / "farts.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["viewers"], {})

    def test_duplicate_invalid_case_collision_and_path_traversal_rejected(self) -> None:
        self.service.create_counter(definition())
        with self.assertRaises(ValueError): self.service.create_counter(definition("FARTS"))
        for invalid in ("../farts", "farts.json", "a/b", "_bad"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError): validate_counter_id(invalid)
        self.assertEqual(counter_id_from_name("Channel Deaths"), "channel_deaths")

    def test_display_name_change_does_not_rename_file_and_delete_isolated(self) -> None:
        self.service.create_counter(definition())
        self.service.create_counter(definition("bonks", display_name="Bonks", singular="bonk", plural="bonks"))
        self.service.update_counter("farts", display_name="Gas Events")
        self.assertTrue((self.root / "farts.json").exists())
        self.service.delete_counter("farts")
        self.assertFalse((self.root / "farts.json").exists())
        self.assertIsNotNone(self.service.get_counter("bonks"))
        self.assertEqual([item.counter_id for item in self.service.list_counters()], ["bonks"])

    def test_definition_requires_a_tracked_scope_and_index_is_deterministic(self) -> None:
        with self.assertRaises(ValueError):
            definition(track_channel_total=False, track_stream_total=False, track_viewer_total=False)
        self.service.create_counter(definition("zebra"))
        self.service.create_counter(definition("alpha"))
        payload = json.loads((self.root / "index.json").read_text(encoding="utf-8"))
        self.assertEqual([item["counter_id"] for item in payload["counters"]], ["alpha", "zebra"])

    def test_versions_invalid_payload_and_backup_recovery(self) -> None:
        self.root.mkdir(parents=True)
        (self.root / "index.json").write_text('{"version": 99, "counters": []}', encoding="utf-8")
        with self.assertRaises(ValueError): self.service.list_counters()
        (self.root / "index.json").write_text('{"version": 1, "counters": "bad"}', encoding="utf-8")
        with self.assertRaises(ValueError): self.service.list_counters()
        self.store.save_definitions([definition()])
        self.store.mutate_data("farts", lambda payload: payload.update(channel_total=3))
        self.store.mutate_data("farts", lambda payload: payload.update(channel_total=7))
        (self.root / "farts.json").write_text("{broken", encoding="utf-8")
        self.assertEqual(self.service.get_values("farts").channel_total, 3)

    def test_invalid_named_counter_payload_is_rejected(self) -> None:
        self.store.save_definitions([definition()])
        (self.root / "farts.json").write_text(
            '{"version": 1, "counter_id": "wrong", "viewers": {}}',
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            self.service.get_values("farts")

    def test_structurally_invalid_payload_recovers_from_backup(self) -> None:
        self.service.create_counter(definition())
        self.service.update_values("farts", 2, ("channel_total",))
        self.service.update_values("farts", 3, ("channel_total",))
        (self.root / "farts.json").write_text('{"version":1,"counter_id":"farts","viewers":[]}', encoding="utf-8")
        self.assertEqual(self.service.get_values("farts").channel_total, 2)

    def test_delete_rolls_back_definition_and_files_when_index_save_fails(self) -> None:
        self.service.create_counter(definition())
        with patch("products.hub.counters.store.atomic_write_json", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.service.delete_counter("farts")
        self.assertIsNotNone(self.service.get_counter("farts"))
        self.assertTrue((self.root / "farts.json").exists())

    def test_backup_round_trip_includes_index_and_named_value_file(self) -> None:
        self.service.create_counter(definition(), 7)
        manager = BackupManager(Path(self.temp.name), Path(self.temp.name) / "archives")
        archive = manager.create("counter-test")
        restore_root = Path(self.temp.name) / "restored"
        BackupManager(restore_root).restore(archive)
        restored = CounterService(CounterStore(restore_root / "counters"))
        self.assertEqual(restored.get_values("farts").channel_total, 7)

    def test_default_command_seed_and_restore_never_create_counters(self) -> None:
        root = Path(self.temp.name) / "defaults"
        routines = RoutineStore(root / "routines.json")
        commands = TwitchCommandTriggerStore(root / "commands.json", routines)
        commands.seed_default_commands()
        commands.restore_default_commands()
        restarted = CounterService(CounterStore(root / "counters"))
        self.assertEqual(restarted.list_counters(), ())
        self.assertFalse((root / "counters").exists())


class CounterValueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory(); self.root = Path(self.temp.name) / "counters"
        self.service = CounterService(CounterStore(self.root)); self.service.create_counter(definition())
    def tearDown(self) -> None: self.temp.cleanup()

    def test_multiple_scopes_same_stream_and_new_stream_rollover(self) -> None:
        operation = self.service.update_values("farts", 1, ("channel_total", "stream_total", "viewer_total", "viewer_stream_total"), user_id="111", login="steve", display_name="Steve", stream_id="stream-1")
        self.assertEqual(operation.updated_scopes, ("channel_total", "stream_total", "viewer_total"))
        self.service.update_counter("farts", track_viewer_stream_total=True)
        self.service.update_values("farts", 2, ("stream_total", "viewer_stream_total"), user_id="111", stream_id="stream-1")
        same = CounterService(CounterStore(self.root)).get_values("farts", user_id="111", stream_id="stream-1")
        self.assertEqual((same.channel_total, same.stream_total, same.viewer_total, same.viewer_stream_total), (1, 3, 1, 2))
        new = self.service.update_values("farts", 1, ("stream_total", "viewer_stream_total"), user_id="111", stream_id="stream-2")
        self.assertEqual((new.values.stream_total, new.values.viewer_stream_total), (1, 1))

    def test_offline_stream_skipped_lifetime_updates_and_missing_viewer_read_zero(self) -> None:
        result = self.service.update_values("farts", 2, ("channel_total", "stream_total"))
        self.assertEqual(result.status, "partial_success")
        self.assertEqual(result.skipped_scopes, ("stream_total",))
        self.assertEqual(result.values.channel_total, 2)
        self.assertEqual(self.service.get_values("farts", user_id="missing").viewer_total, 0)
        self.assertEqual(self.service.viewer_rows("farts"), [])

    def test_minimum_set_reset_cached_name_and_remove_viewer(self) -> None:
        self.service.update_values("farts", -10, ("channel_total",))
        self.assertEqual(self.service.get_values("farts").channel_total, 0)
        self.assertEqual(self.service.set_value("farts", "channel_total", -1).status, "invalid_value")
        self.service.update_values("farts", 3, ("viewer_total",), user_id="111", display_name="Steve")
        self.service.update_values("farts", 1, ("viewer_total",), user_id="111", display_name="Stephen")
        self.assertEqual(self.service.get_values("farts", user_id="111").viewer_display_name, "Stephen")
        self.service.reset("farts", ("viewer_total",), user_id="111")
        self.assertEqual(self.service.get_values("farts", user_id="111").viewer_total, 0)
        self.assertTrue(self.service.remove_viewer("farts", "111")); self.assertEqual(self.service.viewer_rows("farts"), [])

    def test_concurrent_increments_do_not_get_lost(self) -> None:
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(lambda _index: self.service.update_values("farts", 1, ("channel_total",)), range(100)))
        self.assertEqual(self.service.get_values("farts").channel_total, 100)

    def test_failed_write_preserves_last_good_file(self) -> None:
        self.service.update_values("farts", 2, ("channel_total",))
        with patch("products.hub.counters.store.atomic_write_json", side_effect=OSError("disk full")):
            result = self.service.update_values("farts", 1, ("channel_total",))
        self.assertEqual(result.status, "persistence_failed")
        self.assertEqual(self.service.get_values("farts").channel_total, 2)

    def test_leaderboard_and_bot_exclusion(self) -> None:
        service = CounterService(CounterStore(self.root), bot_checker=lambda uid: uid == "bot")
        service.update_values("farts", 2, ("viewer_total",), user_id="a", display_name="Alpha")
        service.update_values("farts", 5, ("viewer_total",), user_id="b", display_name="Beta")
        skipped = service.update_values("farts", 9, ("channel_total", "viewer_total"), user_id="bot", display_name="Bot")
        self.assertEqual(skipped.status, "skipped_known_bot")
        self.assertEqual([row["user_id"] for row in service.leaderboard("farts", limit=2)], ["b", "a"])

    def test_minimum_status_missing_viewer_and_granular_all_viewer_reset(self) -> None:
        minimum = self.service.update_values("farts", -1, ("channel_total",))
        self.assertEqual(minimum.status, "minimum_reached")
        missing = self.service.update_values("farts", 1, ("viewer_total",), user_id="--")
        self.assertEqual(missing.status, "missing_viewer")
        self.assertEqual(self.service.viewer_rows("farts"), [])
        self.service.update_counter("farts", track_viewer_stream_total=True)
        for user_id in ("a", "b"):
            self.service.update_values("farts", 3, ("viewer_total", "viewer_stream_total"), user_id=user_id, stream_id="stream-1")
        result = self.service.reset("farts", (), stream_id="stream-1", all_viewer_scopes=("viewer_total",))
        self.assertEqual(result.status, "success")
        self.assertEqual([row["total"] for row in self.service.viewer_rows("farts", stream_id="stream-1")], [0, 0])
        self.assertEqual([row["stream_total"] for row in self.service.viewer_rows("farts", stream_id="stream-1")], [3, 3])

    def test_reset_updates_lifetime_and_skips_stream_scopes_offline(self) -> None:
        self.service.update_counter("farts", track_viewer_stream_total=True)
        self.service.update_values("farts", 4, ("viewer_total", "viewer_stream_total"), user_id="a", stream_id="stream-1")
        result = self.service.reset("farts", (), all_viewer_scopes=("viewer_total", "viewer_stream_total"))
        self.assertEqual(result.status, "partial_success")
        self.assertEqual(result.updated_scopes, ("viewer_total",))
        self.assertEqual(result.skipped_scopes, ("viewer_stream_total",))
        rows = self.service.viewer_rows("farts", stream_id="stream-1")
        self.assertEqual((rows[0]["total"], rows[0]["stream_total"]), (0, 4))


class CounterTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory(); self.service = CounterService(CounterStore(Path(self.temp.name) / "counters")); self.service.create_counter(definition())
        self.registry = TaskRegistry(); register_counter_tasks(self.registry, self.service, lambda: "stream-1")
    def tearDown(self) -> None: self.temp.cleanup()

    def test_registration_update_outputs_get_set_reset_and_leaderboard(self) -> None:
        self.assertEqual(set(self.registry.registered_types()), {"counter.update", "counter.get_value", "counter.set_value", "counter.reset", "counter.get_leaderboard"})
        context = {"user_id": "111", "user_login": "steve", "user": "Steve"}; trigger = TriggerEvent("command", "twitch", "command", context)
        update = TaskDefinition("1", "counter.update", "Update", {"counter_id": "farts", "amount": "1", "channel_total": True, "stream_total": True, "viewer_total": True, "output_prefix": "farts"})
        self.assertTrue(self.registry.execute(update, trigger).succeeded)
        self.assertEqual(context["automation.farts_channel_total"], "1"); self.assertEqual(context["automation.farts_viewer_total"], "1"); self.assertEqual(context["automation.farts_status"], "success")
        self.assertEqual(context["automation.farts_skipped_scopes"], "")
        leaderboard = TaskDefinition("2", "counter.get_leaderboard", "Board", {"counter_id": "farts", "viewer_scope": "lifetime", "limit": 5, "output_prefix": "farts"})
        self.assertTrue(self.registry.execute(leaderboard, trigger).succeeded); self.assertIn("Steve", context["automation.farts_leaderboard"])

    def test_missing_counter_and_missing_viewer_are_controlled(self) -> None:
        trigger = TriggerEvent("command", "twitch", "command", {})
        missing = TaskDefinition("1", "counter.update", "Update", {"counter_id": "gone", "amount": "1", "viewer_total": True, "output_prefix": "gone"})
        result = self.registry.execute(missing, trigger)
        self.assertFalse(result.succeeded); self.assertIn("does not exist", result.detail); self.assertEqual(trigger.context["automation.gone_status"], "missing_counter")

        missing_viewer = TaskDefinition("2", "counter.update", "Update", {"counter_id": "farts", "amount": "1", "viewer_total": True, "output_prefix": "farts"})
        result = self.registry.execute(missing_viewer, trigger)
        self.assertFalse(result.succeeded)
        self.assertEqual(trigger.context["automation.farts_status"], "missing_viewer")

    def test_generated_output_names_are_deterministic_and_prefixable(self) -> None:
        from products.hub.automation.variable_outputs import generated_output_definitions
        names = tuple(item.name for item in generated_output_definitions(
            "counter.update", {"counter_id": "farts", "output_prefix": "party_farts"}
        ))
        self.assertIn("automation.party_farts_channel_total", names)
        self.assertIn("automation.party_farts_status", names)
        self.assertIn("automation.party_farts_skipped_scopes", names)

    def test_decrease_operation_viewer_rank_and_disabled_status(self) -> None:
        context = {"user_id": "111", "user_login": "steve", "user": "Steve"}
        trigger = TriggerEvent("command", "twitch", "command", context)
        self.service.set_value("farts", "viewer_total", 5, user_id="111", display_name="Steve")
        decrease = TaskDefinition("1", "counter.update", "Decrease", {"counter_id": "farts", "operation": "decrease", "amount": "2", "viewer_total": True})
        self.assertTrue(self.registry.execute(decrease, trigger).succeeded)
        self.assertEqual(context["automation.farts_viewer_total"], "3")
        rank = TaskDefinition("2", "counter.get_value", "Rank", {"counter_id": "farts", "scope": "viewer_rank"})
        self.assertTrue(self.registry.execute(rank, trigger).succeeded)
        self.assertEqual(context["automation.farts_viewer_rank"], "1")
        self.service.set_enabled("farts", False)
        self.assertFalse(self.registry.execute(rank, trigger).succeeded)
        self.assertEqual(context["automation.farts_status"], "disabled_counter")


if __name__ == "__main__":
    unittest.main()
