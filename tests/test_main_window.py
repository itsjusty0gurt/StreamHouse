import logging
import os
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from core.logger import Logger
from core.settings import AppSettings
from twitch.auth import TwitchAuthState
from twitch.chatter_history import ChatterRecord
from twitch.service import TwitchConnectionState
from twitch.models import (
    TwitchChatNotice,
    TwitchEmote,
    TwitchEvent,
    TwitchEventTransport,
    TwitchFragmentType,
    TwitchMessage,
    TwitchMessageFragment,
)
from ui.main_window import MainWindow


class MainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.original_logger = Logger._logger
        Logger._logger = logging.Logger("SallyUItest", logging.DEBUG)

        self.settings_patch = patch(
            "ui.main_window.SettingsStore.load",
            return_value=AppSettings(),
        )
        self.settings_patch.start()
        self.window_state_store = Mock()
        self.window_state_store.restore.return_value = False
        self.chatter_history_store = Mock()
        self.chatter_history_store.records = {}
        self.chatter_history_store.is_regular.return_value = False
        self.chatter_history_store.is_bot.return_value = False
        self.chatter_history_store.REGULAR_ACTIVE_DAYS = 5
        self.chatter_history_store.REGULAR_MESSAGES = 25
        self.chatter_history_store.REGULAR_SNAPSHOT_DAYS = 10
        self.activity_history_store = Mock()
        self.activity_history_store.entries = []
        self.activity_history_store.load.return_value = []
        self.activity_history_store.refresh_interval_ms.return_value = 60_000
        self.activity_history_store.add.side_effect = lambda entry: (
            self.activity_history_store.entries.insert(0, entry)
        )
        self.session_store = Mock()
        self.session_store.current = None
        self.session_store.sessions = []
        self.session_store.LIMIT = 100
        self.session_store.retention_days = 365
        self.release_controller = Mock()
        self.release_controller.automatic_backup.return_value = None
        self.window = MainWindow(
            window_state_store=self.window_state_store,
            chatter_history_store=self.chatter_history_store,
            activity_history_store=self.activity_history_store,
            session_store=self.session_store,
            release_controller=self.release_controller,
            auto_upgrade_permissions=False,
        )

    def tearDown(self) -> None:
        self.window.close()
        self.application.processEvents()
        self.settings_patch.stop()
        Logger._logger = self.original_logger

    def test_navigation_selects_one_button_and_correct_page(self) -> None:
        cases = (
            (self.window.ui.dashboardButton, self.window.ui.dashboardPage),
            (self.window.ui.twitchButton, self.window.ui.twitchPage),
            (self.window.ai_button, self.window.ai_page),
            (self.window.ui.logsButton, self.window.ui.logsPage),
            (self.window.ui.settingsButton, self.window.ui.settingsPage),
        )
        buttons = [button for button, _ in cases]

        for button, page in cases:
            button.click()

            self.assertIs(self.window.ui.mainStack.currentWidget(), page)
            self.assertTrue(button.isChecked())
            self.assertEqual(
                sum(candidate.isChecked() for candidate in buttons),
                1,
            )
            self.assertEqual(self.window.statusBar().currentMessage(), "")

    def test_memories_page_searches_and_shows_viewer_profile(self) -> None:
        self.chatter_history_store.records = {
            "42": ChatterRecord(
                user_id="42",
                user_name="KnownViewer",
                first_seen="2026-07-01T12:00:00+00:00",
                last_seen="2026-07-12T12:00:00+00:00",
                active_days=["2026-07-01", "2026-07-12"],
                message_count=18,
                snapshot_days=2,
                roles=["VIP"],
                tags=["friend"],
                session_messages={"2026-07-12T10:00:00+00:00": 4},
                timeline=[
                    {
                        "type": "channel.follow",
                        "text": "KnownViewer followed the channel",
                        "timestamp": "2026-07-12T09:00:00+00:00",
                        "session_id": "",
                    }
                ],
            )
        }
        self.window.show_memories()

        self.assertIs(
            self.window.ui.mainStack.currentWidget(),
            self.window.ai_page,
        )
        self.assertIs(
            self.window.ai_tabs.currentWidget(),
            self.window.memories_page,
        )
        self.assertEqual(self.window.memory_viewer_list.count(), 1)
        self.window.memory_viewer_list.setCurrentRow(0)
        self.assertEqual(self.window.memory_name_label.text(), "KnownViewer")
        self.assertEqual(self.window.memory_groups_label.text(), "VIP")
        self.assertEqual(self.window.memory_messages_label.text(), "18")
        self.assertEqual(self.window.memory_sessions_table.rowCount(), 1)
        self.assertEqual(self.window.memory_timeline_list.count(), 1)
        self.assertEqual(self.window.memory_tags_edit.text(), "friend")

        self.window.memory_search_edit.setText("missing")
        self.assertEqual(self.window.memory_viewer_list.count(), 0)

    def test_ai_workspace_contains_analytics_dashboard(self) -> None:
        tab_names = [
            self.window.ai_tabs.tabText(index)
            for index in range(self.window.ai_tabs.count())
        ]
        self.assertEqual(
            tab_names,
            ["Memories", "Stream Sessions", "Analytics"],
        )
        self.assertEqual(
            self.window.analytics_labels["sessions"].text(),
            "0",
        )
        self.assertEqual(self.window.analytics_range_combo.count(), 4)

    def test_every_primary_page_and_ai_tab_is_constructed(self) -> None:
        pages = (
            self.window.ui.dashboardPage,
            self.window.ui.twitchPage,
            self.window.ai_page,
            self.window.connections_page,
            self.window.ui.logsPage,
            self.window.ui.settingsPage,
        )
        self.assertTrue(all(page is not None for page in pages))
        self.assertEqual(self.window.ai_tabs.count(), 3)
        self.assertTrue(self.window.create_backup_button.isEnabled())

    def test_window_geometry_is_restored_and_saved(self) -> None:
        self.window_state_store.restore.assert_called_once_with(self.window)

        self.window.close()

        self.window_state_store.save.assert_called_once_with(self.window)

    def test_navigation_buttons_are_square_edged_and_flush(self) -> None:
        style = self.window.ui.navigationFrame.styleSheet()

        self.assertIn("border-radius: 0px", style)
        self.assertIn("padding: 0px", style)
        self.assertEqual(self.window.ui.verticalLayout.spacing(), 0)
        margins = self.window.ui.verticalLayout.contentsMargins()
        self.assertEqual(
            (margins.left(), margins.top(), margins.right(), margins.bottom()),
            (0, 0, 0, 0),
        )

    def test_page_titles_are_hidden_and_companion_uses_compact_layout(self) -> None:
        for title in (
            self.window.ui.dashboardTitleLabel,
            self.window.ui.twitchTitleLabel,
            self.window.ui.logsTitleLabel,
            self.window.ui.settingsTitleLabel,
        ):
            self.assertTrue(title.isHidden())

        overview = self.window.stream_live_label.parentWidget()
        self.assertLessEqual(overview.maximumHeight(), 72)
        self.assertLessEqual(
            self.window.chatter_list.parentWidget().maximumWidth(),
            210,
        )
        self.assertGreaterEqual(
            self.window.activity_feed_list.parentWidget().minimumWidth(),
            300,
        )

    def test_twitch_activity_feed_formats_bus_events(self) -> None:
        event = TwitchEvent(
            subscription_type="channel.raid",
            version="1",
            received_at=datetime.now(timezone.utc),
            message_id="raid-1",
            broadcaster_user_id="42",
            broadcaster_user_login="channel",
            broadcaster_user_name="Channel",
            transport=TwitchEventTransport.WEBSOCKET,
            payload={
                "event": {
                    "from_broadcaster_user_name": "Raider",
                    "viewers": 25,
                }
            },
        )

        self.window.handle_twitch_activity(event)

        self.assertEqual(self.window.activity_feed_list.count(), 1)
        self.assertIn(
            "Raider raided with 25 viewers",
            self.window.activity_feed_list.item(0).text(),
        )

    def test_log_buttons_feed_the_ui(self) -> None:
        self.window.ui.testInfoButton.click()
        self.window.ui.testWarningButton.click()
        self.window.ui.testErrorButton.click()
        self.application.processEvents()

        output = self.window.ui.logOutput.toPlainText()
        self.assertIn("Developer test information message.", output)
        self.assertIn("Developer test warning message.", output)
        self.assertIn("Developer test error message.", output)

    def test_settings_are_applied_to_controls(self) -> None:
        settings = AppSettings(
            startup_page="Logs",
            log_level="WARNING",
            ui_log_limit=500,
            show_developer_tools=False,
            twitch_chat_show_timestamps=False,
            twitch_chat_font_family="Consolas",
            twitch_chat_font_size=14,
        )

        self.window._settings_to_controls(settings)
        self.window._apply_settings(settings)

        self.assertEqual(self.window.ui.startupPageCombo.currentText(), "Logs")
        self.assertEqual(self.window.ui.logLevelCombo.currentText(), "WARNING")
        self.assertEqual(self.window.ui.logOutput.maximumBlockCount(), 500)
        self.assertFalse(
            self.window.ui.toggleDeveloperToolsButton.isEnabled()
        )
        self.assertTrue(self.window.developer_dock.isHidden())
        self.assertFalse(self.window.ui.twitchChatTimestampCheck.isChecked())
        self.assertEqual(self.window.ui.twitchChatFontSizeSpin.value(), 14)
        self.assertEqual(
            self.window.ui.twitchChatOutput.document()
            .defaultFont()
            .pointSize(),
            14,
        )

    def test_developer_tools_toggle_opens_right_dock(self) -> None:
        self.window.show()
        self.application.processEvents()

        self.assertTrue(self.window.developer_dock.isHidden())
        self.window.ui.toggleDeveloperToolsButton.click()
        self.application.processEvents()

        self.assertTrue(self.window.developer_dock.isVisible())
        self.assertEqual(
            self.window.dockWidgetArea(self.window.developer_dock),
            Qt.DockWidgetArea.RightDockWidgetArea,
        )
        self.assertEqual(
            self.window.ui.toggleDeveloperToolsButton.text(),
            "Close Developer Tools",
        )
        self.assertEqual(self.window.ui.twitchDetailTabs.count(), 1)
        self.assertEqual(self.window.developer_dock.widget().count(), 3)

        self.window.ui.showDeveloperToolsCheck.setChecked(False)
        self.application.processEvents()
        self.assertTrue(self.window.developer_dock.isHidden())
        self.assertFalse(
            self.window.ui.toggleDeveloperToolsButton.isEnabled()
        )

    def test_twitch_simulation_updates_page_and_dashboard(self) -> None:
        self.window.ui.twitchChannelEdit.setText("TestChannel")
        self.window.ui.twitchConnectButton.click()
        self.application.processEvents()

        self.assertEqual(
            self.window.ui.twitchConnectionStatusLabel.text(),
            "Connected to #testchannel",
        )
        self.assertEqual(
            self.window.ui.twitchStatusLabel.text(),
            "Connected (#testchannel)",
        )
        self.assertTrue(
            self.window.ui.simulateTwitchMessageButton.isEnabled()
        )

        self.assertTrue(
            self.window.ui.twitchListenerUrlLabel.text().startswith(
                "http://127.0.0.1:"
            )
        )

        self.window.ui.simulationUsernameEdit.setText("viewer")
        self.window.ui.simulationMessageEdit.setText("Hello Sally!")
        self.window.ui.simulateTwitchMessageButton.click()
        self.application.processEvents()

        self.assertIn(
            "viewer: Hello Sally!",
            self.window.ui.twitchChatOutput.toPlainText().replace("\n", " "),
        )
        chat_lines = [
            line
            for line in self.window.ui.twitchChatOutput.toPlainText().splitlines()
            if line
        ]
        self.assertEqual(len(chat_lines), 1)
        self.assertTrue(chat_lines[0].endswith("viewer: Hello Sally!"))
        self.assertEqual(
            self.window.ui.twitchChatCountLabel.text(),
            "Chat - 1 message",
        )
        self.assertEqual(self.window.ui.simulationMessageEdit.text(), "")

        self.window.ui.clearTwitchChatButton.click()
        self.assertIn(
            "No chat messages yet",
            self.window.ui.twitchChatOutput.toPlainText(),
        )
        self.assertEqual(
            self.window.ui.twitchChatCountLabel.text(),
            "Chat - 0 messages",
        )

        self.window.ui.twitchDisconnectButton.click()
        self.application.processEvents()
        self.assertEqual(
            self.window.ui.twitchStatusLabel.text(),
            "Disconnected",
        )
        self.assertEqual(
            self.window.ui.twitchListenerUrlLabel.text(),
            "Stopped",
        )

    def test_twitch_auth_state_updates_account_row(self) -> None:
        self.assertEqual(self.window.ui.twitchChannelLabel.text(), "Your channel")

        self.window.handle_twitch_auth_changed(
            TwitchAuthState.WAITING,
            "Enter ABCDEFGH at twitch.tv/activate",
        )
        self.assertIn("ABCDEFGH", self.window.ui.twitchAccountStatusLabel.text())
        self.assertFalse(self.window.ui.twitchSignInButton.isEnabled())
        self.assertTrue(self.window.ui.twitchSignOutButton.isEnabled())

    def test_signed_in_user_can_update_missing_permissions_without_sign_out(self) -> None:
        self.window.auto_upgrade_permissions = True
        self.window.twitch_auth.token = Mock(
            scopes=["user:read:chat"],
            user_id="42",
        )
        self.window.twitch_service.connect = Mock(return_value=True)
        self.window.refresh_stream_companion = Mock()
        self.window.twitch_auth.sign_in = Mock()

        self.window.handle_twitch_auth_changed(
            TwitchAuthState.SIGNED_IN,
            "sallybot",
        )
        QTest.qWait(150)

        self.assertTrue(self.window.ui.twitchSignInButton.isEnabled())
        self.assertEqual(
            self.window.ui.twitchSignInButton.text(),
            "Update Permissions",
        )
        self.assertFalse(
            self.window.update_companion_permissions_button.isHidden()
        )
        self.window.twitch_auth.sign_in.assert_called_once_with()

        self.window.handle_twitch_auth_changed(
            TwitchAuthState.SIGNED_IN,
            "sallybot",
        )
        self.application.processEvents()
        self.window.twitch_auth.sign_in.assert_called_once_with()

    def test_twitch_send_controls_follow_connection_and_clear_on_success(self) -> None:
        self.window.handle_twitch_status_changed(
            TwitchConnectionState.CONNECTED,
            "channel",
        )
        self.window.twitch_service.send_message = Mock(return_value=True)
        self.window.ui.twitchSendEdit.setText("Hello Twitch")

        self.window.ui.twitchSendButton.click()

        self.window.twitch_service.send_message.assert_called_once_with(
            "Hello Twitch"
        )
        self.assertEqual(self.window.ui.twitchSendEdit.text(), "")

    def test_twitch_status_is_shown_in_bottom_status_bar(self) -> None:
        self.window.handle_twitch_status_changed(
            TwitchConnectionState.CONNECTED,
            "mychannel",
        )

        self.assertEqual(
            self.window.twitch_status_bar_label.text(),
            "Twitch: Connected to #mychannel",
        )

    def test_connections_are_on_separate_page_from_companion(self) -> None:
        self.window.show_connections()

        self.assertIs(
            self.window.ui.mainStack.currentWidget(),
            self.window.connections_page,
        )
        self.assertIs(
            self.window.ui.twitchConnectionGroup.parentWidget(),
            self.window.connections_page,
        )
        self.window.show_twitch()
        self.assertIs(
            self.window.twitch_channel_splitter.widget(1),
            self.window.chatter_list.parentWidget(),
        )

    def test_companion_refresh_updates_stream_stats_and_chatters(self) -> None:
        token = Mock(
            user_id="42",
            scopes=[
                "moderator:read:chatters",
                "moderation:read",
                "moderator:read:vips",
                "channel:read:subscriptions",
            ],
        )
        self.window.twitch_auth.token = token
        self.window.twitch_service.broadcaster_user_id = "42"
        self.window.twitch_service.helix.get_companion_snapshot = Mock(
            return_value={"stream": None, "followers": 123, "subscribers": 7}
        )
        self.window.twitch_service.helix.get_chatters = Mock(
            return_value=[
                {"user_id": "1", "user_name": "ModOne"},
                {"user_id": "2", "user_name": "VipOne"},
                {"user_id": "3", "user_name": "SubOne"},
                {"user_id": "4", "user_name": "ViewerOne"},
            ]
        )
        self.window.twitch_service.helix.get_chat_roles = Mock(
            return_value=({"1"}, {"2"}, {"3"})
        )
        self.window.companion_thread_pool.start = Mock(
            side_effect=lambda worker: worker.run()
        )

        self.window.refresh_stream_companion()
        self.application.processEvents()

        self.assertEqual(self.window.stream_live_label.text(), "Offline")
        self.assertEqual(self.window.stream_followers_label.text(), "123 followers")
        self.assertEqual(self.window.stream_subscribers_label.text(), "7 subscribers")
        expected_groups = (
            ("Moderators (1)", "ModOne"),
            ("VIPs (1)", "VipOne"),
            ("Subscribers (1)", "SubOne"),
            ("Bots (0)", None),
            ("Regulars (0)", None),
            ("Viewers (1)", "ViewerOne"),
        )
        for index, (group_text, child_text) in enumerate(expected_groups):
            group = self.window.chatter_list.topLevelItem(index)
            self.assertEqual(group.text(0), group_text)
            if child_text is not None:
                self.assertEqual(group.child(0).text(0), child_text)

        self.window.handle_twitch_auth_changed(
            TwitchAuthState.SIGNED_IN,
            "sallybot",
        )
        self.assertEqual(self.window.ui.twitchAccountStatusLabel.text(), "sallybot")
        self.assertTrue(self.window.ui.twitchSignOutButton.isEnabled())

    def test_twitch_event_viewer_filters_details_and_clears(self) -> None:
        self.window.ui.twitchChannelEdit.setText("channel")
        self.window.ui.twitchConnectButton.click()
        self.window.twitch_service.simulate_message("viewer", "hello")
        self.application.processEvents()

        self.assertEqual(self.window.ui.twitchEventTable.rowCount(), 1)
        self.assertEqual(
            self.window.ui.twitchEventTable.item(0, 3).text(),
            "Processed",
        )
        details = self.window.ui.twitchEventDetails.toPlainText()
        self.assertIn('"subscription_type": "channel.chat.message"', details)
        self.assertNotIn("message-signature", details)

        self.window.ui.copyTwitchEventButton.click()
        self.assertEqual(QApplication.clipboard().text(), details)

        self.window.ui.twitchEventResultCombo.setCurrentText("Rejected")
        self.assertEqual(self.window.ui.twitchEventTable.rowCount(), 0)
        self.window.ui.twitchEventResultCombo.setCurrentText("All results")
        self.window.ui.twitchEventSearchEdit.setText("viewer")
        self.assertEqual(self.window.ui.twitchEventTable.rowCount(), 1)
        self.window.ui.twitchEventSearchEdit.setText("does-not-exist")
        self.assertEqual(self.window.ui.twitchEventTable.rowCount(), 0)

        self.window.ui.clearTwitchEventsButton.click()
        self.assertEqual(self.window.twitch_event_diagnostics, [])
        self.assertEqual(self.window.ui.twitchEventDetails.toPlainText(), "")

    def test_twitch_event_pause_preserves_selected_event(self) -> None:
        self.window.ui.twitchChannelEdit.setText("channel")
        self.window.ui.twitchConnectButton.click()
        self.window.twitch_service.simulate_message("first", "one")
        self.application.processEvents()

        first_id = self.window.twitch_event_diagnostics[0].message_id
        self.window.ui.twitchEventTable.selectRow(0)
        self.window.ui.pauseTwitchEventsCheck.setChecked(True)
        self.window.twitch_service.simulate_message("second", "two")
        self.application.processEvents()

        selected_item = self.window.ui.twitchEventTable.item(
            self.window.ui.twitchEventTable.currentRow(),
            0,
        )
        selected_diagnostic = selected_item.data(Qt.ItemDataRole.UserRole)
        self.assertEqual(selected_diagnostic.message_id, first_id)

    def test_official_event_simulator_sends_editable_follow_payload(self) -> None:
        self.assertGreaterEqual(self.window.ui.twitchEventTypeCombo.count(), 80)
        follow_index = self.window.ui.twitchEventTypeCombo.findText(
            "channel.follow (v2)"
        )
        self.assertGreaterEqual(follow_index, 0)
        self.window.ui.twitchEventTypeCombo.setCurrentIndex(follow_index)
        self.assertIn(
            '"followed_at"',
            self.window.ui.twitchEventPayloadEdit.toPlainText(),
        )

        self.window.ui.twitchChannelEdit.setText("channel")
        self.window.ui.twitchConnectButton.click()
        self.window.ui.sendTwitchEventButton.click()
        self.application.processEvents()

        self.assertEqual(self.window.ui.twitchEventTable.rowCount(), 1)
        self.assertIn(
            "channel.follow",
            self.window.ui.twitchEventDetails.toPlainText(),
        )

    def test_event_simulator_reports_invalid_json(self) -> None:
        self.window.ui.twitchEventPayloadEdit.setPlainText("not-json")
        self.window.ui.sendTwitchEventButton.setEnabled(True)
        self.window.ui.sendTwitchEventButton.click()

        self.assertIn("Invalid event JSON", self.window.ui.twitchErrorLabel.text())

    def test_twitch_validation_error_is_visible(self) -> None:
        self.window.ui.twitchConnectButton.click()
        self.application.processEvents()

        self.assertEqual(
            self.window.ui.twitchErrorLabel.text(),
            "A Twitch channel is required.",
        )

    def test_twitch_chat_escapes_viewer_markup(self) -> None:
        self.window.ui.twitchChannelEdit.setText("channel")
        self.window.ui.twitchConnectButton.click()
        self.window.twitch_service.simulate_message(
            "<b>viewer</b>",
            "<script>not markup</script>",
        )
        self.application.processEvents()

        plain_text = self.window.ui.twitchChatOutput.toPlainText()
        rendered_html = self.window.ui.twitchChatOutput.toHtml()
        self.assertIn("<b>viewer</b>", plain_text)
        self.assertIn("<script>not markup</script>", plain_text)
        self.assertNotIn("<script>not markup</script>", rendered_html)

    def test_twitch_emote_is_rendered_as_cached_cdn_image(self) -> None:
        message = TwitchMessage(
            username="viewer",
            text="Kappa",
            received_at=datetime.now(timezone.utc),
            fragments=(
                TwitchMessageFragment(
                    type=TwitchFragmentType.EMOTE,
                    text="Kappa",
                    emote=TwitchEmote("25", "0", "0", ("static",)),
                ),
            ),
        )

        self.window.handle_twitch_message(message)

        rendered_html = self.window.ui.twitchChatOutput.toHtml()
        self.assertIn("/emoticons/v2/25/static/dark/1.0", rendered_html)
        self.assertIn("width='20'", rendered_html)

    def test_twitch_chat_turns_urls_into_links(self) -> None:
        self.window.ui.twitchChannelEdit.setText("channel")
        self.window.twitch_service.connect("channel")
        self.window.twitch_service.simulate_message(
            "viewer",
            "See https://example.com/test",
        )
        self.application.processEvents()

        self.assertIn(
            "<a href='https://example.com/test'>",
            self.window.ui.twitchChatOutput.toHtml(),
        )

    def test_twitch_moderation_notice_is_shown_in_chat(self) -> None:
        notice = TwitchChatNotice(
            kind="delete",
            text="A message from viewer was deleted.",
            received_at=datetime.now(timezone.utc),
            target_message_id="message-1",
        )

        self.window.handle_twitch_notice(notice)

        self.assertIn(
            "A message from viewer was deleted.",
            self.window.ui.twitchChatOutput.toPlainText(),
        )

    def test_twitch_timestamp_can_be_hidden(self) -> None:
        self.window.settings = AppSettings(
            twitch_chat_show_timestamps=False
        )
        self.window._apply_settings(self.window.settings)
        self.window.ui.twitchChannelEdit.setText("channel")
        self.window.ui.twitchConnectButton.click()
        self.window.twitch_service.simulate_message("viewer", "hello")
        self.application.processEvents()

        self.assertEqual(
            self.window.ui.twitchChatOutput.toPlainText().strip(),
            "viewer: hello",
        )


if __name__ == "__main__":
    unittest.main()
