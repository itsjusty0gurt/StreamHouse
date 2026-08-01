from __future__ import annotations

import logging
import platform
import sys
import traceback
from datetime import datetime
from pathlib import Path
from time import perf_counter
from typing import ClassVar

from shared.streamhouse_runtime.version import VERSION
from shared.streamhouse_runtime.paths import user_data_root


class DefaultLogFieldsFilter(logging.Filter):
    """Ensure every log record contains Streamhouse diagnostic fields."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "source"):
            record.source = "GENERAL"

        return True


class StreamhouseFormatter(logging.Formatter):
    """
    Formats Streamhouse log messages.

    The level and source fields are centered so every log line remains
    evenly aligned.
    """

    RESET = "\033[0m"

    COLORS = {
        logging.DEBUG: "\033[90m",
        logging.INFO: "\033[97m",
        logging.WARNING: "\033[93m",
        logging.ERROR: "\033[91m",
        logging.CRITICAL: "\033[97;41m",
    }

    def __init__(
        self,
        fmt: str,
        datefmt: str,
        use_colors: bool = False,
        level_width: int = 9,
        source_width: int = 8,
    ) -> None:
        super().__init__(
            fmt=fmt,
            datefmt=datefmt,
        )

        self.use_colors = use_colors
        self.level_width = level_width
        self.source_width = source_width

    @staticmethod
    def _center_left_bias(text: str, width: int) -> str:
        """
        Center text while placing an odd extra padding space on the left.
        """

        text = str(text)

        if len(text) >= width:
            return text[:width]

        padding = width - len(text)

        left_padding = (padding + 1) // 2
        right_padding = padding // 2

        return (
            (" " * left_padding)
            + text
            + (" " * right_padding)
        )

    def format(self, record: logging.LogRecord) -> str:
        original_source = getattr(
            record,
            "source",
            "GENERAL",
        )

        original_level_display = getattr(
            record,
            "level_display",
            None,
        )

        record.source = str(original_source).center(
            self.source_width,
        )

        record.level_display = self._center_left_bias(
            record.levelname,
            self.level_width,
        )

        try:
            formatted_message = super().format(record)
        finally:
            record.source = original_source

            if original_level_display is None:
                try:
                    del record.level_display
                except AttributeError:
                    pass
            else:
                record.level_display = original_level_display

        if not self.use_colors:
            return formatted_message

        color = self.COLORS.get(
            record.levelno,
            self.RESET,
        )

        return f"{color}{formatted_message}{self.RESET}"


class Logger:
    """Central logging system shared by Streamhouse Hub and Streamhouse AI."""

    LEVEL_WIDTH = 9
    SOURCE_WIDTH = 8

    _logger: ClassVar[logging.Logger | None] = None
    _timers: ClassVar[dict[str, float]] = {}

    @classmethod
    def setup(
        cls,
        level: int = logging.DEBUG,
        clear_latest: bool = True,
    ) -> None:
        """
        Configure console logging, latest.log, and the daily log file.
        """

        if cls._logger is not None:
            return

        logs_directory = user_data_root() / "logs"
        archive_directory = logs_directory / "archive"

        logs_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        archive_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        latest_log_path = logs_directory / "latest.log"

        current_date = datetime.now().strftime("%Y-%m-%d")
        daily_log_path = logs_directory / f"{current_date}.log"

        logger = logging.getLogger("Streamhouse")
        logger.setLevel(level)
        logger.propagate = False

        # Prevent duplicate output if setup is called again.
        logger.handlers.clear()
        logger.filters.clear()

        default_fields_filter = DefaultLogFieldsFilter()

        # Spaces are used instead of vertical separators.
        log_format = (
            "%(asctime)s.%(msecs)03d  "
            "[%(level_display)s]  "
            "[%(source)s]  "
            "%(message)s"
        )

        console_formatter = StreamhouseFormatter(
            fmt=log_format,
            datefmt="%H:%M:%S",
            use_colors=True,
            level_width=cls.LEVEL_WIDTH,
            source_width=cls.SOURCE_WIDTH,
        )

        file_formatter = StreamhouseFormatter(
            fmt=log_format,
            datefmt="%Y-%m-%d %H:%M:%S",
            use_colors=False,
            level_width=cls.LEVEL_WIDTH,
            source_width=cls.SOURCE_WIDTH,
        )

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(console_formatter)
        console_handler.addFilter(default_fields_filter)

        latest_handler = logging.FileHandler(
            filename=latest_log_path,
            mode="w" if clear_latest else "a",
            encoding="utf-8",
        )
        latest_handler.setLevel(level)
        latest_handler.setFormatter(file_formatter)
        latest_handler.addFilter(default_fields_filter)

        daily_handler = logging.FileHandler(
            filename=daily_log_path,
            mode="a",
            encoding="utf-8",
        )
        daily_handler.setLevel(level)
        daily_handler.setFormatter(file_formatter)
        daily_handler.addFilter(default_fields_filter)

        logger.addHandler(console_handler)
        logger.addHandler(latest_handler)
        logger.addHandler(daily_handler)

        cls._logger = logger

        cls._write_startup_banner()

    @classmethod
    def _ensure_setup(cls) -> logging.Logger:
        """Set up logging automatically if needed."""

        if cls._logger is None:
            cls.setup()

        if cls._logger is None:
            raise RuntimeError(
                "The logging system could not be initialized."
            )

        return cls._logger

    @classmethod
    def shutdown(cls) -> None:
        """Flush and close Streamhouse-owned handlers."""
        if cls._logger is None:
            return
        for handler in list(cls._logger.handlers):
            try:
                handler.flush()
                handler.close()
            finally:
                cls._logger.removeHandler(handler)
        cls._logger = None

    @classmethod
    def _clean_source(cls, source: str) -> str:
        """Normalize and limit a source name."""

        clean_source = source.strip().upper()

        if not clean_source:
            clean_source = "GENERAL"

        return clean_source[: cls.SOURCE_WIDTH]

    @classmethod
    def _log(
        cls,
        level: int,
        message: str,
        source: str,
        exc_info: bool = False,
    ) -> None:
        """Send a message through the configured Python logger."""

        logger = cls._ensure_setup()

        logger.log(
            level,
            message,
            extra={
                "source": cls._clean_source(source),
            },
            exc_info=exc_info,
        )

    @classmethod
    def debug(
        cls,
        message: str,
        source: str = "GENERAL",
    ) -> None:
        cls._log(
            logging.DEBUG,
            message,
            source,
        )

    @classmethod
    def info(
        cls,
        message: str,
        source: str = "GENERAL",
    ) -> None:
        cls._log(
            logging.INFO,
            message,
            source,
        )

    @classmethod
    def warning(
        cls,
        message: str,
        source: str = "GENERAL",
    ) -> None:
        cls._log(
            logging.WARNING,
            message,
            source,
        )

    @classmethod
    def error(
        cls,
        message: str,
        source: str = "GENERAL",
    ) -> None:
        cls._log(
            logging.ERROR,
            message,
            source,
        )

    @classmethod
    def critical(
        cls,
        message: str,
        source: str = "GENERAL",
    ) -> None:
        cls._log(
            logging.CRITICAL,
            message,
            source,
        )

    @classmethod
    def exception(
        cls,
        message: str,
        source: str = "GENERAL",
    ) -> None:
        """
        Log the current exception and its full traceback.

        Call this from inside an except block.
        """

        cls._log(
            logging.ERROR,
            message,
            source,
            exc_info=True,
        )

    @classmethod
    def add_handler(cls, handler: logging.Handler) -> None:
        """Attach an additional output handler to the Streamhouse logger."""

        logger = cls._ensure_setup()

        if handler not in logger.handlers:
            logger.addHandler(handler)

    @classmethod
    def remove_handler(cls, handler: logging.Handler) -> None:
        """Detach and close an additional output handler."""

        logger = cls._ensure_setup()

        if handler in logger.handlers:
            logger.removeHandler(handler)
            handler.close()

    @classmethod
    def set_level(cls, level: int) -> None:
        """Set the minimum level for the Streamhouse logger and its handlers."""

        logger = cls._ensure_setup()
        logger.setLevel(level)

        for handler in logger.handlers:
            handler.setLevel(level)

    @classmethod
    def timer_start(cls, timer_name: str) -> None:
        """Start a named performance timer."""

        cls._timers[timer_name] = perf_counter()

        cls.debug(
            f'Timer started: "{timer_name}".',
            source="TIMER",
        )

    @classmethod
    def timer_end(
        cls,
        timer_name: str,
        source: str = "TIMER",
    ) -> float | None:
        """
        Stop a named timer, log its duration, and return the duration.
        """

        start_time = cls._timers.pop(
            timer_name,
            None,
        )

        if start_time is None:
            cls.warning(
                f'Timer "{timer_name}" was never started.',
                source=source,
            )
            return None

        elapsed_seconds = perf_counter() - start_time

        if elapsed_seconds < 1:
            elapsed_text = f"{elapsed_seconds * 1000:.0f} ms"
        else:
            elapsed_text = f"{elapsed_seconds:.3f} seconds"

        cls.info(
            f"{timer_name} complete ({elapsed_text}).",
            source=source,
        )

        return elapsed_seconds

    @classmethod
    def log_unhandled_exception(
        cls,
        exception_type: type[BaseException],
        exception_value: BaseException,
        exception_traceback,
    ) -> None:
        """Log exceptions that were not caught by the application."""

        if issubclass(
            exception_type,
            KeyboardInterrupt,
        ):
            sys.__excepthook__(
                exception_type,
                exception_value,
                exception_traceback,
            )
            return

        formatted_traceback = "".join(
            traceback.format_exception(
                exception_type,
                exception_value,
                exception_traceback,
            )
        )

        cls.critical(
            (
                "Unhandled application exception:\n"
                f"{formatted_traceback}"
            ),
            source="SYSTEM",
        )

    @classmethod
    def _write_startup_banner(cls) -> None:
        """Write one easy-to-spot startup banner."""

        separator = "=" * 60

        banner = (
            f"\n{separator}\n"
            " Streamhouse\n"
            "\n"
            f" Version : {VERSION}\n"
            f" Python  : {platform.python_version()}\n"
            f" Platform: {platform.system()} {platform.release()}\n"
            f" Started : {datetime.now():%Y-%m-%d %H:%M:%S}\n"
            f"{separator}"
        )

        cls.info(
            banner,
            source="SYSTEM",
        )
