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
                }
            ),
            encoding="utf-8",
        )

        settings = self.store.load()

        self.assertEqual(settings.startup_page, "Dashboard")
        self.assertEqual(settings.log_level, "DEBUG")
        self.assertEqual(settings.ui_log_limit, 10_000)
        self.assertTrue(settings.show_developer_tools)

    def test_non_object_json_is_rejected(self) -> None:
        self.settings_path.write_text("[]", encoding="utf-8")

        with self.assertRaises(ValueError):
            self.store.load()


if __name__ == "__main__":
    unittest.main()
