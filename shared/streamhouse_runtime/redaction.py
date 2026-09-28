from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any


_SECRET_NAMES = (
    r"authorization|access[_ -]?token|refresh[_ -]?token|api[_ -]?key|"
    r"client[_ -]?secret|password|obs[_ -]?password|relay[_ -]?(?:key|secret)|"
    r"x-api-key|"
    r"streamhouse_relay_(?:base|keys|db)|sally_relay_(?:base|keys|db)|"
    r"cookie|session[_ -]?(?:secret|token)|x-(?:streamhouse|sally)-key|token|secret"
)
_SECRET_KEY = re.compile(rf"(?i)^(?:{_SECRET_NAMES})$")
_SECRET_PATTERNS = (
    re.compile(
        r"(?i)(authorization\s*[:=]\s*(?:bearer|oauth|basic)\s+)([^\s,;]+)"
    ),
    re.compile(
        rf"(?i)((?:\"|')?(?:{_SECRET_NAMES})(?:\"|')?\s*[:=]\s*)"
        r"(\"[^\"]*\"|'[^']*'|[^\s,;]+)"
    ),
    re.compile(
        r"(?i)([?&#](?:access_token|refresh_token|api_key|client_secret|key|token|"
        r"secret|password|code)=)([^&#\s]+)"
    ),
    re.compile(r"(?i)((?:https?|wss?)://)([^/@\s]+)@"),
)


def is_secret_key(value: object) -> bool:
    """Return whether a mapping/header key conventionally owns a secret."""

    return _SECRET_KEY.fullmatch(str(value).strip()) is not None


def redact_secret_text(value: object, *, marker: str = "<REDACTED>") -> str:
    """Redact labeled credentials, credential URLs, headers, and URL userinfo."""

    text = str(value)
    for pattern in _SECRET_PATTERNS:
        replacement = rf"\1{marker}"
        text = pattern.sub(replacement, text)
    return text


def redact_secret_data(value: Any, *, marker: str = "<REDACTED>") -> Any:
    """Recursively redact credential-bearing structured values."""

    if isinstance(value, Mapping):
        return {
            str(key): (
                marker
                if is_secret_key(key)
                else redact_secret_data(item, marker=marker)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_secret_data(item, marker=marker) for item in value]
    if isinstance(value, str):
        return redact_secret_text(value, marker=marker)
    return value
