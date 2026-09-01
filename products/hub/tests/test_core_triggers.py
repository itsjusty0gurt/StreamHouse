import json
import tempfile
import unittest
from pathlib import Path

from products.hub.automation.core_triggers import CoreTriggerStore
from products.hub.automation.routines import RoutineStore


class CoreTriggerStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.routines = RoutineStore(root / "routines.json")
        self.store = CoreTriggerStore(root / "core_triggers.json", self.routines)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_round_trip_links_lifecycle_trigger_to_routine(self) -> None:
        routine = self.routines.add("Start Stream")
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

    def test_timer_configuration_round_trips_in_current_schema(self) -> None:
        routine = self.routines.add("Random promo")
        trigger = self.store.add_timer(
            routine.routine_id,
            timer_mode="random",
            timer_minimum="1.5",
            timer_minimum_unit="minutes",
            timer_maximum="2",
            timer_maximum_unit="hours",
        )

        loaded = CoreTriggerStore(self.store.path, self.routines)
        saved = loaded.load()[0]
        payload = json.loads(self.store.path.read_text(encoding="utf-8"))

        self.assertEqual(payload["version"], 2)
        self.assertEqual(saved.trigger_id, trigger.trigger_id)
        self.assertEqual(saved.timer_mode, "random")
        self.assertEqual(saved.timer_minimum, "1.5")
        self.assertEqual(saved.timer_maximum_unit, "hours")

    def test_unknown_core_event_is_rejected(self) -> None:
        routine = self.routines.add("Unknown")
        with self.assertRaisesRegex(ValueError, "not supported"):
            self.store.add(routine.routine_id, "application.exploded")

    def test_obsolete_or_unversioned_schema_is_rejected(self) -> None:
        for payload in (
            {"triggers": []},
            {"version": 0, "triggers": []},
            {"version": 1, "triggers": []},
            {"version": "1", "triggers": []},
        ):
            with self.subTest(payload=payload):
                self.store.path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Unsupported Core trigger"):
                    self.store.load()

    def test_current_schema_does_not_invent_missing_trigger_ids(self) -> None:
        routine = self.routines.add("Lifecycle")
        self.store.path.write_text(
            json.dumps(
                {
                    "version": self.store.VERSION,
                    "triggers": [
                        {
                            "routine_id": routine.routine_id,
                            "event_type": "application.started",
                            "enabled": True,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        self.assertEqual(self.store.load(), [])


if __name__ == "__main__":
    unittest.main()
