from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from unittest.mock import Mock

from products.hub.automation.variable_providers import AdsVariableProvider
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.twitch.ads import AdsService, SnoozeRejected


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

    def test_live_helix_unix_timestamps_drive_countdowns_not_cooldown(self) -> None:
        source = self.schedule(seconds=301)
        source["last_ad_at"] = (self.now - timedelta(minutes=2)).isoformat()
        for encode in (int, str):
            with self.subTest(encoding=encode):
                schedule = dict(source)
                for name in ("next_ad_at", "last_ad_at", "snooze_refresh_at"):
                    schedule[name] = encode(int(datetime.fromisoformat(schedule[name]).timestamp()))
                self.service.apply_schedule(schedule, now=self.now)
                values = self.service.state.values(self.now)
                self.assertEqual(values["next_in"], 301)
                self.assertEqual(values["snooze_refresh_in"], 900)
                self.assertIsNone(values["manual_retry_after"])
        events = self.service.tick(self.now + timedelta(seconds=1))
        self.assertEqual([event.event_type for event in events], ["ads.warning.5_minutes"])

    def test_unix_snooze_response_reschedules_warning(self) -> None:
        self.service.apply_schedule(self.schedule(seconds=301), now=self.now)
        self.service.tick(self.now + timedelta(seconds=1))
        self.client.snooze_result = {
            "next_ad_at": int((self.now + timedelta(seconds=601)).timestamp()),
            "snooze_refresh_at": int((self.now + timedelta(minutes=30)).timestamp()),
            "snooze_count": 1,
        }
        self.service.snooze(now=self.now + timedelta(seconds=2))
        self.assertEqual(self.service.state.values(self.now)["next_in"], 601)
        events = self.service.tick(self.now + timedelta(seconds=301))
        self.assertEqual([event.event_type for event in events], ["ads.warning.5_minutes"])

    def test_missing_and_invalid_ad_times_are_unavailable_not_epoch_dates(self) -> None:
        for value in (0, "0", -1, "-1", None, "", "invalid", 10**30, True):
            with self.subTest(value=value):
                self.service.apply_schedule({
                    "next_ad_at": value,
                    "last_ad_at": value,
                    "snooze_refresh_at": value,
                }, now=self.now)
                self.assertIsNone(self.service.state.next_at)
                self.assertIsNone(self.service.state.last_at)
                self.assertIsNone(self.service.state.snooze_refresh_at)

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
        schedule = {**self.schedule(), "last_ad_at": self.now.isoformat()}
        self.service.apply_schedule(schedule, now=self.now)

        self.assertEqual(result["retry_after"], 45)
        self.assertEqual(self.client.commercial_calls, [180])
        with self.assertRaisesRegex(ValueError, "45 seconds"):
            self.service.run_commercial(60, now=self.now)
        self.assertEqual(self.client.commercial_calls, [180])
        self.service.run_commercial(60, now=self.now + timedelta(seconds=45))
        self.assertEqual(self.client.commercial_calls, [180, 60])

    def test_pending_action_rejects_another_write_without_blocking(self) -> None:
        def snooze() -> dict:
            with self.assertRaisesRegex(ValueError, "already in progress"):
                self.service.run_commercial(60, now=self.now)
            return self.schedule()

        self.client.snooze_next_ad = Mock(side_effect=snooze)
        self.service.snooze(now=self.now)
        self.assertEqual(self.client.commercial_calls, [])
        self.service.run_commercial(60, now=self.now)
        self.assertEqual(self.client.commercial_calls, [60])

    def test_offline_state_has_no_schedule_warnings(self) -> None:
        self.service.apply_schedule(self.schedule(seconds=301), now=self.now)
        self.service.observe_ad_break(
            {"started_at": self.now.isoformat(), "duration_seconds": 60},
            received_at=self.now,
        )
        self.service.set_channel_live(False)

        self.assertFalse(self.service.state.in_progress)
        self.assertFalse(self.service.state.schedule_available)
        self.assertEqual(self.service.tick(self.now + timedelta(seconds=61)), ())

    def test_a_past_schedule_does_not_emit_late_warnings(self) -> None:
        self.service.apply_schedule(self.schedule(seconds=301), now=self.now)
        self.assertEqual(self.service.tick(self.now + timedelta(seconds=302)), ())

    def test_account_reset_drops_a_pending_snooze_response(self) -> None:
        def snooze() -> dict:
            self.service.reset()
            return self.schedule()

        self.client.snooze_next_ad = Mock(side_effect=snooze)
        self.service.snooze(now=self.now)
        self.assertFalse(self.service.state.schedule_available)
        self.assertIsNone(self.service.state.next_at)

    def test_snooze_invalidates_earlier_schedule_responses(self) -> None:
        old = self.schedule()
        new = self.schedule(seconds=900)
        revision = self.service.schedule_revision
        self.service.apply_schedule(old, now=self.now, expected_revision=revision)
        self.client.snooze_result = new
        self.service.snooze(now=self.now)
        self.assertFalse(self.service.apply_schedule(old, now=self.now, expected_revision=revision))
        self.assertEqual(self.service.state.values(self.now)["next_in"], 900)
        self.assertTrue(self.service.apply_schedule(
            new, now=self.now, expected_revision=self.service.schedule_revision
        ))

    def test_schedule_unavailable_then_restored_does_not_repeat_a_warning(self) -> None:
        schedule = self.schedule(seconds=301)
        self.service.apply_schedule(schedule, now=self.now)
        self.assertEqual(len(self.service.tick(self.now + timedelta(seconds=1))), 1)
        self.service.apply_schedule(None, error="Twitch timed out")
        self.assertFalse(self.service.state.schedule_available)
        self.service.apply_schedule(schedule, now=self.now + timedelta(seconds=3))
        self.assertEqual(self.service.tick(self.now + timedelta(seconds=4)), ())

    def test_uncertain_snooze_blocks_service_writes_and_preserves_confirmed_read_only_state(self) -> None:
        self.service.apply_schedule(self.schedule(), now=self.now)
        self.service.run_commercial(60, now=self.now)
        self.service.observe_ad_break(
            {"started_at": self.now.isoformat(), "duration_seconds": 60},
            received_at=self.now,
        )
        revision = self.service.schedule_revision
        self.client.snooze_next_ad = Mock(side_effect=TimeoutError("response lost"))
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, "Snooze outcome unknown"):
                self.service.snooze(now=self.now)
        self.client.snooze_next_ad.assert_called_once()
        self.assertEqual(self.service.state.snooze_unreconciled, "unknown")
        self.assertIsNone(self.service.state.next_at)
        self.assertIsNone(self.service.state.values(self.now)["snooze_count"])
        self.assertTrue(self.service.state.in_progress)
        self.assertEqual(self.service.state.values(self.now)["manual_retry_after"], 45)
        self.assertFalse(self.service.apply_schedule(self.schedule(), expected_revision=revision))

    def test_definitive_snooze_rejection_invalidates_schedule_without_claiming_uncertainty(self) -> None:
        self.service.apply_schedule(self.schedule(), now=self.now)
        self.client.snooze_error = SnoozeRejected("Twitch rejected Snooze (HTTP 429)")
        with self.assertRaisesRegex(SnoozeRejected, "HTTP 429"):
            self.service.snooze(now=self.now)
        self.assertEqual(self.service.state.snooze_unreconciled, "rejected")
        self.assertFalse(self.service.state.schedule_available)
        self.assertFalse(self.service.apply_schedule(self.schedule()))
        self.assertTrue(self.service.apply_schedule(
            {**self.schedule(), "snooze_count": 0}, now=self.now,
            expected_revision=self.service.schedule_revision,
        ))
        self.assertEqual(self.service.state.snooze_unreconciled, "")
        self.assertEqual(self.service.state.snooze_count, 0)

    def test_incomplete_success_reply_does_not_confirm_a_snooze(self) -> None:
        self.client.snooze_result = {"snooze_count": 1}
        with self.assertRaisesRegex(ValueError, "Snooze outcome unknown"):
            self.service.snooze(now=self.now)
        self.assertEqual(self.service.state.snooze_unreconciled, "unknown")

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
