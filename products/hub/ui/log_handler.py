from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal


class LogEmitter(QObject):
    """Carries formatted log messages safely onto Qt's UI thread."""

    message_ready = Signal(str)


class QtLogHandler(logging.Handler):
    """Forward Python log records to a Qt signal."""

    def __init__(self) -> None:
        super().__init__()
        self.emitter = LogEmitter()

    def emit(self, record: logging.LogRecord) -> None:
        if not hasattr(record, "source"):
            record.source = "GENERAL"

        try:
            message = self.format(record)
            self.emitter.message_ready.emit(message)
        except Exception:
            self.handleError(record)
