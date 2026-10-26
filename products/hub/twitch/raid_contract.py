from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


RAID_COUNTDOWN_SECONDS = 90


@dataclass(frozen=True, slots=True)
class OutgoingRaidStartedEvent:
    """One Hub-initiated raid accepted by Twitch's Start Raid endpoint."""

    started_at: datetime
    countdown_seconds: int
    source_id: str
    source_login: str
    source_name: str
    target_id: str
    target_login: str
    target_name: str
    viewers: int

