import os
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from products.hub.twitch.auth import TwitchToken
from products.hub.ui.raid_page import (
    RaidActionWorker,
    RaidCandidate,
    RaidCandidatesWorker,
    RaidPage,
    format_raid_uptime,
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
        token = TwitchToken(
            "access",
            "refresh",
            9999999999,
            ["user:read:follows", "channel:manage:raids"],
            user_id="channel-1",
        )
        self.auth = Mock(token=token)
        self.service = Mock(broadcaster_user_id="channel-1")
        self.page = RaidPage(self.service, self.auth)

    def tearDown(self) -> None:
        self.page.shutdown()
        self.page.deleteLater()
        self.application.processEvents()

    def _apply(self, values: list[dict]) -> None:
        self.page._generation += 1
        worker = RaidCandidatesWorker(self.service, self.page._generation)
        self.page._load_workers.add(worker)
        self.page._load_completed(worker, self.page._generation, values)
        self.application.processEvents()

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
        self.service.execute_slash_action.assert_not_called()

        worker = self.page.raid_pool.start.call_args.args[0]
        self.assertIsInstance(worker, RaidActionWorker)
        self.service.execute_slash_action.return_value = (True, "Channel Name")
        worker.run()
        self.application.processEvents()

        request = self.service.execute_slash_action.call_args.args[0]
        self.assertEqual(request.action, "raid")
        self.assertEqual(request.user_id, "viewer-1")
        self.assertEqual(self.page.status_label.text(), "Raid started for Channel Name.")
        self.assertEqual(QThread.currentThread(), self.page.thread())

    def test_raid_failure_restores_button_and_reports_error(self) -> None:
        self._apply([_stream()])
        candidate = self.page._candidates[0]
        worker = RaidActionWorker(self.service, candidate)
        self.page._raid_workers.add(worker)
        self.page._cards[candidate.user_id].set_raid_pending(True)

        self.page._raid_completed(worker, candidate, False, "network")

        self.assertTrue(self.page._cards[candidate.user_id].raid_button.isEnabled())
        self.assertIn("Couldn’t start", self.page.status_label.text())

    def test_responsive_grid_uses_fewer_columns_when_narrow(self) -> None:
        self.assertEqual(RaidPage.columns_for_width(300), 1)
        self.assertEqual(RaidPage.columns_for_width(660), 2)
        self.assertEqual(RaidPage.columns_for_width(990), 3)

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
