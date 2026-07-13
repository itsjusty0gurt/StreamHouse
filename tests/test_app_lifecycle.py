import unittest
from unittest.mock import patch

from app.app import register_events, shutdown_application
from core.events import Events


class AppLifecycleTests(unittest.TestCase):
    def tearDown(self) -> None:
        Events.clear()

    @patch("app.app.Logger")
    def test_shutdown_clears_application_events(self, _logger) -> None:
        Events.subscribe("lifecycle", lambda: None)
        shutdown_application()
        self.assertEqual(Events.listener_count(), 0)

    @patch("app.app.Logger")
    def test_register_events_is_safe_without_plugins(self, _logger) -> None:
        register_events()
        self.assertEqual(Events.listener_count(), 0)


if __name__ == "__main__":
    unittest.main()
