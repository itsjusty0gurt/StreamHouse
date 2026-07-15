from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from ai.providers import OllamaProvider


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


class MemoryExtractor:
    """Extract conservative, evidence-backed memory proposals from chat."""

    CATEGORIES = frozenset(
        {
            "Preference",
            "Personal",
            "Project",
            "Relationship",
            "Community",
            "Goal",
            "General",
        }
    )
    MIN_CONFIDENCE = 0.55
    MAX_PROPOSALS = 3
    SENSITIVE_PATTERN = re.compile(
        r"\b(?:password|passcode|home address|street address|phone number|"
        r"e-?mail address|bank account|credit card|social security|income|debt|"
        r"diagnos(?:is|ed)|medical condition|medication|religion|religious|"
        r"political party|sexuality|sexual orientation)\b",
        re.I,
    )

    def extract(
        self,
        provider: OllamaProvider,
        user_name: str,
        messages: Iterable[BufferedChatMessage],
        existing_memories: Iterable[str] = (),
    ) -> tuple[ExtractedMemory, ...]:
        batch = tuple(messages)
        if not batch:
            return ()
        prompt = self._prompt(user_name, batch, existing_memories)
        payload = provider.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You extract durable viewer memories from Twitch chat. "
                        "Return JSON only and follow the supplied privacy rules."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            think=False,
        )
        message = payload.get("message", {})
        content = (
            str(message.get("content", ""))
            if isinstance(message, dict)
            else ""
        )
        parsed = self._parse_json(content)
        candidates = parsed.get("memories", [])
        if not isinstance(candidates, list):
            raise ValueError("Local AI memory response omitted a memories list.")
        by_id = {item.buffer_id: item for item in batch}
        proposals: list[ExtractedMemory] = []
        for candidate in candidates[: self.MAX_PROPOSALS]:
            proposal = self._validate_candidate(candidate, by_id)
            if proposal is not None:
                proposals.append(proposal)
        return tuple(proposals)

    @staticmethod
    def _prompt(
        user_name: str,
        messages: tuple[BufferedChatMessage, ...],
        existing_memories: Iterable[str],
    ) -> str:
        chat = [
            {
                "id": item.buffer_id,
                "timestamp": item.timestamp,
                "text": item.text,
            }
            for item in messages
        ]
        existing = [str(text)[:500] for text in existing_memories][:20]
        return f"""
Viewer: {user_name}

Identify only durable facts the viewer explicitly stated about themselves.
Good memories include stable preferences, ongoing projects, relationships,
community context, or goals likely to help in a future conversation.

Do not infer anything. Ignore greetings, jokes, commands, temporary moods,
one-off stream reactions, usernames, and facts about other people. Do not save
passwords, addresses, financial details, medical details, political or religious
beliefs, sexual information, or other highly sensitive data. Avoid duplicates of
the existing memories. It is correct to return an empty list.

Return exactly one JSON object in this shape:
{{"memories":[{{"text":"Viewer explicitly likes puzzle games",\
"category":"Preference","key":"favorite-game-genre",\
"confidence":0.85,"evidence_ids":["message-id"]}}]}}

Allowed categories: Preference, Personal, Project, Relationship, Community,
Goal, General. Use a stable lowercase key for facts that could later change.
Every proposal must cite one or more IDs from the supplied chat messages.
Maximum {MemoryExtractor.MAX_PROPOSALS} proposals.

Existing approved memories:
{json.dumps(existing, ensure_ascii=False)}

Chat messages:
{json.dumps(chat, ensure_ascii=False)}
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
                raise ValueError("Local AI did not return valid memory JSON.")
            try:
                value = json.loads(clean[start : end + 1])
            except json.JSONDecodeError as error:
                raise ValueError(
                    "Local AI did not return valid memory JSON."
                ) from error
        if not isinstance(value, dict):
            raise ValueError("Local AI memory response must be a JSON object.")
        return value

    def _validate_candidate(
        self,
        candidate: object,
        messages: dict[str, BufferedChatMessage],
    ) -> ExtractedMemory | None:
        if not isinstance(candidate, dict):
            return None
        text = str(candidate.get("text", "")).strip()[:1000]
        category = str(candidate.get("category", "General")).strip().title()
        key = str(candidate.get("key", "")).strip().casefold()[:100]
        try:
            confidence = float(candidate.get("confidence", 0.0))
        except (TypeError, ValueError):
            return None
        if (
            len(text) < 8
            or category not in self.CATEGORIES
            or confidence < self.MIN_CONFIDENCE
            or self.SENSITIVE_PATTERN.search(f"{key} {text}")
        ):
            return None
        evidence_ids = candidate.get("evidence_ids", [])
        if not isinstance(evidence_ids, list):
            return None
        evidence = []
        for buffer_id in dict.fromkeys(str(value) for value in evidence_ids):
            source = messages.get(buffer_id)
            if source is None:
                continue
            evidence.append(
                {
                    "text": source.text,
                    "timestamp": source.timestamp,
                    "message_id": source.message_id,
                }
            )
        if not evidence:
            return None
        return ExtractedMemory(
            text=text,
            category=category,
            key=key,
            confidence=min(max(confidence, 0.0), 1.0),
            evidence=tuple(evidence),
        )
