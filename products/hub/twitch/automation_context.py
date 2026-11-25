"""Small, user-facing projections of Twitch lifecycle notification payloads."""

from collections.abc import Mapping

from products.hub.automation.variable_registry import VariableDataType
from products.hub.twitch.catalog import ADDITIONAL_AUTOMATION_EVENT_TYPES


ADDITIONAL_ACTIVITY_EVENTS = frozenset(ADDITIONAL_AUTOMATION_EVENT_TYPES)
TEXT = VariableDataType.TEXT
INTEGER = VariableDataType.INTEGER
TIME = VariableDataType.DATETIME

# (context name, payload field, Picker label, data type)
_BROADCASTER = (
    ("event.broadcaster_id", "broadcaster_user_id", "Event Broadcaster ID", TEXT),
    ("event.broadcaster_login", "broadcaster_user_login", "Event Broadcaster Login", TEXT),
    ("event.broadcaster_name", "broadcaster_user_name", "Event Broadcaster Display Name", TEXT),
)
_FIELDS = {
    "channel.update": (
        ("channel_update.title", "title", "Updated Stream Title", TEXT),
        ("channel_update.category_id", "category_id", "Updated Category ID", TEXT),
        ("channel_update.category_name", "category_name", "Updated Category Name", TEXT),
        ("channel_update.language", "language", "Updated Broadcast Language", TEXT),
    ),
    "channel.poll": (
        ("poll.id", "id", "Poll ID", TEXT),
        ("poll.title", "title", "Poll Title", TEXT),
        ("poll.status", "status", "Poll Status", TEXT),
        ("poll.started_at", "started_at", "Poll Started At", TIME),
        ("poll.ends_at", "ends_at", "Poll Scheduled End", TIME),
        ("poll.ended_at", "ended_at", "Poll Ended At", TIME),
    ),
    "channel.prediction": (
        ("prediction.id", "id", "Prediction ID", TEXT),
        ("prediction.title", "title", "Prediction Title", TEXT),
        ("prediction.status", "status", "Prediction Status", TEXT),
        ("prediction.started_at", "started_at", "Prediction Started At", TIME),
        ("prediction.locks_at", "locks_at", "Prediction Scheduled Lock", TIME),
        ("prediction.locked_at", "locked_at", "Prediction Locked At", TIME),
        ("prediction.ended_at", "ended_at", "Prediction Ended At", TIME),
        ("prediction.winning_outcome_id", "winning_outcome_id", "Winning Outcome ID", TEXT),
    ),
    "channel.hype_train": (
        ("hype_train.id", "id", "Hype Train ID", TEXT),
        ("hype_train.level", "level", "Hype Train Level", INTEGER),
        ("hype_train.progress", "progress", "Hype Train Progress", INTEGER),
        ("hype_train.goal", "goal", "Hype Train Level Goal", INTEGER),
        ("hype_train.total", "total", "Hype Train Total", INTEGER),
        ("hype_train.started_at", "started_at", "Hype Train Started At", TIME),
        ("hype_train.expires_at", "expires_at", "Hype Train Expires At", TIME),
        ("hype_train.ended_at", "ended_at", "Hype Train Ended At", TIME),
        ("hype_train.cooldown_ends_at", "cooldown_ends_at", "Hype Train Cooldown Ends At", TIME),
    ),
    "channel.shield_mode": (
        ("shield_mode.started_at", "started_at", "Shield Mode Activated At", TIME),
        ("shield_mode.ended_at", "ended_at", "Shield Mode Deactivated At", TIME),
    ),
    "channel.shoutout": (
        ("shoutout.viewer_count", "viewer_count", "Shoutout Viewer Count", INTEGER),
        ("shoutout.started_at", "started_at", "Shoutout Sent At", TIME),
        ("shoutout.cooldown_ends_at", "cooldown_ends_at", "Shoutout Cooldown Ends At", TIME),
        ("shoutout.target_cooldown_ends_at", "target_cooldown_ends_at", "Target Shoutout Cooldown Ends At", TIME),
    ),
}
_SUMMARIES = (
    ("channel_update.classification_labels", "Content Classification Labels", "Comma-separated Twitch label IDs."),
    ("poll.choices", "Poll Choices", "Choice titles with vote totals when Twitch supplies them."),
    ("poll.choice_ids", "Poll Choice IDs", "Choice IDs in Twitch's order."),
    ("prediction.outcomes", "Prediction Outcomes", "Outcome titles with user and point totals when Twitch supplies them."),
    ("prediction.outcome_ids", "Prediction Outcome IDs", "Outcome IDs in Twitch's order."),
    ("prediction.winning_outcome", "Winning Outcome", "Title matching the winning outcome ID, when available."),
    ("hype_train.top_contributions", "Hype Train Top Contributions", "Contributor name, contribution type, and total, when available."),
    ("shoutout.other_id", "Other Shoutout Channel ID", "Target channel for a sent shoutout or source channel for a received shoutout."),
    ("shoutout.other_login", "Other Shoutout Channel Login", "Login of the other channel in the shoutout."),
    ("shoutout.other_name", "Other Shoutout Channel Display Name", "Display name of the other channel in the shoutout."),
)
LIFECYCLE_CONTEXT_DEFINITIONS = (
    *((name, label, f"{label} supplied by the matching Twitch notification.", kind, (name,))
      for name, _, label, kind in (*_BROADCASTER, *(field for fields in _FIELDS.values() for field in fields))),
    *((name, label, description, TEXT, (name,)) for name, label, description in _SUMMARIES),
)


def _scalar(value: object) -> str:
    if isinstance(value, str):
        return value
    return str(value) if type(value) is int else ""


def _rows(event: Mapping, key: str) -> list[Mapping]:
    values = event.get(key)
    return [row for row in values[:10] if isinstance(row, Mapping)] if isinstance(values, list) else []


def _summary(rows: list[Mapping], title_key: str, stats: tuple[tuple[str, str], ...]) -> str:
    lines = []
    for row in rows:
        title = _scalar(row.get(title_key)).strip() or _scalar(row.get("user_login")).strip()
        extras = [f"{value} {label}".strip() for key, label in stats if (value := _scalar(row.get(key)))]
        if title:
            lines.append(f"{title} ({', '.join(extras)})" if extras else title)
    return "; ".join(lines)


def lifecycle_context(event_type: str, event: Mapping) -> dict[str, str]:
    if event_type not in ADDITIONAL_ACTIVITY_EVENTS:
        return {}
    family = event_type if event_type == "channel.update" else event_type.rsplit(".", 1)[0]
    context = {}
    for name, key, _, kind in (*_BROADCASTER, *_FIELDS.get(family, ())):
        value = event.get(key)
        if kind == INTEGER and type(value) is not int:
            continue
        if family == "channel.update" and isinstance(value, str):
            context[name] = value  # Twitch uses an empty category to mean cleared.
        elif text := _scalar(value):
            context[name] = text
    if family == "channel.update":
        labels = event.get("content_classification_labels")
        if isinstance(labels, list):
            context["channel_update.classification_labels"] = ", ".join(
                value for value in labels[:32] if isinstance(value, str)
            )
    elif family in {"channel.poll", "channel.prediction"}:
        is_poll = family == "channel.poll"
        key = "choices" if is_poll else "outcomes"
        prefix = "poll" if is_poll else "prediction"
        rows = _rows(event, key)
        stats = (("votes", "votes"),) if is_poll else (("users", "users"), ("channel_points", "points"))
        context[f"{prefix}.{key}"] = _summary(rows, "title", stats)
        context[f"{prefix}.{key[:-1]}_ids"] = ", ".join(
            value for row in rows if (value := _scalar(row.get("id")))
        )
        if winner := context.get("prediction.winning_outcome_id"):
            for row in rows:
                if _scalar(row.get("id")) == winner:
                    context["prediction.winning_outcome"] = _scalar(row.get("title"))
                    break
    elif family == "channel.hype_train":
        context["hype_train.top_contributions"] = _summary(
            _rows(event, "top_contributions"), "user_name", (("total", "total"), ("type", "")),
        )
    elif family == "channel.shoutout":
        direction = "to" if event_type.endswith(".create") else "from"
        for suffix in ("id", "login", "name"):
            if text := _scalar(event.get(f"{direction}_broadcaster_user_{suffix}")):
                context[f"shoutout.other_{suffix}"] = text
    return context
