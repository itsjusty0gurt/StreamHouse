import logging
import unittest
from unittest.mock import patch

from core.logger import Logger


class LoggerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.test_logger = logging.Logger("SallyAITest", logging.DEBUG)
        self.original_logger = Logger._logger
        Logger._logger = self.test_logger

    def tearDown(self) -> None:
        for handler in list(self.test_logger.handlers):
            handler.close()
        Logger._logger = self.original_logger

    def test_add_and_remove_handler(self) -> None:
        handler = logging.NullHandler()

        Logger.add_handler(handler)
        self.assertIn(handler, self.test_logger.handlers)

        Logger.remove_handler(handler)
        self.assertNotIn(handler, self.test_logger.handlers)

    def test_set_level_updates_logger_and_handlers(self) -> None:
        handler = logging.NullHandler(level=logging.DEBUG)
        self.test_logger.addHandler(handler)

        Logger.set_level(logging.WARNING)

        self.assertEqual(self.test_logger.level, logging.WARNING)
        self.assertEqual(handler.level, logging.WARNING)

    def test_source_is_normalized_and_limited(self) -> None:
        handler = logging.Handler()
        handler.emit = unittest.mock.Mock()
        self.test_logger.addHandler(handler)

        Logger.info("Example", source=" long-source-name ")

        record = handler.emit.call_args.args[0]
        self.assertEqual(record.source, "LONG-SOU")
        self.assertEqual(record.getMessage(), "Example")

    def test_timer_reports_missing_start(self) -> None:
        with patch.object(Logger, "warning") as warning:
            result = Logger.timer_end("missing")

        self.assertIsNone(result)
        warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
