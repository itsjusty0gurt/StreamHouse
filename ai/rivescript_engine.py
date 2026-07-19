from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from rivescript import RiveScript

from core.json_store import atomic_write_json, load_json_with_backup
from core.paths import user_data_root


class RiveScriptRuleStore:
    """Local, streamer-authored deterministic response rules."""

    MAX_RULES = 500

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_root() / "ai" / "rivescript_rules.json"
        self.rules: list[dict[str, object]] = []

    def load(self) -> None:
        if not self.path.exists():
            return
        payload = load_json_with_backup(self.path)
        if not isinstance(payload, dict):
            raise ValueError("RiveScript rules must contain a JSON object.")
        self.rules = [
            self._validated_rule(value)
            for value in payload.get("rules", [])
            if isinstance(value, dict)
        ][-self.MAX_RULES :]

    def save(self) -> None:
        atomic_write_json(
            self.path,
            {"version": 1, "rules": self.rules[-self.MAX_RULES :]},
        )

    def add(self, trigger: str, reply: str, *, name: str = "") -> str:
        clean_trigger = self.clean_trigger(trigger)
        clean_reply = self.clean_reply(reply)
        self._ensure_unique_trigger(clean_trigger)
        self._validate_script(clean_trigger, clean_reply)
        rule_id = uuid4().hex
        self.rules.append(
            {
                "id": rule_id,
                "name": self.clean_name(name) or clean_trigger[:60],
                "trigger": clean_trigger,
                "reply": clean_reply,
                "enabled": True,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self.rules = self.rules[-self.MAX_RULES :]
        self.save()
        return rule_id

    def update(
        self,
        rule_id: str,
        *,
        trigger: str,
        reply: str,
        name: str,
    ) -> bool:
        clean_trigger = self.clean_trigger(trigger)
        clean_reply = self.clean_reply(reply)
        self._ensure_unique_trigger(clean_trigger, excluding=rule_id)
        self._validate_script(clean_trigger, clean_reply)
        for rule in self.rules:
            if rule.get("id") != rule_id:
                continue
            rule.update(
                {
                    "name": self.clean_name(name) or clean_trigger[:60],
                    "trigger": clean_trigger,
                    "reply": clean_reply,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            self.save()
            return True
        return False

    def set_enabled(self, rule_id: str, enabled: bool) -> bool:
        for rule in self.rules:
            if rule.get("id") == rule_id:
                rule["enabled"] = bool(enabled)
                rule["updated_at"] = datetime.now(timezone.utc).isoformat()
                self.save()
                return True
        return False

    def delete(self, rule_id: str) -> bool:
        before = len(self.rules)
        self.rules = [rule for rule in self.rules if rule.get("id") != rule_id]
        if len(self.rules) == before:
            return False
        self.save()
        return True

    def get(self, rule_id: str) -> dict[str, object] | None:
        return next(
            (rule for rule in self.rules if rule.get("id") == rule_id),
            None,
        )

    @staticmethod
    def clean_trigger(trigger: str) -> str:
        clean = " ".join(str(trigger).casefold().strip().split())[:300]
        if not clean:
            raise ValueError("A RiveScript trigger is required.")
        if any(character in clean for character in "\r\n"):
            raise ValueError("RiveScript triggers must use one line.")
        if clean.startswith(("+", "-", "!", ">", "<")):
            raise ValueError("Enter the trigger only, without a RiveScript command.")
        return clean

    @staticmethod
    def clean_reply(reply: str) -> str:
        clean = " ".join(str(reply).strip().split())[:500]
        if not clean:
            raise ValueError("A RiveScript reply is required.")
        return clean

    @staticmethod
    def clean_name(name: str) -> str:
        return " ".join(str(name).strip().split())[:100]

    @staticmethod
    def suggest_trigger(message: str) -> str:
        clean = re.sub(r"https?://\S+", "", message.casefold())
        clean = re.sub(r"(?<!\w)@[a-z0-9_]+", "", clean)
        clean = re.sub(r"[^a-z0-9*#_\s]", " ", clean)
        return " ".join(clean.split())[:300]

    def _ensure_unique_trigger(
        self, trigger: str, *, excluding: str = ""
    ) -> None:
        if any(
            rule.get("id") != excluding
            and str(rule.get("trigger", "")).casefold() == trigger.casefold()
            for rule in self.rules
        ):
            raise ValueError("A rule with that trigger already exists.")

    @staticmethod
    def _validate_script(trigger: str, reply: str) -> None:
        engine = RiveScript(utf8=True, strict=True)
        engine.stream(f"! version = 2.0\n+ {trigger}\n- {reply}")
        engine.sort_replies()

    @classmethod
    def _validated_rule(cls, value: dict[str, object]) -> dict[str, object]:
        trigger = cls.clean_trigger(str(value.get("trigger", "")))
        reply = cls.clean_reply(str(value.get("reply", "")))
        cls._validate_script(trigger, reply)
        return {
            "id": str(value.get("id", "")) or uuid4().hex,
            "name": cls.clean_name(str(value.get("name", ""))) or trigger[:60],
            "trigger": trigger,
            "reply": reply,
            "enabled": bool(value.get("enabled", True)),
            "created_at": str(value.get("created_at", "")),
            "updated_at": str(value.get("updated_at", "")),
        }


class SallyRiveScriptEngine:
    NO_MATCH_PREFIX = "[ERR: No Reply Matched]"

    def __init__(self, store: RiveScriptRuleStore) -> None:
        self.store = store
        self._engine = RiveScript(utf8=True, strict=True)
        self._trigger_to_rule: dict[str, dict[str, object]] = {}
        self.rebuild()

    def rebuild(self) -> None:
        engine = RiveScript(utf8=True, strict=True)
        trigger_to_rule: dict[str, dict[str, object]] = {}
        scripts: list[str] = ["! version = 2.0"]
        for rule in self.store.rules:
            if not bool(rule.get("enabled", True)):
                continue
            trigger = str(rule.get("trigger", ""))
            reply = str(rule.get("reply", ""))
            scripts.extend((f"+ {trigger}", f"- {reply}"))
            trigger_to_rule[trigger] = rule
        if len(scripts) > 1:
            engine.stream("\n".join(scripts))
            engine.sort_replies()
        self._engine = engine
        self._trigger_to_rule = trigger_to_rule

    def match(self, user_id: str, message: str) -> tuple[str, str] | None:
        if not self._trigger_to_rule:
            return None
        reply = self._engine.reply(user_id or "anonymous", message)
        if not reply or reply.startswith("[ERR:"):
            return None
        trigger = self._engine.last_match(user_id or "anonymous")
        rule = self._trigger_to_rule.get(trigger)
        if rule is None:
            return None
        return str(rule.get("id", "")), reply[:500]
