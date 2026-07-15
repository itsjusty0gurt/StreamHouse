from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QObject, QRunnable, Signal, Slot

from ai.providers import OllamaProvider
from ai.response_engine import (
    ResponseDecision,
    ResponseDecisionEngine,
    ResponseMessage,
)


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
        endpoint: str,
        model: str,
        personality: str,
        allow_mild_profanity: bool,
        allow_strong_profanity: bool,
    ) -> None:
        super().__init__()
        self.messages = messages
        self.recent_chat = recent_chat
        self.endpoint = endpoint
        self.model = model
        self.personality = personality
        self.allow_mild_profanity = allow_mild_profanity
        self.allow_strong_profanity = allow_strong_profanity
        self.signals = ResponseDecisionSignals()

    @Slot()
    def run(self) -> None:
        try:
            decisions = ResponseDecisionEngine().decide(
                OllamaProvider(self.endpoint, self.model, timeout=60.0),
                self.messages,
                self.recent_chat,
                self.personality,
                self.allow_mild_profanity,
                self.allow_strong_profanity,
            )
        except Exception as error:
            self.signals.failed.emit(self.messages, str(error))
            return
        self.signals.completed.emit(ResponseBatchResult(decisions))
