from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from products.hub.automation.routines import RoutineStore
from products.hub.automation.variable_providers import ChannelInformationVariableProvider
from products.hub.automation.variable_registry import VariableRegistry
from products.hub.twitch.channel_information import ChannelInformationStore
from products.hub.twitch.commands import TwitchCommandTriggerStore
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

    def test_social_checkboxes_update_preview_and_save_all_fields(self) -> None:
        discord_check, discord_edit, discord_expose, _error = self.page.social_rows["discord"]
        youtube_check, youtube_edit, youtube_expose, _error = self.page.social_rows["youtube"]
        discord_check.setChecked(True)
        discord_expose.setChecked(True)
        discord_edit.setText("discord.gg/example")
        youtube_check.setChecked(False)
        youtube_expose.setChecked(False)
        youtube_edit.setText("https://youtube.com/@example")
        self.page.schedule_edit.setPlainText("Friday at 8 PM")
        self.page.other_expose_checks["schedule"].setChecked(True)
        self.page.rules_edit.setPlainText("Be kind.")
        self.page.server_info_edit.setPlainText("play.example.com")

        self.assertIn("Discord: https://discord.gg/example", self.page.socials_preview_label.text())
        self.assertNotIn("YouTube", self.page.socials_preview_label.text())
        self.page.save_button.click()

        loaded = ChannelInformationStore(self.information.path).load()
        self.assertTrue(loaded.social_links["discord"].enabled_in_socials)
        self.assertTrue(loaded.social_links["discord"].expose_as_variable)
        self.assertFalse(loaded.social_links["youtube"].enabled_in_socials)
        self.assertEqual(loaded.social_links["youtube"].url, "https://youtube.com/@example")
        self.assertEqual(loaded.schedule, "Friday at 8 PM")
        self.assertTrue(loaded.expose_schedule)
        self.assertEqual(loaded.rules, "Be kind.")
        self.assertEqual(loaded.server_info, "play.example.com")

    def test_checked_blank_link_shows_validation_and_does_not_save(self) -> None:
        include, _edit, _expose, error = self.page.social_rows["discord"]
        include.setChecked(True)

        self.page.save_button.click()

        self.assertIn("Add a link", error.text())
        self.assertFalse(self.information.path.exists())

    def test_enable_after_save_is_explicit(self) -> None:
        self.page.focus_for_command("discord")
        include, edit, expose, _error = self.page.social_rows["discord"]
        include.setChecked(True)
        expose.setChecked(True)
        edit.setText("https://discord.gg/example")

        self.assertFalse(self.page.enable_after_saving_check.isChecked())
        self.page.save_button.click()
        self.assertIsNone(self.commands.default("discord"))

        edit.setText("https://discord.gg/example-two")
        self.page.enable_after_saving_check.setChecked(True)
        self.page.save_button.click()
        discord = self.commands.default("discord")
        self.assertIsNotNone(discord)
        self.assertTrue(discord.enabled)
        self.assertIsNotNone(self.commands.routine_store.get(discord.routine_id))

    def test_exposed_live_edit_updates_store_without_saving(self) -> None:
        registry = VariableRegistry()
        registry.register(ChannelInformationVariableProvider(self.information))
        _include, edit, expose, _error = self.page.social_rows["discord"]
        expose.setChecked(True)
        edit.setText("discord.gg/live")

        snapshot = self.information.snapshot()
        self.assertEqual(snapshot.social_links["discord"].url, "https://discord.gg/live")
        self.assertTrue(snapshot.social_links["discord"].expose_as_variable)
        self.assertEqual(
            registry.resolve("socials.discord").display_value,
            "https://discord.gg/live",
        )
        self.assertFalse(self.information.path.exists())

        edit.setText("discord.gg/updated")
        self.assertEqual(
            self.information.snapshot().social_links["discord"].url,
            "https://discord.gg/updated",
        )
        self.assertEqual(
            registry.resolve("socials.discord").display_value,
            "https://discord.gg/updated",
        )
        expose.setChecked(False)
        self.assertIsNone(registry.definition("socials.discord"))


if __name__ == "__main__":
    unittest.main()
