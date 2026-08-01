import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from products.hub.automation.core_triggers import CoreTriggerStore
from products.hub.automation.routines import RoutineStore
from shared.streamhouse_runtime.paths import (
    consume_deprecation_warnings,
    migrate_legacy_user_data,
    smoke_test_enabled,
    user_data_root,
)
from products.hub.core.resources import resource_path
from products.hub.core.settings import SettingsStore
from products.hub.twitch.activity_history import ActivityHistoryStore
from products.hub.twitch.automation_triggers import TwitchEventTriggerStore
from products.hub.twitch.chatter_history import ChatterHistoryStore
from products.hub.twitch.commands import TwitchCommandTriggerStore
from products.hub.twitch.session_history import StreamSessionStore
from products.hub.twitch.token_store import TwitchTokenStore


class UserDataPathTests(unittest.TestCase):
    def tearDown(self) -> None:
        consume_deprecation_warnings()

    def test_default_stores_use_streamhouse_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {"STREAMHOUSE_DATA_DIR": directory},
                clear=True,
            ):
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

    def test_new_environment_variable_precedes_legacy_value(self) -> None:
        with tempfile.TemporaryDirectory() as new_dir, tempfile.TemporaryDirectory() as old_dir:
            with patch.dict(
                os.environ,
                {
                    "STREAMHOUSE_DATA_DIR": new_dir,
                    "SALLY_DATA_DIR": old_dir,
                },
                clear=True,
            ):
                self.assertEqual(user_data_root(), Path(new_dir).resolve())
                self.assertEqual(consume_deprecation_warnings(), ())

    def test_legacy_environment_variable_is_supported_with_warning(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {"SALLY_DATA_DIR": directory},
                clear=True,
            ):
                self.assertEqual(user_data_root(), Path(directory).resolve())
                self.assertEqual(
                    consume_deprecation_warnings(),
                    (
                        "SALLY_DATA_DIR is deprecated; "
                        "use STREAMHOUSE_DATA_DIR instead.",
                    ),
                )

    def test_default_root_is_streamhouse_under_local_app_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {"LOCALAPPDATA": directory},
                clear=True,
            ):
                self.assertEqual(
                    user_data_root(),
                    Path(directory).resolve() / "Streamhouse",
                )

    def test_smoke_test_environment_uses_new_then_legacy_name(self) -> None:
        with patch.dict(
            os.environ,
            {
                "STREAMHOUSE_SMOKE_TEST": "0",
                "SALLY_SMOKE_TEST": "1",
            },
            clear=True,
        ):
            self.assertFalse(smoke_test_enabled())
            self.assertEqual(consume_deprecation_warnings(), ())
        with patch.dict(
            os.environ,
            {"SALLY_SMOKE_TEST": "1"},
            clear=True,
        ):
            self.assertTrue(smoke_test_enabled())
            self.assertEqual(
                consume_deprecation_warnings(),
                (
                    "SALLY_SMOKE_TEST is deprecated; "
                    "use STREAMHOUSE_SMOKE_TEST instead.",
                ),
            )

    def test_legacy_tree_migration_is_idempotent_and_never_overwrites(self) -> None:
        with tempfile.TemporaryDirectory() as legacy_directory, tempfile.TemporaryDirectory() as data_directory:
            legacy = Path(legacy_directory)
            data = Path(data_directory)
            source = legacy / "config" / "settings.json"
            source.parent.mkdir(parents=True)
            source.write_text('{"startup_page":"AI"}', encoding="utf-8")
            secret = legacy / "twitch-token.dat"
            secret.write_bytes(b"\x01opaque-dpapi\xff")
            training = legacy / "training" / "examples.json"
            training.parent.mkdir(parents=True)
            training.write_text('{"examples":[]}', encoding="utf-8")
            existing = data / "config" / "settings.json"
            existing.parent.mkdir(parents=True)
            existing.write_text('{"startup_page":"Logs"}', encoding="utf-8")

            first = migrate_legacy_user_data(
                legacy,
                data,
                include_development_files=False,
            )
            second = migrate_legacy_user_data(
                legacy,
                data,
                include_development_files=False,
            )

            self.assertEqual(first.copied_files, 2)
            self.assertEqual(first.existing_files, 1)
            self.assertEqual(second.copied_files, 0)
            self.assertEqual(second.existing_files, 3)
            self.assertIn("Logs", existing.read_text(encoding="utf-8"))
            self.assertEqual((data / "twitch-token.dat").read_bytes(), secret.read_bytes())
            self.assertTrue((data / "training" / "examples.json").exists())
            self.assertTrue(source.exists())

    def test_resource_path_supports_packaged_bundle_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("products.hub.core.resources.sys._MEIPASS", directory, create=True):
                self.assertEqual(
                    resource_path("assets/icon.png"),
                    Path(directory) / "assets" / "icon.png",
                )


if __name__ == "__main__":
    unittest.main()
