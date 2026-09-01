import logging
import os
import tempfile
import unittest
from threading import Event
from time import monotonic
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import (
    QApplication,
    QBoxLayout,
    QDialog,
    QHeaderView,
    QLabel,
    QMenu,
    QMessageBox,
)
from PySide6.QtTest import QSignalSpy, QTest

from shared.streamhouse_runtime.logger import Logger
from shared.streamhouse_shared.models import (
    ExtractedMemory,
    ResponseDecision,
    ResponseMessage,
)
from products.hub.core.settings import AppSettings
from products.hub.twitch.auth import TwitchAuthState
from products.hub.config.twitch import TWITCH_BOT_SCOPES, TWITCH_SCOPES
from products.hub.twitch.chatter_history import ChatterHistoryStore, ChatterRecord
from products.hub.automation.routines import RoutineStore
from products.hub.automation.models import (
    DEFAULT_AUTOMATION_QUEUE_ID,
    AutomationExecutionResult,
    RoutineExecutionResult,
    TaskExecutionResult,
    TriggerEvent,
)
from products.hub.obs_service.triggers import OBS_TRIGGER_TYPES
from products.hub.obs_service.models import ObsConnectionState, ObsEvent
from products.hub.twitch.commands import TwitchCommandTriggerStore
from products.hub.twitch.channel_information import ChannelInformationStore
from products.hub.twitch.automation_triggers import TwitchEventTriggerStore
from products.hub.twitch.service import TwitchConnectionState
from products.hub.twitch.session_history import StreamSession
from products.hub.twitch.models import (
    TwitchChatNotice,
    TwitchEmote,
    TwitchEvent,
    TwitchEventTransport,
    TwitchFragmentType,
    TwitchMessage,
    TwitchMessageFragment,
    TwitchReply,
)
from products.hub.ui.main_window import MainWindow
from products.hub.ui.automation_page import RunHistoryDetailsDialog
from shared.streamhouse_shared.protocol import PROTOCOL_VERSION
from products.hub.ui.channel_snapshot_worker import ChannelSnapshotResult
from products.hub.ui.memory_worker import MemoryExtractionResult
from products.hub.ui.response_worker import ResponseBatchResult
from products.hub.ui.streamhouse_ai_worker import StreamhouseAIHealthResult
from products.hub.streamhouse_hub.ai_client import StreamhouseAIStatus
from products.hub.streamhouse_hub.ai_lifecycle import AIConnectionState
from products.hub.ui.twitch_command_dialog import TwitchCommandDialog, TwitchCommandManagerDialog


class MainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.original_logger = Logger._logger
        Logger._logger = logging.Logger("StreamhouseUITest", logging.DEBUG)

        self.settings_patch = patch(
            "products.hub.ui.main_window.SettingsStore.load",
            return_value=AppSettings(),
        )
        self.settings_patch.start()
        self.window_state_store = Mock()
        self.window_state_store.restore.return_value = False
        self.chatter_history_store = Mock()
        self.chatter_history_store.records = {}
        self.chatter_history_store.is_regular.return_value = False
        self.chatter_history_store.is_bot.return_value = False
        self.chatter_history_store.has_memory_consent.return_value = False
        self.chatter_history_store.can_create_keynotes.return_value = False
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
        self.training_store = Mock()
        self.training_store.examples = []
        self.test_report_store = Mock()
        self.test_report_store.events = []
        self.test_report_store.summary.return_value = {
            "total": 0,
            "sent": 0,
            "ignored": 0,
            "missed": 0,
            "blocked": 0,
            "failed": 0,
            "average_latency_ms": 0,
        }
        self.test_report_store.selected_events.return_value = []
        self.twitch_command_directory = tempfile.TemporaryDirectory()
        command_root = Path(self.twitch_command_directory.name)
        self.twitch_command_trigger_store = TwitchCommandTriggerStore(
            command_root / "commands.json",
            RoutineStore(command_root / "routines.json"),
        )
        self.twitch_event_trigger_store = TwitchEventTriggerStore(
            command_root / "event_triggers.json",
            self.twitch_command_trigger_store.routine_store,
        )
        self.window = MainWindow(
            window_state_store=self.window_state_store,
            chatter_history_store=self.chatter_history_store,
            activity_history_store=self.activity_history_store,
            session_store=self.session_store,
            release_controller=self.release_controller,
            training_store=self.training_store,
            test_report_store=self.test_report_store,
            twitch_command_trigger_store=self.twitch_command_trigger_store,
            channel_information_store=ChannelInformationStore(
                command_root / "channel-information.json"
            ),
            twitch_event_trigger_store=self.twitch_event_trigger_store,
            auto_upgrade_permissions=False,
        )
        if self._testMethodName != "test_hub_starts_with_ai_disconnected":
            generation = self.window.ai_lifecycle.begin_verification(
                "http://127.0.0.1:8765"
            )
            self.window.ai_lifecycle.mark_ready(generation)

    def tearDown(self) -> None:
        self.window.close()
        self.application.processEvents()
        self.twitch_command_directory.cleanup()
        self.settings_patch.stop()
        Logger._logger = self.original_logger

    def test_navigation_selects_one_button_and_correct_page(self) -> None:
        self.assertEqual(self.window.windowTitle(), "Streamhouse Hub")
        cases = (
            (self.window.ui.dashboardButton, self.window.ui.dashboardPage),
            (self.window.ui.twitchButton, self.window.ui.twitchPage),
            (self.window.ai_button, self.window.ai_page),
            (self.window.automation_button, self.window.automation_page),
            (self.window.ui.logsButton, self.window.ui.logsPage),
            (self.window.ui.settingsButton, self.window.settings_container),
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

    def test_streamhouse_ai_presence_is_event_driven(self) -> None:
        self.assertFalse(hasattr(self.window, "ai_health_timer"))
        with patch.object(self.window, "_check_streamhouse_ai") as connect:
            self.window._handle_streamhouse_ai_presence(
                PROTOCOL_VERSION,
                9123,
            )
        self.assertEqual(
            self.window.ai_remote_endpoint_edit.text(),
            "http://127.0.0.1:9123",
        )
        connect.assert_called_once_with()

        self.window._handle_streamhouse_ai_presence(PROTOCOL_VERSION, 0)
        self.assertEqual(self.window.ai_remote_stack.currentIndex(), 0)
        self.assertEqual(
            self.window.ai_remote_connection_detail.text(),
            "Streamhouse AI is not running.",
        )

    def test_hub_starts_with_ai_disconnected(self) -> None:
        self.assertTrue(self.window.settings.local_ai_enabled)
        self.assertTrue(self.window.settings.streamhouse_ai_endpoint)
        self.assertIs(
            self.window.ai_connection_state,
            AIConnectionState.DISCONNECTED,
        )
        self.training_store.connect.assert_not_called()
        self.test_report_store.connect.assert_not_called()

    def test_ai_presence_verifies_then_becomes_ready(self) -> None:
        self.window.ai_lifecycle.disconnect()
        with patch.object(self.window, "_check_streamhouse_ai"):
            self.window._handle_streamhouse_ai_presence(PROTOCOL_VERSION, 9123)
        generation = self.window.ai_connection_generation
        self.assertIs(self.window.ai_connection_state, AIConnectionState.VERIFYING)

        self.window._apply_streamhouse_ai_health(
            StreamhouseAIHealthResult(
                StreamhouseAIStatus(True, PROTOCOL_VERSION),
                {},
                generation,
            )
        )

        self.assertIs(self.window.ai_connection_state, AIConnectionState.READY)

    def test_stale_health_result_cannot_restore_ready(self) -> None:
        self.window.ai_lifecycle.disconnect()
        with patch.object(self.window, "_check_streamhouse_ai"):
            self.window._handle_streamhouse_ai_presence(PROTOCOL_VERSION, 9123)
        stale_generation = self.window.ai_connection_generation
        self.window._handle_streamhouse_ai_presence(PROTOCOL_VERSION, 0)

        self.window._apply_streamhouse_ai_health(
            StreamhouseAIHealthResult(
                StreamhouseAIStatus(True, PROTOCOL_VERSION),
                {},
                stale_generation,
            )
        )

        self.assertIs(
            self.window.ai_connection_state, AIConnectionState.DISCONNECTED
        )

    def test_portrait_mode_reflows_navigation_and_major_splitters(self) -> None:
        self.window._apply_responsive_layout(True)
        self.assertEqual(
            self.window.ui.horizontalLayout.direction(),
            QBoxLayout.Direction.TopToBottom,
        )
        self.assertEqual(
            self.window.ui.verticalLayout.direction(),
            QBoxLayout.Direction.LeftToRight,
        )
        self.assertEqual(
            self.window.twitch_channel_splitter.orientation(),
            Qt.Orientation.Vertical,
        )
        self.assertEqual(
            self.window.automation_page.routines_splitter.orientation(),
            Qt.Orientation.Vertical,
        )
        self.assertEqual(
            self.window.channel_side_splitter.orientation(),
            Qt.Orientation.Horizontal,
        )
        self.assertIs(
            self.window.stream_tools_layout.itemAt(0).widget(),
            self.window.stream_overview_group,
        )
        self.assertIs(
            self.window.stream_tools_layout.itemAt(1).widget(),
            self.window.ad_manager_group,
        )
        preview_index = self.window.soundboard_page.editor_grid.indexOf(
            self.window.soundboard_page.preview_panel
        )
        self.assertEqual(
            self.window.soundboard_page.editor_grid.getItemPosition(
                preview_index
            ),
            (0, 0, 1, 2),
        )

    def test_automation_page_edits_grouped_routine_and_shows_tasks(self) -> None:
        store = self.twitch_command_trigger_store.routine_store
        group = store.add_group("Chat Commands")
        routine = store.add(
            "Welcome",
            group_id=group.group_id,
            description="Original description",
        )
        store.add_task(
            routine.routine_id,
            task_type="twitch.send_chat_message",
            name="Say hello",
            config={"message": "Hello {user.display_name}", "as_bot": True},
        )

        page = self.window.automation_page
        rows_inserted = QSignalSpy(page.task_list.model().rowsInserted)
        self.window.show_automation()
        page.select_routine(routine.routine_id)

        self.assertIs(self.window.ui.mainStack.currentWidget(), page)
        self.assertEqual(page.routine_title_label.text(), "Welcome")
        self.assertEqual(page.task_list.count(), 1)
        self.assertGreater(rows_inserted.count(), 0)
        self.assertIn("Say hello", page.task_list.item(0).text())
        self.assertEqual(page.editor_tabs.currentWidget(), page.task_list.parentWidget())
        self.assertIn("Chat Commands", page.routine_summary_label.text())
        page.settings_name_edit.setText("Welcome Everyone")
        page.settings_description_edit.setText("Updated description")
        page.save_settings_button.click()
        saved = store.get(routine.routine_id)
        self.assertEqual(saved.name, "Welcome Everyone")
        self.assertEqual(saved.description, "Updated description")

    def test_automation_page_can_delete_orphaned_command_routine(self) -> None:
        store = self.twitch_command_trigger_store.routine_store
        routine = store.create_managed(
            trigger_id="missing-command",
            name="Yippie",
            managed_by=self.twitch_command_trigger_store.MANAGED_BY,
            task_type="twitch.send_chat_message",
            task_name="Response",
            task_config={"message": "Yippie!", "as_bot": True},
        )
        page = self.window.automation_page
        page.select_routine(routine.routine_id)

        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            page._delete_routine()

        self.assertIsNone(store.get(routine.routine_id))

    def test_automation_queues_tab_assigns_and_displays_pending_routines(self) -> None:
        page = self.window.automation_page
        queue = self.window.automation_queue_store.add("Soundboard")
        self.window.automation_queue_store.update(queue.queue_id, paused=True)
        store = self.twitch_command_trigger_store.routine_store
        routine = store.add("Play sound", trigger_id="sound", queue_id=queue.queue_id)
        store.add_task(
            routine.routine_id,
            task_type="core.wait",
            name="Tiny delay",
            config={"duration": "0", "unit": "seconds"},
        )

        execution = self.window.automation_service.publish_trigger(
            TriggerEvent("sound", "test", "event", {"user": "Viewer"})
        )
        page.refresh(routine.routine_id)
        page._refresh_queues(queue.queue_id)

        self.assertTrue(execution.succeeded)
        self.assertEqual(page.tabs.tabText(1), "Queues")
        self.assertEqual(page.pending_queue_list.count(), 1)
        self.assertIn("Play sound", page.pending_queue_list.item(0).text())
        self.assertEqual(
            page.settings_queue_combo.currentData(),
            queue.queue_id,
        )

    def test_default_queue_is_visible_protected_and_used_by_routine_editor(self) -> None:
        page = self.window.automation_page
        store = self.twitch_command_trigger_store.routine_store
        routine = store.add("Beginner routine")

        self.assertIs(
            self.window.command_automation_service.queue_manager,
            self.window.automation_queue_manager,
        )

        page.refresh(routine.routine_id)
        page._refresh_queues(DEFAULT_AUTOMATION_QUEUE_ID)

        self.assertEqual(page.queue_list.count(), 1)
        self.assertIn("Default Queue", page.queue_list.item(0).text())
        self.assertFalse(page.delete_queue_button.isEnabled())
        self.assertEqual(
            page.settings_queue_combo.currentData(),
            DEFAULT_AUTOMATION_QUEUE_ID,
        )
        self.assertEqual(page.settings_queue_combo.currentText(), "Default Queue")
        self.assertIn("Default Queue", page.routine_summary_label.text())

    def test_deleting_custom_queue_reassigns_routines_to_default(self) -> None:
        page = self.window.automation_page
        queue = self.window.automation_queue_store.add("Alerts")
        routine = self.twitch_command_trigger_store.routine_store.add(
            "Alert routine",
            queue_id=queue.queue_id,
        )
        page._refresh_queues(queue.queue_id)

        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            page._delete_queue()

        self.assertIsNone(self.window.automation_queue_store.get(queue.queue_id))
        self.assertEqual(
            self.twitch_command_trigger_store.routine_store.get(
                routine.routine_id
            ).queue_id,
            DEFAULT_AUTOMATION_QUEUE_ID,
        )

    def test_task_add_menu_is_grouped_by_service(self) -> None:
        menu = QMenu()
        add_menu = self.window.automation_page._add_task_submenu(menu)
        self.assertEqual(
            [action.text() for action in add_menu.actions()],
            ["Core", "Counters", "OBS", "Twitch"],
        )
        core_menu = add_menu.actions()[0].menu()
        counters_menu = add_menu.actions()[1].menu()
        obs_menu = add_menu.actions()[2].menu()
        twitch_menu = add_menu.actions()[3].menu()
        self.assertIn("Launch application", [action.text() for action in core_menu.actions()])
        scripts_menu = next(
            action.menu()
            for action in core_menu.actions()
            if action.text() == "Scripts"
        )
        self.assertEqual(
            [action.text() for action in scripts_menu.actions()],
            ["Run Python script"],
        )
        self.assertIn("Change scene", [action.text() for action in obs_menu.actions()])
        self.assertEqual(
            [action.text() for action in counters_menu.actions()],
            ["Increase", "Decrease", "Set", "Reset"],
        )
        twitch_tasks = [action.text() for action in twitch_menu.actions()]
        self.assertIn("Send chat message", twitch_tasks)
        self.assertIn("Run commercial", twitch_tasks)
        commercial_menu = next(
            action.menu()
            for action in twitch_menu.actions()
            if action.text() == "Run commercial"
        )
        self.assertEqual(
            [
                action.text()
                for action in commercial_menu.actions()
                if not action.isSeparator()
            ],
            [
                "30 seconds",
                "60 seconds",
                "90 seconds",
                "180 seconds",
                "Customize…",
            ],
        )
        self.assertIn("Moderate user", twitch_tasks)
        self.assertIn("Fulfill or refund redemption", twitch_tasks)

    def test_commercial_duration_menu_adds_configured_task_directly(self) -> None:
        routine = self.twitch_command_trigger_store.routine_store.add(
            "Commercial break"
        )
        page = self.window.automation_page
        page.select_routine(routine.routine_id)
        menu = QMenu()
        add_menu = page._add_task_submenu(menu)
        twitch_menu = add_menu.actions()[3].menu()
        commercial_menu = next(
            action.menu()
            for action in twitch_menu.actions()
            if action.text() == "Run commercial"
        )

        commercial_menu.actions()[2].trigger()

        saved = self.twitch_command_trigger_store.routine_store.get(
            routine.routine_id
        )
        self.assertEqual(len(saved.tasks), 1)
        self.assertEqual(saved.tasks[0].task_type, "twitch.run_commercial")
        self.assertEqual(saved.tasks[0].config, {"length": 90})

    def test_trigger_add_menu_is_grouped_by_service(self) -> None:
        routine = self.twitch_command_trigger_store.routine_store.add("Trigger menu")
        page = self.window.automation_page
        page.select_routine(routine.routine_id)
        menu = QMenu()
        add_menu = page._add_trigger_submenu(menu)
        self.assertEqual(
            [action.text() for action in add_menu.actions()],
            ["Core", "OBS", "Twitch"],
        )
        self.assertEqual(
            [action.text() for action in add_menu.actions()[0].menu().actions()],
            ["Program Event"],
        )
        program_event_menu = add_menu.actions()[0].menu().actions()[0].menu()
        self.assertEqual(
            [action.text() for action in program_event_menu.actions()],
            ["Application Started", "Application Closing"],
        )
        self.assertEqual(
            [action.text() for action in add_menu.actions()[1].menu().actions()],
            list(OBS_TRIGGER_TYPES.values()),
        )
        self.assertEqual(
            [action.text() for action in add_menu.actions()[2].menu().actions()],
            ["Chat", "Ads", "Events"],
        )
        twitch_menu = add_menu.actions()[2].menu()
        self.assertEqual(
            [action.text() for action in twitch_menu.actions()[0].menu().actions()],
            ["Chat Command…", "Keyword / Phrase…", "First Message Of Stream"],
        )
        self.assertEqual(
            [action.text() for action in twitch_menu.actions()[1].menu().actions()],
            [
                "5 Minute Warning",
                "3 Minute Warning",
                "2 Minute Warning",
                "1 Minute Warning",
                "Ads Started",
                "Ads Ended",
            ],
        )
        self.assertEqual(
            [action.text() for action in twitch_menu.actions()[2].menu().actions()],
            [
                "Follow",
                "Subscribe",
                "Subscription › Gift",
                "Subscription › Message",
                "Cheer",
                "Raid",
                "Channel Points Custom Reward Redemption › Add",
                "Stream › Online",
                "Stream › Offline",
            ],
        )

    def test_core_program_event_menu_adds_selected_trigger_directly(self) -> None:
        routine = self.twitch_command_trigger_store.routine_store.add(
            "Direct Core trigger"
        )
        page = self.window.automation_page
        page.select_routine(routine.routine_id)
        menu = QMenu()
        add_menu = page._add_trigger_submenu(menu)
        program_event_menu = add_menu.actions()[0].menu().actions()[0].menu()

        program_event_menu.actions()[0].trigger()

        triggers = page.core_trigger_store.for_routine(routine.routine_id)
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0].event_type, "application.started")
        self.assertTrue(triggers[0].enabled)

    def test_twitch_eventsub_menu_adds_selected_trigger_directly(self) -> None:
        routine = self.twitch_command_trigger_store.routine_store.add(
            "Direct Twitch trigger"
        )
        page = self.window.automation_page
        page.select_routine(routine.routine_id)
        menu = QMenu()
        add_menu = page._add_trigger_submenu(menu)
        twitch_menu = add_menu.actions()[2].menu()
        event_menu = twitch_menu.actions()[2].menu()

        event_menu.actions()[0].trigger()

        triggers = page.event_trigger_store.for_routine(routine.routine_id)
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0].event_type, "channel.follow")
        self.assertEqual(triggers[0].filters, {})
        self.assertTrue(triggers[0].enabled)

    def test_obs_menu_adds_selected_trigger_directly(self) -> None:
        routine = self.twitch_command_trigger_store.routine_store.add(
            "Direct OBS trigger"
        )
        page = self.window.automation_page
        page.select_routine(routine.routine_id)
        menu = QMenu()
        add_menu = page._add_trigger_submenu(menu)
        obs_menu = add_menu.actions()[1].menu()

        obs_menu.actions()[2].trigger()

        triggers = page.obs_trigger_store.for_routine(routine.routine_id)
        self.assertEqual(len(triggers), 1)
        self.assertEqual(triggers[0].event_type, "CurrentProgramSceneChanged")
        self.assertEqual(triggers[0].filters, {})
        self.assertTrue(triggers[0].enabled)

    def test_variable_help_uses_selected_routine_trigger_context(self) -> None:
        routine = self.twitch_command_trigger_store.routine_store.add(
            "OBS variables"
        )
        self.window.obs_trigger_store.add(
            routine.routine_id,
            "CurrentProgramSceneChanged",
        )
        context = self.window.automation_page._preview_context_for_routine(routine)

        self.assertEqual(context["obs.scene"], "Gameplay")
        self.assertEqual(context["obs.source"], "Camera")
        self.assertEqual(context["event.reward_id"], "reward-123")

    def test_variable_help_includes_values_generated_by_routine_tasks(self) -> None:
        routine = self.twitch_command_trigger_store.routine_store.add(
            "Random greeting"
        )
        self.twitch_command_trigger_store.routine_store.add_task(
            routine.routine_id,
            task_type="core.file_random_line",
            name="Choose greeting",
            config={"path": "greetings.txt", "variable": "random_line"},
        )
        routine = self.twitch_command_trigger_store.routine_store.get(
            routine.routine_id
        )

        context = self.window.automation_page._preview_context_for_routine(routine)

        self.assertEqual(context["automation.random_line"], "Example")

    def test_generated_output_discovery_respects_task_order(self) -> None:
        store = self.twitch_command_trigger_store.routine_store
        routine = store.add("Ordered outputs")
        first = store.add_task(
            routine.routine_id,
            task_type="core.file_random_line",
            name="First output",
            config={"path": "first.txt", "variable": "first_value"},
        )
        consumer = store.add_task(
            routine.routine_id,
            task_type="twitch.send_chat_message",
            name="Consumer",
            config={"message": "{automation.first_value}", "as_bot": True},
        )
        store.add_task(
            routine.routine_id,
            task_type="core.file_random_line",
            name="Later output",
            config={"path": "later.txt", "variable": "later_value"},
        )
        routine = store.get(routine.routine_id)
        page = self.window.automation_page

        before_first = page._output_definitions_before(routine, first.task_id)
        before_consumer = page._output_definitions_before(routine, consumer.task_id)
        all_outputs = page._output_definitions_before(routine)

        self.assertEqual(before_first, ())
        self.assertEqual(
            tuple(item.name for item in before_consumer),
            ("automation.first_value",),
        )
        self.assertEqual(
            tuple(item.name for item in all_outputs),
            ("automation.first_value", "automation.later_value"),
        )
        preview = page._preview_context_for_routine(routine, consumer.task_id)
        self.assertIn("automation.first_value", preview)
        self.assertNotIn("automation.later_value", preview)

    def test_twitch_command_manager_lists_existing_and_respects_routine_limit(self) -> None:
        existing = self.twitch_command_trigger_store.add("hello", "Hello!")
        empty_routine = self.twitch_command_trigger_store.routine_store.add(
            "Empty routine"
        )
        manager = TwitchCommandManagerDialog(
            self.twitch_command_trigger_store,
            empty_routine.routine_id,
        )
        self.assertEqual(manager.command_list.count(), 1)
        self.assertIn("!hello", manager.command_list.item(0).text())
        self.assertTrue(manager.create_button.isEnabled())
        self.assertFalse(manager.edit_button.isEnabled())
        self.assertFalse(manager.select_button.isEnabled())
        manager.command_list.setCurrentRow(0)
        self.assertTrue(manager.edit_button.isEnabled())
        self.assertTrue(manager.select_button.isEnabled())
        manager.select_button.click()
        self.assertEqual(manager.selected_trigger_id, existing.trigger_id)
        self.assertEqual(manager.selected_routine_id, existing.routine_id)
        manager.close()

        attached_manager = TwitchCommandManagerDialog(
            self.twitch_command_trigger_store,
            existing.routine_id,
        )
        self.assertFalse(attached_manager.create_button.isEnabled())
        attached_manager.close()

    def test_twitch_command_dialog_allows_trigger_only_command(self) -> None:
        dialog = TwitchCommandDialog(self.window)
        dialog.name_edit.setText("lights")

        values = dialog.values()

        self.assertEqual(values["response"], "")
        command = self.twitch_command_trigger_store.add(**values)
        self.assertEqual(
            self.twitch_command_trigger_store.response_for(command),
            "",
        )
        self.assertEqual(
            self.twitch_command_trigger_store.routine_store.get(
                command.routine_id
            ).tasks,
            [],
        )
        dialog.close()

    def test_task_drag_reorder_persists_to_routine(self) -> None:
        store = self.twitch_command_trigger_store.routine_store
        routine = store.add("Reorder me")
        first = store.add_task(
            routine.routine_id,
            task_type="core.wait",
            name="First",
            config={"duration": "1", "unit": "seconds"},
        )
        second = store.add_task(
            routine.routine_id,
            task_type="core.wait",
            name="Second",
            config={"duration": "2", "unit": "seconds"},
        )
        page = self.window.automation_page
        page.select_routine(routine.routine_id)
        moved = page.task_list.model().moveRow(
            page.task_list.rootIndex(),
            0,
            page.task_list.rootIndex(),
            2,
        )
        self.assertTrue(moved)
        self.assertEqual(
            [task.task_id for task in store.get(routine.routine_id).tasks],
            [first.task_id, second.task_id],
        )

        self.application.processEvents()

        self.assertEqual(
            [task.task_id for task in store.get(routine.routine_id).tasks],
            [second.task_id, first.task_id],
        )
        page.task_list.setCurrentRow(1)
        self.assertEqual(page._selected_task().task_id, first.task_id)
        self.assertTrue(page.task_list.isEnabled())

    def test_task_copy_and_paste_preserves_config_with_new_id(self) -> None:
        store = self.twitch_command_trigger_store.routine_store
        source = store.add("Source")
        original = store.add_task(
            source.routine_id,
            task_type="core.wait",
            name="Wait briefly",
            config={"duration": "2.5", "unit": "seconds"},
        )
        destination = store.add("Destination")
        page = self.window.automation_page
        page.select_routine(source.routine_id)
        page.task_list.setCurrentRow(0)
        page._copy_task()
        page.select_routine(destination.routine_id)

        page._paste_task()

        pasted = store.get(destination.routine_id).tasks[0]
        self.assertEqual(pasted.task_type, original.task_type)
        self.assertEqual(pasted.name, original.name)
        self.assertEqual(pasted.config, original.config)
        self.assertNotEqual(pasted.task_id, original.task_id)

    def test_routine_drag_handler_persists_group_and_order(self) -> None:
        store = self.twitch_command_trigger_store.routine_store
        group = store.add_group("Moved here")
        first = store.add("First", group_id=group.group_id)
        second = store.add("Second")
        page = self.window.automation_page

        page._routine_dropped(second.routine_id, group.group_id, 0)

        self.assertEqual(
            [routine.routine_id for routine in store.grouped(group.group_id)],
            [second.routine_id, first.routine_id],
        )
        self.assertEqual(page._selected_routine_id, second.routine_id)

    def test_routine_drop_waits_until_drag_event_has_finished(self) -> None:
        store = self.twitch_command_trigger_store.routine_store
        group = store.add_group("Moved here")
        first = store.add("First", group_id=group.group_id)
        second = store.add("Second")
        page = self.window.automation_page

        page.routine_tree._schedule_routine_drop(
            second.routine_id,
            group.group_id,
            0,
        )

        self.assertEqual(
            [routine.routine_id for routine in store.grouped(group.group_id)],
            [first.routine_id],
        )

        self.application.processEvents()

        self.assertEqual(
            [routine.routine_id for routine in store.grouped(group.group_id)],
            [second.routine_id, first.routine_id],
        )
        self.assertEqual(page._selected_routine_id, second.routine_id)
        self.assertTrue(page.routine_tree.isEnabled())

    def test_ungrouped_routines_stay_above_custom_groups(self) -> None:
        store = self.twitch_command_trigger_store.routine_store
        group = store.add_group("Custom Group")
        store.add("Grouped", group_id=group.group_id)
        store.add("Loose Routine")

        page = self.window.automation_page
        page.refresh()

        self.assertTrue(page.routine_tree.topLevelItem(0).text(0).startswith("Ungrouped"))
        self.assertTrue(page.routine_tree.topLevelItem(1).text(0).startswith("Custom Group"))

    def test_alphabetical_routine_view_keeps_ungrouped_first(self) -> None:
        store = self.twitch_command_trigger_store.routine_store
        zebra_group = store.add_group("Zebra")
        store.add_group("Alpha")
        ungrouped_zulu = store.add("Zulu")
        ungrouped_alpha = store.add("Alpha")
        store.add("Zulu grouped", group_id=zebra_group.group_id)
        store.add("Alpha grouped", group_id=zebra_group.group_id)
        page = self.window.automation_page

        page.sort_routines_button.setChecked(True)

        self.assertTrue(page.routine_tree.topLevelItem(0).text(0).startswith("Ungrouped"))
        self.assertTrue(page.routine_tree.topLevelItem(1).text(0).startswith("Alpha"))
        self.assertTrue(page.routine_tree.topLevelItem(2).text(0).startswith("Zebra"))
        ungrouped_item = page.routine_tree.topLevelItem(0)
        self.assertEqual(
            [
                ungrouped_item.child(index).data(0, Qt.ItemDataRole.UserRole)
                for index in range(ungrouped_item.childCount())
            ],
            [ungrouped_alpha.routine_id, ungrouped_zulu.routine_id],
        )
        zebra_item = page.routine_tree.topLevelItem(2)
        self.assertEqual(
            [
                store.get(
                    zebra_item.child(index).data(0, Qt.ItemDataRole.UserRole)
                ).name
                for index in range(zebra_item.childCount())
            ],
            ["Alpha grouped", "Zulu grouped"],
        )
        self.assertFalse(page.routine_tree.property("routine_reorder_enabled"))

        page.sort_routines_button.setChecked(False)

        self.assertTrue(page.routine_tree.topLevelItem(1).text(0).startswith("Zebra"))
        self.assertEqual(
            [routine.routine_id for routine in store.grouped("")],
            [ungrouped_zulu.routine_id, ungrouped_alpha.routine_id],
        )
        self.assertTrue(page.routine_tree.property("routine_reorder_enabled"))

    def test_routine_rows_show_validation_and_counts(self) -> None:
        store = self.twitch_command_trigger_store.routine_store
        routine = store.add("Needs tasks")
        page = self.window.automation_page
        page.select_routine(routine.routine_id)
        item = page.routine_tree.topLevelItem(0).child(0)

        self.assertIn("[!]", item.text(0))
        self.assertIn("Manual", item.text(0))
        self.assertIn("0 tasks", item.text(0))
        self.assertIn("no tasks", item.toolTip(0).lower())

    def test_run_history_exposes_task_details_and_duration(self) -> None:
        store = self.twitch_command_trigger_store.routine_store
        routine = store.add("History details")
        task = store.add_task(
            routine.routine_id,
            task_type="core.wait",
            name="Short wait",
            config={"duration": "0", "unit": "seconds"},
        )
        page = self.window.automation_page
        page.record_execution(
            AutomationExecutionResult(
                event_id="event",
                trigger_id="manual",
                routine_results=(
                    RoutineExecutionResult(
                        routine_id=routine.routine_id,
                        succeeded=True,
                        task_results=(
                            TaskExecutionResult(
                                task.task_id,
                                task.task_type,
                                True,
                                "Waited.",
                                12,
                            ),
                        ),
                        queue_id=DEFAULT_AUTOMATION_QUEUE_ID,
                        started_at="2026-08-31T18:00:00+00:00",
                        finished_at="2026-08-31T18:00:00.012000+00:00",
                        duration_ms=12,
                        trigger_service="streamhouse",
                        trigger_type="manual",
                        context_values=(
                            ("command.data", "historical value"),
                            ("event.oauth_token", "must not appear"),
                        ),
                    ),
                ),
            ),
            "Manual test",
        )

        self.assertIn("Short wait", page.history_details.toPlainText())
        self.assertIn("12 ms", page.history_details.toPlainText())
        self.assertIn("Waited.", page.history_details.toPlainText())
        entry = page.history[0]
        dialog = RunHistoryDetailsDialog(entry, page)
        self.assertEqual(dialog.summary_labels["Routine"].text(), "History details")
        self.assertEqual(dialog.summary_labels["Final status"].text(), "Completed")
        self.assertEqual(dialog.task_tree.topLevelItem(0).text(0), "Short wait")
        self.assertEqual(dialog.task_tree.topLevelItem(0).text(1), "Completed")
        self.assertEqual(dialog.context_table.item(0, 0).text(), "command.data")
        self.assertEqual(
            dialog.context_table.item(0, 1).text(),
            "historical value",
        )
        self.assertEqual(dialog.context_table.rowCount(), 1)
        with patch(
            "products.hub.ui.automation_page.RunHistoryDetailsDialog.exec",
            return_value=QDialog.DialogCode.Rejected,
        ) as open_details:
            page.history_table.selectRow(0)
            page._open_history_details(page.history_table)
            open_details.assert_called_once_with()
        dialog.deleteLater()

    def test_run_history_details_handles_nested_failure_and_missing_optional_data(self) -> None:
        store = self.twitch_command_trigger_store.routine_store
        child = store.add("Nested child")
        child_task = store.add_task(
            child.routine_id,
            task_type="core.wait",
            name="Child wait",
        )
        parent = store.add("Parent history")
        parent_task = store.add_task(
            parent.routine_id,
            task_type="core.run_routine",
            name="Run child",
        )
        nested = RoutineExecutionResult(
            routine_id=child.routine_id,
            succeeded=False,
            task_results=(
                TaskExecutionResult(
                    child_task.task_id,
                    child_task.task_type,
                    False,
                    "Child failed. Authorization: Bearer private-token",
                ),
            ),
            detail="Nested routine failed.",
        )
        page = self.window.automation_page
        page.record_execution(
            AutomationExecutionResult(
                "nested-event",
                "manual",
                (
                    RoutineExecutionResult(
                        parent.routine_id,
                        False,
                        (
                            TaskExecutionResult(
                                parent_task.task_id,
                                parent_task.task_type,
                                False,
                                "Parent stopped.",
                                nested_results=(nested,),
                            ),
                        ),
                        detail="Routine stopped after a failed task.",
                    ),
                ),
            )
        )

        dialog = RunHistoryDetailsDialog(page.history[0], page)
        parent_item = dialog.task_tree.topLevelItem(0)
        self.assertEqual(parent_item.text(1), "Failed")
        self.assertEqual(parent_item.child(0).text(0), "Nested child")
        self.assertEqual(parent_item.child(0).child(0).text(0), "Child wait")
        self.assertNotIn(
            "private-token",
            parent_item.child(0).child(0).text(3),
        )
        self.assertIn("[REDACTED]", parent_item.child(0).child(0).text(3))
        self.assertEqual(dialog.summary_labels["Started"].text(), "Not recorded")
        self.assertEqual(
            dialog.summary_labels["Failure reason"].text(),
            "Routine stopped after a failed task.",
        )
        self.assertEqual(dialog.context_table.rowCount(), 0)
        dialog.deleteLater()

    def test_queue_stop_controls_and_cancelled_history_state(self) -> None:
        page = self.window.automation_page
        manager = self.window.automation_queue_manager
        store = self.twitch_command_trigger_store.routine_store
        routine = store.add("Emergency recovery")
        task = store.add_task(
            routine.routine_id,
            task_type="core.wait",
            name="Long wait",
            config={"duration": "10", "unit": "seconds"},
        )
        trigger = TriggerEvent("manual", "test", "manual", {})
        manager.enqueue(
            DEFAULT_AUTOMATION_QUEUE_ID,
            routine.routine_id,
            routine.name,
            trigger,
        )
        current = manager.take_ready(DEFAULT_AUTOMATION_QUEUE_ID)
        self.assertIsNotNone(current)
        manager.enqueue(
            DEFAULT_AUTOMATION_QUEUE_ID,
            routine.routine_id,
            routine.name,
            trigger,
        )
        page._refresh_queues(DEFAULT_AUTOMATION_QUEUE_ID)

        self.assertTrue(page.stop_current_routine_button.isEnabled())
        self.assertTrue(page.stop_queue_button.isEnabled())
        page.stop_current_routine_button.click()
        self.assertTrue(manager.current_cancelled(DEFAULT_AUTOMATION_QUEUE_ID))
        self.assertEqual(manager.count(DEFAULT_AUTOMATION_QUEUE_ID), 1)
        self.assertFalse(page.stop_current_routine_button.isEnabled())

        page.stop_queue_button.click()
        self.assertEqual(manager.count(DEFAULT_AUTOMATION_QUEUE_ID), 0)
        self.assertFalse(page.stop_queue_button.isEnabled())
        manager.complete(DEFAULT_AUTOMATION_QUEUE_ID)

        page.record_execution(
            AutomationExecutionResult(
                event_id="cancelled-event",
                trigger_id="manual",
                routine_results=(
                    RoutineExecutionResult(
                        routine_id=routine.routine_id,
                        succeeded=False,
                        task_results=(
                            TaskExecutionResult(
                                task.task_id,
                                task.task_type,
                                False,
                                "Cancelled by user.",
                                15,
                                cancelled=True,
                            ),
                        ),
                        detail="Cancelled by user.",
                        cancelled=True,
                    ),
                ),
            ),
            "Manual test",
        )

        self.assertEqual(page.history[0]["result"], "Cancelled")
        self.assertIn("Long wait — Cancelled", page.history[0]["details"])
        dialog = RunHistoryDetailsDialog(page.history[0], page)
        self.assertEqual(dialog.summary_labels["Final status"].text(), "Cancelled")
        self.assertEqual(
            dialog.summary_labels["Failure reason"].text(),
            "Cancelled by user.",
        )
        self.assertEqual(dialog.task_tree.topLevelItem(0).text(1), "Cancelled")
        dialog.deleteLater()

    def test_automation_page_lists_command_event_and_core_triggers_together(self) -> None:
        command = self.twitch_command_trigger_store.add("hello", "Hello")
        event_trigger = self.twitch_event_trigger_store.add(
            command.routine_id,
            "channel.raid",
            filters={"from_broadcaster_user_login": "friend"},
        )
        core_trigger = self.window.core_trigger_store.add(
            command.routine_id, "application.started"
        )

        self.window.automation_page.select_routine(command.routine_id)

        page = self.window.automation_page
        self.assertEqual(page.trigger_list.count(), 3)
        self.assertEqual(page.editor_tabs.tabText(0), "Triggers (3)")
        page._select_trigger("event", event_trigger.trigger_id)
        self.assertIn("channel.raid", page.trigger_detail_label.text())
        self.assertIn(
            "from_broadcaster_user_login=friend",
            page.trigger_detail_label.text(),
        )
        page._select_trigger("core", core_trigger.trigger_id)
        self.assertIn("Application Started", page.trigger_detail_label.text())

    def test_core_started_and_closing_triggers_execute_once(self) -> None:
        store = self.twitch_command_trigger_store.routine_store
        started = store.add("On startup")
        store.add_task(
            started.routine_id,
            task_type="twitch.send_chat_message",
            name="Started",
            config={"message": "Hub started", "as_bot": True},
        )
        closing = store.add("On closing")
        store.add_task(
            closing.routine_id,
            task_type="twitch.send_chat_message",
            name="Closing",
            config={"message": "Hub closing", "as_bot": True},
        )
        self.window.core_trigger_store.add(
            started.routine_id, "application.started"
        )
        self.window.core_trigger_store.add(
            closing.routine_id, "application.closing"
        )
        self.window.twitch_service.send_message = Mock(return_value=True)

        self.window.fire_application_started_trigger()
        self.window.fire_application_started_trigger()
        self.window.close()
        self.window.close()

        self.assertEqual(
            self.window.twitch_service.send_message.call_args_list,
            [
                unittest.mock.call("Hub started", as_bot=True),
                unittest.mock.call("Hub closing", as_bot=True),
            ],
        )
        self.assertEqual(len(self.window.automation_page.history), 2)

    def test_twitch_command_can_open_its_connected_automation_routine(self) -> None:
        command = self.twitch_command_trigger_store.add(
            "socials", "Links for {user.display_name}"
        )
        self.window._refresh_twitch_commands(command.trigger_id)

        self.window.open_twitch_command_routine_button.click()

        self.assertIs(
            self.window.ui.mainStack.currentWidget(),
            self.window.automation_page,
        )
        self.assertEqual(
            self.window.automation_page._selected_routine_id,
            command.routine_id,
        )
        self.assertEqual(
            self.window.automation_page.routine_title_label.text(),
            "Command !socials",
        )

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

    def test_channel_workspace_keeps_stream_sessions_inside_analytics(self) -> None:
        ai_tab_names = [
            self.window.ai_tabs.tabText(index)
            for index in range(self.window.ai_tabs.count())
        ]
        channel_tab_names = [
            self.window.channel_tabs.tabText(index)
            for index in range(self.window.channel_tabs.count())
        ]
        self.assertEqual(
            ai_tab_names,
            [
                "Memories",
                "Reply Review",
                "Test Report",
                "Training",
                "Personality",
            ],
        )
        self.assertEqual(
            channel_tab_names,
            [
                "Chat",
                "Analytics",
                "Soundboard",
                "Channel Information",
                "Commands",
                "Channel Points",
                "Counters",
                "User",
            ],
        )
        self.assertEqual(
            self.window.analytics_labels["sessions"].text(),
            "0",
        )
        self.assertEqual(self.window.analytics_range_combo.count(), 4)
        self.assertTrue(self.window.ui.twitchDetailTabs.tabBar().isHidden())
        self.assertTrue(self.window.ui.twitchChatCountLabel.isHidden())

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
        self.assertEqual(self.window.ai_tabs.count(), 5)
        self.assertEqual(self.window.channel_tabs.count(), 8)
        self.assertEqual(
            [
                self.window.automation_page.tabs.tabText(index)
                for index in range(self.window.automation_page.tabs.count())
            ],
            ["Routines", "Queues", "Task Library", "Variables", "Run History"],
        )
        self.assertFalse(self.window.channel_points_page.create_button.isEnabled())
        self.assertEqual(
            [
                self.window.logs_tabs.tabText(index)
                for index in range(self.window.logs_tabs.count())
            ],
            ["Application", "Twitch Events"],
        )
        self.assertEqual(
            self.window.logs_tabs.indexOf(self.window.ui.twitchEventsTab),
            1,
        )
        self.assertTrue(self.window.create_backup_button.isEnabled())

    def test_task_library_shows_and_searches_registry_descriptions(self) -> None:
        page = self.window.automation_page
        self.assertEqual(
            {
                metadata.task_type
                for metadata in self.window.task_registry.visible_metadata()
            },
            set(self.window.task_registry.registered_types()),
        )

        def task_item(task_type: str):
            pending = [
                page.task_library_tree.topLevelItem(index)
                for index in range(page.task_library_tree.topLevelItemCount())
            ]
            while pending:
                item = pending.pop()
                pending.extend(
                    item.child(index) for index in range(item.childCount())
                )
                if item.data(0, Qt.ItemDataRole.UserRole) == task_type:
                    return item
            return None

        for task_type, expected_text in (
            ("twitch.send_chat_message", "Twitch chat"),
            ("counter.increase", "selected Counter"),
            ("obs.set_program_scene", "OBS program scene"),
            ("core.file_read", "automation.* output"),
        ):
            item = task_item(task_type)
            self.assertIsNotNone(item)
            page.task_library_tree.setCurrentItem(item)
            self.application.processEvents()
            self.assertIn(
                expected_text,
                page.task_library_description_label.text(),
            )
            self.assertTrue(item.text(1))
        self.assertTrue(page.task_library_description_label.wordWrap())
        self.assertIn(
            "routine-scoped automation.*",
            page.task_library_facts_label.text(),
        )

        category = page.task_library_tree.topLevelItem(0)
        page.task_library_tree.setCurrentItem(category)
        self.assertEqual(page.task_library_title_label.text(), "Select a task")

        page.task_library_search_edit.setText("configured chat account")
        self.application.processEvents()
        self.assertIsNotNone(task_item("twitch.send_chat_message"))
        self.assertIsNone(task_item("counter.increase"))

        page.task_library_search_edit.setText("deterministic")
        self.application.processEvents()
        self.assertIsNotNone(task_item("obs.set_scene_item_enabled"))

    def test_task_library_renders_structured_metadata_and_real_outputs(self) -> None:
        page = self.window.automation_page

        def task_item(task_type: str):
            pending = [
                page.task_library_tree.topLevelItem(index)
                for index in range(page.task_library_tree.topLevelItemCount())
            ]
            while pending:
                item = pending.pop()
                pending.extend(
                    item.child(index) for index in range(item.childCount())
                )
                if item.data(0, Qt.ItemDataRole.UserRole) == task_type:
                    return item
            return None

        page.task_library_search_edit.clear()
        page.task_library_tree.setCurrentItem(task_item("core.wait"))
        self.application.processEvents()
        wait_help = page.task_library_help_browser.toPlainText()
        for heading in ("What it does", "Inputs", "Variables", "Notes / Limitations", "Example"):
            self.assertIn(heading, wait_help)
        self.assertIn("Duration", wait_help)
        self.assertIn("Accepts canonical Variables", wait_help)
        self.assertNotIn("Outputs", wait_help)
        self.assertNotIn("None", wait_help)

        page.task_library_tree.setCurrentItem(
            task_item("twitch.get_stream_information")
        )
        self.application.processEvents()
        output_help = page.task_library_help_browser.toPlainText()
        self.assertIn("Outputs", output_help)
        self.assertIn("{automation.stream_title}", output_help)
        self.assertNotIn("{automation.random_line}", output_help)
        self.assertIn("Requires Twitch broadcaster authorization", output_help)

        page.task_library_tree.setCurrentItem(task_item("obs.raw_request"))
        self.application.processEvents()
        raw_help = page.task_library_help_browser.toPlainText()
        self.assertIn("Request type", raw_help)
        self.assertIn("JSON object", raw_help)
        self.assertIn("active OBS connection", raw_help)

        for metadata in self.window.task_registry.visible_metadata():
            item = task_item(metadata.task_type)
            self.assertIsNotNone(item, metadata.task_type)
            page.task_library_tree.setCurrentItem(item)
            self.application.processEvents()
            rendered = page.task_library_help_browser.toPlainText()
            self.assertIn("What it does", rendered, metadata.task_type)
            self.assertNotIn("None", rendered, metadata.task_type)

    def test_custom_twitch_command_sends_as_bot_and_skips_ai_reasoning(self) -> None:
        command = self.twitch_command_trigger_store.add(
            "hello",
            "Hello {user.display_name}! Welcome to {stream.channel}.",
            aliases=["hi"],
            global_cooldown_seconds=0,
            user_cooldown_seconds=0,
        )
        self.window.twitch_service.channel = "streamhousechannel"
        self.window.twitch_service.send_message = Mock(return_value=True)
        self.window._queue_response_decision = Mock()

        self.window.handle_twitch_message(
            TwitchMessage(
                username="Viewer",
                text="!hi",
                received_at=datetime.now(timezone.utc),
                user_id="viewer-1",
                user_login="viewer",
                broadcaster_user_id="broadcaster-1",
            )
        )

        self.window.twitch_service.send_message.assert_called_once_with(
            "Hello Viewer! Welcome to streamhousechannel.",
            as_bot=True,
        )
        self.assertEqual(command.uses, 1)
        self.window._queue_response_decision.assert_not_called()

    def test_twitch_command_task_resolves_live_obs_and_twitch_variables(self) -> None:
        self.twitch_command_trigger_store.add(
            "status",
            "Scene is {obs.current_scene}; playing {stream.category}.",
            global_cooldown_seconds=0,
            user_cooldown_seconds=0,
        )
        self.window.last_channel_snapshot = ChannelSnapshotResult(
            request_id=1,
            snapshot={
                "stream": None,
                "channel": {"game_name": "Science & Technology"},
            },
        )
        self.window.obs_service._current_program_scene = "Gameplay"
        self.window.obs_service.state = ObsConnectionState.CONNECTED
        self.window.obs_service._identified = True
        self.window.twitch_service.send_message = Mock(return_value=True)

        self.window.handle_twitch_message(
            TwitchMessage(
                username="Viewer",
                text="!status",
                received_at=datetime.now(timezone.utc),
                user_id="viewer-1",
                user_login="viewer",
                broadcaster_user_id="broadcaster-1",
            )
        )

        self.window.twitch_service.send_message.assert_called_once_with(
            "Scene is Gameplay; playing Science & Technology.",
            as_bot=True,
        )

    def test_twitch_command_actions_require_a_selected_command(self) -> None:
        self.window._refresh_twitch_commands()

        self.assertTrue(self.window.add_twitch_command_button.isEnabled())
        self.assertFalse(self.window.edit_twitch_command_button.isEnabled())
        self.assertFalse(self.window.toggle_twitch_command_button.isEnabled())
        self.assertFalse(self.window.delete_twitch_command_button.isEnabled())
        self.assertFalse(self.window.reset_twitch_command_button.isEnabled())

    def test_default_template_configures_one_routine_then_supports_reset(self) -> None:
        self.window._refresh_twitch_commands()
        self.assertEqual(self.twitch_command_trigger_store.routine_store.routines, [])
        self.assertEqual(self.twitch_command_trigger_store.routine_store.groups, [])
        row = next(
            row
            for row in range(self.window.twitch_commands_table.rowCount())
            if self.window.twitch_commands_table.item(row, 1).text() == "!uptime"
        )
        self.window.twitch_commands_table.selectRow(row)
        self.assertEqual(
            self.window.twitch_commands_table.item(row, 0).text(),
            "Not Configured",
        )
        self.assertEqual(
            self.window.twitch_commands_table.item(row, 7).text(),
            "Default Template",
        )
        self.assertEqual(self.window.edit_twitch_command_button.text(), "Configure Selected")
        self.window.edit_twitch_command_button.click()

        command = self.twitch_command_trigger_store.default("uptime")
        self.assertIsNotNone(command)
        self.assertEqual(len(self.twitch_command_trigger_store.routine_store.routines), 1)
        self.assertEqual(
            self.twitch_command_trigger_store.routine_store.groups[0].name,
            "Commands",
        )
        command_group = next(
            self.window.automation_page.routine_tree.topLevelItem(index)
            for index in range(
                self.window.automation_page.routine_tree.topLevelItemCount()
            )
            if self.window.automation_page.routine_tree.topLevelItem(index)
            .text(0)
            .startswith("Commands")
        )
        self.assertEqual(command_group.childCount(), 1)
        self.assertEqual(
            command_group.child(0).data(0, Qt.ItemDataRole.UserRole),
            command.routine_id,
        )
        self.assertTrue(self.window.reset_twitch_command_button.isEnabled())

        with patch.object(
            QMessageBox,
            "question",
            return_value=QMessageBox.StandardButton.Yes,
        ), patch.object(
            self.twitch_command_trigger_store,
            "reset_default",
            wraps=self.twitch_command_trigger_store.reset_default,
        ) as reset:
            self.window.reset_twitch_command_button.click()
        reset.assert_called_once_with(command.default_id)

    def test_configured_defaults_render_first_and_open_channel_information(self) -> None:
        self.twitch_command_trigger_store.add("alpha", "Hello")
        self.twitch_command_trigger_store.configure_default("discord")
        self.window._refresh_twitch_commands()

        names = [
            self.window.twitch_commands_table.item(row, 1).text()
            for row in range(self.window.twitch_commands_table.rowCount())
        ]
        self.assertEqual(
            names[:12],
            [
                "!uptime", "!followage", "!accountage", "!title", "!game", "!commands",
                "!discord", "!socials", "!youtube", "!schedule", "!rules", "!server",
            ],
        )
        self.assertEqual(names[12:], ["!alpha"])
        discord_row = names.index("!discord")
        self.assertEqual(
            self.window.twitch_commands_table.item(discord_row, 0).text(),
            "Setup Required",
        )
        self.assertEqual(
            self.window.twitch_commands_table.item(discord_row, 7).text(),
            "Default",
        )
        self.window.twitch_commands_table.selectRow(discord_row)
        self.window.configure_channel_information_button.click()
        self.assertIs(
            self.window.channel_tabs.currentWidget(),
            self.window.channel_information_page,
        )
        self.assertTrue(
            self.window.channel_information_page.enable_after_saving_check.isHidden()
        )

    def test_social_update_refreshes_commands_routines_and_variables_together(self) -> None:
        page = self.window.channel_information_page
        include, edit, update, _error = page.social_rows["discord"]
        include.setChecked(True)
        edit.setText("discord.gg/example")
        self.assertIsNone(self.twitch_command_trigger_store.default("discord"))
        with patch.object(self.window.automation_page, "refresh",
                          wraps=self.window.automation_page.refresh) as refresh:
            update.click()
            refresh.assert_called_once()
        table = self.window.twitch_commands_table
        states = {table.item(row, 1).text(): table.item(row, 0).text()
                  for row in range(table.rowCount())}
        self.assertEqual(states["!discord"], "Enabled")
        self.assertEqual(states["!socials"], "Enabled")
        command = self.twitch_command_trigger_store.default("discord")
        routine = self.twitch_command_trigger_store.routine_store.get(command.routine_id)
        self.assertEqual(self.twitch_command_trigger_store.routine_store.get_group(routine.group_id).name,
                         "Commands")
        self.assertEqual(self.window.variable_registry.resolve("socials.discord").value,
                         "https://discord.gg/example")

    def test_command_filter_keeps_matching_defaults_before_customs(self) -> None:
        self.twitch_command_trigger_store.add("socialparty", "Party")
        self.window.twitch_command_search_edit.setText("social")

        names = [
            self.window.twitch_commands_table.item(row, 1).text()
            for row in range(self.window.twitch_commands_table.rowCount())
        ]
        self.assertEqual(names, ["!socials", "!socialparty"])

    def test_network_backed_command_runs_off_the_qt_thread(self) -> None:
        self.twitch_command_trigger_store.configure_default("uptime")
        started = Event()
        release = Event()

        def slow_stream_lookup():
            started.set()
            release.wait(2)
            return None

        self.window.twitch_service.get_stream_information = slow_stream_lookup
        self.window.twitch_service.send_message = Mock(return_value=True)
        incoming = TwitchMessage(
            username="Viewer",
            text="!uptime",
            received_at=datetime.now(timezone.utc),
            user_id="42",
            user_login="viewer",
            broadcaster_user_id="1",
        )

        before = monotonic()
        self.assertTrue(self.window._handle_twitch_custom_command(incoming, False))
        self.assertLess(monotonic() - before, 0.5)
        self.assertTrue(started.wait(1))
        release.set()
        self.window.command_thread_pool.waitForDone(2_000)
        self.application.processEvents()

        self.window.twitch_service.send_message.assert_called_once_with(
            "The channel is currently offline.",
            as_bot=True,
        )

    def test_settings_groups_are_separated_into_top_tabs(self) -> None:
        self.assertEqual(
            [
                self.window.settings_tabs.tabText(index)
                for index in range(self.window.settings_tabs.count())
            ],
            ["Application", "Chat", "AI", "Developer"],
        )
        self.assertIsNotNone(self.window.ui.generalSettingsGroup.parentWidget())
        self.assertIsNotNone(self.window.local_ai_settings_group.parentWidget())

    def test_memory_buttons_follow_viewer_and_memory_selection(self) -> None:
        buttons = (
            self.window.add_memory_button,
            self.window.edit_memory_button,
            self.window.pin_memory_button,
            self.window.archive_memory_button,
            self.window.delete_memory_button,
            self.window.export_memory_button,
            self.window.erase_memories_button,
            self.window.approve_memory_button,
            self.window.reject_memory_button,
        )
        self.assertTrue(all(not button.isEnabled() for button in buttons))

        memory = {
            "id": "memory-1",
            "text": "Likes puzzle games",
            "category": "Preference",
            "status": "pending",
            "confidence": 0.8,
            "pinned": False,
            "archived": False,
            "created_at": "2026-07-18T00:00:00+00:00",
            "last_confirmed_at": "2026-07-18T00:00:00+00:00",
            "evidence": [],
        }
        record = ChatterRecord(
            user_id="viewer-1",
            user_name="Viewer",
            first_seen="2026-07-01T00:00:00+00:00",
            last_seen="2026-07-18T00:00:00+00:00",
            memory_consent="opted_in",
            memory_enabled=True,
            memory_stream_ids=["1", "2", "3", "4", "5"],
            memories=[memory],
        )
        self.chatter_history_store.records = {"viewer-1": record}
        self.chatter_history_store.has_memory_consent.return_value = True
        self.chatter_history_store.can_create_keynotes.return_value = True
        self.chatter_history_store.get_memory.return_value = memory
        self.window.settings.ai_viewer_memory_enabled = True

        self.window._refresh_memory_viewer_list()
        self.window.memory_viewer_list.setCurrentRow(0)

        self.assertTrue(self.window.add_memory_button.isEnabled())
        self.assertTrue(self.window.export_memory_button.isEnabled())
        self.assertFalse(self.window.edit_memory_button.isEnabled())
        self.window.memory_ai_list.setCurrentRow(0)
        self.assertTrue(self.window.edit_memory_button.isEnabled())
        self.assertTrue(self.window.approve_memory_button.isEnabled())
        self.assertTrue(self.window.reject_memory_button.isEnabled())

    def test_window_geometry_is_restored_and_saved(self) -> None:
        self.window_state_store.restore.assert_called_once_with(self.window)

        self.window.close()

        self.window_state_store.save.assert_called_once_with(self.window)

    def test_window_screen_fit_is_attached_without_replacing_native_frame(self) -> None:
        import sys

        self.window.show()
        self.application.processEvents()
        self.assertIs(self.window.window_geometry._handle, self.window.windowHandle())
        if sys.platform == "win32":
            self.assertFalse(self.window.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(self.window.screen().availableGeometry().contains(
            self.window.frameGeometry()))

    def test_native_drag_notifications_only_defer_geometry_correction(self) -> None:
        import ctypes
        import sys
        from ctypes import wintypes
        from shiboken6 import VoidPtr

        if sys.platform != "win32":
            self.skipTest("Windows native move loop")
        message = wintypes.MSG()
        message.message = 0x0231  # WM_ENTERSIZEMOVE
        pointer = VoidPtr(ctypes.addressof(message))
        self.window.nativeEvent(b"windows_generic_MSG", pointer)
        self.assertTrue(self.window.window_geometry._interactive_move)
        self.window.window_geometry.request_fit()
        self.assertFalse(self.window.window_geometry._fit_timer.isActive())
        message.message = 0x0232  # WM_EXITSIZEMOVE
        self.window.nativeEvent(b"windows_generic_MSG", pointer)
        self.assertFalse(self.window.window_geometry._interactive_move)
        self.assertTrue(self.window.window_geometry._fit_timer.isActive())

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

    def test_page_titles_are_hidden_and_channel_workspace_uses_compact_layout(self) -> None:
        for title in (
            self.window.ui.dashboardTitleLabel,
            self.window.ui.twitchTitleLabel,
            self.window.ui.logsTitleLabel,
            self.window.ui.settingsTitleLabel,
        ):
            self.assertTrue(title.isHidden())

        overview = self.window.stream_overview_group
        self.assertLessEqual(overview.maximumHeight(), 118)
        self.assertIsNot(overview, self.window.ad_manager_group)
        self.assertEqual(
            len(overview.findChildren(type(self.window.stream_status_card))),
            5,
        )
        self.assertLessEqual(
            self.window.chatter_list.parentWidget().maximumWidth(),
            210,
        )
        self.assertLessEqual(
            self.window.activity_feed_list.parentWidget().minimumWidth(),
            140,
        )

    def test_window_can_shrink_to_small_screen_size(self) -> None:
        self.assertLessEqual(self.window.minimumWidth(), 520)
        self.assertLessEqual(self.window.minimumHeight(), 360)
        self.assertEqual(self.window.ui.mainStack.minimumSizeHint().width(), 0)
        self.window.resize(600, 400)
        self.assertEqual((self.window.width(), self.window.height()), (600, 400))
        self.assertTrue(self.window.settings_container.widgetResizable())

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
        card = self.window.activity_feed_list.itemWidget(
            self.window.activity_feed_list.item(0)
        )
        self.assertIsNotNone(card)
        self.assertEqual(card.objectName(), "activityFeedCard")
        self.assertEqual(
            card.findChild(QLabel, "activityFeedCategory").text(),
            "RAIDS",
        )
        self.assertEqual(
            card.findChild(QLabel, "activityFeedBody").text(),
            "Raider raided with 25 viewers",
        )
        self.assertEqual(
            card.findChild(QLabel, "activityFeedAge").text(),
            "just now",
        )

    def test_twitch_event_trigger_executes_connected_routine(self) -> None:
        routine_store = self.twitch_command_trigger_store.routine_store
        routine = routine_store.add("Thank follower")
        routine_store.add_task(
            routine.routine_id,
            task_type="twitch.send_chat_message",
            name="Thank them",
            config={"message": "Thanks for following, {user.display_name}!", "as_bot": True},
        )
        self.twitch_event_trigger_store.add(
            routine.routine_id, "channel.follow"
        )
        self.window.twitch_service.send_message = Mock(return_value=True)
        event = TwitchEvent(
            subscription_type="channel.follow",
            version="2",
            received_at=datetime.now(timezone.utc),
            message_id="follow-automation",
            broadcaster_user_id="42",
            broadcaster_user_login="channel",
            broadcaster_user_name="Channel",
            transport=TwitchEventTransport.SIMULATOR,
            payload={
                "event": {
                    "user_id": "viewer-1",
                    "user_login": "viewer",
                    "user_name": "TestViewer",
                    "broadcaster_user_name": "Channel",
                }
            },
        )

        self.window.handle_twitch_activity(event)

        self.window.twitch_service.send_message.assert_called_once_with(
            "Thanks for following, TestViewer!", as_bot=True
        )
        self.assertEqual(self.window.automation_page.history[0]["result"], "Completed")

    def test_obs_trigger_uses_mute_alias_and_twitch_channel_context(self) -> None:
        routine_store = self.twitch_command_trigger_store.routine_store
        routine = routine_store.add("Report mute state")
        routine_store.add_task(
            routine.routine_id,
            task_type="twitch.send_chat_message",
            name="Report status",
            config={
                "message": "Mic muted: {obs.muted} while playing {stream.category}",
                "as_bot": True,
            },
        )
        self.window.obs_trigger_store.add(
            routine.routine_id,
            "InputMuteStateChanged",
        )
        self.window.last_channel_snapshot = ChannelSnapshotResult(
            request_id=1,
            snapshot={
                "stream": None,
                "channel": {
                    "game_name": "Science & Technology",
                    "title": "Building Streamhouse",
                },
            },
        )
        self.window.twitch_service.send_message = Mock(return_value=True)

        self.window._handle_obs_automation_event(
            ObsEvent(
                "InputMuteStateChanged",
                {"inputName": "Mic/Aux", "inputMuted": True},
            )
        )

        self.window.twitch_service.send_message.assert_called_once_with(
            "Mic muted: true while playing Science & Technology",
            as_bot=True,
        )

    def test_stream_online_event_posts_training_notice_once(self) -> None:
        self.window.settings.ai_training_capture_enabled = True
        self.window.settings.ai_training_notice_enabled = True
        self.window.twitch_service.state = TwitchConnectionState.CONNECTED
        self.window.twitch_service.send_pinned_message = Mock(
            return_value=(True, True)
        )
        self.window.settings_store.save = Mock()
        self.window.refresh_channel_snapshot = Mock()
        event = TwitchEvent(
            subscription_type="stream.online",
            version="1",
            received_at=datetime.now(timezone.utc),
            message_id="online-1",
            broadcaster_user_id="42",
            broadcaster_user_login="channel",
            broadcaster_user_name="Channel",
            transport=TwitchEventTransport.WEBSOCKET,
            payload={
                "event": {
                    "id": "stream-42",
                    "started_at": datetime.now(timezone.utc).isoformat(),
                }
            },
        )

        self.window.handle_twitch_activity(event)
        self.window.handle_twitch_activity(event)

        self.window.twitch_service.send_pinned_message.assert_called_once()
        self.assertEqual(self.window.current_memory_stream_id, "stream-42")
        self.assertEqual(
            self.window.settings.ai_training_notice_stream_id,
            "stream-42",
        )
        self.assertEqual(self.window.refresh_channel_snapshot.call_count, 2)

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
            twitch_last_ad_duration=120,
            local_ai_endpoint="http://localhost:11434",
            local_ai_model="qwen3:14b",
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
        self.assertEqual(
            self.window.local_ai_endpoint_edit.text(),
            "http://localhost:11434",
        )
        self.assertEqual(self.window.local_ai_model_edit.text(), "qwen3:14b")
        self.assertFalse(self.window.ai_viewer_memory_check.isChecked())
        self.assertFalse(self.window.ai_memory_reasoning_check.isEnabled())
        self.assertTrue(self.window.ai_memory_reasoning_check.isChecked())
        self.assertEqual(self.window.ai_memory_threshold_spin.value(), 10)
        self.assertTrue(self.window.ai_response_decisions_check.isChecked())
        self.assertFalse(hasattr(self.window, "ai_auto_send_replies_check"))
        self.assertEqual(self.window.ai_response_max_age_spin.value(), 15)
        self.assertEqual(self.window.ai_response_interval_spin.value(), 8)
        self.assertEqual(self.window.ai_conversation_followup_spin.value(), 180)
        self.assertFalse(self.window.ai_interjections_check.isChecked())
        self.assertEqual(self.window.ai_interjection_interval_spin.value(), 300)
        self.assertEqual(self.window.ai_interjection_min_messages_spin.value(), 6)
        self.assertFalse(self.window.ai_training_capture_check.isChecked())
        self.assertTrue(self.window.ai_training_notice_check.isChecked())
        self.assertFalse(self.window.ai_training_notice_check.isEnabled())
        self.assertIn(
            "Participation is optional",
            self.window.ai_training_notice_edit.text(),
        )
        self.assertIn("quick-witted", self.window.ai_personality_edit.toPlainText())
        self.assertFalse(
            self.window.ai_allow_mild_profanity_check.isChecked()
        )
        self.assertFalse(
            self.window.ai_allow_strong_profanity_check.isChecked()
        )
        self.assertEqual(self.window.ui.twitchChatFontSizeSpin.value(), 14)
        self.assertEqual(self.window.ad_length_combo.currentData(), 120)
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
            "Chat",
        )
        self.assertEqual(self.window.ui.simulationMessageEdit.text(), "")

        self.window.ui.clearTwitchChatButton.click()
        self.assertIn(
            "No chat messages yet",
            self.window.ui.twitchChatOutput.toPlainText(),
        )
        self.assertEqual(
            self.window.ui.twitchChatCountLabel.text(),
            "Chat",
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

    def test_bot_auth_has_independent_connection_controls(self) -> None:
        self.window.twitch_bot_auth.token = Mock(
            scopes=["user:read:chat", "user:write:chat", "user:bot"],
            user_id="bot-1",
            login="testbot",
        )

        self.window.handle_twitch_bot_auth_changed(
            TwitchAuthState.SIGNED_IN,
            "testbot",
        )

        self.assertEqual(
            self.window.twitch_bot_account_status_label.text(),
            "@testbot — Connected",
        )
        self.assertFalse(self.window.twitch_bot_sign_in_button.isEnabled())
        self.assertTrue(self.window.twitch_bot_sign_out_button.isEnabled())

        self.window.response_decision_thread_pool.start = Mock()
        self.window.handle_twitch_message(
            TwitchMessage(
                username="testbot",
                text="A message the bot just sent",
                received_at=datetime.now(timezone.utc),
                message_id="bot-message-1",
                user_id="bot-1",
            )
        )
        self.window.response_decision_thread_pool.start.assert_not_called()

    def test_personality_language_permissions_are_saved(self) -> None:
        self.window.settings_store.save = Mock()
        self.window.ai_personality_edit.setPlainText(
            "Dry, playful, and very concise."
        )
        self.window.ai_allow_strong_profanity_check.setChecked(True)

        self.window._save_personality()

        saved = self.window.settings_store.save.call_args.args[0]
        self.assertEqual(saved.ai_personality, "Dry, playful, and very concise.")
        self.assertTrue(saved.ai_allow_mild_profanity)
        self.assertTrue(saved.ai_allow_strong_profanity)
        self.assertEqual(
            self.window.ai_personality_status_label.text(),
            "Saved locally; Streamhouse AI is currently unavailable.",
        )

    def test_signed_in_user_can_update_missing_permissions_without_sign_out(self) -> None:
        self.window.auto_upgrade_permissions = True
        self.window.twitch_auth.token = Mock(
            scopes=["user:read:chat"],
            user_id="42",
        )
        self.window.twitch_service.connect = Mock(return_value=True)
        self.window.refresh_channel_snapshot = Mock()
        self.window.twitch_auth.sign_in = Mock()

        self.window.handle_twitch_auth_changed(
            TwitchAuthState.SIGNED_IN,
            "testbot",
        )
        QTest.qWait(150)

        self.assertTrue(self.window.ui.twitchSignInButton.isEnabled())
        self.assertEqual(
            self.window.ui.twitchSignInButton.text(),
            "Update Permissions",
        )
        self.assertFalse(
            self.window.update_channel_permissions_button.isHidden()
        )
        self.window.twitch_auth.sign_in.assert_called_once_with()

        self.window.handle_twitch_auth_changed(
            TwitchAuthState.SIGNED_IN,
            "testbot",
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
            "Hello Twitch",
            as_bot=False,
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

    def test_connections_are_on_separate_page_from_channel_workspace(self) -> None:
        self.window.show_connections()

        self.assertIs(
            self.window.ui.mainStack.currentWidget(),
            self.window.connections_page,
        )
        self.assertIs(
            self.window.ui.twitchConnectionGroup.parentWidget(),
            self.window.twitch_connections_group,
        )
        self.assertTrue(
            self.window.twitch_connections_group.isAncestorOf(
                self.window.twitch_bot_account_group
            )
        )
        self.assertTrue(
            self.window.twitch_connections_group.isAncestorOf(
                self.window.twitch_health_group
            )
        )
        self.window.show_twitch()
        self.assertIs(
            self.window.twitch_channel_splitter.widget(1),
            self.window.channel_side_splitter,
        )
        self.assertIs(
            self.window.channel_side_splitter.widget(0),
            self.window.chatter_list.parentWidget(),
        )

    def test_twitch_group_shows_account_chat_eventsub_and_scope_health(self) -> None:
        secret = "token-that-must-not-appear"
        self.window.twitch_auth.token = Mock(
            scopes=list(TWITCH_SCOPES),
            user_id="broadcaster-1",
            login="streamer",
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).timestamp(),
            access_token=secret,
        )
        self.window.twitch_bot_auth.token = Mock(
            scopes=list(TWITCH_BOT_SCOPES),
            user_id="bot-1",
            login="helperbot",
            access_token=secret,
        )
        self.window._last_twitch_auth_state = TwitchAuthState.SIGNED_IN
        self.window._last_twitch_bot_auth_state = TwitchAuthState.SIGNED_IN
        self.window.handle_twitch_auth_changed(
            TwitchAuthState.SIGNED_IN,
            "streamer",
        )
        self.window.handle_twitch_bot_auth_changed(
            TwitchAuthState.SIGNED_IN,
            "helperbot",
        )
        self.window.twitch_service.state = TwitchConnectionState.CONNECTED
        self.window.handle_twitch_status_changed(
            TwitchConnectionState.CONNECTED,
            "streamer",
        )

        self.assertEqual(
            self.window.ui.twitchConnectionGroup.title(),
            "Main / Broadcaster Account",
        )
        self.assertEqual(self.window.twitch_bot_account_group.title(), "Bot Account")
        self.assertEqual(self.window.health_auth_label.text(), "Connected")
        self.assertEqual(self.window.health_bot_auth_label.text(), "Connected")
        self.assertEqual(self.window.health_chat_label.text(), "Connected")
        self.assertEqual(self.window.health_eventsub_label.text(), "Connected")
        self.assertEqual(self.window.health_permissions_label.text(), "Ready")
        visible_text = " ".join(
            child.text()
            for child in self.window.twitch_connections_group.findChildren(QLabel)
        )
        self.assertNotIn(secret, visible_text)
        self.assertTrue(self.window.ui.twitchSignOutButton.isEnabled())
        self.assertTrue(self.window.twitch_bot_sign_out_button.isEnabled())

    def test_twitch_health_distinguishes_missing_scopes_and_disconnected_service(self) -> None:
        self.window.twitch_auth.token = Mock(
            scopes=[],
            user_id="broadcaster-1",
            login="streamer",
            expires_at=(datetime.now(timezone.utc) + timedelta(hours=1)).timestamp(),
        )
        self.window._last_twitch_auth_state = TwitchAuthState.SIGNED_IN
        self.window.handle_twitch_auth_changed(
            TwitchAuthState.SIGNED_IN,
            "streamer",
        )

        self.assertEqual(
            self.window.ui.twitchAccountStatusLabel.text(),
            "@streamer — Needs authorization",
        )
        self.assertEqual(self.window.health_auth_label.text(), "Needs Authorization")
        self.assertTrue(
            self.window.health_permissions_label.text().startswith("Missing Scope")
        )
        self.assertIn(
            "Broadcaster:",
            self.window.health_permissions_label.toolTip(),
        )

        self.window.twitch_auth.token.scopes = list(TWITCH_SCOPES)
        self.window.twitch_service.state = TwitchConnectionState.DISCONNECTED
        self.window._refresh_twitch_health()
        self.assertEqual(self.window.health_chat_label.text(), "Disconnected")
        self.assertEqual(self.window.health_eventsub_label.text(), "Disconnected")

    def test_channel_points_and_commands_columns_are_user_resizable(self) -> None:
        for table in (
            self.window.channel_points_page.table,
            self.window.twitch_commands_table,
        ):
            header = table.horizontalHeader()
            for column in range(table.columnCount()):
                self.assertEqual(
                    header.sectionResizeMode(column),
                    QHeaderView.ResizeMode.Interactive,
                )

    def test_obs_connection_is_below_twitch_group_and_saves_automatically(self) -> None:
        layout = self.window.connections_page.layout()
        self.assertLess(
            layout.indexOf(self.window.twitch_connections_group),
            layout.indexOf(self.window.obs_connection_group),
        )
        self.assertFalse(hasattr(self.window, "obs_save_button"))
        self.window.obs_config_store.save = Mock()
        self.window.obs_connection_save_timer.setInterval(0)
        self.window.obs_host_edit.setText("192.168.1.50")
        self.application.processEvents()
        self.window.obs_config_store.save.assert_called_once()
        config, _password = self.window.obs_config_store.save.call_args.args
        self.assertEqual(config.host, "192.168.1.50")

    def test_obs_default_audio_input_saves_and_resolves_muted_variable(self) -> None:
        self.window.obs_config_store.save = Mock()
        self.window.obs_connection_save_timer.setInterval(0)
        self.window.obs_default_mute_input_edit.setText("Mic/Aux")
        self.application.processEvents()
        config, _password = self.window.obs_config_store.save.call_args.args
        self.assertEqual(config.default_mute_input, "Mic/Aux")

        self.window.obs_service.current_mute_state = Mock(
            return_value=("Mic/Aux", True)
        )

        resolved = self.window._resolve_task_variables("Mic is {obs.muted}", {})
        self.window.obs_service.current_mute_state.assert_not_called()
        self.assertEqual(resolved, {})

    def test_channel_snapshot_updates_stream_stats_and_chatters(self) -> None:
        token = Mock(
            user_id="42",
            scopes=[
                "moderator:read:chatters",
                "moderation:read",
                "channel:read:vips",
                "channel:read:subscriptions",
            ],
        )
        self.window.twitch_auth.token = token
        self.window.twitch_service.broadcaster_user_id = "42"
        self.window.twitch_service.helix.get_channel_snapshot = Mock(
            return_value={"stream": None, "followers": 123, "subscribers": 7}
        )
        self.window.twitch_service.helix.get_chatters = Mock(
            return_value=[
                {"user_id": "1", "user_name": "ModOne"},
                {"user_id": "2", "user_name": "VipOne"},
                {"user_id": "3", "user_name": "SubOne"},
                {"user_id": "4", "user_name": "ViewerOne"},
                {"user_id": "5", "user_name": "BotMod"},
                {"user_id": "42", "user_name": "Streamer"},
            ]
        )
        self.window.twitch_service.helix.get_chat_roles = Mock(
            return_value=({"1", "5"}, {"2"}, {"3"})
        )
        self.chatter_history_store.is_bot.side_effect = lambda user_id: user_id == "5"
        self.window.channel_snapshot_thread_pool.start = Mock(
            side_effect=lambda worker: worker.run()
        )

        self.window.refresh_channel_snapshot()
        self.application.processEvents()

        self.assertEqual(self.window.stream_live_label.text(), "OFFLINE")
        self.assertEqual(self.window.stream_followers_label.text(), "123")
        self.assertEqual(self.window.stream_subscribers_label.text(), "7")
        self.assertEqual(self.window.chatter_title_label.text(), "Chatters (5)")
        expected_groups = (
            ("Moderators (1)", "ModOne"),
            ("VIPs (1)", "VipOne"),
            ("Subscribers (1)", "SubOne"),
            ("Bots (1)", "BotMod"),
            ("Regulars (0)", None),
            ("Viewers (1)", "ViewerOne"),
        )
        for index, (group_text, child_text) in enumerate(expected_groups):
            group = self.window.chatter_list.topLevelItem(index)
            self.assertEqual(group.text(0), group_text)
            if child_text is not None:
                self.assertEqual(group.child(0).text(0), child_text)

        viewer = self.window.chatter_list.topLevelItem(5).child(0)
        details = viewer.data(0, Qt.ItemDataRole.UserRole)
        self.assertEqual(details["user_id"], "4")
        self.chatter_history_store.records["4"] = ChatterRecord(
            user_id="4",
            user_name="ViewerOne",
            first_seen="2026-07-12T00:00:00+00:00",
            last_seen="2026-07-12T00:00:00+00:00",
        )
        self.chatter_history_store.set_manual_group.side_effect = (
            lambda user_id, group: setattr(
                self.chatter_history_store.records[user_id],
                "manual_group",
                group,
            )
        )
        self.window._set_local_chatter_group("4", "Regulars")
        self.chatter_history_store.save.assert_called()
        self.assertEqual(
            self.window.chatter_list.topLevelItem(4).child(0).text(0),
            "ViewerOne",
        )
        self.window.handle_twitch_auth_changed(
            TwitchAuthState.SIGNED_IN,
            "testbot",
        )
        self.assertEqual(
            self.window.ui.twitchAccountStatusLabel.text(),
            "@testbot — Needs authorization",
        )
        self.assertTrue(self.window.ui.twitchSignOutButton.isEnabled())

    def test_local_bot_classification_drives_chat_and_counter_filters(self) -> None:
        self.chatter_history_store.records["bot-1"] = ChatterRecord(
            user_id="bot-1",
            user_name="HelperBot",
            first_seen="2026-08-22T00:00:00+00:00",
            last_seen="2026-08-22T00:00:00+00:00",
            manual_group="Bots",
        )
        self.chatter_history_store.is_bot.side_effect = lambda user_id: bool(
            self.chatter_history_store.records.get(user_id)
            and self.chatter_history_store.records[user_id].manual_group == "Bots"
        )
        self.window._handle_twitch_first_message = Mock()
        self.window._handle_twitch_keyword_phrase = Mock()

        self.assertTrue(self.window.counter_service.bot_checker("bot-1"))
        self.window.handle_twitch_message(
            TwitchMessage(
                username="HelperBot",
                text="hello chat",
                received_at=datetime.now(timezone.utc),
                message_id="bot-message",
                user_id="bot-1",
            )
        )

        self.window._handle_twitch_first_message.assert_not_called()
        self.window._handle_twitch_keyword_phrase.assert_not_called()

    def test_live_overview_cards_and_ad_manager_show_schedule(self) -> None:
        now = datetime.now(timezone.utc)
        self.window.twitch_auth.token = Mock(
            scopes=[
                "channel:read:ads",
                "channel:manage:ads",
                "channel:edit:commercial",
            ]
        )
        self.window._apply_channel_snapshot(
            ChannelSnapshotResult(
                request_id=self.window.channel_snapshot_request_id,
                snapshot={
                    "stream": {
                        "id": "stream-1",
                        "viewer_count": 42,
                        "started_at": (now - timedelta(seconds=3661)).isoformat(),
                    },
                    "followers": 445,
                    "subscribers": 12,
                    "ad_schedule": {
                        "next_ad_at": (now + timedelta(minutes=10)).isoformat(),
                        "last_ad_at": (now - timedelta(minutes=20)).isoformat(),
                        "duration": 90,
                        "preroll_free_time": 300,
                        "snooze_count": 2,
                        "snooze_refresh_at": (
                            now + timedelta(minutes=30)
                        ).isoformat(),
                    },
                },
            )
        )

        self.assertEqual(self.window.stream_live_label.text(), "LIVE")
        self.assertEqual(self.window.stream_viewers_label.text(), "42")
        self.assertEqual(self.window.stream_followers_label.text(), "445")
        self.assertEqual(self.window.stream_subscribers_label.text(), "12")
        self.assertTrue(self.window.stream_time_label.text().startswith("01:01:"))
        self.assertIn("#ff4f64", self.window.stream_status_card.styleSheet())
        self.assertIn("Next ads in -", self.window.ad_next_label.text())
        self.assertEqual(self.window.ad_detail_label.text(), "Next break: 90 sec")
        self.assertIn("Snoozes: 2", self.window.ad_snooze_status_label.text())
        self.assertFalse(hasattr(self.window, "ad_schedule_progress"))
        self.assertTrue(self.window.run_ad_button.isEnabled())
        self.assertTrue(self.window.snooze_ad_button.isEnabled())

    def test_ads_live_snapshot_from_real_worker_enables_controls(self) -> None:
        now = datetime.now(timezone.utc)
        self.window.twitch_auth.token = Mock(user_id="42", scopes=[
            "channel:read:ads", "channel:manage:ads", "channel:edit:commercial",
        ])
        self.window.twitch_service.broadcaster_user_id = "42"
        helix = Mock()
        helix.get_channel_snapshot.return_value = {
            "stream": {"id": "test-stream", "started_at": now.isoformat(), "viewer_count": 7},
            "ad_schedule": {
                "next_ad_at": int((now + timedelta(minutes=10)).timestamp()),
                "last_ad_at": 0,
                "snooze_refresh_at": int((now + timedelta(minutes=20)).timestamp()),
                "snooze_count": 2, "duration": 180, "preroll_free_time": 300,
            },
        }
        self.window.twitch_service.helix = helix
        self.window.refresh_channel_snapshot()
        deadline = monotonic() + 3
        while self.window.channel_snapshot_in_flight and monotonic() < deadline:
            QTest.qWait(10)
        self.assertFalse(self.window.channel_snapshot_in_flight)
        self.assertTrue(self.window.stream_is_live)
        self.assertEqual(self.window.stream_viewers_label.text(), "7")
        self.assertIn("Next ads in -", self.window.ad_next_label.text())
        self.assertIn("Next in", self.window.ad_snooze_status_label.text())
        self.assertNotIn("—", self.window.ad_preroll_label.text())
        self.assertIn(self.window.ad_preroll_label.text(), (
            "Preroll-free: 05:00", "Preroll-free: 04:59", "Preroll-free: 04:58",
        ))
        self.assertTrue(self.window.run_ad_button.isEnabled())
        self.assertTrue(self.window.snooze_ad_button.isEnabled())

    def test_stream_events_update_ads_without_waiting_for_old_refresh(self) -> None:
        self.window.twitch_auth.token = Mock(scopes=[
            "channel:read:ads", "channel:manage:ads", "channel:edit:commercial",
        ])
        self.window.refresh_channel_snapshot = Mock()
        for event_type, is_live in (("stream.online", True), ("stream.offline", False)):
            with self.subTest(event_type=event_type):
                old_request = self.window.channel_snapshot_request_id
                self.window.channel_snapshot_in_flight = True
                event = TwitchEvent(
                    subscription_type=event_type, version="1",
                    received_at=datetime.now(timezone.utc), message_id=event_type,
                    broadcaster_user_id="42", broadcaster_user_login="test",
                    broadcaster_user_name="Test", transport=TwitchEventTransport.WEBSOCKET,
                    payload={"event": {"id": "stream", "started_at": datetime.now(timezone.utc).isoformat()}},
                )
                self.window.handle_twitch_activity(event)
                self.assertEqual(self.window.stream_is_live, is_live)
                self.assertEqual(self.window.run_ad_button.isEnabled(), is_live)
                self.assertEqual(self.window.snooze_ad_button.isEnabled(), is_live)
                self.window._apply_channel_snapshot(ChannelSnapshotResult(
                    request_id=old_request,
                    snapshot={"stream": None if is_live else {"id": "old-stream"}},
                ))
                self.assertEqual(self.window.stream_is_live, is_live)
                self.assertEqual(self.window.run_ad_button.isEnabled(), is_live)
                if not is_live:
                    self.assertEqual(self.window.ad_preroll_label.text(), "Preroll-free: —")

    def test_stream_session_duration_uses_hours_and_minutes(self) -> None:
        self.session_store.sessions = [
            StreamSession(
                started_at="2026-07-20T08:00:00+00:00",
                ended_at="2026-07-20T10:07:59+00:00",
            )
        ]

        self.window._refresh_session_history()

        self.assertEqual(self.window.session_table.item(0, 1).text(), "2h:07m")

    def test_running_ad_remembers_duration_and_applies_retry_cooldown(self) -> None:
        self.window.stream_is_live = True
        self.window.twitch_auth.token = Mock(scopes=["channel:edit:commercial"])
        self.window.twitch_service.auth = self.window.twitch_auth
        self.window.twitch_service.broadcaster_user_id = "42"
        self.window.twitch_service.helix.start_commercial = Mock(
            return_value={"message": "Commercial started", "retry_after": 480}
        )
        self.window.settings_store.save = Mock()
        self.window.ad_length_combo.setCurrentIndex(
            self.window.ad_length_combo.findData(90)
        )

        with patch("products.hub.ui.main_window.QTimer.singleShot"):
            self.window.run_commercial()
            self.window.ads_thread_pool.waitForDone(2_000)
            self.application.processEvents()

        self.assertEqual(self.window.settings.twitch_last_ad_duration, 90)
        self.window.settings_store.save.assert_called_once_with(
            self.window.settings
        )
        self.window.twitch_service.helix.start_commercial.assert_called_once_with(
            "42", 90, self.window.twitch_auth.token
        )
        self.assertFalse(self.window.run_ad_button.isEnabled())

    def test_ads_permissions_are_actionable_inside_ad_manager(self) -> None:
        self.window._last_twitch_auth_state = TwitchAuthState.SIGNED_IN
        self.window.twitch_auth.token = Mock(scopes=["channel:read:ads"])

        self.window.handle_twitch_auth_changed(
            TwitchAuthState.SIGNED_IN, "streamer"
        )
        self.window.stream_is_live = True
        self.window._update_ad_control_state()

        self.assertFalse(
            self.window.update_channel_permissions_button.isHidden()
        )
        self.assertEqual(
            self.window.update_channel_permissions_button.text(), "Enable Ads"
        )
        self.assertIn(
            "channel:edit:commercial", self.window.run_ad_button.toolTip()
        )
        self.assertIn(
            "channel:manage:ads", self.window.snooze_ad_button.toolTip()
        )

    def test_broadcaster_reauthorization_reopens_eventsub_connection(self) -> None:
        self.window._last_twitch_auth_state = TwitchAuthState.WAITING
        self.window.twitch_auth.token = Mock(
            scopes=[
                "channel:read:ads",
                "channel:manage:ads",
                "channel:edit:commercial",
            ]
        )
        self.window.twitch_service.state = TwitchConnectionState.CONNECTED
        self.window.twitch_service.channel = "streamer"
        self.window.twitch_service.disconnect = Mock()
        self.window.connect_twitch = Mock()
        self.window.refresh_channel_snapshot = Mock()

        self.window.handle_twitch_auth_changed(
            TwitchAuthState.SIGNED_IN, "streamer"
        )

        self.window.twitch_service.disconnect.assert_called_once_with()
        self.window.connect_twitch.assert_called_once_with()
        self.window.refresh_channel_snapshot.assert_called_once_with()

    def test_ad_schedule_api_failure_has_compact_visible_status(self) -> None:
        self.window.stream_is_live = True
        self.window.twitch_auth.token = Mock(scopes=["channel:read:ads"])

        self.window._apply_channel_snapshot(
            ChannelSnapshotResult(
                request_id=self.window.channel_snapshot_request_id,
                snapshot={"stream": {"id": "stream-1"}, "ad_schedule": None},
                warnings=("ad schedule: HTTP Error 403: Forbidden",),
            )
        )

        self.assertEqual(self.window.ad_next_label.text(), "Ad schedule error")
        self.assertIn("see Logs", self.window.ad_detail_label.text())

    def test_ad_worker_failure_is_visible_from_chat_workspace(self) -> None:
        worker = Mock()

        self.window._ads_action_failed(
            worker,
            "commercial",
            "Twitch could not start the commercial (HTTP 400): channel offline",
        )

        self.assertIn(
            "Could not start commercial", self.window.ad_detail_label.text()
        )
        self.assertIn("channel offline", self.window.ad_detail_label.text())

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

    def test_twitch_chat_context_menu_uses_qcontextmenu_position(self) -> None:
        event = QContextMenuEvent(
            QContextMenuEvent.Reason.Mouse,
            QPoint(4, 4),
            QPoint(4, 4),
        )

        self.window.ui.twitchChatOutput.contextMenuEvent(event)

        self.assertTrue(event.isAccepted())

    def test_twitch_emote_is_rendered_as_cached_cdn_image(self) -> None:
        message = TwitchMessage(
            username="viewer",
            text="Kappa",
            received_at=datetime.now(timezone.utc),
            message_id="message-1",
            user_id="viewer-1",
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
        self.assertIn("data-user-id='viewer-1'", rendered_html)
        self.assertIn("data-message-id='message-1'", rendered_html)

    def test_first_message_stream_trigger_runs_once_per_viewer(self) -> None:
        routine = self.twitch_command_trigger_store.routine_store.add(
            "Welcome viewers"
        )
        self.twitch_event_trigger_store.add(
            routine.routine_id,
            "channel.chat.first_message",
        )
        started = datetime.now(timezone.utc)
        self.twitch_event_trigger_store.observe_stream(
            {"id": "stream-1"},
            started,
        )
        self.window.stream_is_live = True
        execution = Mock(succeeded=True, handled=True)
        self.window.automation_service.publish_trigger = Mock(
            return_value=execution
        )
        self.window.automation_page.record_execution = Mock()
        self.window.settings.ai_response_decisions_enabled = False
        message = TwitchMessage(
            username="Viewer",
            text="hello",
            received_at=started,
            message_id="message-1",
            user_id="viewer-1",
            broadcaster_user_id="streamer-1",
            broadcaster_user_name="Streamer",
        )

        self.window.handle_twitch_message(message)
        self.window.handle_twitch_message(message)

        self.window.automation_service.publish_trigger.assert_called_once()
        trigger = (
            self.window.automation_service.publish_trigger.call_args.args[0]
        )
        self.assertEqual(trigger.trigger_type, "first_message")
        self.assertEqual(trigger.context["user"], "Viewer")

    def test_keyword_phrase_chat_trigger_publishes_accurate_context(self) -> None:
        routine = self.twitch_command_trigger_store.routine_store.add(
            "Coffee response"
        )
        self.twitch_event_trigger_store.add_keyword_phrase(
            routine.routine_id, "coffee"
        )
        execution = Mock(succeeded=True, handled=True)
        self.window.automation_service.publish_trigger = Mock(
            return_value=execution
        )
        self.window.automation_page.record_execution = Mock()
        self.window.settings.ai_response_decisions_enabled = False

        self.window.handle_twitch_message(
            TwitchMessage(
                username="Viewer",
                text="I think coffee is better than tea",
                received_at=datetime.now(timezone.utc),
                message_id="message-1",
                user_id="viewer-1",
                user_login="viewer",
            )
        )

        trigger = self.window.automation_service.publish_trigger.call_args.args[0]
        self.assertEqual(trigger.trigger_type, "keyword_phrase")
        self.assertEqual(trigger.context["keyword.match"], "coffee")
        self.assertEqual(trigger.context["keyword.before"], "I think")
        self.assertEqual(trigger.context["keyword.after"], "is better than tea")
        self.assertNotIn("command_data", trigger.context)

    def test_ad_break_event_updates_ui_and_started_then_calculated_end_triggers(self) -> None:
        routines = self.twitch_command_trigger_store.routine_store
        started_routine = routines.add("Ads started")
        ended_routine = routines.add("Ads ended")
        self.twitch_event_trigger_store.add(started_routine.routine_id, "ads.started")
        self.twitch_event_trigger_store.add(ended_routine.routine_id, "ads.ended")
        execution = Mock(succeeded=True, handled=True)
        self.window.automation_service.publish_trigger = Mock(return_value=execution)
        self.window.automation_page.record_execution = Mock()
        now = datetime.now(timezone.utc)
        self.window.stream_is_live = True
        self.window.twitch_auth.token = Mock(scopes=["channel:read:ads"])
        event = TwitchEvent(
            subscription_type="channel.ad_break.begin",
            version="1",
            received_at=now,
            message_id="ad-1",
            broadcaster_user_id="streamer-1",
            broadcaster_user_login="streamer",
            broadcaster_user_name="Streamer",
            transport=TwitchEventTransport.SIMULATOR,
            payload={
                "event": {
                    "started_at": now.isoformat(),
                    "duration_seconds": 60,
                    "is_automatic": True,
                }
            },
        )

        self.window.handle_twitch_activity(event)
        self.window._update_stream_overview_clock()

        self.assertTrue(self.window.ads_service.state.in_progress)
        self.assertIn("Ads Running -", self.window.ad_next_label.text())
        started_trigger = self.window.automation_service.publish_trigger.call_args_list[0].args[0]
        self.assertEqual(started_trigger.context["ads.is_automatic"], "true")

        ended = self.window.ads_service.tick(now + timedelta(seconds=60))[0]
        with patch("products.hub.ui.main_window.QTimer.singleShot"):
            self.window._publish_ads_event(ended)

        self.assertFalse(self.window.ads_service.state.in_progress)
        self.assertEqual(
            self.window.automation_service.publish_trigger.call_args_list[-1].args[0].trigger_id,
            self.twitch_event_trigger_store.for_routine(ended_routine.routine_id)[0].trigger_id,
        )

    def test_live_chat_starts_local_memory_reasoning_at_threshold(self) -> None:
        self.window.settings.ai_viewer_memory_enabled = True
        self.window.settings.ai_memory_message_threshold = 5
        self.window.settings.ai_response_decisions_enabled = False
        self.chatter_history_store.records["viewer-1"] = ChatterRecord(
            user_id="viewer-1",
            user_name="Viewer",
            first_seen="2026-07-13T00:00:00+00:00",
            last_seen="2026-07-13T00:00:00+00:00",
        )
        self.window.memory_reasoning_thread_pool.start = Mock()
        self.chatter_history_store.has_memory_consent.return_value = True
        self.chatter_history_store.can_create_keynotes.return_value = True

        for index in range(5):
            self.window.handle_twitch_message(
                TwitchMessage(
                    username="Viewer",
                    text=f"I keep working on my puzzle game, update {index}.",
                    received_at=datetime.now(timezone.utc),
                    message_id=f"message-{index}",
                    user_id="viewer-1",
                )
            )

        self.window.memory_reasoning_thread_pool.start.assert_called_once()
        self.assertIn("viewer-1", self.window.memory_extraction_in_flight)
        self.assertEqual(
            len(self.window.memory_message_buffers["viewer-1"]),
            5,
        )

    def test_memory_reasoning_skips_broadcaster_and_bots(self) -> None:
        self.window.settings.ai_viewer_memory_enabled = True
        self.window.twitch_service.broadcaster_user_id = "streamer-1"
        for user_id in ("streamer-1", "bot-1"):
            self.chatter_history_store.records[user_id] = ChatterRecord(
                user_id=user_id,
                user_name=user_id,
                first_seen="2026-07-13T00:00:00+00:00",
                last_seen="2026-07-13T00:00:00+00:00",
                is_bot=user_id == "bot-1",
            )
        self.chatter_history_store.is_bot.side_effect = lambda user_id: bool(
            self.chatter_history_store.records.get(user_id)
            and self.chatter_history_store.records[user_id].is_bot
        )
        for user_id in ("streamer-1", "bot-1"):
            self.window.handle_twitch_message(
                TwitchMessage(
                    username=user_id,
                    text="I like puzzle games.",
                    received_at=datetime.now(timezone.utc),
                    message_id=f"message-{user_id}",
                    user_id=user_id,
                )
            )

        self.assertNotIn("streamer-1", self.window.memory_message_buffers)
        self.assertNotIn("bot-1", self.window.memory_message_buffers)

    def test_memory_reasoning_creates_pending_review_proposal(self) -> None:
        self.window.settings.ai_viewer_memory_enabled = True
        store = ChatterHistoryStore(Path("unused.json"))
        store.observe_message("viewer-1", "Viewer")
        store.opt_in_memory("viewer-1", "Viewer")
        for index in range(store.MEMORY_REGULAR_STREAMS):
            store.record_memory_stream("viewer-1", f"stream-{index}")
        self.window.chatter_history = store
        self.window._save_chatter_history = Mock()
        result = MemoryExtractionResult(
            user_id="viewer-1",
            user_name="Viewer",
            buffer_ids=("one",),
            proposals=(
                ExtractedMemory(
                    text="Viewer is building a puzzle game",
                    category="Project",
                    key="current-project",
                    confidence=0.9,
                    evidence=(
                        {
                            "text": "I am building a puzzle game.",
                            "timestamp": "2026-07-13T00:00:00+00:00",
                            "message_id": "message-1",
                        },
                    ),
                ),
            ),
            generation=self.window.ai_connection_generation,
        )

        self.window._apply_memory_extraction(result)

        memory = store.records["viewer-1"].memories[0]
        self.assertEqual(memory["status"], "pending")
        self.assertEqual(memory["source"], "local-ai:qwen3:14b")
        self.assertEqual(memory["evidence"][0]["message_id"], "message-1")

    def test_live_chat_starts_local_reply_decision(self) -> None:
        store = ChatterHistoryStore(Path("unused.json"))
        store.observe_message("viewer-1", "Viewer")
        self.window.chatter_history = store
        self.window._save_chatter_history = Mock()
        self.window.response_decision_thread_pool.start = Mock()

        self.window.handle_twitch_message(
            TwitchMessage(
                username="Viewer",
                text="Sally, what game should we play next?",
                received_at=datetime.now(timezone.utc),
                message_id="message-reply-1",
                user_id="viewer-1",
            )
        )

        self.window.response_decision_thread_pool.start.assert_called_once()
        self.assertTrue(self.window.response_decision_in_flight)
        self.assertEqual(len(self.window.recent_ai_chat), 1)
        self.assertEqual(self.window.recent_ai_chat.maxlen, 100)

    def test_broadcaster_messages_are_evaluated_for_cohost_reasoning(self) -> None:
        store = ChatterHistoryStore(Path("unused.json"))
        store.observe_message("streamer-1", "Streamer")
        self.window.chatter_history = store
        self.window._save_chatter_history = Mock()
        self.window.twitch_service.broadcaster_user_id = "streamer-1"
        self.window.response_decision_thread_pool.start = Mock()

        for message_id, text in (
            ("ordinary", "That was a fun match."),
            ("trigger", "hey sally, say hello to chat"),
        ):
            self.window.handle_twitch_message(
                TwitchMessage(
                    username="Streamer",
                    text=text,
                    received_at=datetime.now(timezone.utc),
                    message_id=message_id,
                    user_id="streamer-1",
                )
            )

        self.window.response_decision_thread_pool.start.assert_called_once()
        worker = self.window.response_decision_thread_pool.start.call_args.args[0]
        self.assertEqual(worker.messages[0].message_id, "ordinary")
        self.assertEqual(
            [message.message_id for message in self.window.response_decision_queue],
            ["trigger"],
        )

    def test_sally_name_and_twitch_reply_are_direct_address_signals(self) -> None:
        self.window.twitch_bot_auth.token = Mock(
            user_id="bot-1", login="sally_b0t"
        )
        named = TwitchMessage(
            username="Viewer",
            text="what do you think, Sally?",
            received_at=datetime.now(timezone.utc),
            user_id="viewer-1",
        )
        replied = TwitchMessage(
            username="Viewer",
            text="that makes sense",
            received_at=datetime.now(timezone.utc),
            user_id="viewer-1",
            reply=TwitchReply(
                parent_message_id="one",
                parent_message_body="Sally said something",
                parent_user_id="bot-1",
                parent_user_name="Sally",
                parent_user_login="sally_b0t",
                thread_message_id="one",
                thread_user_id="bot-1",
                thread_user_name="Sally",
                thread_user_login="sally_b0t",
            ),
        )

        self.assertEqual(
            self.window._sally_address_signals(named, named.text),
            (True, False),
        )
        self.assertEqual(
            self.window._sally_address_signals(replied, replied.text),
            (True, True),
        )
        third_person = TwitchMessage(
            username="Viewer",
            text="I think Sally has been funny tonight",
            received_at=datetime.now(timezone.utc),
            user_id="viewer-1",
        )
        self.assertEqual(
            self.window._sally_address_signals(third_person, third_person.text),
            (False, False),
        )

        for text in (
            "Say hello sally",
            "Are you sassy Sally?",
            "Ok thanks Sally, bye!",
        ):
            natural = TwitchMessage(
                username="Viewer",
                text=text,
                received_at=datetime.now(timezone.utc),
                user_id="viewer-1",
            )
            self.assertEqual(
                self.window._sally_address_signals(natural, natural.text),
                (True, False),
                text,
            )

    def test_unknown_other_viewer_wording_ends_sally_turn(self) -> None:
        self.window.response_decision_thread_pool.start = Mock()
        self.window.recent_ai_chat.append(
            {
                "speaker": "sally",
                "viewer": "Sally",
                "message": "What about you?",
                "user_id": "streamer-1",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        self.window.handle_twitch_message(
            TwitchMessage(
                username="itsjusty0gurt",
                text="Shes pretty quick eh, jimbob?",
                received_at=datetime.now(timezone.utc),
                message_id="unknown-other-viewer",
                user_id="streamer-1",
            )
        )

        worker = self.window.response_decision_thread_pool.start.call_args.args[0]
        message = worker.messages[0]
        self.assertTrue(message.third_person_reference)
        self.assertTrue(message.addressed_to_other)
        self.assertFalse(message.conversation_continuation)
        self.assertFalse(message.response_expected)

    def test_broadcaster_can_continue_recent_sally_conversation(self) -> None:
        self.window.twitch_service.broadcaster_user_id = "streamer-1"
        self.window.response_decision_thread_pool.start = Mock()
        self.window.recent_ai_chat.append(
            {
                "speaker": "sally",
                "viewer": "Sally",
                "message": "I'm doing great. How about you?",
                "user_id": "streamer-1",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        self.window.handle_twitch_message(
            TwitchMessage(
                username="Streamer",
                text="i am good",
                received_at=datetime.now(timezone.utc),
                message_id="followup",
                user_id="streamer-1",
            )
        )

        self.window.response_decision_thread_pool.start.assert_called_once()
        worker = self.window.response_decision_thread_pool.start.call_args.args[0]
        message = worker.messages[0]
        self.assertTrue(message.conversation_continuation)
        self.assertTrue(message.response_expected)
        self.assertIn("How about you?", message.previous_ai_reply)

    def test_conversation_context_expires_after_configured_window(self) -> None:
        now = datetime.now(timezone.utc)
        self.window.settings.ai_conversation_followup_seconds = 60
        self.window.recent_ai_chat.append(
            {
                "speaker": "sally",
                "viewer": "Sally",
                "message": "How about you?",
                "user_id": "viewer-1",
                "timestamp": (now - timedelta(seconds=61)).isoformat(),
            }
        )

        context = self.window._conversation_context(
            "viewer-1", "i am good", now
        )

        self.assertEqual(context, (False, "", False))

    def test_model_ending_closes_recent_conversation(self) -> None:
        now = datetime.now(timezone.utc)
        self.window.recent_ai_chat.append(
            {
                "speaker": "sally",
                "viewer": "Sally",
                "message": "Anything else?",
                "user_id": "viewer-1",
                "timestamp": now.isoformat(),
            }
        )
        decision = ResponseDecision(
            request_id="end",
            message_id="end-message",
            user_id="viewer-1",
            user_name="Viewer",
            source_text="no thanks, bye",
            received_at=now.isoformat(),
            decision="ignore",
            reply="",
            reason="Conversation ended.",
            confidence=0.95,
            conversation_state="end",
        )

        self.window._update_conversation_state(decision)

        self.assertEqual(
            self.window._conversation_context(
                "viewer-1", "talking to someone else", datetime.now(timezone.utc)
            ),
            (False, "", False),
        )

    def test_third_person_message_to_another_viewer_ends_sally_turn(self) -> None:
        store = ChatterHistoryStore(Path("unused.json"))
        store.observe_message("streamer-1", "itsjusty0gurt")
        store.observe_message("viewer-2", "Tarumes")
        self.window.chatter_history = store
        self.window._save_chatter_history = Mock()
        self.window.response_decision_thread_pool.start = Mock()
        self.window.recent_ai_chat.append(
            {
                "speaker": "sally",
                "viewer": "Sally",
                "message": "What about you?",
                "user_id": "streamer-1",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

        self.window.handle_twitch_message(
            TwitchMessage(
                username="itsjusty0gurt",
                text="she's pretty quick still eh tarumes?",
                received_at=datetime.now(timezone.utc),
                message_id="third-person",
                user_id="streamer-1",
            )
        )

        worker = self.window.response_decision_thread_pool.start.call_args.args[0]
        message = worker.messages[0]
        self.assertTrue(message.third_person_reference)
        self.assertTrue(message.addressed_to_other)
        self.assertFalse(message.conversation_continuation)
        self.assertFalse(message.response_expected)

    def test_reasoning_approved_reply_is_sent_and_added_to_review(self) -> None:
        self.window.twitch_service.state = TwitchConnectionState.CONNECTED
        self.window.twitch_service.send_message = Mock(return_value=True)
        decision = ResponseDecision(
            request_id="request-1",
            message_id="message-1",
            user_id="viewer-1",
            user_name="Viewer",
            source_text="Sally, hello!",
            received_at=datetime.now(timezone.utc).isoformat(),
            decision="reply",
            reply="Hey Viewer!",
            reason="Sally was directly addressed.",
            confidence=0.9,
            solicited=True,
        )

        self.window._apply_response_batch(
            ResponseBatchResult((decision,), self.window.ai_connection_generation)
        )

        self.window.twitch_service.send_message.assert_called_once_with(
            "Hey Viewer!"
        )
        self.assertEqual(self.window.reply_review_table.rowCount(), 1)
        self.assertEqual(
            self.window.reply_review_table.item(0, 4).text(),
            "Hey Viewer!",
        )
        diagnostic = self.test_report_store.record.call_args.kwargs
        self.assertEqual(diagnostic["outcome"], "sent")
        self.assertEqual(diagnostic["reason"], "sent")
        self.assertTrue(diagnostic["response_expected"])
        self.assertNotIn("source_text", diagnostic)
        self.assertNotIn("user_id", diagnostic)

    def test_required_model_ignore_is_reported_as_missed_anonymously(self) -> None:
        decision = ResponseDecision(
            request_id="request-missed",
            message_id="message-missed",
            user_id="viewer-secret",
            user_name="SecretViewer",
            source_text="Hey Sally, are you there?",
            received_at=datetime.now(timezone.utc).isoformat(),
            decision="ignore",
            reply="",
            reason="Model omitted this required response.",
            confidence=0.1,
            solicited=True,
        )

        self.window._add_reply_decision(decision)

        diagnostic = self.test_report_store.record.call_args.kwargs
        self.assertEqual(diagnostic["outcome"], "missed")
        self.assertEqual(diagnostic["reason"], "model_omitted_message")
        self.assertNotIn("source_text", diagnostic)
        self.assertNotIn("user_name", diagnostic)

    def test_approved_reply_sends_automatically_and_rejects_stale_reply(self) -> None:
        self.window.twitch_service.state = TwitchConnectionState.CONNECTED
        self.window.twitch_service.send_message = Mock(return_value=True)
        fresh = ResponseDecision(
            request_id="request-1",
            message_id="message-1",
            user_id="viewer-1",
            user_name="Viewer",
            source_text="Sally?",
            received_at=datetime.now(timezone.utc).isoformat(),
            decision="reply",
            reply="I'm here!",
            reason="Directly addressed.",
            confidence=0.9,
            solicited=True,
        )

        self.assertTrue(self.window._maybe_auto_send_reply(fresh))
        self.assertEqual(self.window.recent_ai_chat[-1]["speaker"], "sally")
        self.assertEqual(self.window.recent_ai_chat[-1]["viewer"], "Sally")
        self.assertEqual(self.window.recent_ai_chat[-1]["message"], "I'm here!")
        self.assertEqual(self.window.recent_ai_chat[-1]["user_id"], "viewer-1")
        self.assertIn("timestamp", self.window.recent_ai_chat[-1])

        stale = ResponseDecision(
            request_id="request-2",
            message_id="message-2",
            user_id="viewer-1",
            user_name="Viewer",
            source_text="Old question",
            received_at="2026-01-01T00:00:00+00:00",
            decision="reply",
            reply="Late answer",
            reason="Question.",
            confidence=0.9,
        )
        self.window.last_auto_reply_at = 0.0
        self.assertFalse(self.window._maybe_auto_send_reply(stale))
        self.assertEqual(
            self.window.twitch_service.send_message.call_args_list,
            [unittest.mock.call("I'm here!")],
        )

    def test_hey_sally_bypasses_confidence_and_normal_reply_gap(self) -> None:
        self.window.twitch_service.state = TwitchConnectionState.CONNECTED
        self.window.twitch_service.send_message = Mock(return_value=True)
        self.window.last_auto_reply_at = 100.0
        decision = ResponseDecision(
            request_id="request-direct",
            message_id="message-direct",
            user_id="viewer-1",
            user_name="Viewer",
            source_text="hey sally, are you there?",
            received_at=(
                datetime.now(timezone.utc) - timedelta(seconds=30)
            ).isoformat(),
            decision="reply",
            reply="I'm here!",
            reason="Direct invocation.",
            confidence=0.1,
        )

        with patch("products.hub.ui.main_window.monotonic", return_value=100.0):
            self.assertTrue(self.window._maybe_auto_send_reply(decision))

    def test_expected_followup_bypasses_confidence_and_normal_reply_gap(self) -> None:
        self.window.twitch_service.state = TwitchConnectionState.CONNECTED
        self.window.twitch_service.send_message = Mock(return_value=True)
        self.window.last_auto_reply_at = 100.0
        decision = ResponseDecision(
            request_id="request-followup",
            message_id="message-followup",
            user_id="viewer-1",
            user_name="Viewer",
            source_text="i am good",
            received_at=(
                datetime.now(timezone.utc) - timedelta(seconds=30)
            ).isoformat(),
            decision="reply",
            reply="Glad to hear it!",
            reason="Expected conversation follow-up.",
            confidence=0.1,
            response_expected=True,
        )

        with patch("products.hub.ui.main_window.monotonic", return_value=100.0):
            self.assertTrue(self.window._maybe_auto_send_reply(decision))

    def test_interjection_requires_high_confidence_and_separate_cooldown(self) -> None:
        self.window.settings.ai_interjections_enabled = True
        self.window.settings.ai_interjection_min_interval_seconds = 180
        self.window.settings.ai_interjection_min_messages = 6
        self.window.viewer_messages_since_ai_reply = 6
        self.window.twitch_service.state = TwitchConnectionState.CONNECTED
        self.window.twitch_service.send_message = Mock(return_value=True)
        decision = ResponseDecision(
            request_id="interjection",
            message_id="message-interjection",
            user_id="viewer-1",
            user_name="Viewer",
            source_text="This boss has way too much health.",
            received_at=datetime.now(timezone.utc).isoformat(),
            decision="reply",
            reply="That health bar has its own health bar.",
            reason="Relevant co-host joke.",
            confidence=0.9,
            engagement_type="interjection",
        )

        with patch("products.hub.ui.main_window.monotonic", return_value=200.0):
            self.assertTrue(self.window._maybe_auto_send_reply(decision))
        with patch("products.hub.ui.main_window.monotonic", return_value=201.0):
            self.assertFalse(self.window._maybe_auto_send_reply(decision))

    def test_model_direct_label_cannot_bypass_unsolicited_guards(self) -> None:
        self.window.settings.ai_interjections_enabled = False
        self.window.twitch_service.state = TwitchConnectionState.CONNECTED
        self.window.twitch_service.send_message = Mock(return_value=True)
        decision = ResponseDecision(
            request_id="model-guessed-direct",
            message_id="message-guessed-direct",
            user_id="viewer-1",
            user_name="Viewer",
            source_text="she still talks too much though",
            received_at=datetime.now(timezone.utc).isoformat(),
            decision="reply",
            reply="I do not talk too much!",
            reason="Model inferred direct address.",
            confidence=0.99,
            engagement_type="direct",
            solicited=False,
        )

        self.assertFalse(self.window._maybe_auto_send_reply(decision))
        self.window.twitch_service.send_message.assert_not_called()

    def test_failed_model_still_falls_back_for_hey_sally(self) -> None:
        self.window.twitch_service.state = TwitchConnectionState.CONNECTED
        self.window.twitch_service.send_message = Mock(return_value=True)
        message = ResponseMessage(
            request_id="request-failed",
            message_id="message-failed",
            user_id="viewer-1",
            user_name="Viewer",
            text="hey sally, can you hear me?",
            received_at=datetime.now(timezone.utc).isoformat(),
        )

        self.window._response_batch_failed(
            ((message,), self.window.ai_connection_generation),
            ValueError("model offline"),
        )

        self.window.twitch_service.send_message.assert_called_once()
        self.assertEqual(
            self.window.reply_review_table.item(0, 3).text(), "SENT"
        )

    def test_sent_invocation_discards_queued_duplicate_retries(self) -> None:
        duplicate = ResponseMessage(
            request_id="queued-duplicate",
            message_id="queued-message",
            user_id="viewer-1",
            user_name="Viewer",
            text="hey sally, not much and you?",
            received_at=datetime.now(timezone.utc).isoformat(),
        )
        different = ResponseMessage(
            request_id="queued-different",
            message_id="other-message",
            user_id="viewer-1",
            user_name="Viewer",
            text="hey sally, what game is next?",
            received_at=datetime.now(timezone.utc).isoformat(),
        )
        self.window.response_decision_queue.extend((duplicate, different))
        sent = ResponseDecision(
            request_id="sent",
            message_id="sent-message",
            user_id="viewer-1",
            user_name="Viewer",
            source_text="hey sally, not much and you?",
            received_at=datetime.now(timezone.utc).isoformat(),
            decision="reply",
            reply="Doing great!",
            reason="Direct invocation.",
            confidence=0.9,
        )

        self.window._drop_duplicate_queued_invocations(sent)

        self.assertEqual(
            [item.request_id for item in self.window.response_decision_queue],
            ["queued-different"],
        )

    def test_sent_natural_address_discards_queued_duplicate_retries(self) -> None:
        self.window.response_decision_queue.append(
            ResponseMessage(
                request_id="queued-natural",
                message_id="queued-message",
                user_id="viewer-1",
                user_name="Viewer",
                text="say hello sally",
                received_at=datetime.now(timezone.utc).isoformat(),
                directed_at_ai=True,
            )
        )
        sent = ResponseDecision(
            request_id="sent-natural",
            message_id="sent-message",
            user_id="viewer-1",
            user_name="Viewer",
            source_text="say hello sally",
            received_at=datetime.now(timezone.utc).isoformat(),
            decision="reply",
            reply="Hello!",
            reason="Natural direct address.",
            confidence=0.9,
            solicited=True,
        )

        self.window._drop_duplicate_queued_invocations(sent)

        self.assertEqual(list(self.window.response_decision_queue), [])

    def test_sally_memory_commands_opt_in_report_and_delete(self) -> None:
        self.window.settings.ai_viewer_memory_enabled = True
        store = ChatterHistoryStore(Path("unused.json"))
        self.window.chatter_history = store
        self.window._save_chatter_history = Mock()
        self.window.twitch_service.send_message = Mock(return_value=True)
        self.window.current_memory_stream_id = "stream-1"

        def command(text: str) -> None:
            self.window.handle_twitch_message(
                TwitchMessage(
                    username="Viewer",
                    text=text,
                    received_at=datetime.now(timezone.utc),
                    message_id=text,
                    user_id="viewer-1",
                )
            )

        command("!sallymemory on")
        record = store.records["viewer-1"]
        self.assertEqual(record.memory_consent, "opted_in")
        self.assertEqual(record.memory_stream_ids, ["stream-1"])

        command("!sallymemory status")
        self.assertIn("1/5", self.window.twitch_service.send_message.call_args.args[0])

        command("!sallymemory delete")
        self.assertIn("viewer-1", self.window.pending_memory_deletions)
        command("!sallymemory confirmdelete")
        self.assertNotIn("viewer-1", store.records)
        self.assertIn(
            "all of your locally stored Sally data was deleted",
            self.window.twitch_service.send_message.call_args.args[0],
        )

    def test_master_memory_switch_blocks_collection_but_not_cohost_chat(self) -> None:
        store = ChatterHistoryStore(Path("unused.json"))
        self.window.chatter_history = store
        self.window._save_chatter_history = Mock()
        self.window.twitch_service.send_message = Mock(return_value=True)
        self.window.response_decision_thread_pool.start = Mock()
        self.window.settings.ai_viewer_memory_enabled = False

        self.window.handle_twitch_message(
            TwitchMessage(
                username="Viewer",
                text="!sallymemory on",
                received_at=datetime.now(timezone.utc),
                message_id="memory-command",
                user_id="viewer-1",
            )
        )
        self.assertFalse(store.has_memory_consent("viewer-1"))
        self.assertIn(
            "disabled by the streamer",
            self.window.twitch_service.send_message.call_args.args[0],
        )

        self.window.handle_twitch_message(
            TwitchMessage(
                username="Viewer",
                text="Sally, are you ready for tomorrow?",
                received_at=datetime.now(timezone.utc),
                message_id="cohost-message",
                user_id="viewer-1",
            )
        )

        self.window.response_decision_thread_pool.start.assert_called_once()
        self.assertNotIn("viewer-1", self.window.memory_message_buffers)
        self.assertEqual(store.records["viewer-1"].daily_memory, [])

    def test_training_capture_requires_runtime_viewer_opt_in(self) -> None:
        self.window.settings.ai_training_capture_enabled = True
        self.window.twitch_service.send_message = Mock(return_value=True)
        self.window.training_store.capture = Mock(return_value="example-1")

        self.window.handle_twitch_message(
            TwitchMessage(
                username="Viewer",
                text="!sallytrain on",
                received_at=datetime.now(timezone.utc),
                message_id="training-on",
                user_id="viewer-1",
            )
        )
        self.assertIn("viewer-1", self.window.training_opted_in_users)
        decision = ResponseDecision(
            request_id="training",
            message_id="message-1",
            user_id="viewer-1",
            user_name="Viewer",
            source_text="What do you think, Sally?",
            received_at=datetime.now(timezone.utc).isoformat(),
            decision="reply",
            reply="I think it is a good idea.",
            reason="Direct question.",
            confidence=0.9,
            engagement_type="direct",
            conversation_state="start",
        )

        self.window._capture_training_decision(decision)

        self.window.training_store.capture.assert_called_once_with(
            "viewer-1", decision
        )

    def test_training_delete_command_removes_participant_samples(self) -> None:
        self.window.settings.ai_training_capture_enabled = True
        self.window.training_opted_in_users.add("viewer-1")
        self.window.training_store.delete_participant.return_value = 3
        self.window.twitch_service.send_message = Mock(return_value=True)

        self.window.handle_twitch_message(
            TwitchMessage(
                username="Viewer",
                text="!sallytrain delete",
                received_at=datetime.now(timezone.utc),
                message_id="training-delete",
                user_id="viewer-1",
            )
        )

        self.window.training_store.delete_participant.assert_called_once_with(
            "viewer-1"
        )
        self.assertNotIn("viewer-1", self.window.training_opted_in_users)
        self.assertIn(
            "3 saved training sample(s) were deleted",
            self.window.twitch_service.send_message.call_args.args[0],
        )

    def test_training_disclosure_is_sent_only_once_per_stream(self) -> None:
        self.window.settings.ai_training_capture_enabled = True
        self.window.settings.ai_training_notice_enabled = True
        self.window.twitch_service.state = TwitchConnectionState.CONNECTED
        self.window.twitch_service.send_pinned_message = Mock(
            return_value=(True, True)
        )
        self.window.settings_store.save = Mock()
        self.window.current_memory_stream_id = "stream-1"

        self.window._maybe_announce_training_capture()
        self.window._maybe_announce_training_capture()

        self.window.twitch_service.send_pinned_message.assert_called_once()
        self.assertIn(
            "Participation is optional",
            self.window.twitch_service.send_pinned_message.call_args.args[0],
        )
        self.assertEqual(
            self.window.settings.ai_training_notice_stream_id,
            "stream-1",
        )
        self.window.settings_store.save.assert_called_once_with(
            self.window.settings
        )

    def test_training_disclosure_is_not_triggered_by_first_chat_message(self) -> None:
        self.window.settings.ai_training_capture_enabled = True
        self.window.settings.ai_training_notice_enabled = True
        self.window.twitch_service.state = TwitchConnectionState.CONNECTED
        self.window.twitch_service.send_pinned_message = Mock(
            return_value=(True, True)
        )
        self.window.current_memory_stream_id = "stream-1"
        self.window.response_decision_thread_pool.start = Mock()

        self.window.handle_twitch_message(
            TwitchMessage(
                username="Viewer",
                text="hello chat",
                received_at=datetime.now(timezone.utc),
                message_id="ordinary-chat",
                user_id="viewer-1",
            )
        )

        self.window.twitch_service.send_pinned_message.assert_not_called()

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


    def test_disconnected_chat_never_launches_ai_and_only_direct_gets_fallback(self) -> None:
        self.window.ai_lifecycle.disconnect()
        self.window.response_decision_thread_pool.start = Mock()
        self.window.twitch_service.state = TwitchConnectionState.CONNECTED
        self.window.twitch_service.send_message = Mock(return_value=True)

        self.window.handle_twitch_message(
            TwitchMessage(
                username="Viewer",
                text="ordinary chat",
                received_at=datetime.now(timezone.utc),
                message_id="ordinary",
                user_id="viewer-1",
            )
        )
        self.window.handle_twitch_message(
            TwitchMessage(
                username="Viewer",
                text="hey sally, are you there?",
                received_at=datetime.now(timezone.utc),
                message_id="direct",
                user_id="viewer-1",
            )
        )

        self.window.response_decision_thread_pool.start.assert_not_called()
        self.window.twitch_service.send_message.assert_called_once()
        self.test_report_store.record.assert_not_called()

    def test_repeated_signed_in_events_do_not_restart_twitch(self) -> None:
        self.window.twitch_auth.token = Mock(scopes=[], user_id="channel-1")
        self.window.twitch_service.state = TwitchConnectionState.CONNECTED
        self.window.twitch_service.channel = "channel"
        self.window.twitch_service.connect = Mock()
        self.window.twitch_service.disconnect = Mock()
        self.window._last_twitch_auth_state = TwitchAuthState.SIGNED_IN
        self.window._last_twitch_bot_auth_state = TwitchAuthState.SIGNED_IN

        self.window.handle_twitch_auth_changed(TwitchAuthState.SIGNED_IN, "channel")
        self.window.handle_twitch_bot_auth_changed(
            TwitchAuthState.SIGNED_IN, "testbot"
        )

        self.window.twitch_service.disconnect.assert_not_called()
        self.window.twitch_service.connect.assert_not_called()

    def test_initial_signed_in_transition_connects_when_disconnected(self) -> None:
        self.window.twitch_auth.token = Mock(scopes=[], user_id="channel-1")
        self.window.twitch_service.state = TwitchConnectionState.DISCONNECTED
        self.window.twitch_service.connect = Mock(return_value=True)
        self.window._last_twitch_auth_state = TwitchAuthState.SIGNED_OUT

        self.window.handle_twitch_auth_changed(TwitchAuthState.SIGNED_IN, "channel")

        self.window.twitch_service.connect.assert_called_once()

    def test_transport_failure_disconnects_clears_queue_and_blocks_stale_result(self) -> None:
        generation = self.window.ai_connection_generation
        message = ResponseMessage(
            request_id="request",
            message_id="message",
            user_id="viewer-1",
            user_name="Viewer",
            text="ordinary chat",
            received_at=datetime.now(timezone.utc).isoformat(),
        )
        self.window.response_decision_queue.append(message)
        self.window._response_batch_failed(
            ((message,), generation), ConnectionRefusedError("refused")
        )

        self.assertIs(
            self.window.ai_connection_state, AIConnectionState.DISCONNECTED
        )
        self.assertEqual(list(self.window.response_decision_queue), [])
        self.window.twitch_service.send_message = Mock(return_value=True)
        stale = ResponseDecision(
            request_id="request",
            message_id="message",
            user_id="viewer-1",
            user_name="Viewer",
            source_text="hey sally",
            received_at=datetime.now(timezone.utc).isoformat(),
            decision="reply",
            reply="stale reply",
            reason="direct",
            confidence=1.0,
        )
        self.window._apply_response_batch(ResponseBatchResult((stale,), generation))
        self.window.twitch_service.send_message.assert_not_called()

    def test_ai_reopens_only_after_new_presence_and_successful_health(self) -> None:
        self.window.ai_lifecycle.disconnect()
        self.window.response_decision_thread_pool.start = Mock()
        self.window.response_decision_queue.append(
            ResponseMessage(
                request_id="request",
                message_id="message",
                user_id="viewer-1",
                user_name="Viewer",
                text="hello",
                received_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        self.window._start_next_response_batch()
        self.window.response_decision_thread_pool.start.assert_not_called()

        with patch.object(self.window, "_check_streamhouse_ai"):
            self.window._handle_streamhouse_ai_presence(PROTOCOL_VERSION, 9123)
        generation = self.window.ai_connection_generation
        self.window._apply_streamhouse_ai_health(
            StreamhouseAIHealthResult(
                StreamhouseAIStatus(True, PROTOCOL_VERSION), {}, generation
            )
        )
        self.window.response_decision_queue.append(
            ResponseMessage(
                request_id="request-2",
                message_id="message-2",
                user_id="viewer-1",
                user_name="Viewer",
                text="hello again",
                received_at=datetime.now(timezone.utc).isoformat(),
            )
        )
        self.window._start_next_response_batch()
        self.window.response_decision_thread_pool.start.assert_called_once()


if __name__ == "__main__":
    unittest.main()
