from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from ai.memory_extractor import BufferedChatMessage, ExtractedMemory, MemoryExtractor
from ai.providers import OllamaProvider


@dataclass(frozen=True, slots=True)
class MemoryExtractionResult:
    user_id: str
    user_name: str
    buffer_ids: tuple[str, ...]
    proposals: tuple[ExtractedMemory, ...]


class MemoryExtractionSignals(QObject):
    completed = Signal(object)
    failed = Signal(str, object, str)


class MemoryExtractionWorker(QRunnable):
    def __init__(
        self,
        user_id: str,
        user_name: str,
        messages: tuple[BufferedChatMessage, ...],
        existing_memories: tuple[str, ...],
        endpoint: str,
        model: str,
    ) -> None:
        super().__init__()
        self.user_id = user_id
        self.user_name = user_name
        self.messages = messages
        self.existing_memories = existing_memories
        self.endpoint = endpoint
        self.model = model
        self.signals = MemoryExtractionSignals()

    @Slot()
    def run(self) -> None:
        buffer_ids = tuple(message.buffer_id for message in self.messages)
        try:
            provider = OllamaProvider(
                self.endpoint,
                self.model,
                timeout=120.0,
            )
            proposals = MemoryExtractor().extract(
                provider,
                self.user_name,
                self.messages,
                self.existing_memories,
            )
        except Exception as error:
            self.signals.failed.emit(
                self.user_id,
                buffer_ids,
                str(error),
            )
            return
        self.signals.completed.emit(
            MemoryExtractionResult(
                user_id=self.user_id,
                user_name=self.user_name,
                buffer_ids=buffer_ids,
                proposals=proposals,
            )
        )
