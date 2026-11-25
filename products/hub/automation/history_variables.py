"""Bounded, session-only views of already materialized execution Variables."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from itertools import islice
import re
from typing import Iterable, Mapping

from products.hub.automation.variable_registry import VARIABLE_NAME_PATTERN, VariableSnapshot
from shared.streamhouse_runtime.redaction import is_secret_key, redact_secret_text


MAX_VARIABLES = 96
MAX_VALUE_CHARS = 2_048
MAX_SNAPSHOT_CHARS = 16_384
_MAX_SCAN = 256
_MAX_PROTECTED = 32
_PRIVATE_NAMES = frozenset({
    "chat.message", "command.data", "command.target", "event.input",
    "keyword.after", "keyword.before", "keyword.match", "keyword.message",
    "subscription.message", "channel_points.user_input", "event.request_text",
    "event.resolution_text",
})
_PRIVATE_FIELDS = frozenset({"payload", "raw_event", "raw_payload", "http_body"})
_SECRET_WORDS = frozenset({
    "authorization", "authentication", "credential", "credentials", "oauth",
    "password", "secret", "token", "cookie", "apikey",
})
_SECRET_COMPOUNDS = ("_api_key_", "_relay_key_", "_private_key_", "_auth_header_", "_auth_code_")
_BARE_AUTH = re.compile(r"(?i)^\s*(?:bearer|oauth|basic)\s+\S+")


def _protected_name(name: str) -> bool:
    if name in _PRIVATE_NAMES:
        return True
    for part in name.split("."):
        key = part.replace("-", "_")
        if (key in _PRIVATE_FIELDS or key.endswith("_http_body")
                or is_secret_key(key)
                or _SECRET_WORDS.intersection(key.split("_"))
                or any(piece in f"_{key}_" for piece in _SECRET_COMPOUNDS)):
            return True
    return False


def _fingerprint(value: str) -> bytes:
    return sha256(value.encode("utf-8", errors="surrogatepass")).digest()


@dataclass(frozen=True, slots=True)
class HistoryVariable:
    name: str
    source: str
    state: str
    value: str | None = None


@dataclass(frozen=True, slots=True)
class VariableHistoryView:
    variables: tuple[HistoryVariable, ...]
    limited: bool = False

    def visible_values(self) -> tuple[tuple[str, str], ...]:
        """Safe scalar-only result for existing Run History consumers."""
        return tuple((item.name, item.value) for item in self.variables if item.value is not None)


class ExecutionContext(dict[str, str]):
    """The existing mutable root context plus bounded provenance metadata.

    Providers and Tasks still work with the same mapping. Protected-value
    fingerprints only live with this execution, never in its history result.
    """

    def __init__(self, supplied: Mapping[str, object]) -> None:
        super().__init__()
        self.origins: dict[str, str] = {}
        self._structured: set[str] = set()
        self._protected: set[bytes] = set()
        self._protected_keys: set[str] = set()
        self._protection_limited = False
        for raw_name, raw_value in supplied.items():
            self._write(str(raw_name), str(raw_value), "Trigger",
                        structured=not isinstance(raw_value, (str, int, float, bool, type(None))))

    def _write(self, name: str, value: str, source: str, *, structured: bool = False) -> None:
        super().__setitem__(name, value)
        if not isinstance(name, str) or len(name) > 128:
            return
        canonical = name.strip().casefold()
        if canonical in self.origins or len(self.origins) < _MAX_SCAN:
            self.origins[canonical] = source
            if structured:
                self._structured.add(canonical)
            else:
                self._structured.discard(canonical)
        protected = structured or _protected_name(canonical)
        if protected and len(self._protected_keys) < _MAX_PROTECTED:
            self._protected_keys.add(name)
        if not isinstance(value, str) or not 0 < len(value) <= MAX_VALUE_CHARS:
            return
        if protected or any(
            isinstance(original := self.get(key), str)
            and 6 <= len(original) <= len(value) and original in value
            for key in self._protected_keys
        ):
            digest = _fingerprint(value)
            if len(self._protected) < _MAX_PROTECTED:
                self._protected.add(digest)
            elif digest not in self._protected:
                self._protection_limited = True

    def __setitem__(self, name: str, value: str) -> None:
        self._write(name, value, "Task", structured=not isinstance(value, (str, int, float, bool, type(None))))

    def update(self, values=(), /, **kwargs) -> None:
        for name, value in values.items() if hasattr(values, "items") else values:
            self[name] = value
        for name, value in kwargs.items():
            self[name] = value

    def materialize(self, snapshots: Iterable[VariableSnapshot]) -> None:
        for snapshot in snapshots:
            if not snapshot.available:
                continue
            name = snapshot.definition.name
            value = snapshot.display_value
            # Context providers often echo an already-supplied trigger/Task
            # value; that is not a new origin.
            if name in self and self[name] == value:
                continue
            provider = snapshot.definition.source
            source = ("Custom Variable" if provider == "Custom" else
                      "Built-in context" if provider in {"Core", "Hub", "Hub Runtime"} else
                      f"Provider: {provider}" if provider else "Provider")
            self._write(name, value, source[:96], structured=not isinstance(
                snapshot.value, (str, int, float, bool, type(None))
            ))

    def snapshot(self) -> VariableHistoryView:
        return _snapshot(self, self.origins, self._structured, self._protected, self._protection_limited)


def _snapshot(
    context: Mapping[str, object],
    origins: Mapping[str, str],
    structured: set[str],
    fingerprints: set[bytes],
    protection_limited: bool,
) -> VariableHistoryView:
    items = {}
    protected_values: set[str] = set()
    limited = len(context) > _MAX_SCAN
    for raw_name, raw_value in islice(context.items(), _MAX_SCAN):
        if not isinstance(raw_name, str) or len(raw_name) > 128:
            limited = True
            continue
        name = raw_name.strip().casefold()
        if not VARIABLE_NAME_PATTERN.fullmatch(name):
            continue
        source = origins.get(name, "Unknown")
        protected = _protected_name(name)
        if protected and isinstance(raw_value, str) and 6 <= len(raw_value) <= MAX_VALUE_CHARS:
            if len(protected_values) < _MAX_PROTECTED:
                protected_values.add(raw_value)
            elif raw_value not in protected_values:
                protection_limited = True
        items[name] = (name, raw_value, source, protected)

    # Trigger and Task values are the most useful when the budget fills.
    ordered = sorted(items.values(), key=lambda item: (item[2] not in {"Trigger", "Task"}, item[0]))
    rows = []
    remaining = MAX_SNAPSHOT_CHARS
    for name, raw_value, source, protected in ordered:
        if len(rows) >= MAX_VARIABLES or remaining < len(name) + len(source):
            limited = True
            break
        remaining -= len(name) + len(source)
        value = None
        if protected:
            state = "Redacted"
        elif name in structured or not isinstance(raw_value, (str, int, float, bool, type(None))):
            state = "Omitted"
            limited = True
        elif raw_value is None:
            state = "Unavailable"
        else:
            text = str(raw_value)
            if len(text) > MAX_VALUE_CHARS:
                state = "Omitted"
                limited = True
            elif text and (protection_limited or _BARE_AUTH.match(text)
                           or (fingerprints and _fingerprint(text) in fingerprints)
                           or any(value in text for value in protected_values)
                           or redact_secret_text(text) != text):
                state = "Redacted"
            elif len(text) > remaining:
                state = "Omitted"
                limited = True
            else:
                value = text
                state = "Populated" if text else "Empty"
                remaining -= len(text)
        rows.append(HistoryVariable(name, source, state, value))
    return VariableHistoryView(tuple(rows), limited or protection_limited)
