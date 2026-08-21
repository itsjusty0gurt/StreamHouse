from __future__ import annotations

import unittest
from datetime import datetime, timezone

from products.hub.automation.models import TaskDefinition, TriggerEvent
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.value_tasks import (
    format_readable_duration,
    register_value_tasks,
)


class ValueTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = TaskRegistry()
        register_value_tasks(self.registry)

    def test_calendar_duration_uses_readable_largest_units(self) -> None:
        self.assertEqual(
            format_readable_duration(
                datetime(2025, 1, 15, tzinfo=timezone.utc),
                datetime(2026, 3, 15, tzinfo=timezone.utc),
            ),
            "1 year 2 months",
        )
        self.assertEqual(
            format_readable_duration(
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 5, 7, tzinfo=timezone.utc),
            ),
            "4 months 6 days",
        )

    def test_format_duration_handles_missing_invalid_and_future_values(self) -> None:
        for value, status in (
            ("", "missing"),
            ("not-a-date", "invalid"),
            ("2999-01-01T00:00:00Z", "future"),
        ):
            trigger = TriggerEvent("test", "core", "test", {"automation.start": value})
            task = TaskDefinition(
                "duration",
                "core.format_duration",
                "Format",
                {"start": "{automation.start}", "output_variable": "age"},
            )

            result = self.registry.execute(task, trigger)

            self.assertTrue(result.succeeded)
            self.assertEqual(trigger.context["automation.age"], "")
            self.assertEqual(trigger.context["automation.age_status"], status)

    def test_select_text_renders_the_selected_case_into_routine_context(self) -> None:
        trigger = TriggerEvent(
            "test",
            "core",
            "test",
            {"automation.status": "live", "automation.duration": "2 hours"},
        )
        task = TaskDefinition(
            "select",
            "core.select_text",
            "Select",
            {
                "selector": "{automation.status}",
                "cases": {"live": "Live for {automation.duration}.", "offline": "Offline."},
                "default": "Unknown.",
                "output_variable": "response",
            },
        )

        result = self.registry.execute(task, trigger)

        self.assertTrue(result.succeeded)
        self.assertEqual(trigger.context["automation.response"], "Live for 2 hours.")


if __name__ == "__main__":
    unittest.main()
