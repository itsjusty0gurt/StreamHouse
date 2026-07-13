from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(slots=True)
class TwitchHealth:
    auth_state: str = "Signed out"
    connection_state: str = "Disconnected"
    eventsub_state: str = "Stopped"
    last_companion_success: datetime | None = None
    last_companion_error: str = ""
    missing_scopes: set[str] = field(default_factory=set)

    def companion_succeeded(self, warnings: tuple[str, ...]) -> None:
        self.last_companion_success = datetime.now(timezone.utc)
        self.last_companion_error = "; ".join(warnings)

    def companion_failed(self, message: str) -> None:
        self.last_companion_error = message

    @staticmethod
    def elapsed_text(value: datetime | None) -> str:
        if value is None:
            return "Never"
        seconds = max(
            int((datetime.now(timezone.utc) - value).total_seconds()),
            0,
        )
        if seconds < 60:
            return "Just now"
        if seconds < 3600:
            return f"{seconds // 60}m ago"
        return f"{seconds // 3600}h ago"
