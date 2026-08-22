from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from products.hub.automation.variable_providers import AdsVariableProvider
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.twitch.ads import AdsService


class FakeTwitchAdsClient:
    def __init__(self) -> None:
        self.commercial_calls: list[int] = []
        self.snooze_result: dict[str, object] = {}
        self.snooze_error: Exception | None = None

    def run_commercial(self, length: int) -> dict:
        self.commercial_calls.append(length)
        return {"length": length, "retry_after": 45, "message": "Started"}

    def snooze_next_ad(self) -> dict:
        if self.snooze_error is not None:
            raise self.snooze_error
        return dict(self.snooze_result)


class AdsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc)
        self.client = FakeTwitchAdsClient()
        self.service = AdsService(self.client)
        self.service.set_channel_live(True)

    def schedule(self, *, seconds: int = 600) -> dict[str, object]:
        return {
            "next_ad_at": (self.now + timedelta(seconds=seconds)).isoformat(),
            "last_ad_at": (self.now - timedelta(minutes=20)).isoformat(),
            "duration": 90,
            "preroll_free_time": 120,
            "snooze_count": 2,
            "snooze_refresh_at": (self.now + timedelta(minutes=15)).isoformat(),
        }

    def test_schedule_parses_countdowns_and_global_values(self) -> None:
        self.service.apply_schedule(self.schedule(), now=self.now)

        values = self.service.state.values(self.now)

        self.assertEqual(values["next_in"], 600)
        self.assertEqual(values["next_duration"], 90)
        self.assertEqual(values["preroll_free_time"], 120)
        self.assertEqual(values["snooze_count"], 2)
        self.assertFalse(values["in_progress"])

    def test_warnings_fire_once_and_reset_for_a_snoozed_schedule(self) -> None:
        self.service.apply_schedule(self.schedule(seconds=301), now=self.now)

        first = self.service.tick(self.now + timedelta(seconds=1))
        repeated = self.service.tick(self.now + timedelta(seconds=2))

        self.assertEqual([item.event_type for item in first], ["ads.warning.5_minutes"])
        self.assertEqual(repeated, ())

        self.client.snooze_result = self.schedule(seconds=601)
        self.service.snooze(now=self.now + timedelta(seconds=2))
        rescheduled = self.service.tick(self.now + timedelta(seconds=303))

        self.assertEqual(
            [item.event_type for item in rescheduled],
            ["ads.warning.5_minutes"],
        )

    def test_each_warning_threshold_fires_as_the_schedule_crosses_it(self) -> None:
        self.service.apply_schedule(self.schedule(seconds=301), now=self.now)

        events = []
        for elapsed in (1, 121, 181, 241):
            events.extend(self.service.tick(self.now + timedelta(seconds=elapsed)))

        self.assertEqual(
            [item.event_type for item in events],
            [
                "ads.warning.5_minutes",
                "ads.warning.3_minutes",
                "ads.warning.2_minutes",
                "ads.warning.1_minute",
            ],
        )

    def test_ad_begin_and_calculated_end_keep_event_context(self) -> None:
        started = self.service.observe_ad_break(
            {
                "started_at": self.now.isoformat(),
                "duration_seconds": 90,
                "is_automatic": False,
                "requester_user_id": "42",
                "requester_user_name": "Streamer",
            },
            received_at=self.now,
        )

        self.assertEqual(started.event_type, "ads.started")
        self.assertEqual(started.context["ads.requester.id"], "42")
        self.assertEqual(started.context["ads.remaining"], "90")
        self.assertTrue(self.service.state.in_progress)

        self.assertEqual(self.service.tick(self.now + timedelta(seconds=89)), ())
        ended = self.service.tick(self.now + timedelta(seconds=90))

        self.assertEqual([item.event_type for item in ended], ["ads.ended"])
        self.assertEqual(ended[0].context["ads.duration"], "90")
        self.assertFalse(self.service.state.in_progress)

    def test_commercial_cooldown_blocks_repeat_api_requests(self) -> None:
        result = self.service.run_commercial(180, now=self.now)

        self.assertEqual(result["retry_after"], 45)
        self.assertEqual(self.client.commercial_calls, [180])
        with self.assertRaisesRegex(ValueError, "45 seconds"):
            self.service.run_commercial(60, now=self.now)
        self.assertEqual(self.client.commercial_calls, [180])

    def test_offline_state_has_no_schedule_warnings(self) -> None:
        self.service.apply_schedule(self.schedule(seconds=301), now=self.now)
        self.service.set_channel_live(False)

        self.assertEqual(self.service.tick(self.now + timedelta(seconds=1)), ())

    def test_failed_snooze_does_not_fake_a_schedule_change(self) -> None:
        self.service.apply_schedule(self.schedule(), now=self.now)
        original = self.service.state.next_at
        self.client.snooze_error = OSError("Twitch unavailable")

        with self.assertRaisesRegex(OSError, "unavailable"):
            self.service.snooze(now=self.now)

        self.assertEqual(self.service.state.next_at, original)
        self.assertEqual(self.service.state.snooze_count, 2)

    def test_ads_variables_use_registry_provider_and_types(self) -> None:
        self.service.apply_schedule(self.schedule(), now=self.now)
        registry = VariableRegistry()
        registry.register(
            AdsVariableProvider(lambda: self.service.state.values(self.now))
        )

        self.assertEqual(registry.resolve("ads.next_in").value, 600)
        self.assertEqual(registry.resolve("ads.snooze_count").value, 2)
        self.assertFalse(registry.resolve("ads.in_progress").value)
        self.assertIsNone(registry.resolve("ads.duration").value)


if __name__ == "__main__":
    unittest.main()
