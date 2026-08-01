from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable

from shared.streamhouse_shared.models import ResponseDecision, ResponseMessage


class ResponsePolicy:
    """Tiny bot-side guarantees that do not require an AI backend."""

    @staticmethod
    def requires_reply(text: str) -> bool:
        return " ".join(text.casefold().strip().split()).startswith("hey sally")

    @classmethod
    def message_requires_reply(cls, message: ResponseMessage) -> bool:
        return (
            cls.requires_reply(message.text)
            or message.response_expected
            or message.directed_at_sally
            or message.reply_to_sally
        )

    @staticmethod
    def _normalized_reply(text: str) -> str:
        return " ".join(re.findall(r"[a-z0-9']+", text.casefold()))

    @classmethod
    def _is_duplicate_reply(cls, reply: str, prior_replies: Iterable[str]) -> bool:
        normalized = cls._normalized_reply(reply)
        if not normalized:
            return False
        for prior in prior_replies:
            other = cls._normalized_reply(prior)
            if not other:
                continue
            if normalized == other:
                return True
            if min(len(normalized), len(other)) >= 20 and SequenceMatcher(
                None, normalized, other
            ).ratio() >= 0.86:
                return True
        return False

    @classmethod
    def fallback_reply(
        cls,
        message: ResponseMessage,
        prior_replies: Iterable[str],
    ) -> ResponseDecision:
        options = (
            f"@{message.user_name}, I'm here—tell me a little more.",
            f"@{message.user_name}, I caught that. What do you want to dig into?",
            f"@{message.user_name}, you've got me—what's on your mind?",
        )
        reply = next(
            (item for item in options if not cls._is_duplicate_reply(item, prior_replies)),
            options[0],
        )
        return ResponseDecision(
            request_id=message.request_id,
            message_id=message.message_id,
            user_id=message.user_id,
            user_name=message.user_name,
            source_text=message.text,
            received_at=message.received_at,
            decision="reply",
            reply=reply,
            reason="Required response needed a fallback reply.",
            confidence=1.0,
            response_expected=message.response_expected,
            engagement_type=(
                "conversation" if message.conversation_continuation else "direct"
            ),
            conversation_state="continue",
            solicited=True,
        )
