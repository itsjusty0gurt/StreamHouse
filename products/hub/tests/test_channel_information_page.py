from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from datetime import datetime, timezone

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QCheckBox
from shared.streamhouse_runtime.json_store import atomic_write_json
from products.hub.ui.variable_picker import VariablePickerDialog

from products.hub.automation.routines import RoutineStore
from products.hub.automation.variable_providers import ChannelInformationVariableProvider
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.twitch.channel_information import ChannelInformationStore
from products.hub.twitch.commands import (
    TwitchCommandTriggerStore, TwitchCommandSetupState, TwitchCommandTriggerDispatcher,
    TwitchCommandTriggerOutcome,
)
from products.hub.twitch.models import TwitchMessage
from products.hub.twitch.tasks import register_twitch_tasks
from products.hub.automation.tasks import TaskRegistry
from products.hub.automation.service import AutomationService
from products.hub.ui.channel_information_page import ChannelInformationPage


class ChannelInformationPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.information = ChannelInformationStore(root / "channel-information.json")
        self.information.load()
        self.commands = TwitchCommandTriggerStore(
            root / "commands.json", RoutineStore(root / "routines.json")
        )
        self.page = ChannelInformationPage(self.information, self.commands)

    def tearDown(self) -> None:
        self.page.deleteLater()
        self.application.processEvents()
        self.temporary.cleanup()

    def registry(self):
        registry = VariableRegistry()
        registry.register(ChannelInformationVariableProvider(self.information))
        return registry

    def test_clean_definitions_are_always_discoverable_without_exposure_controls(self) -> None:
        registry = self.registry()
        for service_id in self.page.social_rows:
            snapshot = registry.resolve(f"socials.{service_id}")
            self.assertTrue(snapshot.available)
            self.assertEqual(snapshot.value, "")
        self.assertEqual(len(registry.definitions()), 11)
        self.assertEqual(registry.aliases(), ())
        self.assertFalse(any("Expose" in widget.objectName() or "Expose" in widget.text()
                             for widget in self.page.findChildren(QCheckBox)))
        picker = VariablePickerDialog(registry)
        picker.search_edit.setText("socials.discord")
        self.assertEqual(picker.selected_placeholder(), "{socials.discord}")
        picker.close()

    def test_drafts_and_include_only_change_on_update(self) -> None:
        include, edit, update, _error = self.page.social_rows["discord"]
        registry = self.registry()
        self.assertFalse(update.isEnabled())
        edit.setText("https://discord.gg/example")
        self.assertTrue(update.isEnabled())
        self.assertEqual(registry.resolve("socials.discord").value, "")
        self.assertIsNone(self.commands.default("discord"))
        self.assertFalse(self.information.path.exists())
        edit.clear()
        self.assertFalse(update.isEnabled())
        include.setChecked(True)
        self.assertTrue(update.isEnabled())
        include.setChecked(False)
        self.assertFalse(update.isEnabled())
        edit.setText("  discord.gg/example  ")
        include.setChecked(True)
        self.assertNotIn("discord.gg/example", self.page.socials_preview_label.text())
        update.click()
        self.assertFalse(update.isEnabled())
        self.assertEqual(edit.text(), "https://discord.gg/example")
        self.assertEqual(registry.resolve("socials.discord").value, edit.text())
        self.assertIn(edit.text(), self.information.build_social_links_message())
        loaded = ChannelInformationStore(self.information.path).load()
        self.assertTrue(loaded.social_links["discord"].enabled_in_socials)
        self.assertEqual(loaded.social_links["discord"].url, edit.text())
        self.assertNotIn("expose", self.information.path.read_text())

    def test_managed_commands_sync_stable_identity_and_clearing(self) -> None:
        include, edit, update, _error = self.page.social_rows["discord"]
        edit.setText("discord.gg/first")
        update.click()
        discord = self.commands.default("discord")
        routine_id = discord.routine_id
        self.assertTrue(discord.enabled)
        self.assertIsNone(self.commands.default("socials"))
        self.assertEqual(self.commands.setup_state(discord, self.information),
                         TwitchCommandSetupState.ENABLED)
        routine = self.commands.routine_store.get(routine_id)
        self.assertEqual(self.commands.routine_store.get_group(routine.group_id).name, "Commands")
        include.setChecked(True)
        update.click()
        socials_id = self.commands.default("socials").routine_id
        self.assertTrue(self.commands.default("socials").enabled)
        edit.setText("discord.gg/second")
        self.assertIn("/first", self.information.build_social_links_message())
        update.click()
        self.assertEqual(self.commands.default("discord").routine_id, routine_id)
        self.assertEqual(self.commands.default("socials").routine_id, socials_id)
        self.assertEqual(len(self.commands.triggers), 2)
        self.assertEqual(len(self.commands.routine_store.groups), 1)
        edit.clear()
        update.click()  # Empty + Include is allowed; it is simply unconfigured.
        self.assertFalse(self.commands.default("discord").enabled)
        self.assertFalse(self.commands.default("socials").enabled)
        self.assertEqual(self.commands.setup_state(self.commands.default("discord"), self.information),
                         TwitchCommandSetupState.SETUP_REQUIRED)
        self.assertEqual(self.registry().resolve("socials.discord").value, "")
        self.assertEqual(self.information.build_social_links_message(), "")
        edit.setText("discord.gg/restored")
        update.click()
        self.assertEqual(self.commands.default("discord").routine_id, routine_id)
        self.assertTrue(self.commands.default("socials").enabled)

    def test_multiple_socials_and_include_do_not_disable_individual_command(self) -> None:
        for service_id in ("discord", "youtube"):
            include, edit, update, _error = self.page.social_rows[service_id]
            edit.setText(f"https://{service_id}.example")
            include.setChecked(True)
            update.click()
        include, _edit, update, _error = self.page.social_rows["discord"]
        include.setChecked(False)
        update.click()
        self.assertTrue(self.commands.default("discord").enabled)
        self.assertTrue(self.commands.default("socials").enabled)
        self.assertNotIn("Discord", self.information.build_social_links_message())
        include, _edit, update, _error = self.page.social_rows["youtube"]
        include.setChecked(False)
        update.click()
        self.assertTrue(self.commands.default("youtube").enabled)
        self.assertFalse(self.commands.default("socials").enabled)

    def test_custom_commands_and_other_drafts_are_preserved(self) -> None:
        custom = self.commands.add("discord", "Custom {socials.discord}")
        other = self.commands.add("mydiscord", "{socials.discord}")
        include, edit, update, _error = self.page.social_rows["discord"]
        youtube_include, youtube_edit, youtube_update, _error = self.page.social_rows["youtube"]
        youtube_edit.setText("youtube.com/draft")
        self.page.schedule_edit.setPlainText("Unsaved schedule")
        edit.setText("discord.gg/example")
        include.setChecked(True)
        update.click()
        self.assertIsNone(self.commands.default("discord"))
        self.assertEqual(self.commands.response_for(self.commands.get(custom.trigger_id)),
                         "Custom {socials.discord}")
        self.assertEqual(youtube_edit.text(), "youtube.com/draft")
        self.assertTrue(youtube_update.isEnabled())
        self.assertEqual(self.information.snapshot().schedule, "")
        self.assertEqual(self.information.snapshot().social_links["youtube"].url, "")
        self.page.save_button.click()
        self.assertEqual(self.information.snapshot().schedule, "Unsaved schedule")
        self.assertEqual(youtube_edit.text(), "youtube.com/draft")
        self.assertTrue(youtube_update.isEnabled())
        edit.clear()
        update.click()
        self.assertIsNotNone(self.commands.get(custom.trigger_id))
        self.assertIsNotNone(self.commands.routine_store.get(other.routine_id))

    def test_invalid_update_preserves_committed_value_and_draft(self) -> None:
        include, edit, update, error = self.page.social_rows["discord"]
        edit.setText("discord.gg/old")
        update.click()
        edit.setText("not a valid link")
        include.setChecked(True)
        update.click()
        self.assertIn("spaces", error.text())
        self.assertTrue(update.isEnabled())
        self.assertEqual(edit.text(), "not a valid link")
        self.assertEqual(self.registry().resolve("socials.discord").value, "https://discord.gg/old")
        self.assertFalse(self.information.snapshot().social_links["discord"].enabled_in_socials)
        self.assertIsNone(self.commands.default("socials"))

    def test_write_failure_rolls_back_all_stores_and_keeps_draft(self) -> None:
        # Fail each boundary, including after routines/commands were already saved.
        for failing_path in (self.commands.routine_store.path, self.commands.path, self.information.path):
            with self.subTest(path=failing_path.name):
                include, edit, update, error = self.page.social_rows["discord"]
                edit.setText("discord.gg/new")
                include.setChecked(True)
                def fail_selected(path, payload):
                    if path == failing_path:
                        raise OSError("Test disk failure")
                    atomic_write_json(path, payload)
                with patch("products.hub.twitch.channel_information.atomic_write_json",
                           side_effect=fail_selected):
                    update.click()
                self.assertIn("Test disk failure", error.text())
                self.assertEqual(self.registry().resolve("socials.discord").value, "")
                self.assertEqual(self.commands.triggers, [])
                self.assertEqual(self.commands.routine_store.routines, [])
                self.assertEqual(self.commands.routine_store.groups, [])
                self.assertTrue(update.isEnabled())
                self.assertEqual(edit.text(), "discord.gg/new")
                self.assertFalse(self.information.path.exists())
                self.assertFalse(self.commands.path.exists())
                self.assertFalse(self.commands.routine_store.path.exists())

    def test_failure_preserves_existing_files_backups_and_runtime_values(self) -> None:
        include, edit, update, _error = self.page.social_rows["discord"]
        edit.setText("discord.gg/old")
        update.click()
        edit.setText("discord.gg/committed")
        update.click()
        paths = (self.commands.path, self.commands.routine_store.path, self.information.path)
        originals = {path: path.read_bytes() if path.exists() else None
                     for target in paths for path in (target, target.with_suffix(target.suffix + ".bak"))}
        edit.setText("discord.gg/failed")
        include.setChecked(True)
        def fail_information(path, payload):
            if path == self.information.path:
                raise OSError("Test disk failure")
            atomic_write_json(path, payload)
        with patch("products.hub.twitch.channel_information.atomic_write_json", side_effect=fail_information):
            update.click()
        for path, content in originals.items():
            self.assertEqual(path.read_bytes() if path.exists() else None, content)
        self.assertEqual(self.registry().resolve("socials.discord").value, "https://discord.gg/committed")
        self.assertIsNone(self.commands.default("socials"))
        self.assertTrue(update.isEnabled())

    def test_restart_restores_commands_group_and_committed_values(self) -> None:
        include, edit, update, _error = self.page.social_rows["discord"]
        include.setChecked(True)
        edit.setText("discord.gg/saved")
        update.click()
        routine_id = self.commands.default("discord").routine_id
        edit.setText("discord.gg/unsaved")
        information = ChannelInformationStore(self.information.path)
        information.load()
        commands = TwitchCommandTriggerStore(self.commands.path, RoutineStore(self.commands.routine_store.path))
        commands.load()
        self.assertEqual(information.snapshot().social_links["discord"].url, "https://discord.gg/saved")
        self.assertEqual(commands.default("discord").routine_id, routine_id)
        self.assertTrue(commands.default("socials").enabled)
        self.assertEqual(len(commands.routine_store.groups), 1)

    def test_social_setup_no_longer_needs_enable_after_save(self) -> None:
        self.page.focus_for_command("discord")
        self.assertTrue(self.page.enable_after_saving_check.isHidden())
        self.page.focus_for_command("schedule")
        self.assertFalse(self.page.enable_after_saving_check.isHidden())
        self.page.schedule_edit.setPlainText("Friday at 8 PM")
        self.assertEqual(self.registry().resolve("channel.schedule").value, "")
        self.page.enable_after_saving_check.setChecked(True)
        self.page.save_button.click()
        self.assertTrue(self.commands.default("schedule").enabled)
        self.assertEqual(self.registry().resolve("channel.schedule").value, "Friday at 8 PM")

    def test_chat_execution_uses_committed_rows_not_drafts(self) -> None:
        registry = self.registry()
        tasks = TaskRegistry()
        twitch = Mock()
        twitch.send_message.return_value = True
        register_twitch_tasks(tasks, twitch, command_provider=lambda: self.commands,
                              channel_information_provider=lambda: self.information,
                              variable_registry=registry)
        automation = AutomationService(self.commands.routine_store, tasks, variable_registry=registry)
        include, edit, update, _error = self.page.social_rows["discord"]
        edit.setText("discord.gg/old")
        include.setChecked(True)
        update.click()
        edit.setText("discord.gg/new")
        for committed_url in ("https://discord.gg/old", "https://discord.gg/new"):
            for name in ("discord", "socials"):
                dispatcher = TwitchCommandTriggerDispatcher(self.commands, channel_information=self.information)
                event = dispatcher.evaluate(TwitchMessage(
                    username="Viewer", user_id="123", user_login="viewer",
                    text=f"!{name}", received_at=datetime.now(timezone.utc),
                ))
                self.assertEqual(event.outcome, TwitchCommandTriggerOutcome.READY)
                result = automation.publish_trigger(event.to_event())
                self.assertTrue(result.succeeded)
                self.assertIn(committed_url, twitch.send_message.call_args.args[0])
            update.click()

    def test_resize_keeps_typography_stable_and_reflows_controls(self) -> None:
        self.page.show()
        self.page.resize(900, 700)
        self.application.processEvents()
        body_size = self.page.font().pointSizeF()
        editor_size = self.page.rules_edit.font().pointSizeF()
        wide_editor_width = self.page.rules_edit.width()
        self.assertFalse(self.page.property("compactLayout"))

        self.page.resize(420, 520)
        self.application.processEvents()

        self.assertEqual(self.page.size().toTuple(), (420, 520))
        self.assertTrue(self.page.property("compactLayout"))
        self.assertEqual(self.page.font().pointSizeF(), body_size)
        self.assertEqual(self.page.rules_edit.font().pointSizeF(), editor_size)
        self.assertGreater(self.page.rules_edit.width(), 300)
        self.assertGreaterEqual(self.page.rules_edit.height(), 72)
        self.assertGreater(self.page.scroll_area.verticalScrollBar().maximum(), 0)
        self.assertEqual(self.page.scroll_area.horizontalScrollBar().maximum(), 0)
        include, edit, update, _error = self.page.social_rows["discord"]
        for control in (include, edit):
            self.assertTrue(control.isEnabled())
        for control in (include, edit, update, self.page.save_button):
            self.assertTrue(control.isVisible())

        self.page.resize(900, 700)
        self.application.processEvents()
        self.assertFalse(self.page.property("compactLayout"))
        self.assertEqual(self.page.font().pointSizeF(), body_size)
        self.assertEqual(self.page.rules_edit.font().pointSizeF(), editor_size)
        self.assertGreater(self.page.rules_edit.width(), wide_editor_width - 5)

    def test_long_text_uses_editor_and_page_scrolling(self) -> None:
        self.page.rules_edit.setPlainText("\n".join(f"Rule {index}" for index in range(40)))
        self.page.show()
        self.page.resize(420, 360)
        self.application.processEvents()

        self.assertTrue(self.page.property("compactLayout"))
        self.assertTrue(self.page.socials_preview_label.wordWrap())
        self.assertGreater(
            self.page.rules_edit.verticalScrollBar().maximum(),
            0,
        )
        self.assertGreater(
            self.page.scroll_area.verticalScrollBar().maximum(),
            0,
        )


if __name__ == "__main__":
    unittest.main()
