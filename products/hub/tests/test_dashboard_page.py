from __future__ import annotations

import unittest

from PySide6.QtWidgets import QApplication, QLabel

from products.hub.obs_service.models import ObsConnectionState
from products.hub.twitch.auth import TwitchAuthState
from products.hub.twitch.service import TwitchConnectionState
from products.hub.ui.dashboard_page import (
    DashboardPage,
    DashboardStatus,
    current_build_description,
    summarize_obs_connection,
    summarize_twitch_connection,
)
from shared.streamhouse_runtime.version import VERSION


class DashboardPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_product_version_build_and_alpha_scope_are_present(self) -> None:
        page = DashboardPage(build_description="Test build")

        self.assertEqual(page.version_label.text(), f"Version {VERSION}")
        self.assertEqual(page.build_label.text(), "Test build")
        visible_text = " ".join(
            label.text() for label in page.findChildren(QLabel)
        )
        self.assertIn("Streamhouse Hub", visible_text)
        self.assertIn("Everything for your stream under one roof.", visible_text)
        self.assertNotIn("Streamhouse AI", visible_text)
        self.assertNotIn("Check for Updates", visible_text)
        self.assertIn(current_build_description(), {"Development build", "Packaged Windows build"})

    def test_twitch_summary_distinguishes_connected_partial_and_attention(self) -> None:
        connected = summarize_twitch_connection(
            TwitchAuthState.SIGNED_IN,
            TwitchConnectionState.CONNECTED,
        )
        missing_scope = summarize_twitch_connection(
            TwitchAuthState.SIGNED_IN,
            TwitchConnectionState.CONNECTED,
            broadcaster_missing_scopes={"channel:read:ads"},
        )
        bot_partial = summarize_twitch_connection(
            TwitchAuthState.SIGNED_IN,
            TwitchConnectionState.CONNECTED,
            bot_auth_state=TwitchAuthState.SIGNED_IN,
            bot_missing_scopes={"user:read:chat"},
        )
        disconnected = summarize_twitch_connection(
            TwitchAuthState.SIGNED_IN,
            TwitchConnectionState.DISCONNECTED,
        )

        self.assertIs(connected.status, DashboardStatus.CONNECTED)
        self.assertIs(missing_scope.status, DashboardStatus.NEEDS_ATTENTION)
        self.assertIs(bot_partial.status, DashboardStatus.PARTIAL)
        self.assertIs(disconnected.status, DashboardStatus.DISCONNECTED)

    def test_obs_summary_uses_existing_connection_state(self) -> None:
        self.assertIs(
            summarize_obs_connection(ObsConnectionState.CONNECTED).status,
            DashboardStatus.CONNECTED,
        )
        self.assertIs(
            summarize_obs_connection(ObsConnectionState.CONNECTING).status,
            DashboardStatus.PARTIAL,
        )
        self.assertIs(
            summarize_obs_connection(ObsConnectionState.ERROR).status,
            DashboardStatus.NEEDS_ATTENTION,
        )
        self.assertIs(
            summarize_obs_connection(ObsConnectionState.DISCONNECTED).status,
            DashboardStatus.DISCONNECTED,
        )

    def test_page_updates_status_and_routes_to_connections(self) -> None:
        page = DashboardPage()
        requests: list[bool] = []
        page.connections_requested.connect(lambda: requests.append(True))

        page.update_twitch(
            TwitchAuthState.SIGNED_IN,
            TwitchConnectionState.CONNECTED,
        )
        page.update_obs(ObsConnectionState.CONNECTED)
        page.connections_button.click()

        self.assertEqual(page.twitch_status_label.text(), "Connected")
        self.assertEqual(page.obs_status_label.text(), "Connected")
        self.assertTrue(page.attention_frame.isHidden())
        self.assertEqual(requests, [True])

    def test_help_buttons_use_configured_project_and_issue_urls(self) -> None:
        opened: list[str] = []
        page = DashboardPage(
            project_url="https://example.test/project",
            issue_tracker_url="https://example.test/project/issues",
            url_opener=lambda url: opened.append(url.toString()),
        )

        page.report_bug_button.click()
        page.feedback_button.click()
        page.project_button.click()

        self.assertEqual(len(opened), 3)
        self.assertTrue(opened[0].startswith("https://example.test/project/issues/new"))
        self.assertIn("Bug", opened[0])
        self.assertIn("Idea", opened[1])
        self.assertEqual(opened[2], "https://example.test/project")
        self.assertEqual(page.about_button.text(), "About")

    def test_missing_optional_links_are_hidden_without_breaking_page(self) -> None:
        page = DashboardPage(project_url="", issue_tracker_url="")

        self.assertTrue(page.report_bug_button.isHidden())
        self.assertTrue(page.feedback_button.isHidden())
        self.assertTrue(page.project_button.isHidden())
        self.assertFalse(page.about_button.isHidden())
        page.resize(420, 520)
        self.assertLessEqual(page.minimumSizeHint().width(), 420)


if __name__ == "__main__":
    unittest.main()
