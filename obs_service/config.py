from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from core.json_store import atomic_write_json, load_json_with_backup
from core.paths import user_data_root
from core.secret_store import SecretStore


@dataclass(slots=True)
class ObsConnectionConfig:
    host: str = "127.0.0.1"
    port: int = 4455
    auto_connect: bool = False

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> ObsConnectionConfig:
        host = str(values.get("host", "127.0.0.1")).strip() or "127.0.0.1"
        try:
            port = int(values.get("port", 4455))
        except (TypeError, ValueError):
            port = 4455
        return cls(
            host=host[:255],
            port=min(max(port, 1), 65535),
            auto_connect=bool(values.get("auto_connect", False)),
        )


class ObsConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_root() / "obs" / "connection.json"
        self.secret_store = SecretStore(
            self.path.with_name("password.dat"), "Sally AI OBS password"
        )

    def load(self) -> tuple[ObsConnectionConfig, str]:
        config = ObsConnectionConfig()
        if self.path.exists():
            payload = load_json_with_backup(self.path)
            if not isinstance(payload, dict):
                raise ValueError("OBS connection settings must be a JSON object.")
            config = ObsConnectionConfig.from_dict(payload)
        return config, self.secret_store.load()

    def save(self, config: ObsConnectionConfig, password: str) -> None:
        atomic_write_json(self.path, {"version": 1, **asdict(config)})
        self.secret_store.save(password)
