from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from shared.streamhouse_runtime.json_store import (
    UnsupportedJsonSchemaError,
    atomic_write_json,
    json_store_exists,
    load_validated_json,
)
from shared.streamhouse_runtime.paths import user_data_root
from products.hub.core.secret_store import SecretStore


@dataclass(slots=True)
class ObsConnectionConfig:
    host: str = "127.0.0.1"
    port: int = 4455
    auto_connect: bool = False
    default_mute_input: str = ""

    def validate(self) -> None:
        if any(marker in self.host for marker in ("@", "/", "?", "#")):
            raise ValueError(
                "Enter an OBS host name or IP address without credentials or a URL."
            )
        if not 1 <= self.port <= 65535:
            raise ValueError("OBS WebSocket port must be between 1 and 65535.")

    @classmethod
    def from_dict(cls, values: dict[str, Any]) -> ObsConnectionConfig:
        host = str(values.get("host", "127.0.0.1")).strip() or "127.0.0.1"
        if any(marker in host for marker in ("@", "/", "?", "#")):
            host = "127.0.0.1"
        try:
            port = int(values.get("port", 4455))
        except (TypeError, ValueError):
            port = 4455
        return cls(
            host=host[:255],
            port=min(max(port, 1), 65535),
            auto_connect=bool(values.get("auto_connect", False)),
            default_mute_input=str(values.get("default_mute_input", "")).strip()[:255],
        )


class ObsConfigStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_root() / "obs" / "connection.json"
        self.secret_store = SecretStore(
            self.path.with_name("password.dat"), "Streamhouse OBS password"
        )

    def load(self) -> tuple[ObsConnectionConfig, str]:
        config = ObsConnectionConfig()
        if json_store_exists(self.path):
            config = load_validated_json(self.path, self._parse_payload)
        return config, self.secret_store.load()

    @staticmethod
    def _parse_payload(payload: object) -> ObsConnectionConfig:
        if not isinstance(payload, dict):
            raise ValueError("OBS connection settings must be a JSON object.")
        version = payload.get("version")
        if type(version) is not int or version != 1:
            raise UnsupportedJsonSchemaError(
                f"Unsupported OBS connection version {version}; expected 1."
            )
        return ObsConnectionConfig.from_dict(payload)

    def save(self, config: ObsConnectionConfig, password: str) -> None:
        config.validate()
        atomic_write_json(self.path, {"version": 1, **asdict(config)})
        self.secret_store.save(password)
