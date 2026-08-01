from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from products.hub.streamhouse_hub.ai_client import StreamhouseAIClient, StreamhouseAIStatus


@dataclass(frozen=True, slots=True)
class StreamhouseAIHealthResult:
    status: StreamhouseAIStatus
    settings: dict


class StreamhouseAIHealthSignals(QObject):
    completed = Signal(object)


class StreamhouseAIHealthWorker(QRunnable):
    def __init__(self, endpoint: str) -> None:
        super().__init__()
        self.endpoint = endpoint
        self.signals = StreamhouseAIHealthSignals()

    @Slot()
    def run(self) -> None:
        client = StreamhouseAIClient(self.endpoint, timeout=3.0)
        status = client.ping()
        settings = {}
        if status.protocol_version:
            try:
                settings = client.get_settings()
            except OSError:
                settings = {}
        self.signals.completed.emit(StreamhouseAIHealthResult(status, settings))
