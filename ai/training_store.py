from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sally_shared.models import ResponseDecision
from core.json_store import atomic_write_json, load_json_with_backup
from core.paths import user_data_root


class TrainingStore:
    """Local, pseudonymous, opt-in examples for future classifier training."""

    MAX_EXAMPLES = 2_000
    PENDING_RETENTION_DAYS = 30
    LABELS = (
        "direct",
        "conversation",
        "interjection",
        "ignore",
        "conversation_end",
    )

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_root() / "training" / "examples.json"
        self.salt = secrets.token_hex(32)
        self.examples: list[dict[str, object]] = []

    def load(self) -> None:
        if not self.path.exists():
            return
        payload = load_json_with_backup(self.path)
        if not isinstance(payload, dict):
            raise ValueError("Training data must contain a JSON object.")
        salt = payload.get("salt")
        if isinstance(salt, str) and len(salt) >= 32:
            self.salt = salt
        values = payload.get("examples", [])
        self.examples = [
            value
            for value in values
            if isinstance(value, dict)
            and isinstance(value.get("id"), str)
            and isinstance(value.get("message"), str)
        ][-self.MAX_EXAMPLES :]
        self.prune(save=False)

    def save(self) -> None:
        atomic_write_json(
            self.path,
            {"version": 1, "salt": self.salt, "examples": self.examples},
        )

    def participant_hash(self, user_id: str) -> str:
        return hashlib.sha256(
            f"{self.salt}:{user_id}".encode("utf-8")
        ).hexdigest()

    @staticmethod
    def sanitize(text: str) -> str:
        clean = " ".join(text.strip().split())[:500]
        clean = re.sub(r"https?://\S+", "<url>", clean, flags=re.IGNORECASE)
        return re.sub(r"(?<!\w)@[a-z0-9_]+", "@<user>", clean, flags=re.I)

    def capture(self, user_id: str, decision: ResponseDecision) -> str:
        example_id = uuid4().hex
        self.examples.append(
            {
                "id": example_id,
                "participant": self.participant_hash(user_id),
                "message": self.sanitize(decision.source_text),
                "model_label": (
                    "conversation_end"
                    if decision.conversation_state == "end"
                    else decision.engagement_type
                    if decision.engagement_type != "none"
                    else decision.decision
                ),
                "label": "",
                "decision": decision.decision,
                "conversation_state": decision.conversation_state,
                "confidence": round(decision.confidence, 4),
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "reviewed": False,
            }
        )
        self.examples = self.examples[-self.MAX_EXAMPLES :]
        self.save()
        return example_id

    def label(self, example_id: str, label: str) -> bool:
        if label not in self.LABELS:
            raise ValueError("Unknown training label.")
        for example in self.examples:
            if example.get("id") == example_id:
                example["label"] = label
                example["reviewed"] = True
                self.save()
                return True
        return False

    def delete(self, example_id: str) -> bool:
        before = len(self.examples)
        self.examples = [
            item for item in self.examples if item.get("id") != example_id
        ]
        if len(self.examples) == before:
            return False
        self.save()
        return True

    def delete_participant(self, user_id: str) -> int:
        participant = self.participant_hash(user_id)
        before = len(self.examples)
        self.examples = [
            item
            for item in self.examples
            if item.get("participant") != participant
        ]
        removed = before - len(self.examples)
        if removed:
            self.save()
        return removed

    def clear(self) -> int:
        count = len(self.examples)
        self.examples.clear()
        self.save()
        return count

    def prune(self, *, save: bool = True) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=self.PENDING_RETENTION_DAYS
        )
        kept: list[dict[str, object]] = []
        for example in self.examples:
            if bool(example.get("reviewed")):
                kept.append(example)
                continue
            try:
                captured = datetime.fromisoformat(
                    str(example.get("captured_at", "")).replace("Z", "+00:00")
                )
            except ValueError:
                continue
            if captured >= cutoff:
                kept.append(example)
        removed = len(self.examples) - len(kept)
        self.examples = kept[-self.MAX_EXAMPLES :]
        if removed and save:
            self.save()
        return removed
