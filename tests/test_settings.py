import json
import tempfile
import unittest
from pathlib import Path

from core.settings import AppSettings, SettingsStore


class SettingsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.settings_path = (
            Path(self.temporary_directory.name) / "settings.json"
        )
        self.store = SettingsStore(self.settings_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_missing_file_returns_defaults(self) -> None:
        self.assertEqual(self.store.load(), AppSettings())

    def test_settings_round_trip(self) -> None:
        expected = AppSettings(
            startup_page="Logs",
            log_level="WARNING",
            ui_log_limit=750,
            show_developer_tools=False,
            twitch_chat_show_timestamps=False,
            twitch_chat_font_family="Consolas",
            twitch_chat_font_size=13,
        )

        self.store.save(expected)

        self.assertEqual(self.store.load(), expected)
        self.assertFalse(self.settings_path.with_suffix(".tmp").exists())

    def test_invalid_values_fall_back_or_are_clamped(self) -> None:
        self.settings_path.write_text(
            json.dumps(
                {
                    "startup_page": "Unknown",
                    "log_level": "NOISY",
                    "ui_log_limit": 99_999,
                    "show_developer_tools": "yes",
                    "twitch_chat_show_timestamps": "yes",
                    "twitch_chat_font_family": "",
                    "twitch_chat_font_size": 100,
                }
            ),
            encoding="utf-8",
        )

        settings = self.store.load()

        self.assertEqual(settings.startup_page, "Dashboard")
        self.assertEqual(settings.log_level, "DEBUG")
        self.assertEqual(settings.ui_log_limit, 10_000)
        self.assertTrue(settings.show_developer_tools)
        self.assertTrue(settings.twitch_chat_show_timestamps)
        self.assertEqual(settings.twitch_chat_font_family, "Segoe UI")
        self.assertEqual(settings.twitch_chat_font_size, 24)

    def test_non_object_json_is_rejected(self) -> None:
        self.settings_path.write_text("[]", encoding="utf-8")

        with self.assertRaises(ValueError):
            self.store.load()

    def test_legacy_memories_startup_page_migrates_to_ai(self) -> None:
        self.settings_path.write_text(
            json.dumps({"startup_page": "Memories"}),
            encoding="utf-8",
        )

        self.assertEqual(self.store.load().startup_page, "AI")


if __name__ == "__main__":
    unittest.main()
