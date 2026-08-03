from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from products.hub.counters.models import CounterDefinition, CounterValues, SCOPES
from products.hub.counters.store import CounterStore


@dataclass(frozen=True, slots=True)
class CounterOperation:
    status: str
    values: CounterValues
    updated_scopes: tuple[str, ...] = ()
    skipped_scopes: tuple[str, ...] = ()
    amount_changed: int = 0
    detail: str = ""


class CounterService:
    def __init__(self, store: CounterStore, *, bot_checker: Callable[[str], bool] | None = None) -> None:
        self.store = store
        self.bot_checker = bot_checker or (lambda _user_id: False)

    def list_counters(self) -> tuple[CounterDefinition, ...]:
        return tuple(self.store.list_definitions())

    def get_counter(self, counter_id: str) -> CounterDefinition | None:
        clean = counter_id.strip().casefold()
        return next((item for item in self.list_counters() if item.counter_id == clean), None)

    def create_counter(self, definition: CounterDefinition, starting_total: int = 0) -> CounterDefinition:
        if starting_total < definition.minimum:
            raise ValueError("Starting total is below this counter's minimum.")
        self.store.create(definition, starting_total)
        return definition

    def update_counter(self, counter_id: str, **changes: Any) -> CounterDefinition:
        updated = self._require(counter_id).updated(**changes)
        self.store.update_definition(updated)
        return updated

    def delete_counter(self, counter_id: str) -> None:
        self.store.delete(counter_id)

    def _require(self, counter_id: str) -> CounterDefinition:
        definition = self.get_counter(counter_id)
        if definition is None:
            raise KeyError(f'Counter "{counter_id}" does not exist.')
        return definition

    @staticmethod
    def _viewer(payload: dict[str, Any], user_id: str, *, create: bool = False) -> dict[str, Any] | None:
        viewers = payload.setdefault("viewers", {})
        viewer = viewers.get(user_id)
        if viewer is None and create:
            viewer = {"login": "", "display_name": "", "total": 0, "current_stream": {"stream_id": "", "value": 0}}
            viewers[user_id] = viewer
        return viewer

    @staticmethod
    def _stream_value(container: dict[str, Any] | None, stream_id: str) -> int:
        current = (container or {}).get("current_stream", {})
        return int(current.get("value", 0)) if stream_id and current.get("stream_id") == stream_id else 0

    def get_values(self, counter_id: str, *, user_id: str = "", stream_id: str = "") -> CounterValues:
        self._require(counter_id)
        return self._values_from_payload(self.store.read_data(counter_id), user_id, stream_id)

    def update_values(self, counter_id: str, amount: int, scopes: Iterable[str], *, user_id: str = "", login: str = "", display_name: str = "", stream_id: str = "") -> CounterOperation:
        definition = self._require(counter_id)
        selected = self._validate_scopes(scopes)
        if not definition.enabled:
            return CounterOperation("disabled", self.get_values(counter_id, user_id=user_id, stream_id=stream_id), skipped_scopes=selected, detail="Counter is disabled.")
        if user_id and definition.exclude_known_bots and self.bot_checker(user_id):
            return CounterOperation("skipped_bot", self.get_values(counter_id, user_id=user_id, stream_id=stream_id), skipped_scopes=selected, detail="Known bot was excluded.")
        updated: list[str] = []
        skipped: list[str] = []

        def mutate(payload: dict[str, Any]) -> CounterValues:
            viewer = None
            for scope in selected:
                if not definition.tracks(scope) or (scope.startswith("viewer_") and not user_id) or ("stream" in scope and not stream_id):
                    skipped.append(scope)
                    continue
                if scope.startswith("viewer_"):
                    viewer = self._viewer(payload, user_id, create=True)
                    viewer["login"] = login.strip() or viewer.get("login", "")
                    viewer["display_name"] = display_name.strip() or viewer.get("display_name", "") or login.strip()
                self._adjust(payload, viewer, scope, int(amount), stream_id, definition.minimum)
                updated.append(scope)
            return self._values_from_payload(payload, user_id, stream_id)

        values = self.store.mutate_data(counter_id, mutate)
        status = "updated" if updated and not skipped else "partial" if updated else "skipped"
        detail = ""
        if skipped:
            detail = "Skipped: " + ", ".join(skipped) + ". Stream scopes require an active Twitch stream; viewer scopes require a viewer; disabled tracking scopes are not modified."
        return CounterOperation(status, values, tuple(updated), tuple(skipped), int(amount) if updated else 0, detail)

    @staticmethod
    def _adjust(payload: dict[str, Any], viewer: dict[str, Any] | None, scope: str, amount: int, stream_id: str, minimum: int) -> None:
        if scope == "channel_total":
            payload["channel_total"] = max(minimum, int(payload.get("channel_total", 0)) + amount)
        elif scope == "viewer_total" and viewer is not None:
            viewer["total"] = max(minimum, int(viewer.get("total", 0)) + amount)
        else:
            container = payload if scope == "stream_total" else viewer
            if container is not None:
                current = container.get("current_stream", {})
                previous = int(current.get("value", 0)) if current.get("stream_id") == stream_id else 0
                container["current_stream"] = {"stream_id": stream_id, "value": max(minimum, previous + amount)}

    def set_value(self, counter_id: str, scope: str, value: int, *, user_id: str = "", login: str = "", display_name: str = "", stream_id: str = "") -> CounterOperation:
        definition = self._require(counter_id)
        selected = self._validate_scopes((scope,))
        if not definition.tracks(scope):
            return CounterOperation("skipped", self.get_values(counter_id, user_id=user_id, stream_id=stream_id), skipped_scopes=selected, detail="Scope is not tracked.")
        if scope.startswith("viewer_") and not user_id:
            raise ValueError("A Twitch viewer is required for this scope.")
        if "stream" in scope and not stream_id:
            raise ValueError("An active Twitch stream is required for this scope.")
        if int(value) < definition.minimum:
            raise ValueError(f"Value cannot be below {definition.minimum}.")

        def mutate(payload: dict[str, Any]) -> CounterValues:
            viewer = self._viewer(payload, user_id, create=scope.startswith("viewer_")) if user_id else None
            if viewer is not None:
                viewer["login"] = login.strip() or viewer.get("login", "")
                viewer["display_name"] = display_name.strip() or viewer.get("display_name", "") or login.strip()
            if scope == "channel_total": payload["channel_total"] = int(value)
            elif scope == "viewer_total" and viewer is not None: viewer["total"] = int(value)
            else:
                container = payload if scope == "stream_total" else viewer
                if container is not None: container["current_stream"] = {"stream_id": stream_id, "value": int(value)}
            return self._values_from_payload(payload, user_id, stream_id)
        return CounterOperation("set", self.store.mutate_data(counter_id, mutate), selected)

    def reset(self, counter_id: str, scopes: Iterable[str], *, user_id: str = "", stream_id: str = "", all_viewers: bool = False) -> CounterOperation:
        definition = self._require(counter_id)
        selected = self._validate_scopes(scopes) if not all_viewers else ()
        if any(scope.startswith("viewer_") for scope in selected) and not user_id:
            raise ValueError("A Twitch viewer is required for viewer resets.")
        if any("stream" in scope for scope in selected) and not stream_id:
            raise ValueError("An active Twitch stream is required for stream resets.")
        def mutate(payload: dict[str, Any]) -> CounterValues:
            if all_viewers:
                for viewer in payload.get("viewers", {}).values():
                    viewer["total"] = definition.minimum
                    viewer["current_stream"] = {"stream_id": stream_id, "value": definition.minimum} if stream_id else {"stream_id": "", "value": 0}
            else:
                viewer = self._viewer(payload, user_id) if user_id else None
                for scope in selected:
                    if scope == "channel_total": payload["channel_total"] = definition.minimum
                    elif scope == "stream_total": payload["current_stream"] = {"stream_id": stream_id, "value": definition.minimum}
                    elif scope == "viewer_total" and viewer is not None: viewer["total"] = definition.minimum
                    elif scope == "viewer_stream_total" and viewer is not None: viewer["current_stream"] = {"stream_id": stream_id, "value": definition.minimum}
            return self._values_from_payload(payload, user_id, stream_id)
        return CounterOperation("reset", self.store.mutate_data(counter_id, mutate), selected)

    def viewer_rows(self, counter_id: str, *, stream_id: str = "") -> list[dict[str, Any]]:
        self._require(counter_id)
        payload = self.store.read_data(counter_id)
        return [{"user_id": uid, "login": str(item.get("login", "")), "display_name": str(item.get("display_name", "")), "total": int(item.get("total", 0)), "stream_total": self._stream_value(item, stream_id)} for uid, item in payload.get("viewers", {}).items()]

    def remove_viewer(self, counter_id: str, user_id: str) -> bool:
        self._require(counter_id)
        def mutate(payload: dict[str, Any]) -> bool:
            return payload.get("viewers", {}).pop(user_id, None) is not None
        return bool(self.store.mutate_data(counter_id, mutate))

    def leaderboard(self, counter_id: str, *, stream_id: str = "", current_stream: bool = False, limit: int = 5, include_zero: bool = False) -> list[dict[str, Any]]:
        rows = self.viewer_rows(counter_id, stream_id=stream_id)
        key = "stream_total" if current_stream else "total"
        rows = [row for row in rows if include_zero or row[key] != 0]
        return sorted(rows, key=lambda row: (-row[key], (row["display_name"] or row["login"]).casefold(), row["user_id"]))[:max(1, min(int(limit), 25))]

    def format_value(self, counter_id: str, value: int) -> str:
        definition = self._require(counter_id)
        return f"{value:,} {definition.singular if abs(value) == 1 else definition.plural}"

    @staticmethod
    def _validate_scopes(scopes: Iterable[str]) -> tuple[str, ...]:
        selected = tuple(dict.fromkeys(str(item).strip().casefold() for item in scopes if str(item).strip()))
        if not selected:
            raise ValueError("Select at least one counter scope.")
        invalid = [item for item in selected if item not in SCOPES]
        if invalid:
            raise ValueError(f"Unknown counter scope: {invalid[0]}.")
        return selected

    def _values_from_payload(self, payload: dict[str, Any], user_id: str, stream_id: str) -> CounterValues:
        viewer = self._viewer(payload, user_id) if user_id else None
        score = int(viewer.get("total", 0)) if viewer else 0
        rank = (1 + sum(1 for item in payload.get("viewers", {}).values() if int(item.get("total", 0)) > score)) if viewer else 0
        return CounterValues(int(payload.get("channel_total", 0)), self._stream_value(payload, stream_id), score, self._stream_value(viewer, stream_id), rank, str(viewer.get("display_name", "")) if viewer else "")
