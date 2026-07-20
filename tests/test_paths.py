import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.paths import migrate_legacy_user_data, user_data_root
from core.resources import resource_path
from core.settings import SettingsStore
from automation.routines import RoutineStore
from automation.core_triggers import CoreTriggerStore
from twitch.activity_history import ActivityHistoryStore
from twitch.chatter_history import ChatterHistoryStore
from twitch.commands import TwitchCommandTriggerStore
from twitch.automation_triggers import TwitchEventTriggerStore
from twitch.session_history import StreamSessionStore
from twitch.token_store import TwitchTokenStore


class UserDataPathTests(unittest.TestCase):
    def test_default_stores_use_stable_user_data_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"SALLY_DATA_DIR": directory}):
                root = Path(directory).resolve()
                self.assertEqual(user_data_root(), root)
                paths = (
                    SettingsStore().path,
                    ActivityHistoryStore().path,
                    ChatterHistoryStore().path,
                    StreamSessionStore().path,
                    TwitchCommandTriggerStore().path,
                    TwitchEventTriggerStore().path,
                    RoutineStore().path,
                    CoreTriggerStore().path,
                    TwitchTokenStore().path,
                )
                self.assertTrue(all(root in path.parents for path in paths))

    def test_legacy_files_copy_without_overwriting_new_data(self) -> None:
        with tempfile.TemporaryDirectory() as legacy_directory, tempfile.TemporaryDirectory() as data_directory:
            legacy = Path(legacy_directory)
            data = Path(data_directory)
            source = legacy / "config" / "settings.json"
            source.parent.mkdir(parents=True)
            source.write_text('{"startup_page":"AI"}', encoding="utf-8")
            with patch("core.paths.project_root", return_value=legacy), patch(
                "core.paths.user_data_root", return_value=data
            ):
                migrated = migrate_legacy_user_data()
                self.assertEqual(migrated, ("config/settings.json",))
                destination = data / "config" / "settings.json"
                destination.write_text('{"startup_page":"Logs"}', encoding="utf-8")
                migrate_legacy_user_data()
                self.assertIn("Logs", destination.read_text(encoding="utf-8"))

    def test_resource_path_supports_packaged_bundle_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("core.resources.sys._MEIPASS", directory, create=True):
                self.assertEqual(
                    resource_path("assets/icon.png"),
                    Path(directory) / "assets" / "icon.png",
                )


if __name__ == "__main__":
    unittest.main()
