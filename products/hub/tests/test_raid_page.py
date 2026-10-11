import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings, QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from products.hub.twitch.auth import TwitchToken
from products.hub.twitch.live import TwitchEventSubSocket
from products.hub.twitch.models import TwitchEvent, TwitchEventTransport
from products.hub.twitch.service import TwitchService
from products.hub.ui.raid_page import (
    RAID_COUNTDOWN_SECONDS,
    RAID_SECONDARY_TEXT_COLOR,
    RaidCancelWorker,
    RaidActionWorker,
    RaidCandidate,
    RaidCandidatesWorker,
    RaidMessageWorker,
    RaidPage,
    format_raid_uptime,
    twitch_channel_url,
)


def _stream(
    user_id: str = "viewer-1",
    *,
    login: str = "channel_login",
    name: str = "Channel Name",
    game: str = "Just Chatting",
    title: str = "A very good stream",
    viewers: int = 42,
    stream_type: str = "live",
) -> dict:
    return {
        "id": f"stream-{user_id}",
        "user_id": user_id,
        "user_login": login,
        "user_name": name,
        "game_name": game,
        "title": title,
        "viewer_count": viewers,
        "started_at": "2026-09-25T18:00:00Z",
        "thumbnail_url": "",
        "type": stream_type,
    }


class RaidPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.settings = QSettings(
            str(Path(self.temporary.name) / "raid.ini"),
            QSettings.Format.IniFormat,
        )
        self.url_opener = Mock(return_value=True)
        token = TwitchToken(
            "access",
            "refresh",
            9999999999,
            ["user:read:follows", "channel:manage:raids"],
            user_id="channel-1",
        )
        self.auth = Mock(token=token)
        self.service = Mock(broadcaster_user_id="channel-1")
        self.now = datetime(2026, 9, 25, 19, 0, tzinfo=timezone.utc)
        self.service.start_raid.return_value = self.now
        self.service.cancel_raid.return_value = True
        self.service.send_message.return_value = True
        self.page = RaidPage(
            self.service,
            self.auth,
            clock=lambda: self.now,
            settings=self.settings,
            url_opener=self.url_opener,
        )

    def tearDown(self) -> None:
        self.page.shutdown()
        self.page.deleteLater()
        self.application.processEvents()
        self.settings.sync()
        self.temporary.cleanup()

    def _apply(self, values: list[dict]) -> None:
        self.page._generation += 1
        worker = RaidCandidatesWorker(self.service, self.page._generation)
        self.page._load_workers.add(worker)
        self.page._load_completed(worker, self.page._generation, values)
        self.application.processEvents()

    def _outgoing_raid_event(
        self,
        target_id: str = "viewer-1",
        *,
        target_login: str = "channel_login",
        from_id: str = "channel-1",
    ) -> TwitchEvent:
        return TwitchEvent(
            subscription_type="channel.raid",
            version="1",
            received_at=self.now,
            message_id=f"raid-{target_id}",
            broadcaster_user_id="channel-1",
            broadcaster_user_login="streamer",
            broadcaster_user_name="Streamer",
            transport=TwitchEventTransport.WEBSOCKET,
            payload={
                "event": {
                    "from_broadcaster_user_id": from_id,
                    "to_broadcaster_user_id": target_id,
                    "to_broadcaster_user_login": target_login,
                }
            },
        )

    def test_candidate_uses_live_stream_metadata_and_excludes_offline(self) -> None:
        stream = _stream()
        stream["thumbnail_url"] = (
            "https://example.test/live-{width}x{height}.jpg"
        )
        candidate = RaidCandidate.from_twitch(stream)

        self.assertIsNotNone(candidate)
        self.assertEqual(candidate.user_id, "viewer-1")
        self.assertEqual(candidate.login, "channel_login")
        self.assertEqual(candidate.display_name, "Channel Name")
        self.assertEqual(candidate.category, "Just Chatting")
        self.assertEqual(candidate.title, "A very good stream")
        self.assertEqual(candidate.viewer_count, 42)
        self.assertEqual(
            candidate.thumbnail_url,
            "https://example.test/live-320x180.jpg",
        )
        self.assertIsNone(
            RaidCandidate.from_twitch(_stream(stream_type="offline"))
        )

    def test_card_displays_name_category_title_viewers_uptime_and_raid(self) -> None:
        self._apply([_stream()])

        card = self.page._cards["viewer-1"]
        self.assertEqual(card.name_label.full_text, "Channel Name")
        self.assertEqual(card.category_label.full_text, "Just Chatting")
        self.assertEqual(card.title_label.full_text, "A very good stream")
        self.assertIn("42 viewers", card.stats_label.text())
        self.assertIn("Live", card.stats_label.text())
        self.assertEqual(card.raid_button.text(), "Raid")
        self.assertIn(RAID_SECONDARY_TEXT_COLOR, card.stats_label.styleSheet())
        self.assertIn(RAID_SECONDARY_TEXT_COLOR, card.title_label.styleSheet())
        self.assertIn("palette(highlight)", card.category_label.styleSheet())
        self.assertNotIn(RAID_SECONDARY_TEXT_COLOR, card.name_label.styleSheet())

    def test_search_matches_all_supported_fields_case_insensitively(self) -> None:
        self._apply(
            [
                _stream(),
                _stream(
                    "viewer-2",
                    login="speed_runner",
                    name="Second Channel",
                    game="Super Metroid",
                    title="World Record Attempts",
                    viewers=12,
                ),
            ]
        )

        for query in (
            "SECOND",
            "speed_run",
            "metroid",
            "record attempts",
        ):
            with self.subTest(query=query):
                self.page.search_edit.setText(query)
                self.application.processEvents()
                self.assertTrue(self.page._cards["viewer-1"].isHidden())
                self.assertFalse(self.page._cards["viewer-2"].isHidden())

        self.page.search_edit.setText("missing")
        self.assertIn("No live channels match", self.page.status_label.text())
        self.page.search_edit.clear()
        self.assertFalse(self.page._cards["viewer-1"].isHidden())
        self.assertFalse(self.page._cards["viewer-2"].isHidden())

    def test_loading_is_dispatched_without_calling_service_on_ui_thread(self) -> None:
        self.page.load_pool.start = Mock()

        self.page.refresh()

        self.service.get_followed_live_channels.assert_not_called()
        worker = self.page.load_pool.start.call_args.args[0]
        self.assertIsInstance(worker, RaidCandidatesWorker)
        self.assertIn("Loading", self.page.status_label.text())

    def test_activation_refreshes_each_time_but_not_while_in_flight(self) -> None:
        self.page.load_pool.start = Mock()

        self.page.activate()
        self.page.activate()

        self.page.load_pool.start.assert_called_once()
        worker = self.page.load_pool.start.call_args.args[0]
        self.page._load_completed(worker, self.page._generation, [_stream()])

        self.page.activate()

        self.assertEqual(self.page.load_pool.start.call_count, 2)

    def test_refresh_preserves_active_raid_message_and_cards_while_loading(self) -> None:
        self._apply([_stream(), _stream("viewer-2", name="Second Channel")])
        active = self.page._candidates[0]
        self.page._set_active_raid(active, self.now)
        self.page.raid_message_edit.setText("Raid time!")
        cards = dict(self.page._cards)
        self.page.load_pool.start = Mock()

        self.page.refresh()

        self.assertEqual(self.page._cards, cards)
        self.assertIs(self.page._active_raid.candidate, active)
        self.assertTrue(self.page.countdown_timer.isActive())
        self.assertEqual(self.page.raid_message_edit.text(), "Raid time!")
        self.assertTrue(
            self.page._cards[active.user_id].property("activeRaidTarget")
        )
        self.assertIn("Refreshing", self.page.status_label.text())

        worker = self.page.load_pool.start.call_args.args[0]
        self.page._load_completed(
            worker,
            self.page._generation,
            [_stream(), _stream("viewer-2", name="Second Channel")],
        )

        self.assertEqual(self.page.raid_message_edit.text(), "Raid time!")
        self.assertIs(self.page._active_raid.candidate, active)
        self.assertTrue(
            self.page._cards[active.user_id].property("activeRaidTarget")
        )

    def test_manual_refresh_uses_same_guarded_refresh_path(self) -> None:
        self.page.load_pool.start = Mock()

        self.page.refresh_button.click()
        self.page.refresh_button.click()

        self.page.load_pool.start.assert_called_once()

    def test_resize_and_repaint_do_not_trigger_refresh(self) -> None:
        with patch.object(self.page, "refresh") as refresh:
            self.page.show()
            self.page.resize(800, 700)
            self.page.repaint()
            self.application.processEvents()

        refresh.assert_not_called()

    def test_real_fetch_runs_off_ui_thread_and_completes_on_page(self) -> None:
        worker_threads = []

        def load_channels():
            worker_threads.append(QThread.currentThread())
            return [_stream()]

        self.service.get_followed_live_channels.side_effect = load_channels
        self.page.refresh()
        for _ in range(100):
            if not self.page._loading:
                break
            QTest.qWait(10)

        self.assertFalse(self.page._loading)
        self.assertEqual(len(worker_threads), 1)
        self.assertIsNot(worker_threads[0], self.page.thread())
        self.assertIn("viewer-1", self.page._cards)

    def test_missing_raid_scope_keeps_results_but_disables_actions(self) -> None:
        self.auth.token.scopes = ["user:read:follows"]
        self._apply([_stream()])

        self.assertFalse(self.page._cards["viewer-1"].raid_button.isEnabled())
        self.assertFalse(self.page.permission_label.isHidden())

    def test_signed_out_and_missing_scope_states_are_clear(self) -> None:
        self.auth.token = None
        self.page.refresh()
        self.assertIn("Connect Twitch", self.page.status_label.text())

        self.auth.token = TwitchToken(
            "access", "refresh", 9999999999, [], user_id="channel-1"
        )
        self.page.refresh()
        self.assertIn("Additional Twitch permission", self.page.status_label.text())

    def test_empty_and_api_failure_states(self) -> None:
        self._apply([])
        self.assertIn("No channels you follow", self.page.status_label.text())

        self.page._generation += 1
        worker = RaidCandidatesWorker(self.service, self.page._generation)
        self.page._load_workers.add(worker)
        self.page._load_failed(
            worker, self.page._generation, "network", "test failure"
        )
        self.assertEqual(
            self.page.status_label.text(),
            "Couldn’t load live channels. Try again.",
        )

    def test_raid_requires_confirmation_and_reuses_existing_service_path(self) -> None:
        self._apply([_stream()])
        candidate = self.page._candidates[0]
        self.page.raid_pool.start = Mock()

        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Cancel,
        ):
            self.page._confirm_raid(candidate)
        self.page.raid_pool.start.assert_not_called()

        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.page._confirm_raid(candidate)
            self.page._confirm_raid(candidate)
        self.page.raid_pool.start.assert_called_once()

        worker = self.page.raid_pool.start.call_args.args[0]
        self.assertIsInstance(worker, RaidActionWorker)
        worker.run()
        self.application.processEvents()

        self.service.start_raid.assert_called_once_with("viewer-1")
        self.service.send_message.assert_not_called()
        self.assertEqual(
            self.page.status_label.text(),
            "Raid countdown started for Channel Name.",
        )
        self.assertIsNotNone(self.page._active_raid)
        self.assertEqual(self.page.countdown_label.text(), "Starting in 01:30")
        self.assertEqual(
            self.page.countdown_note_label.text(),
            "Raid will start automatically when the countdown ends.",
        )
        self.assertFalse(hasattr(self.page, "raid_now_button"))
        self.url_opener.assert_not_called()
        self.assertEqual(QThread.currentThread(), self.page.thread())

    def test_raid_failure_restores_button_and_reports_error(self) -> None:
        self._apply([_stream()])
        candidate = self.page._candidates[0]
        worker = RaidActionWorker(self.service, candidate)
        self.page._raid_workers.add(worker)
        self.page._cards[candidate.user_id].set_raid_pending(True)

        self.page._raid_completed(worker, candidate, None, "network")

        self.assertTrue(self.page._cards[candidate.user_id].raid_button.isEnabled())
        self.assertIn("Couldn’t start", self.page.status_label.text())

    def test_raid_message_copy_and_send_reuse_normal_chat_path(self) -> None:
        self.page.raid_message_edit.setText("Raid time!")
        self.page._copy_raid_message()
        self.assertEqual(QApplication.clipboard().text(), "Raid time!")
        self.assertEqual(self.page.status_label.text(), "Raid message copied.")

        self.page.raid_pool.start = Mock()
        self.page._send_raid_message()
        worker = self.page.raid_pool.start.call_args.args[0]
        self.assertIsInstance(worker, RaidMessageWorker)
        self.assertEqual(worker.message, "Raid time!")
        worker.run()
        self.application.processEvents()

        self.service.send_message.assert_called_once_with(
            "Raid time!", as_bot=False
        )
        self.assertEqual(
            self.page.status_label.text(),
            "Raid message sent to chat.",
        )
        self.assertEqual(self.page.raid_message_edit.text(), "Raid time!")

    def test_raid_network_actions_run_off_the_ui_thread(self) -> None:
        worker_threads = []

        def start_raid(_target_id):
            worker_threads.append(QThread.currentThread())
            return self.now

        self.service.start_raid.side_effect = start_raid
        self._apply([_stream()])
        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            self.page._confirm_raid(self.page._candidates[0])
        for _ in range(100):
            if not self.page._raid_workers:
                break
            QTest.qWait(10)

        self.assertEqual(len(worker_threads), 1)
        self.assertIsNot(worker_threads[0], self.page.thread())
        self.assertIsNotNone(self.page._active_raid)

    def test_active_target_blocks_duplicate_start_and_marks_card(self) -> None:
        self._apply(
            [
                _stream(),
                _stream("viewer-2", name="Second Channel"),
            ]
        )
        candidate = self.page._candidates[0]
        self.page._set_active_raid(candidate, self.now)

        self.assertTrue(
            self.page._cards["viewer-1"].property("activeRaidTarget")
        )
        self.assertFalse(self.page._cards["viewer-1"].raid_button.isEnabled())
        self.assertFalse(self.page._cards["viewer-2"].raid_button.isEnabled())
        with patch.object(QMessageBox, "question") as question:
            self.page._confirm_raid(self.page._candidates[1])
        question.assert_not_called()

    def test_countdown_uses_twitch_start_time_and_never_goes_negative(self) -> None:
        self._apply([_stream()])
        self.page._set_active_raid(self.page._candidates[0], self.now)

        self.now += timedelta(seconds=31)
        self.page._update_countdown()
        self.assertEqual(self.page.countdown_label.text(), "Starting in 00:59")

        self.now += timedelta(seconds=RAID_COUNTDOWN_SECONDS)
        self.page._update_countdown()
        self.assertIsNone(self.page._active_raid)
        self.assertFalse(self.page.countdown_timer.isActive())
        self.assertIs(
            self.page._raid_confirmation_target,
            self.page._candidates[0],
        )
        self.assertTrue(self.page.raid_confirmation_timer.isActive())
        self.assertIn(
            "Waiting for Twitch confirmation",
            self.page.status_label.text(),
        )

    def test_countdown_correlation_survives_until_outgoing_confirmation(self) -> None:
        self._apply([_stream()])
        self.page.open_raided_channel_checkbox.setChecked(True)
        candidate = self.page._candidates[0]
        self.page._set_active_raid(candidate, self.now)
        self.now += timedelta(seconds=RAID_COUNTDOWN_SECONDS + 1)

        self.page._update_countdown()
        self.page._handle_raid_event(self._outgoing_raid_event())

        self.url_opener.assert_called_once()
        self.assertEqual(
            self.url_opener.call_args.args[0].toString(),
            "https://www.twitch.tv/channel_login",
        )
        self.assertIsNone(self.page._active_raid)
        self.assertIsNone(self.page._raid_confirmation_target)
        self.assertFalse(self.page.raid_confirmation_timer.isActive())

    def test_unconfirmed_countdown_correlation_expires_without_opening(self) -> None:
        self._apply([_stream()])
        candidate = self.page._candidates[0]
        self.page.open_raided_channel_checkbox.setChecked(True)
        self.page._set_active_raid(candidate, self.now)
        self.now += timedelta(seconds=RAID_COUNTDOWN_SECONDS + 1)
        self.page._update_countdown()

        self.page._expire_raid_confirmation()

        self.assertIsNone(self.page._raid_confirmation_target)
        self.assertTrue(self.page._cards[candidate.user_id].raid_button.isEnabled())
        self.url_opener.assert_not_called()

    def test_cancel_success_clears_active_state(self) -> None:
        self._apply([_stream()])
        self.page._set_active_raid(self.page._candidates[0], self.now)
        self.page.raid_pool.start = Mock()

        self.page._cancel_active_raid()
        worker = self.page.raid_pool.start.call_args.args[0]
        self.assertIsInstance(worker, RaidCancelWorker)
        worker.run()
        self.application.processEvents()

        self.service.cancel_raid.assert_called_once_with()
        self.assertIsNone(self.page._active_raid)
        self.assertEqual(self.page.status_label.text(), "Raid cancelled.")
        self.assertTrue(self.page._cards["viewer-1"].raid_button.isEnabled())
        self.url_opener.assert_not_called()

    def test_cancel_failure_preserves_active_state(self) -> None:
        self._apply([_stream()])
        self.page._set_active_raid(self.page._candidates[0], self.now)
        worker = RaidCancelWorker(self.service)
        self.page._cancel_workers.add(worker)

        self.page._cancel_completed(worker, False, "network")

        self.assertIsNotNone(self.page._active_raid)
        self.assertTrue(self.page.countdown_timer.isActive())
        self.assertIn("Couldn’t cancel", self.page.status_label.text())

    def test_outgoing_raid_event_clears_matching_active_target(self) -> None:
        self._apply([_stream()])
        self.page._set_active_raid(self.page._candidates[0], self.now)
        self.page._handle_raid_event(self._outgoing_raid_event())

        self.assertIsNone(self.page._active_raid)
        self.assertFalse(self.page.open_raided_channel_checkbox.isChecked())
        self.url_opener.assert_not_called()
        self.assertEqual(
            self.page.status_label.text(),
            "Raid sent to Channel Name.",
        )

    def test_confirmed_outgoing_raid_opens_target_in_default_browser_once(self) -> None:
        self._apply([_stream()])
        self.page.open_raided_channel_checkbox.setChecked(True)
        candidate = self.page._candidates[0]
        self.page._set_active_raid(candidate, self.now)
        event = self._outgoing_raid_event(target_login="confirmed_login")

        self.page._handle_raid_event(event)
        self.page._handle_raid_event(event)

        self.url_opener.assert_called_once()
        self.assertEqual(
            self.url_opener.call_args.args[0].toString(),
            "https://www.twitch.tv/confirmed_login",
        )
        self.assertIsNone(self.page._active_raid)

    def test_live_eventsub_notification_reaches_browser_open_activation(self) -> None:
        self._apply([_stream()])
        self.page.open_raided_channel_checkbox.setChecked(True)
        self.page._set_active_raid(self.page._candidates[0], self.now)
        socket = TwitchEventSubSocket(
            on_welcome=lambda _session_id: None,
            on_message=lambda _message: None,
            on_notification=TwitchService._receive_notification,
            on_diagnostic=lambda _diagnostic: None,
            on_revocation=lambda _status: None,
            on_error=lambda _message: None,
            on_bus_event=TwitchService._publish_bus_event,
        )
        event = {
            "from_broadcaster_user_id": "channel-1",
            "from_broadcaster_user_login": "streamer",
            "from_broadcaster_user_name": "Streamer",
            "to_broadcaster_user_id": "viewer-1",
            "to_broadcaster_user_login": "channel_login",
            "to_broadcaster_user_name": "Channel Name",
            "viewers": 42,
        }
        socket._receive_text(
            json.dumps(
                {
                    "metadata": {
                        "message_id": "raid-live-1",
                        "message_type": "notification",
                        "message_timestamp": "2026-09-25T19:01:30Z",
                        "subscription_type": "channel.raid",
                        "subscription_version": "1",
                    },
                    "payload": {
                        "subscription": {"version": "1"},
                        "event": event,
                    },
                }
            )
        )
        self.application.processEvents()

        self.url_opener.assert_called_once()
        self.assertEqual(
            self.url_opener.call_args.args[0].toString(),
            "https://www.twitch.tv/channel_login",
        )
        self.assertIsNone(self.page._raid_confirmation_target)
        socket.deleteLater()

    def test_missing_or_invalid_confirmed_login_does_not_open_browser(self) -> None:
        self._apply([_stream()])
        self.page.open_raided_channel_checkbox.setChecked(True)

        for login in ("", "not/a/login", "https://example.test"):
            self.page._set_active_raid(self.page._candidates[0], self.now)
            with patch("products.hub.ui.raid_page.Logger.warning") as warning:
                self.page._handle_raid_event(
                    self._outgoing_raid_event(target_login=login)
                )
            self.assertIn("valid target login", warning.call_args.args[0])

        self.url_opener.assert_not_called()

    def test_start_cancel_incoming_and_unmatched_raids_do_not_open_browser(self) -> None:
        self._apply([_stream()])
        self.page.open_raided_channel_checkbox.setChecked(True)
        self.page._set_active_raid(self.page._candidates[0], self.now)
        self.url_opener.assert_not_called()

        self.page._handle_raid_event(self._outgoing_raid_event("someone-else"))
        self.page._handle_raid_event(
            self._outgoing_raid_event(from_id="incoming-channel")
        )
        self.url_opener.assert_not_called()

        self.page._clear_active_raid("Raid cancelled.")
        self.page._handle_raid_event(self._outgoing_raid_event())
        self.url_opener.assert_not_called()

    def test_browser_preference_defaults_off_and_persists_in_qsettings(self) -> None:
        self.assertFalse(self.page.open_raided_channel_checkbox.isChecked())
        self.page.open_raided_channel_checkbox.setChecked(True)
        self.settings.sync()

        restored = RaidPage(
            self.service,
            self.auth,
            settings=self.settings,
            url_opener=self.url_opener,
        )
        try:
            self.assertTrue(restored.open_raided_channel_checkbox.isChecked())
        finally:
            restored.shutdown()
            restored.deleteLater()

    def test_twitch_channel_url_accepts_only_login_shaped_values(self) -> None:
        self.assertEqual(
            twitch_channel_url("Some_Channel").toString(),
            "https://www.twitch.tv/Some_Channel",
        )
        self.assertIsNone(twitch_channel_url(""))
        self.assertIsNone(twitch_channel_url(None))
        self.assertIsNone(twitch_channel_url("name/other"))
        self.assertIsNone(twitch_channel_url("https://example.test"))

    def test_hide_show_keeps_active_state_and_shutdown_stops_timer(self) -> None:
        self._apply([_stream()])
        self.page._set_active_raid(self.page._candidates[0], self.now)
        self.page.hide()
        self.page.show()

        self.assertIsNotNone(self.page._active_raid)
        self.assertTrue(self.page.countdown_timer.isActive())

        self.page.shutdown()
        self.assertIsNone(self.page._active_raid)
        self.assertFalse(self.page.countdown_timer.isActive())

    def test_responsive_grid_uses_fewer_columns_when_narrow(self) -> None:
        self.assertEqual(RaidPage.columns_for_width(300), 1)
        self.assertEqual(RaidPage.columns_for_width(670), 2)
        self.assertEqual(RaidPage.columns_for_width(1_010), 3)
        self.assertEqual(RaidPage.columns_for_width(1_350), 4)

    def test_responsive_reflow_preserves_cards_and_active_target(self) -> None:
        self._apply(
            [
                _stream("viewer-1", name="Alpha", viewers=100),
                _stream("viewer-2", name="Beta", viewers=50),
                _stream("viewer-3", name="Gamma", viewers=25),
                _stream("viewer-4", name="Delta", viewers=10),
            ]
        )
        cards = dict(self.page._cards)
        active = self.page._candidates[1]
        self.page._set_active_raid(active, self.now)
        self.page.show()

        for width, expected_columns in ((380, 1), (760, 2), (1_120, 3)):
            self.page.resize(width, 800)
            self.application.processEvents()
            self.assertEqual(self.page._columns, expected_columns)
            self.assertEqual(self.page._cards, cards)
            self.assertEqual(self.page.grid.count(), len(cards))
            self.assertIs(self.page._active_raid.candidate, active)
            self.assertTrue(
                self.page._cards[active.user_id].property("activeRaidTarget")
            )
            for user_id, card in cards.items():
                self.assertIs(self.page._cards[user_id], card)

    def test_resize_does_not_reflow_when_column_count_is_unchanged(self) -> None:
        self._apply([_stream("viewer-1"), _stream("viewer-2")])
        self.page.show()
        self.page.resize(760, 800)
        self.application.processEvents()
        positions = {
            user_id: self.page.grid.indexOf(card)
            for user_id, card in self.page._cards.items()
        }

        with patch.object(self.page.grid, "addWidget", wraps=self.page.grid.addWidget) as add:
            self.page.resize(770, 800)
            self.application.processEvents()

        add.assert_not_called()
        self.assertEqual(
            {
                user_id: self.page.grid.indexOf(card)
                for user_id, card in self.page._cards.items()
            },
            positions,
        )

    def test_resize_preserves_selected_sort_order(self) -> None:
        self._apply(
            [
                _stream("viewer-1", name="Alpha", viewers=100),
                _stream("viewer-2", name="Beta", viewers=5),
            ]
        )
        self.page.sort_combo.setCurrentIndex(
            self.page.sort_combo.findData("viewers_asc")
        )
        self.page._reflow(force=True)

        first = self.page.grid.itemAtPosition(0, 0).widget()
        self.assertEqual(first.candidate.user_id, "viewer-2")

    def test_uptime_is_compact(self) -> None:
        started = datetime(2026, 9, 25, 18, 0, tzinfo=timezone.utc)
        now = datetime(2026, 9, 25, 19, 24, tzinfo=timezone.utc)
        self.assertEqual(format_raid_uptime(started, now=now), "1h 24m")


if __name__ == "__main__":
    unittest.main()
