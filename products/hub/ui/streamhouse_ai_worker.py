from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from products.hub.streamhouse_hub.ai_client import StreamhouseAIClient, StreamhouseAIStatus


@dataclass(frozen=True, slots=True)
class StreamhouseAIHealthResult:
    status: StreamhouseAIStatus
    settings: dict
    generation: int = 0


class StreamhouseAIHealthSignals(QObject):
    completed = Signal(object)


class StreamhouseAIHealthWorker(QRunnable):
    def __init__(self, endpoint: str, generation: int = 0) -> None:
        super().__init__()
        self.endpoint = endpoint
        self.generation = generation
        self.signals = StreamhouseAIHealthSignals()

    @Slot()
    def run(self) -> None:
        client = StreamhouseAIClient(self.endpoint, timeout=3.0)
        status = client.ping()
        self.signals.completed.emit(
            StreamhouseAIHealthResult(status, {}, self.generation)
        )
