import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from products.hub.streamhouse_hub.app import (
    _resolved_data_root,
    register_events,
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
