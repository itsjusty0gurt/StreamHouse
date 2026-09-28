from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from shared.streamhouse_runtime.json_store import atomic_write_json, load_json_with_backup
from shared.streamhouse_runtime.paths import user_data_root


@dataclass(slots=True)
class StreamhouseAISettings:
    ollama_endpoint: str = "http://127.0.0.1:11434"
    model: str = "qwen3:14b"
    personality: str = (
        "Warm, quick-witted, curious, and conversational. Match the streamer's "
        "energy, keep replies concise, and sound like a genuine cohost rather "
        "than a customer-service assistant."
    )
    allow_mild_profanity: bool = False
    allow_strong_profanity: bool = False

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> StreamhouseAISettings:
        defaults = cls()
        endpoint = str(values.get("ollama_endpoint", defaults.ollama_endpoint)).strip()
        if not endpoint.startswith(("http://", "https://")):
            endpoint = defaults.ollama_endpoint
        model = str(values.get("model", defaults.model)).strip()[:200] or defaults.model
        personality = str(values.get("personality", defaults.personality)).strip()[:2000]
        if not personality:
            personality = defaults.personality
        strong = bool(values.get("allow_strong_profanity", False))
        mild = bool(values.get("allow_mild_profanity", False)) or strong
        return cls(endpoint.rstrip("/")[:500], model, personality, mild, strong)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class StreamhouseAISettingsStore:
    VERSION = 2

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_root() / "ai" / "settings.json"

    def load(self) -> StreamhouseAISettings:
        if not self.path.exists():
            return StreamhouseAISettings()
        payload = load_json_with_backup(self.path)
        if not isinstance(payload, dict):
            raise ValueError("Streamhouse AI settings must contain an object.")
        if int(payload.get("_version", 0)) != self.VERSION:
            raise ValueError(
                "Streamhouse AI settings use a discarded pre-alpha schema and "
                "must be reset."
            )
        return StreamhouseAISettings.from_dict(payload)

    def save(self, settings: StreamhouseAISettings) -> None:
        payload = settings.to_dict()
        payload["_version"] = self.VERSION
        atomic_write_json(self.path, payload)
