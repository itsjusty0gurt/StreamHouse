from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QRunnable, Signal, Slot


class AdsActionSignals(QObject):
    completed = Signal(str, object)
    failed = Signal(str, str)


class AdsActionWorker(QRunnable):
    """Run one Twitch Ads service action away from the Qt UI thread."""

    def __init__(self, action: str, operation: Callable[[], dict]) -> None:
        super().__init__()
        self.action = action
        self.operation = operation
        self.signals = AdsActionSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.operation()
        except Exception as error:
            self.signals.failed.emit(self.action, str(error))
            return
        self.signals.completed.emit(self.action, result)
