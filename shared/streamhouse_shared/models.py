from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BufferedChatMessage:
    buffer_id: str
    message_id: str
    user_id: str
    user_name: str
    text: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class ExtractedMemory:
    text: str
    category: str
    key: str
    confidence: float
    evidence: tuple[dict[str, str], ...]


@dataclass(frozen=True, slots=True)
class ResponseMessage:
    request_id: str
    message_id: str
    user_id: str
    user_name: str
    text: str
    received_at: str
    memory_summary: str = ""
    memories: tuple[str, ...] = ()
    conversation_continuation: bool = False
    previous_ai_reply: str = ""
    response_expected: bool = False
    directed_at_ai: bool = False
    reply_to_ai: bool = False
    third_person_reference: bool = False
    addressed_to_other: bool = False


@dataclass(frozen=True, slots=True)
class ResponseDecision:
    request_id: str
    message_id: str
    user_id: str
    user_name: str
    source_text: str
    received_at: str
    decision: str
    reply: str
    reason: str
    confidence: float
    response_expected: bool = False
    engagement_type: str = "none"
    conversation_state: str = "unchanged"
    solicited: bool = False
