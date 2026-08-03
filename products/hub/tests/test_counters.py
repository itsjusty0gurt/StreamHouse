from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from products.hub.automation.models import TaskDefinition, TriggerEvent
from products.hub.automation.tasks import TaskRegistry
from products.hub.counters.models import CounterDefinition, counter_id_from_name, validate_counter_id
from products.hub.counters.service import CounterService
from products.hub.counters.store import CounterStore
from products.hub.counters.tasks import register_counter_tasks
from products.hub.automation.custom_variables import CustomVariableStore


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
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.values.channel_total, 2)
        self.assertEqual(self.service.get_values("farts", user_id="missing").viewer_total, 0)
        self.assertEqual(self.service.viewer_rows("farts"), [])

    def test_minimum_set_reset_cached_name_and_remove_viewer(self) -> None:
        self.service.update_values("farts", -10, ("channel_total",))
        self.assertEqual(self.service.get_values("farts").channel_total, 0)
        with self.assertRaises(ValueError): self.service.set_value("farts", "channel_total", -1)
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
            with self.assertRaises(OSError): self.service.update_values("farts", 1, ("channel_total",))
        self.assertEqual(self.service.get_values("farts").channel_total, 2)

    def test_leaderboard_and_bot_exclusion(self) -> None:
        service = CounterService(CounterStore(self.root), bot_checker=lambda uid: uid == "bot")
        service.update_values("farts", 2, ("viewer_total",), user_id="a", display_name="Alpha")
        service.update_values("farts", 5, ("viewer_total",), user_id="b", display_name="Beta")
        skipped = service.update_values("farts", 9, ("channel_total", "viewer_total"), user_id="bot", display_name="Bot")
        self.assertEqual(skipped.status, "skipped_bot")
        self.assertEqual([row["user_id"] for row in service.leaderboard("farts", limit=2)], ["b", "a"])


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
        self.assertEqual(context["farts_channel_total"], "1"); self.assertEqual(context["farts_viewer_total"], "1"); self.assertEqual(context["farts_status"], "updated")
        leaderboard = TaskDefinition("2", "counter.get_leaderboard", "Board", {"counter_id": "farts", "viewer_scope": "lifetime", "limit": 5, "output_prefix": "farts"})
        self.assertTrue(self.registry.execute(leaderboard, trigger).succeeded); self.assertIn("Steve", context["farts_leaderboard"])

    def test_missing_counter_and_missing_viewer_are_controlled(self) -> None:
        trigger = TriggerEvent("command", "twitch", "command", {})
        missing = TaskDefinition("1", "counter.update", "Update", {"counter_id": "gone", "amount": "1", "viewer_total": True, "output_prefix": "gone"})
        result = self.registry.execute(missing, trigger)
        self.assertFalse(result.succeeded); self.assertIn("does not exist", result.detail); self.assertEqual(trigger.context["gone_status"], "error")

    def test_generated_output_names_are_deterministic_and_prefixable(self) -> None:
        names = CustomVariableStore.generated_names(
            "counter.update", {"counter_id": "farts", "output_prefix": "party_farts"}
        )
        self.assertIn("party_farts_channel_total", names)
        self.assertIn("party_farts_status", names)


if __name__ == "__main__":
    unittest.main()
