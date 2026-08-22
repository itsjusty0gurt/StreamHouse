from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping, Protocol


AD_WARNING_SECONDS = {
    300: "ads.warning.5_minutes",
    180: "ads.warning.3_minutes",
    120: "ads.warning.2_minutes",
    60: "ads.warning.1_minute",
}


class TwitchAdsClient(Protocol):
    def run_commercial(self, length: int) -> dict: ...
    def snooze_next_ad(self) -> dict: ...


@dataclass(frozen=True, slots=True)
class AdsDomainEvent:
    event_type: str
    context: dict[str, str]


@dataclass(slots=True)
class AdsState:
    channel_live: bool = False
    schedule_available: bool = False
    next_at: datetime | None = None
    next_duration: int = 0
    last_at: datetime | None = None
    preroll_free_until: datetime | None = None
    snooze_count: int = 0
    snooze_refresh_at: datetime | None = None
    manual_retry_until: datetime | None = None
    in_progress: bool = False
    started_at: datetime | None = None
    ends_at: datetime | None = None
    duration: int = 0
    is_automatic: bool | None = None
    requester_id: str = ""
    requester_name: str = ""

    def values(self, now: datetime | None = None) -> dict[str, object]:
        current = _aware(now or datetime.now(timezone.utc))
        return {
            "next_at": _iso(self.next_at),
            "next_in": _remaining(self.next_at, current),
            "next_duration": self.next_duration or None,
            "last_at": _iso(self.last_at),
            "snooze_count": self.snooze_count if self.schedule_available else None,
            "snooze_refresh_at": _iso(self.snooze_refresh_at),
            "snooze_refresh_in": _remaining(self.snooze_refresh_at, current),
            "preroll_free_time": _remaining(self.preroll_free_until, current),
            "in_progress": self.in_progress,
            "remaining": (
                _remaining(self.ends_at, current) if self.in_progress else 0
            ),
            "duration": self.duration if self.in_progress else None,
            "started_at": _iso(self.started_at) if self.in_progress else None,
            "is_automatic": self.is_automatic if self.in_progress else None,
            "manual_retry_after": _remaining(self.manual_retry_until, current),
        }


class AdsService:
    """Own cached Twitch ad state and Hub-derived timing events."""

    def __init__(self, twitch: TwitchAdsClient) -> None:
        self.twitch = twitch
        self.state = AdsState()
        self._schedule_key = ""
        self._fired_warnings: set[int] = set()
        self._previous_next_remaining: int | None = None

    def set_channel_live(self, live: bool) -> None:
        self.state.channel_live = bool(live)

    def apply_schedule(
        self,
        value: object,
        *,
        now: datetime | None = None,
    ) -> None:
        current = _aware(now or datetime.now(timezone.utc))
        self.state.schedule_available = isinstance(value, Mapping)
        schedule = value if isinstance(value, Mapping) else {}
        if not self.state.schedule_available:
            self.state.next_duration = 0
            self.state.snooze_count = 0
            self.state.snooze_refresh_at = None
            self.state.preroll_free_until = None
        next_at = _timestamp(schedule.get("next_ad_at"))
        next_key = _iso(next_at)
        if next_key != self._schedule_key:
            self._schedule_key = next_key
            self._fired_warnings.clear()
            self._previous_next_remaining = (
                max(int((next_at - current).total_seconds()), 0) + 1
                if next_at is not None
                else None
            )
        self.state.next_at = next_at
        if "duration" in schedule:
            self.state.next_duration = _nonnegative_int(schedule.get("duration"))
        if "last_ad_at" in schedule:
            self.state.last_at = _timestamp(schedule.get("last_ad_at"))
        if "snooze_refresh_at" in schedule:
            self.state.snooze_refresh_at = _timestamp(
                schedule.get("snooze_refresh_at")
            )
        if "snooze_count" in schedule:
            self.state.snooze_count = _nonnegative_int(schedule.get("snooze_count"))
        if "preroll_free_time" in schedule:
            preroll_seconds = _nonnegative_int(schedule.get("preroll_free_time"))
            self.state.preroll_free_until = (
                current + timedelta(seconds=preroll_seconds)
                if preroll_seconds
                else None
            )
        if self.state.last_at is not None:
            schedule_retry = self.state.last_at + timedelta(minutes=8)
            if schedule_retry > current and (
                self.state.manual_retry_until is None
                or schedule_retry > self.state.manual_retry_until
            ):
                self.state.manual_retry_until = schedule_retry

    def run_commercial(
        self,
        duration: int,
        *,
        now: datetime | None = None,
    ) -> dict:
        current = _aware(now or datetime.now(timezone.utc))
        if (
            self.state.manual_retry_until is not None
            and self.state.manual_retry_until > current
        ):
            remaining = _remaining(self.state.manual_retry_until, current)
            raise ValueError(
                f"Another commercial can run in {remaining} seconds."
            )
        result = self.twitch.run_commercial(duration)
        retry_after = _nonnegative_int(result.get("retry_after"))
        self.state.manual_retry_until = (
            current + timedelta(seconds=retry_after) if retry_after else None
        )
        return result

    def snooze(self, *, now: datetime | None = None) -> dict:
        result = self.twitch.snooze_next_ad()
        self.apply_schedule(result, now=now)
        return result

    def snooze_next_ad(self) -> dict:
        return self.snooze()

    def observe_ad_break(
        self,
        event: Mapping[str, object],
        *,
        received_at: datetime | None = None,
    ) -> AdsDomainEvent:
        fallback = _aware(received_at or datetime.now(timezone.utc))
        started_at = _timestamp(event.get("started_at")) or fallback
        duration = _nonnegative_int(event.get("duration_seconds"))
        self.state.in_progress = True
        self.state.started_at = started_at
        self.state.duration = duration
        self.state.ends_at = started_at + timedelta(seconds=duration)
        automatic = event.get("is_automatic")
        if isinstance(automatic, bool):
            self.state.is_automatic = automatic
        elif automatic is None or not str(automatic).strip():
            self.state.is_automatic = None
        else:
            self.state.is_automatic = str(automatic).strip().casefold() in {
                "1", "true", "yes"
            }
        self.state.requester_id = str(
            event.get("requester_user_id") or ""
        ).strip()
        self.state.requester_name = str(
            event.get("requester_user_name")
            or event.get("requester_user_login")
            or ""
        ).strip()
        self.state.last_at = started_at
        return AdsDomainEvent("ads.started", self.event_context(now=fallback))

    def tick(self, now: datetime | None = None) -> tuple[AdsDomainEvent, ...]:
        current = _aware(now or datetime.now(timezone.utc))
        events: list[AdsDomainEvent] = []
        if (
            self.state.in_progress
            and self.state.ends_at is not None
            and current >= self.state.ends_at
        ):
            context = self.event_context(now=current)
            self._finish_active_ad()
            events.append(AdsDomainEvent("ads.ended", context))

        if self.state.next_at is not None and self.state.channel_live:
            remaining = max(int((self.state.next_at - current).total_seconds()), 0)
            previous = self._previous_next_remaining
            for threshold, event_type in AD_WARNING_SECONDS.items():
                if (
                    threshold not in self._fired_warnings
                    and previous is not None
                    and previous > threshold >= remaining
                ):
                    self._fired_warnings.add(threshold)
                    events.append(
                        AdsDomainEvent(event_type, self.event_context(now=current))
                    )
            self._previous_next_remaining = remaining
        return tuple(events)

    def event_context(self, now: datetime | None = None) -> dict[str, str]:
        values = self.state.values(now)
        context = {
            f"ads.{name}": _context_value(value)
            for name, value in values.items()
            if value is not None
        }
        if self.state.requester_id:
            context["ads.requester.id"] = self.state.requester_id
        if self.state.requester_name:
            context["ads.requester.name"] = self.state.requester_name
        return context

    def _finish_active_ad(self) -> None:
        self.state.in_progress = False
        self.state.started_at = None
        self.state.ends_at = None
        self.state.duration = 0
        self.state.is_automatic = None
        self.state.requester_id = ""
        self.state.requester_name = ""


def _timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _aware(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _aware(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _remaining(value: datetime | None, now: datetime) -> int | None:
    return max(int((value - now).total_seconds()), 0) if value is not None else None


def _nonnegative_int(value: object) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _context_value(value: object) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)
