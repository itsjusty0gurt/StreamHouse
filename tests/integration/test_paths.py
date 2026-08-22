import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from products.hub.automation.core_triggers import CoreTriggerStore
from products.hub.automation.routines import RoutineStore
from shared.streamhouse_runtime.paths import (
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

    def test_smoke_test_environment_uses_streamhouse_name(self) -> None:
        with patch.dict(
            os.environ,
            {"STREAMHOUSE_SMOKE_TEST": "0"},
            clear=True,
        ):
            self.assertFalse(smoke_test_enabled())
        with patch.dict(
            os.environ,
            {"STREAMHOUSE_SMOKE_TEST": "1"},
            clear=True,
        ):
            self.assertTrue(smoke_test_enabled())

    def test_resource_path_supports_packaged_bundle_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch("products.hub.core.resources.sys._MEIPASS", directory, create=True):
                self.assertEqual(
                    resource_path("assets/icon.png"),
                    Path(directory) / "assets" / "icon.png",
                )


if __name__ == "__main__":
    unittest.main()
