from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from shared.streamhouse_shared.models import BufferedChatMessage, ExtractedMemory
from products.hub.streamhouse_hub.ai_client import StreamhouseAIClient


@dataclass(frozen=True, slots=True)
class MemoryExtractionResult:
    user_id: str
    user_name: str
    buffer_ids: tuple[str, ...]
    proposals: tuple[ExtractedMemory, ...]
    generation: int = 0


class MemoryExtractionSignals(QObject):
    completed = Signal(object)
    failed = Signal(str, object, object)


class MemoryExtractionWorker(QRunnable):
    def __init__(
        self,
        user_id: str,
        user_name: str,
        messages: tuple[BufferedChatMessage, ...],
        existing_memories: tuple[str, ...],
        ai_endpoint: str,
        endpoint: str,
        model: str,
        generation: int = 0,
    ) -> None:
        super().__init__()
        self.user_id = user_id
        self.user_name = user_name
        self.messages = messages
        self.existing_memories = existing_memories
        self.ai_endpoint = ai_endpoint
        self.endpoint = endpoint
        self.model = model
        self.generation = generation
        self.signals = MemoryExtractionSignals()

    @Slot()
    def run(self) -> None:
        buffer_ids = tuple(message.buffer_id for message in self.messages)
        try:
            proposals = StreamhouseAIClient(
                self.ai_endpoint,
                timeout=125.0,
            ).extract_memories(
                self.user_name,
                self.messages,
                self.existing_memories,
                self.endpoint,
                self.model,
            )
        except Exception as error:
            self.signals.failed.emit(
                self.user_id,
                (buffer_ids, self.generation),
                error,
            )
            return
        self.signals.completed.emit(
            MemoryExtractionResult(
                user_id=self.user_id,
                user_name=self.user_name,
                buffer_ids=buffer_ids,
                proposals=proposals,
                generation=self.generation,
            )
        )
