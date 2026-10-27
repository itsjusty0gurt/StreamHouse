import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from products.hub.streamhouse_hub.app import (
    _resolved_data_root,
    register_events,
    restore_twitch_identities,
    shutdown_application,
)
from products.hub.core.events import Events


class AppLifecycleTests(unittest.TestCase):
    def tearDown(self) -> None:
        Events.clear()

    @patch("products.hub.streamhouse_hub.app.Logger")
    def test_shutdown_clears_application_events(self, _logger) -> None:
        Events.subscribe("lifecycle", lambda: None)
        shutdown_application()
        self.assertEqual(Events.listener_count(), 0)

    @patch("products.hub.streamhouse_hub.app.Logger")
    def test_register_events_is_safe_without_plugins(self, _logger) -> None:
        register_events()
        self.assertEqual(Events.listener_count(), 0)

    def test_active_diagnostics_root_is_the_runtime_user_data_root(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "active-data"

            self.assertEqual(
                _resolved_data_root(Mock(root=root)),
                root.resolve(),
            )

    def test_disabled_twitch_module_does_not_restore_identities(self) -> None:
        registry = Mock()
        registry.is_enabled.return_value = False
        broadcaster_auth = Mock()
        bot_auth = Mock()

        restore_twitch_identities(registry, broadcaster_auth, bot_auth)

        broadcaster_auth.restore.assert_not_called()
        bot_auth.restore.assert_not_called()

    def test_enabled_twitch_module_restores_both_identities(self) -> None:
        registry = Mock()
        registry.is_enabled.return_value = True
        broadcaster_auth = Mock()
        bot_auth = Mock()

        restore_twitch_identities(registry, broadcaster_auth, bot_auth)

        broadcaster_auth.restore.assert_called_once_with()
        bot_auth.restore.assert_called_once_with()

    @patch("products.hub.streamhouse_hub.app.user_data_root")
    def test_runtime_user_data_root_uses_canonical_resolver_without_diagnostics(
        self,
        resolve_root,
    ) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary) / "canonical-data"
            resolve_root.return_value = root

            self.assertEqual(_resolved_data_root(None), root.resolve())

        resolve_root.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
