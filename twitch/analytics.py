from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from twitch.chatter_history import ChatterRecord
from twitch.session_history import StreamSession


@dataclass(frozen=True, slots=True)
class ViewerAnalytics:
    user_id: str
    user_name: str
    messages: int
    active_days: int
    first_seen: str
    last_seen: str


@dataclass(frozen=True, slots=True)
class AnalyticsSnapshot:
    sessions: tuple[StreamSession, ...]
    session_count: int
    total_hours: float
    average_peak_viewers: float
    highest_peak_viewers: int
    total_messages: int
    messages_per_hour: float
    follows: int
    subscriptions: int
    cheers: int
    raids: int
    known_viewers: int
    new_viewers: int
    returning_viewers: int
    regular_viewers: int
    top_viewers: tuple[ViewerAnalytics, ...]


def build_analytics(
    sessions: Iterable[StreamSession],
    chatters: Iterable[ChatterRecord],
    days: int | None = None,
    now: datetime | None = None,
) -> AnalyticsSnapshot:
    current = now or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=days) if days else None
    filtered_sessions = tuple(
        session
        for session in sessions
        if _in_range(session.started_at, cutoff)
    )
    filtered_chatters = tuple(
        chatter
        for chatter in chatters
        if _in_range(chatter.last_seen, cutoff)
    )
    duration_seconds = sum(
        _duration_seconds(session) for session in filtered_sessions
    )
    total_hours = duration_seconds / 3600
    total_messages = sum(session.messages for session in filtered_sessions)
    peak_total = sum(session.peak_viewers for session in filtered_sessions)
    top_viewers = tuple(
        ViewerAnalytics(
            user_id=chatter.user_id,
            user_name=chatter.user_name,
            messages=chatter.message_count,
            active_days=len(chatter.active_days),
            first_seen=chatter.first_seen,
            last_seen=chatter.last_seen,
        )
        for chatter in sorted(
            filtered_chatters,
            key=lambda item: (item.message_count, len(item.active_days)),
            reverse=True,
        )[:20]
    )
    new_viewers = sum(
        1
        for chatter in filtered_chatters
        if cutoff is None or _in_range(chatter.first_seen, cutoff)
    )
    returning_viewers = sum(
        1 for chatter in filtered_chatters if len(chatter.active_days) > 1
    )
    regular_viewers = sum(
        1
        for chatter in filtered_chatters
        if len(chatter.active_days) >= 5
        and (chatter.message_count >= 25 or chatter.snapshot_days >= 10)
    )
    return AnalyticsSnapshot(
        sessions=filtered_sessions,
        session_count=len(filtered_sessions),
        total_hours=total_hours,
        average_peak_viewers=(
            peak_total / len(filtered_sessions) if filtered_sessions else 0
        ),
        highest_peak_viewers=max(
            (session.peak_viewers for session in filtered_sessions),
            default=0,
        ),
        total_messages=total_messages,
        messages_per_hour=(
            total_messages / total_hours if total_hours else 0
        ),
        follows=sum(session.follows for session in filtered_sessions),
        subscriptions=sum(
            session.subscriptions for session in filtered_sessions
        ),
        cheers=sum(session.cheers for session in filtered_sessions),
        raids=sum(session.raids for session in filtered_sessions),
        known_viewers=len(filtered_chatters),
        new_viewers=new_viewers,
        returning_viewers=returning_viewers,
        regular_viewers=regular_viewers,
        top_viewers=top_viewers,
    )


def _in_range(value: str, cutoff: datetime | None) -> bool:
    if cutoff is None:
        return True
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp >= cutoff
    except ValueError:
        return False


def _duration_seconds(session: StreamSession) -> int:
    try:
        started = datetime.fromisoformat(session.started_at.replace("Z", "+00:00"))
        ended = datetime.fromisoformat(session.ended_at.replace("Z", "+00:00"))
        return max(int((ended - started).total_seconds()), 0)
    except ValueError:
        return 0
