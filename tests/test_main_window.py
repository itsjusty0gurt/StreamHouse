import logging
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication, QLabel, QMenu
from PySide6.QtTest import QSignalSpy, QTest

from core.logger import Logger
from core.settings import AppSettings
from ai.memory_extractor import ExtractedMemory
from ai.response_engine import ResponseDecision, ResponseMessage
from ai.training_store import TrainingStore
from twitch.auth import TwitchAuthState
from twitch.chatter_history import ChatterHistoryStore, ChatterRecord
from automation.routines import RoutineStore
from automation.models import (
    AutomationExecutionResult,
    RoutineExecutionResult,
    TaskExecutionResult,
    TriggerEvent,
)
from obs_service.triggers import OBS_TRIGGER_TYPES
from obs_service.models import ObsEvent
from twitch.commands import TwitchCommandTriggerStore
from twitch.automation_triggers import TwitchEventTriggerStore
from twitch.service import TwitchConnectionState
from twitch.session_history import StreamSession
from twitch.models import (
    TwitchChatNotice,
    TwitchEmote,
    TwitchEvent,
    TwitchEventTransport,
    TwitchFragmentType,
    TwitchMessage,
    TwitchMessageFragment,
    TwitchReply,
)
from ui.main_window import MainWindow
from ui.companion_worker import CompanionRefreshResult
from ui.memory_worker import MemoryExtractionResult
from ui.response_worker import ResponseBatchResult
from ui.twitch_command_dialog import TwitchCommandDialog, TwitchCommandManagerDialog


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
            twitch_event_trigger_store=self.twitch_event_trigger_store,
            auto_upgrade_permissions=False,
        )

    def tearDown(self) -> None:
        self.window.close()
        self.application.processEvents()
        self.twitch_command_directory.cleanup()
        self.settings_patch.stop()
        Logger._logger = self.original_logger

    def test_navigation_selects_one_button_and_correct_page(self) -> None:
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
            config={"message": "Hello {user}", "as_bot": True},
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

    def test_automation_queues_tab_assigns_and_displays_pending_routines(self) -> None:
        page = self.window.automation_page
        queue = self.window.automation_queue_store.add("Soundboard")
        self.window.automation_queue_store.update(queue.queue_id, paused=True)
        store = self.twitch_command_trigger_store.routine_store
        routine = store.add("Play sound", trigger_id="sound", queue_id=queue.queue_id)
        store.add_task(
            routine.routine_id,
            task_type="core.delay",
            name="Tiny delay",
            config={"seconds": 0},
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

    def test_task_add_menu_is_grouped_by_service(self) -> None:
        menu = QMenu()
        add_menu = self.window.automation_page._add_task_submenu(menu)
        self.assertEqual([action.text() for action in add_menu.actions()], ["Core", "OBS", "Twitch"])
        core_menu = add_menu.actions()[0].menu()
        obs_menu = add_menu.actions()[1].menu()
        twitch_menu = add_menu.actions()[2].menu()
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
        twitch_menu = add_menu.actions()[2].menu()
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
            [
                "Chat Command…",
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

        twitch_menu.actions()[1].trigger()

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
        context = self.window.automation_page._sample_context_for_routine(routine)

        self.assertEqual(context["scene"], "Gameplay")
        self.assertEqual(context["source"], "Camera")
        self.assertNotIn("reward_id", context)

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
        manager.command_list.setCurrentRow(0)
        self.assertTrue(manager.edit_button.isEnabled())
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
            task_type="core.delay",
            name="First",
            config={"seconds": 1},
        )
        second = store.add_task(
            routine.routine_id,
            task_type="core.delay",
            name="Second",
            config={"seconds": 2},
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
            task_type="core.delay",
            name="Wait briefly",
            config={"seconds": 2.5},
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
            task_type="core.delay",
            name="Short wait",
            config={"seconds": 0},
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
                    ),
                ),
            ),
            "Manual test",
        )

        self.assertIn("Short wait", page.history_details.toPlainText())
        self.assertIn("12 ms", page.history_details.toPlainText())
        self.assertIn("Waited.", page.history_details.toPlainText())

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
            config={"message": "Sally started", "as_bot": True},
        )
        closing = store.add("On closing")
        store.add_task(
            closing.routine_id,
            task_type="twitch.send_chat_message",
            name="Closing",
            config={"message": "Sally closing", "as_bot": True},
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
                unittest.mock.call("Sally started", as_bot=True),
                unittest.mock.call("Sally closing", as_bot=True),
            ],
        )
        self.assertEqual(len(self.window.automation_page.history), 2)

    def test_twitch_command_can_open_its_connected_automation_routine(self) -> None:
        command = self.twitch_command_trigger_store.add(
            "socials", "Links for {user}"
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
                "Commands",
                "Channel Points",
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
        self.assertEqual(self.window.channel_tabs.count(), 5)
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

    def test_custom_twitch_command_sends_as_bot_and_skips_ai_reasoning(self) -> None:
        command = self.twitch_command_trigger_store.add(
            "hello",
            "Hello {user}! Welcome to {channel}.",
            aliases=["hi"],
            global_cooldown_seconds=0,
            user_cooldown_seconds=0,
        )
        self.window.twitch_service.channel = "sallychannel"
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
            "Hello Viewer! Welcome to sallychannel.",
            as_bot=True,
        )
        self.assertEqual(command.uses, 1)
        self.window._queue_response_decision.assert_not_called()

    def test_twitch_command_task_resolves_live_obs_and_twitch_variables(self) -> None:
        self.twitch_command_trigger_store.add(
            "status",
            "Mic is {muted}; playing {game}.",
            global_cooldown_seconds=0,
            user_cooldown_seconds=0,
        )
        self.window.last_companion_result = CompanionRefreshResult(
            request_id=1,
            snapshot={
                "stream": None,
                "channel": {"game_name": "Science & Technology"},
            },
        )
        self.window.obs_service.current_mute_state = Mock(
            return_value=("Mic/Aux", False)
        )
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

        self.window.obs_service.current_mute_state.assert_called_once_with("")
        self.window.twitch_service.send_message.assert_called_once_with(
            "Mic is Not Muted; playing Science & Technology.",
            as_bot=True,
        )

    def test_twitch_command_actions_require_a_selected_command(self) -> None:
        self.window._refresh_twitch_commands()

        self.assertTrue(self.window.add_twitch_command_button.isEnabled())
        self.assertFalse(self.window.edit_twitch_command_button.isEnabled())
        self.assertFalse(self.window.toggle_twitch_command_button.isEnabled())
        self.assertFalse(self.window.delete_twitch_command_button.isEnabled())

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
            config={"message": "Thanks for following, {user}!", "as_bot": True},
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
                "message": "Mic is {mute} while playing {game}",
                "as_bot": True,
            },
        )
        self.window.obs_trigger_store.add(
            routine.routine_id,
            "InputMuteStateChanged",
        )
        self.window.last_companion_result = CompanionRefreshResult(
            request_id=1,
            snapshot={
                "stream": None,
                "channel": {
                    "game_name": "Science & Technology",
                    "title": "Building Sally",
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
            "Mic is Muted while playing Science & Technology",
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
        self.window.refresh_stream_companion = Mock()
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
        self.assertEqual(self.window.refresh_stream_companion.call_count, 2)

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
            login="sallybot",
        )

        self.window.handle_twitch_bot_auth_changed(
            TwitchAuthState.SIGNED_IN,
            "sallybot",
        )

        self.assertEqual(
            self.window.twitch_bot_account_status_label.text(),
            "@sallybot",
        )
        self.assertFalse(self.window.twitch_bot_sign_in_button.isEnabled())
        self.assertTrue(self.window.twitch_bot_sign_out_button.isEnabled())

        self.window.response_decision_thread_pool.start = Mock()
        self.window.handle_twitch_message(
            TwitchMessage(
                username="sallybot",
                text="A message Sally just sent",
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
            "Saved locally; AI Companion is currently unavailable.",
        )

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

    def test_obs_connection_is_below_bot_and_saves_automatically(self) -> None:
        layout = self.window.connections_page.layout()
        self.assertLess(
            layout.indexOf(self.window.twitch_bot_account_group),
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

        resolved = self.window._resolve_task_variables(
            "Mic is {muted}",
            {"muted": "--", "input": "--"},
        )

        self.window.obs_service.current_mute_state.assert_called_once_with("Mic/Aux")
        self.assertEqual(resolved["muted"], "Muted")

    def test_companion_refresh_updates_stream_stats_and_chatters(self) -> None:
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
        self.window.twitch_service.helix.get_companion_snapshot = Mock(
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
        self.window.known_bot_user_ids.add("5")
        self.window.companion_thread_pool.start = Mock(
            side_effect=lambda worker: worker.run()
        )

        self.window.refresh_stream_companion()
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
        self.assertEqual(
            self.window.chatter_list.topLevelItem(4).child(0).text(0),
            "ViewerOne",
        )
        self.window.handle_twitch_auth_changed(
            TwitchAuthState.SIGNED_IN,
            "sallybot",
        )
        self.assertEqual(self.window.ui.twitchAccountStatusLabel.text(), "sallybot")
        self.assertTrue(self.window.ui.twitchSignOutButton.isEnabled())

    def test_live_overview_cards_and_ad_manager_show_schedule(self) -> None:
        now = datetime.now(timezone.utc)
        self.window.twitch_auth.token = Mock(
            scopes=[
                "channel:read:ads",
                "channel:manage:ads",
                "channel:edit:commercial",
            ]
        )
        self.window._apply_companion_refresh(
            CompanionRefreshResult(
                request_id=self.window.companion_refresh_request_id,
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
        self.assertIn("Next 90s ad in", self.window.ad_next_label.text())
        self.assertIn("Pre-roll free for", self.window.ad_preroll_label.text())
        self.assertIn("#66ffd1", self.window.ad_preroll_label.styleSheet())
        self.assertIn("Snoozes 2", self.window.ad_snooze_status_label.text())
        self.assertGreater(self.window.ad_schedule_progress.value(), 0)
        self.assertTrue(self.window.run_ad_button.isEnabled())
        self.assertTrue(self.window.snooze_ad_button.isEnabled())

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
        self.window.twitch_service.broadcaster_user_id = "42"
        self.window.twitch_service.helix.start_commercial = Mock(
            return_value={"message": "Commercial started", "retry_after": 480}
        )
        self.window.settings_store.save = Mock()
        self.window.ad_length_combo.setCurrentIndex(
            self.window.ad_length_combo.findData(90)
        )

        with patch("ui.main_window.QTimer.singleShot"):
            self.window.run_commercial()

        self.assertEqual(self.window.settings.twitch_last_ad_duration, 90)
        self.window.settings_store.save.assert_called_once_with(
            self.window.settings
        )
        self.window.twitch_service.helix.start_commercial.assert_called_once_with(
            "42", 90, self.window.twitch_auth.token
        )
        self.assertFalse(self.window.run_ad_button.isEnabled())

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
        self.window.known_bot_user_ids.add("bot-1")

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
        self.assertIn("How about you?", message.previous_sally_reply)

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

        self.window._apply_response_batch(ResponseBatchResult((decision,)))

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
        self.window.settings.ai_auto_send_replies = True
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

        with patch("ui.main_window.monotonic", return_value=100.0):
            self.assertTrue(self.window._maybe_auto_send_reply(decision))

    def test_expected_followup_bypasses_confidence_and_normal_reply_gap(self) -> None:
        self.window.settings.ai_auto_send_replies = True
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

        with patch("ui.main_window.monotonic", return_value=100.0):
            self.assertTrue(self.window._maybe_auto_send_reply(decision))

    def test_interjection_requires_high_confidence_and_separate_cooldown(self) -> None:
        self.window.settings.ai_auto_send_replies = True
        self.window.settings.ai_interjections_enabled = True
        self.window.settings.ai_interjection_min_interval_seconds = 180
        self.window.settings.ai_interjection_min_messages = 6
        self.window.viewer_messages_since_sally_reply = 6
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

        with patch("ui.main_window.monotonic", return_value=200.0):
            self.assertTrue(self.window._maybe_auto_send_reply(decision))
        with patch("ui.main_window.monotonic", return_value=201.0):
            self.assertFalse(self.window._maybe_auto_send_reply(decision))

    def test_model_direct_label_cannot_bypass_unsolicited_guards(self) -> None:
        self.window.settings.ai_auto_send_replies = True
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
        self.window.settings.ai_auto_send_replies = True
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

        self.window._response_batch_failed((message,), "model offline")

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
                directed_at_sally=True,
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


if __name__ == "__main__":
    unittest.main()
