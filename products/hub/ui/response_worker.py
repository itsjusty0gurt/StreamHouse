from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from shared.streamhouse_shared.models import (
    ResponseDecision,
    ResponseMessage,
)
from products.hub.streamhouse_hub.ai_client import StreamhouseAIClient


@dataclass(frozen=True, slots=True)
class ResponseBatchResult:
    decisions: tuple[ResponseDecision, ...]


class ResponseDecisionSignals(QObject):
    completed = Signal(object)
    failed = Signal(object, str)


class ResponseDecisionWorker(QRunnable):
    def __init__(
        self,
        messages: tuple[ResponseMessage, ...],
        recent_chat: tuple[dict[str, str], ...],
        companion_endpoint: str,
        endpoint: str,
        model: str,
        personality: str,
        allow_mild_profanity: bool,
        allow_strong_profanity: bool,
    ) -> None:
        super().__init__()
        self.messages = messages
        self.recent_chat = recent_chat
        self.companion_endpoint = companion_endpoint
        self.endpoint = endpoint
        self.model = model
        self.personality = personality
        self.allow_mild_profanity = allow_mild_profanity
        self.allow_strong_profanity = allow_strong_profanity
        self.signals = ResponseDecisionSignals()

    @Slot()
    def run(self) -> None:
        try:
            decisions = StreamhouseAIClient(
                self.companion_endpoint,
                timeout=65.0,
            ).decide(
                self.messages,
                self.recent_chat,
                self.endpoint,
                self.model,
                self.personality,
                self.allow_mild_profanity,
                self.allow_strong_profanity,
            )
        except Exception as error:
            self.signals.failed.emit(self.messages, str(error))
            return
        self.signals.completed.emit(ResponseBatchResult(decisions))
