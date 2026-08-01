import unittest
from unittest.mock import patch

from products.hub.core.events import Events


class EventsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.logger_patch = patch("products.hub.core.events.Logger")
        self.logger_patch.start()
        Events.clear()

    def tearDown(self) -> None:
        Events.clear()
        self.logger_patch.stop()

    def test_subscribe_emit_and_unsubscribe(self) -> None:
        received_messages: list[str] = []

        def receive(message: str) -> None:
            received_messages.append(message)

        Events.subscribe("Example_Event", receive)

        self.assertEqual(Events.listener_count("example_event"), 1)
        self.assertEqual(
            Events.emit("example_event", message="Sally is ready."),
            1,
        )
        self.assertEqual(received_messages, ["Sally is ready."])
        self.assertTrue(Events.unsubscribe("example_event", receive))
        self.assertEqual(Events.listener_count("example_event"), 0)

    def test_duplicate_subscription_is_ignored(self) -> None:
        def receive() -> None:
            pass

        Events.subscribe("example", receive)
        Events.subscribe("example", receive)

        self.assertEqual(Events.listener_count("example"), 1)

    def test_callback_failure_does_not_stop_other_callbacks(self) -> None:
        calls: list[str] = []

        def failing_callback() -> None:
            raise RuntimeError("Expected test failure")

        def successful_callback() -> None:
            calls.append("called")

        Events.subscribe("example", failing_callback)
        Events.subscribe("example", successful_callback)

        self.assertEqual(Events.emit("example"), 1)
        self.assertEqual(calls, ["called"])

    def test_empty_event_name_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Events.listener_count("  ")


if __name__ == "__main__":
    unittest.main()
