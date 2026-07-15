from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from ai.providers import OllamaProvider


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


class ResponseDecisionEngine:
    """Make short, grounded Twitch reply decisions for a batch of messages."""

    MAX_REPLY_LENGTH = 400
    RECENT_CHAT_LIMIT = 30

    def decide(
        self,
        provider: OllamaProvider,
        messages: Iterable[ResponseMessage],
        recent_chat: Iterable[dict[str, str]] = (),
        personality: str = "Warm, quick-witted, and conversational.",
        allow_mild_profanity: bool = False,
        allow_strong_profanity: bool = False,
    ) -> tuple[ResponseDecision, ...]:
        batch = tuple(messages)
        if not batch:
            return ()
        payload = provider.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are Sally, a warm, quick-witted Twitch stream "
                        "cohost. Decide whether to reply and draft brief chat "
                        "responses. Follow the supplied personality and language "
                        "limits. Return JSON only."
                    ),
                },
                {
                    "role": "user",
                    "content": self._prompt(
                        batch,
                        recent_chat,
                        personality,
                        allow_mild_profanity,
                        allow_strong_profanity,
                    ),
                },
            ],
            think=False,
        )
        response = payload.get("message", {})
        content = (
            str(response.get("content", ""))
            if isinstance(response, dict)
            else ""
        )
        parsed = self._parse_json(content)
        values = parsed.get("decisions", [])
        if not isinstance(values, list):
            raise ValueError("Local AI response omitted a decisions list.")
        by_id = {message.request_id: message for message in batch}
        decisions: dict[str, ResponseDecision] = {}
        for value in values:
            decision = self._validate(value, by_id)
            if decision is not None:
                decisions[decision.request_id] = decision
        return tuple(
            decisions.get(
                message.request_id,
                self._ignored(message, "Model omitted this message."),
            )
            for message in batch
        )

    @staticmethod
    def _prompt(
        messages: tuple[ResponseMessage, ...],
        recent_chat: Iterable[dict[str, str]],
        personality: str,
        allow_mild_profanity: bool,
        allow_strong_profanity: bool,
    ) -> str:
        inputs = [
            {
                "id": message.request_id,
                "viewer": message.user_name,
                "message": message.text,
                "approved_memory_summary": message.memory_summary,
                "approved_memories": list(message.memories),
            }
            for message in messages
        ]
        if allow_strong_profanity:
            language_rule = (
                "Strong profanity is permitted when it naturally matches chat, "
                "but never use slurs, hateful language, harassment, or sexual "
                "language aimed at a person."
            )
        elif allow_mild_profanity:
            language_rule = (
                "Occasional mild profanity is permitted; do not use strong "
                "profanity, slurs, hateful language, or targeted insults."
            )
        else:
            language_rule = "Do not use profanity or foul language."
        return f"""
For every input message, choose `reply` or `ignore`.

Any non-bot viewer message beginning with `hey sally` is an explicit public
invocation. Reply to it unless it is unsafe, abusive spam, or asks for something
Sally must not do. This command is available regardless of viewer role.

Sally's personality:
{personality}

Language rule:
{language_rule}

Reply when Sally is directly addressed, asked a genuine question, can add useful
context, or can naturally continue a conversation. A brief greeting may receive
a brief greeting when chat is quiet. Ignore spam, bait, repeated messages,
commands, statements that do not need Sally, and conversations clearly between
other viewers. Never claim an action happened when it did not. Do not mention
stored memories or that you are an AI. Use approved memories only when naturally
relevant. The recent-chat history includes Sally's own successfully sent replies;
use those entries when asked what Sally previously said. Do not repeat a joke or
canned line already present unless the viewer explicitly asks for a repeat. Keep
replies conversational, under 300 characters, normally one sentence, and never
include private or sensitive information.

Return exactly:
{{"decisions":[{{"id":"input-id","decision":"reply",\
"reply":"short response","reason":"brief reason","confidence":0.85}}]}}

Include one decision for every input ID. For `ignore`, reply must be empty.

Recent chat (RAM-only context):
{json.dumps(list(recent_chat)[-ResponseDecisionEngine.RECENT_CHAT_LIMIT:], ensure_ascii=False)}

Messages to decide:
{json.dumps(inputs, ensure_ascii=False)}
""".strip()

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        clean = content.strip()
        clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.I)
        clean = re.sub(r"\s*```$", "", clean)
        try:
            value = json.loads(clean)
        except json.JSONDecodeError:
            start = clean.find("{")
            end = clean.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("Local AI did not return valid reply JSON.")
            try:
                value = json.loads(clean[start : end + 1])
            except json.JSONDecodeError as error:
                raise ValueError(
                    "Local AI did not return valid reply JSON."
                ) from error
        if not isinstance(value, dict):
            raise ValueError("Local AI reply response must be a JSON object.")
        return value

    def _validate(
        self,
        value: object,
        messages: dict[str, ResponseMessage],
    ) -> ResponseDecision | None:
        if not isinstance(value, dict):
            return None
        request_id = str(value.get("id", ""))
        source = messages.get(request_id)
        if source is None:
            return None
        decision = str(value.get("decision", "ignore")).casefold()
        if decision not in {"reply", "ignore"}:
            decision = "ignore"
        reply = " ".join(str(value.get("reply", "")).split())
        if decision == "reply" and not reply:
            decision = "ignore"
        if decision == "ignore":
            reply = ""
        try:
            confidence = float(value.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        return ResponseDecision(
            request_id=request_id,
            message_id=source.message_id,
            user_id=source.user_id,
            user_name=source.user_name,
            source_text=source.text,
            received_at=source.received_at,
            decision=decision,
            reply=reply[: self.MAX_REPLY_LENGTH],
            reason=str(value.get("reason", "")).strip()[:300],
            confidence=min(max(confidence, 0.0), 1.0),
        )

    @staticmethod
    def _ignored(message: ResponseMessage, reason: str) -> ResponseDecision:
        return ResponseDecision(
            request_id=message.request_id,
            message_id=message.message_id,
            user_id=message.user_id,
            user_name=message.user_name,
            source_text=message.text,
            received_at=message.received_at,
            decision="ignore",
            reply="",
            reason=reason,
            confidence=0.0,
        )
