import io
import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from shared.streamhouse_runtime.logger import Logger


class LoggerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.test_logger = logging.Logger("StreamhouseTest", logging.DEBUG)
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

    def test_normal_logs_redact_credentials_headers_and_urls(self) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        self.test_logger.addHandler(handler)

        Logger.warning(
            "Authorization: Bearer bearer-secret "
            "X-Streamhouse-Key=relay-secret "
            "password=plain-password secret=plain-secret api_key=api-secret "
            "https://user:password@example.test/path#token=url-secret"
        )

        rendered = stream.getvalue()
        for secret in (
            "bearer-secret",
            "relay-secret",
            "plain-password",
            "plain-secret",
            "api-secret",
            "user:password",
            "url-secret",
        ):
            self.assertNotIn(secret, rendered)
        self.assertIn("<REDACTED>", rendered)

    def test_exception_traceback_omits_arbitrary_exception_message(self) -> None:
        handler = logging.Handler()
        handler.emit = unittest.mock.Mock()
        self.test_logger.addHandler(handler)

        try:
            raise RuntimeError("refresh_token=private-refresh")
        except RuntimeError:
            Logger.exception("Credential operation failed")

        record = handler.emit.call_args.args[0]
        self.assertNotIn("private-refresh", record.getMessage())
        self.assertIn("RuntimeError", record.getMessage())
        self.assertIn("exception message omitted for privacy", record.getMessage())

    def test_timer_reports_missing_start(self) -> None:
        with patch.object(Logger, "warning") as warning:
            result = Logger.timer_end("missing")

        self.assertIsNone(result)
        warning.assert_called_once()

    def test_shutdown_flushes_and_closes_file_handlers(self) -> None:
        with TemporaryDirectory() as temporary:
            log_path = Path(temporary) / "shutdown.log"
            handler = logging.FileHandler(log_path, encoding="utf-8")
            self.test_logger.addHandler(handler)
            self.test_logger.info("saved before shutdown")

            Logger.shutdown()

            self.assertIsNone(handler.stream)
            self.assertEqual(
                log_path.read_text(encoding="utf-8").strip(),
                "saved before shutdown",
            )

    def test_shutdown_is_idempotent(self) -> None:
        handler = logging.StreamHandler(io.StringIO())
        self.test_logger.addHandler(handler)

        Logger.shutdown()
        Logger.shutdown()

        self.assertEqual(self.test_logger.handlers, [])
        self.assertIsNone(Logger._logger)

    def test_shutdown_tolerates_invalid_non_owned_console_stream(self) -> None:
        class InvalidConsoleStream(io.StringIO):
            def flush(self) -> None:
                raise OSError(22, "Invalid argument")

        handler = logging.StreamHandler(InvalidConsoleStream())
        setattr(handler, Logger._NON_OWNED_STREAM_MARKER, True)
        self.test_logger.addHandler(handler)

        Logger.shutdown()

        self.assertEqual(self.test_logger.handlers, [])
        self.assertIsNone(handler.stream)
        self.assertIsNone(Logger._logger)

    def test_shutdown_does_not_hide_owned_handler_flush_errors(self) -> None:
        class FailingOwnedHandler(logging.Handler):
            def flush(self) -> None:
                raise OSError(22, "Invalid argument")

        handler = FailingOwnedHandler()
        self.test_logger.addHandler(handler)

        with self.assertRaises(OSError):
            Logger.shutdown()

        self.assertEqual(self.test_logger.handlers, [])
        self.assertIsNone(Logger._logger)


if __name__ == "__main__":
    unittest.main()
