from __future__ import annotations

from dataclasses import asdict
from typing import Any, Iterable

from shared.streamhouse_shared.models import (
    BufferedChatMessage,
    ExtractedMemory,
    ResponseDecision,
    ResponseMessage,
)


PROTOCOL_VERSION = 2
LEGACY_PROTOCOL_VERSION = 1
PROTOCOL_HEADER = "X-Streamhouse-Protocol"
LEGACY_PROTOCOL_HEADER = "X-Sally-Protocol"


def response_message_to_dict(message: ResponseMessage) -> dict[str, Any]:
    value = asdict(message)
    value["memories"] = list(message.memories)
    return value


def response_message_from_dict(value: dict[str, Any]) -> ResponseMessage:
    return ResponseMessage(
        request_id=str(value.get("request_id", "")),
        message_id=str(value.get("message_id", "")),
        user_id=str(value.get("user_id", "")),
        user_name=str(value.get("user_name", "")),
        text=str(value.get("text", "")),
        received_at=str(value.get("received_at", "")),
        memory_summary=str(value.get("memory_summary", "")),
        memories=tuple(str(item) for item in value.get("memories", [])),
        conversation_continuation=bool(value.get("conversation_continuation", False)),
        previous_sally_reply=str(value.get("previous_sally_reply", "")),
        response_expected=bool(value.get("response_expected", False)),
        directed_at_sally=bool(value.get("directed_at_sally", False)),
        reply_to_sally=bool(value.get("reply_to_sally", False)),
        third_person_reference=bool(value.get("third_person_reference", False)),
        addressed_to_other=bool(value.get("addressed_to_other", False)),
    )


def response_decision_to_dict(decision: ResponseDecision) -> dict[str, Any]:
    return asdict(decision)


def response_decision_from_dict(value: dict[str, Any]) -> ResponseDecision:
    return ResponseDecision(
        request_id=str(value.get("request_id", "")),
        message_id=str(value.get("message_id", "")),
        user_id=str(value.get("user_id", "")),
        user_name=str(value.get("user_name", "")),
        source_text=str(value.get("source_text", "")),
        received_at=str(value.get("received_at", "")),
        decision=str(value.get("decision", "ignore")),
        reply=str(value.get("reply", "")),
        reason=str(value.get("reason", "")),
        confidence=float(value.get("confidence", 0.0)),
        response_expected=bool(value.get("response_expected", False)),
        engagement_type=str(value.get("engagement_type", "none")),
        conversation_state=str(value.get("conversation_state", "unchanged")),
        solicited=bool(value.get("solicited", False)),
    )


def buffered_message_to_dict(message: BufferedChatMessage) -> dict[str, Any]:
    return asdict(message)


def buffered_message_from_dict(value: dict[str, Any]) -> BufferedChatMessage:
    return BufferedChatMessage(
        buffer_id=str(value.get("buffer_id", "")),
        message_id=str(value.get("message_id", "")),
        user_id=str(value.get("user_id", "")),
        user_name=str(value.get("user_name", "")),
        text=str(value.get("text", "")),
        timestamp=str(value.get("timestamp", "")),
    )


def extracted_memory_to_dict(memory: ExtractedMemory) -> dict[str, Any]:
    value = asdict(memory)
    value["evidence"] = list(memory.evidence)
    return value


def extracted_memory_from_dict(value: dict[str, Any]) -> ExtractedMemory:
    evidence: Iterable[object] = value.get("evidence", [])
    return ExtractedMemory(
        text=str(value.get("text", "")),
        category=str(value.get("category", "General")),
        key=str(value.get("key", "")),
        confidence=float(value.get("confidence", 0.0)),
        evidence=tuple(
            {str(key): str(item) for key, item in row.items()}
            for row in evidence
            if isinstance(row, dict)
        ),
    )
