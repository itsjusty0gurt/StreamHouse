from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from sally_companion.client import CompanionClient, CompanionStatus


@dataclass(frozen=True, slots=True)
class CompanionHealthResult:
    status: CompanionStatus
    settings: dict


class CompanionHealthSignals(QObject):
    completed = Signal(object)


class CompanionHealthWorker(QRunnable):
    def __init__(self, endpoint: str) -> None:
        super().__init__()
        self.endpoint = endpoint
        self.signals = CompanionHealthSignals()

    @Slot()
    def run(self) -> None:
        client = CompanionClient(self.endpoint, timeout=3.0)
        status = client.ping()
        settings = {}
        if status.protocol_version:
            try:
                settings = client.get_settings()
            except OSError:
                settings = {}
        self.signals.completed.emit(CompanionHealthResult(status, settings))
