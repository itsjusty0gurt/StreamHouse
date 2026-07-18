from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
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
    conversation_continuation: bool = False
    previous_sally_reply: str = ""
    response_expected: bool = False
    directed_at_sally: bool = False
    reply_to_sally: bool = False
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
        ordered = tuple(
            decisions.get(
                message.request_id,
                self._ignored(message, "Model omitted this message."),
            )
            for message in batch
        )
        recent_replies = [
            str(item.get("message", ""))
            for item in recent_chat
            if str(item.get("speaker", "")).casefold() == "sally"
        ][-10:]
        accepted_replies: list[str] = []
        retry_messages: list[ResponseMessage] = []
        retry_reasons: dict[str, str] = {}
        for message, decision in zip(batch, ordered):
            if decision.decision == "reply" and self._is_duplicate_reply(
                decision.reply, recent_replies + accepted_replies
            ):
                retry_messages.append(message)
                retry_reasons[message.request_id] = "Draft repeated a recent reply."
            elif self.message_requires_reply(message) and decision.decision != "reply":
                retry_messages.append(message)
                retry_reasons[message.request_id] = "Required response was ignored."
            elif decision.decision == "reply":
                accepted_replies.append(decision.reply)

        replacements: dict[str, ResponseDecision] = {}
        if retry_messages:
            try:
                replacements = self._retry_replies(
                    provider,
                    tuple(retry_messages),
                    recent_replies + accepted_replies,
                    personality,
                    allow_mild_profanity,
                    allow_strong_profanity,
                )
            except Exception:
                # A focused retry is best-effort; direct invocations still get
                # the deterministic fallback below.
                replacements = {}

        final: list[ResponseDecision] = []
        used_replies = list(recent_replies)
        for message, decision in zip(batch, ordered):
            if message.request_id in retry_reasons:
                candidate = replacements.get(message.request_id)
                if (
                    candidate is not None
                    and candidate.decision == "reply"
                    and not self._is_duplicate_reply(candidate.reply, used_replies)
                ):
                    decision = candidate
                elif self.message_requires_reply(message):
                    decision = self._fallback_reply(message, used_replies)
                else:
                    decision = self._ignored(message, retry_reasons[message.request_id])
            if decision.decision == "reply":
                used_replies.append(decision.reply)
            final.append(decision)
        return tuple(final)

    def _retry_replies(
        self,
        provider: OllamaProvider,
        messages: tuple[ResponseMessage, ...],
        blocked_replies: list[str],
        personality: str,
        allow_mild_profanity: bool,
        allow_strong_profanity: bool,
    ) -> dict[str, ResponseDecision]:
        inputs = [
            {
                "id": message.request_id,
                "viewer": message.user_name,
                "message": message.text,
                "conversation_continuation": message.conversation_continuation,
                "previous_sally_reply": message.previous_sally_reply,
                "response_expected": message.response_expected,
                "directed_at_sally": message.directed_at_sally,
                "reply_to_sally": message.reply_to_sally,
                "third_person_reference": message.third_person_reference,
                "addressed_to_other": message.addressed_to_other,
            }
            for message in messages
        ]
        language = (
            "Strong profanity is allowed, but never slurs or hateful language."
            if allow_strong_profanity
            else "Mild profanity is allowed, but no strong profanity."
            if allow_mild_profanity
            else "Do not use profanity."
        )
        payload = provider.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are Sally, a concise Twitch cohost. Produce fresh "
                        "replies for messages whose first draft was missing or repetitive. "
                        "Return JSON only."
                    ),
                },
                {
                    "role": "user",
                    "content": f"""
Reply to every supplied message. Do not ignore any input. Write a new, specific
response that naturally continues that viewer's message. Do not reuse or closely
paraphrase a blocked reply. Keep each reply under 300 characters and normally one
sentence.

Personality: {personality}
Language: {language}
Blocked recent replies: {json.dumps(blocked_replies[-10:], ensure_ascii=False)}
Messages: {json.dumps(inputs, ensure_ascii=False)}

Return exactly:
{{"decisions":[{{"id":"input-id","decision":"reply",\
"reply":"fresh response","reason":"required retry","confidence":0.9,\
"engagement_type":"direct","conversation_state":"continue"}}]}}
""".strip(),
                },
            ],
            think=False,
        )
        response = payload.get("message", {})
        content = str(response.get("content", "")) if isinstance(response, dict) else ""
        parsed = self._parse_json(content)
        values = parsed.get("decisions", [])
        if not isinstance(values, list):
            return {}
        by_id = {message.request_id: message for message in messages}
        return {
            decision.request_id: decision
            for value in values
            if (decision := self._validate(value, by_id)) is not None
        }

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
    def _fallback_reply(
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
            (
                option for option in options
                if not cls._is_duplicate_reply(option, prior_replies)
            ),
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
                "conversation"
                if message.conversation_continuation
                else "direct"
            ),
            conversation_state="continue",
            solicited=True,
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
                "conversation_continuation": message.conversation_continuation,
                "previous_sally_reply": message.previous_sally_reply,
                "response_expected": message.response_expected,
                "directed_at_sally": message.directed_at_sally,
                "reply_to_sally": message.reply_to_sally,
                "third_person_reference": message.third_person_reference,
                "addressed_to_other": message.addressed_to_other,
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
Sally must not do. This command is available regardless of viewer role. If a
viewer repeats an invocation because no Sally reply followed the earlier copy,
answer the newest invocation instead of treating it as spam.

`conversation_continuation` means Sally recently replied to this same viewer.
Use `previous_sally_reply` to understand their next turn. If `response_expected`
is true, the viewer is answering Sally's question or asking a follow-up question;
reply unless doing so would be unsafe. They do not need to say `hey sally` again.

`directed_at_sally` means the message names or mentions Sally.
`reply_to_sally` means Twitch identifies it as a direct reply to Sally's message.
Both require a response unless unsafe. Also infer an implicit address from recent
chat: a viewer may clearly be speaking to Sally through wording and turn order
without using her name. Do not require a magic phrase.

`third_person_reference` means the viewer is discussing Sally as `she`/`her`
rather than speaking to her. `addressed_to_other` means Twitch metadata or clear
wording points to another viewer. These normally end or suspend Sally's active
turn. Do not reply merely because Sally is the subject of viewer-to-viewer chat;
at most classify a genuinely valuable contribution as a rare interjection.

Classify each decision with `engagement_type`: `direct` when the viewer is
addressing Sally, `conversation` for an active Sally/viewer exchange,
`interjection` only when Sally is voluntarily joining a discussion, or `none`
when ignored. Interjections must be rare, relevant, specific to the current
discussion, and add genuine humor or useful context. Do not interject into every
message, greetings between viewers, short acknowledgements, commands, arguments,
or sensitive/personal conversations.

Do not answer every line in an active conversation and do not end every reply
with a question. Ask a follow-up only when it genuinely improves the exchange.
Short commentary, third-person discussion of Sally, and messages aimed at another
viewer usually need no response.

Also return `conversation_state`: `start` when a new Sally conversation begins,
`continue` while the viewer is still engaging Sally, `end` when they say goodbye,
thank Sally and close the exchange, change clearly to another person/topic, or
otherwise finish the interaction, and `unchanged` when no state change is clear.
An ending may be marked even when the correct decision is `ignore`.

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
"reply":"short response","reason":"brief reason","confidence":0.85,\
"engagement_type":"direct|conversation|interjection|none",\
"conversation_state":"start|continue|end|unchanged"}}]}}

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
        engagement_type = str(
            value.get("engagement_type", "")
        ).casefold()
        if engagement_type not in {
            "direct", "conversation", "interjection", "none"
        }:
            engagement_type = (
                "direct"
                if source.directed_at_sally or source.reply_to_sally
                else "conversation"
                if source.conversation_continuation
                else "interjection"
                if decision == "reply"
                else "none"
            )
        if decision == "ignore":
            engagement_type = "none"
        conversation_state = str(
            value.get("conversation_state", "unchanged")
        ).casefold()
        if conversation_state not in {
            "start", "continue", "end", "unchanged"
        }:
            conversation_state = "unchanged"
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
            response_expected=source.response_expected,
            engagement_type=engagement_type,
            conversation_state=conversation_state,
            solicited=self.message_requires_reply(source),
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
            response_expected=message.response_expected,
            engagement_type="none",
            conversation_state="unchanged",
            solicited=ResponseDecisionEngine.message_requires_reply(message),
        )
