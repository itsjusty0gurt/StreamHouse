from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Iterable

from shared.streamhouse_shared.models import ResponseMessage


class ResponsePolicy:
    """Reply addressing and duplicate checks; never generates response text."""

    @staticmethod
    def requires_reply(text: str) -> bool:
        return " ".join(text.casefold().strip().split()).startswith("hey sally")

    @classmethod
    def message_requires_reply(cls, message: ResponseMessage) -> bool:
        return (
            cls.requires_reply(message.text)
            or message.response_expected
            or message.directed_at_ai
            or message.reply_to_ai
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
