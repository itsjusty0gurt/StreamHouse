import json
import tempfile
import unittest
from pathlib import Path

from automation.core_triggers import CoreTriggerStore
from automation.routines import RoutineStore


class CoreTriggerStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.routines = RoutineStore(root / "routines.json")
        self.store = CoreTriggerStore(root / "core_triggers.json", self.routines)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_round_trip_links_lifecycle_trigger_to_routine(self) -> None:
        routine = self.routines.add("Start Sally")
        trigger = self.store.add(routine.routine_id, "application.started")

        loaded_routines = RoutineStore(self.routines.path)
        loaded_routines.load()
        loaded = CoreTriggerStore(self.store.path, loaded_routines)
        loaded.load()

        saved = loaded.get(trigger.trigger_id)
        self.assertIsNotNone(saved)
        self.assertEqual(saved.event_type, "application.started")
        self.assertIn(
            trigger.trigger_id,
            loaded_routines.get(routine.routine_id).trigger_ids,
        )

    def test_evaluate_emits_only_enabled_matching_core_events(self) -> None:
        started = self.routines.add("Started")
        closing = self.routines.add("Closing")
        start_trigger = self.store.add(started.routine_id, "application.started")
        self.store.add(
            closing.routine_id, "application.closing", enabled=False
        )

        events = self.store.evaluate(
            "application.started", {"channel": "testchannel"}
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].trigger_id, start_trigger.trigger_id)
        self.assertEqual(events[0].service, "core")
        self.assertEqual(events[0].trigger_type, "application.started")
        self.assertEqual(events[0].context["event"], "Application Started")
        self.assertEqual(events[0].context["channel"], "testchannel")
        self.assertEqual(self.store.evaluate("application.closing"), ())

    def test_update_and_delete_preserve_routine_linkage(self) -> None:
        routine = self.routines.add("Lifecycle")
        trigger = self.store.add(routine.routine_id, "application.started")

        updated = self.store.update(
            trigger.trigger_id,
            event_type="application.closing",
            enabled=False,
        )
        self.assertEqual(updated.event_type, "application.closing")
        self.assertFalse(updated.enabled)
        self.assertTrue(self.store.delete(trigger.trigger_id))
        self.assertEqual(self.routines.get(routine.routine_id).trigger_ids, ())
        payload = json.loads(self.store.path.read_text(encoding="utf-8"))
        self.assertEqual(payload["triggers"], [])

    def test_unknown_core_event_is_rejected(self) -> None:
        routine = self.routines.add("Unknown")
        with self.assertRaisesRegex(ValueError, "not supported"):
            self.store.add(routine.routine_id, "application.exploded")


if __name__ == "__main__":
    unittest.main()
