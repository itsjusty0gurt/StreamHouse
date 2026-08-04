from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from products.hub.counters.models import CounterDefinition, CounterValues, READ_SCOPES, SCOPES
from products.hub.counters.store import CounterStore


@dataclass(frozen=True, slots=True)
class CounterOperation:
    status: str
    values: CounterValues = field(default_factory=CounterValues)
    updated_scopes: tuple[str, ...] = ()
    skipped_scopes: tuple[str, ...] = ()
    amount_changed: int = 0
    detail: str = ""
    viewer_id: str = ""
    viewer_login: str = ""
    viewer_display_name: str = ""
    stream_id: str = ""


class CounterService:
    def __init__(self, store: CounterStore, *, bot_checker: Callable[[str], bool] | None = None) -> None:
        self.store = store
        self.bot_checker = bot_checker or (lambda _user_id: False)

    def list_counters(self) -> tuple[CounterDefinition, ...]:
        return tuple(self.store.list_definitions())

    def get_counter(self, counter_id: str) -> CounterDefinition | None:
        clean = str(counter_id).strip().casefold()
        return next((item for item in self.list_counters() if item.counter_id == clean), None)

    def create_counter(self, definition: CounterDefinition, starting_total: int = 0) -> CounterDefinition:
        if not definition.track_channel_total and int(starting_total) != 0:
            raise ValueError("A starting channel total requires channel lifetime tracking.")
        if definition.track_channel_total and int(starting_total) < definition.minimum:
            raise ValueError("Starting total is below this counter's minimum.")
        self.store.create(definition, starting_total)
        return definition

    def update_counter(self, counter_id: str, **changes: Any) -> CounterDefinition:
        updated = self._require(counter_id).updated(**changes)
        self.store.update_definition(updated)
        return updated

    def delete_counter(self, counter_id: str) -> None:
        self.store.delete(counter_id)

    def set_enabled(self, counter_id: str, enabled: bool) -> CounterDefinition:
        return self.update_counter(counter_id, enabled=bool(enabled))

    def _require(self, counter_id: str) -> CounterDefinition:
        definition = self.get_counter(counter_id)
        if definition is None:
            raise KeyError(f'Counter "{counter_id}" does not exist.')
        return definition

    @staticmethod
    def _clean_identity(value: str) -> str:
        clean = str(value).strip()
        return "" if clean in {"", "--"} else clean

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
        return self._values_from_payload(
            self.store.read_data(counter_id),
            self._clean_identity(user_id),
            self._clean_identity(stream_id),
        )

    def read_value(self, counter_id: str, scope: str, *, user_id: str = "", stream_id: str = "") -> CounterOperation:
        definition = self.get_counter(counter_id)
        if definition is None:
            return CounterOperation("missing_counter", detail=f'Counter "{counter_id}" does not exist.')
        scope = str(scope).strip().casefold()
        if scope not in READ_SCOPES:
            return CounterOperation("invalid_configuration", detail=f"Unknown counter read scope: {scope}.")
        selected = (scope,)
        user_id = self._clean_identity(user_id)
        stream_id = self._clean_identity(stream_id)
        values = self.get_values(definition.counter_id, user_id=user_id, stream_id=stream_id)
        if not definition.enabled:
            return CounterOperation("disabled_counter", values, skipped_scopes=selected, detail="Counter is disabled.")
        tracked_scope = "viewer_total" if scope == "viewer_rank" else scope
        if not definition.tracks(tracked_scope):
            return CounterOperation("invalid_configuration", values, skipped_scopes=selected, detail="The selected scope is not tracked by this counter.")
        if (scope.startswith("viewer_") or scope == "viewer_rank") and not user_id:
            return CounterOperation("missing_viewer", values, skipped_scopes=selected, detail="A Twitch viewer ID is required for this scope.")
        if "stream" in scope and not stream_id:
            return CounterOperation("stream_unavailable", values, skipped_scopes=selected, detail="No confirmed active Twitch stream is available.")
        return CounterOperation("success", values, viewer_id=user_id, viewer_login=values.viewer_login, viewer_display_name=values.viewer_display_name, stream_id=stream_id)

    def update_values(self, counter_id: str, amount: int, scopes: Iterable[str], *, user_id: str = "", login: str = "", display_name: str = "", stream_id: str = "") -> CounterOperation:
        definition = self.get_counter(counter_id)
        if definition is None:
            return CounterOperation("missing_counter", detail=f'Counter "{counter_id}" does not exist.')
        try:
            selected = self._validate_scopes(scopes)
            amount = int(amount)
        except (TypeError, ValueError) as error:
            return CounterOperation("invalid_value", detail=str(error))
        user_id = self._clean_identity(user_id)
        login = self._clean_identity(login)
        display_name = self._clean_identity(display_name)
        stream_id = self._clean_identity(stream_id)
        before = self.get_values(counter_id, user_id=user_id, stream_id=stream_id)
        if not definition.enabled:
            return CounterOperation("disabled_counter", before, skipped_scopes=selected, detail="Counter is disabled.", viewer_id=user_id, viewer_login=login, viewer_display_name=display_name, stream_id=stream_id)
        if user_id and definition.exclude_known_bots and self.bot_checker(user_id):
            return CounterOperation("skipped_known_bot", before, skipped_scopes=selected, detail="The reliably identified bot was excluded from the entire update.", viewer_id=user_id, viewer_login=login, viewer_display_name=display_name, stream_id=stream_id)
        updated: list[str] = []
        skipped: list[str] = []
        clamped = False

        def mutate(payload: dict[str, Any]) -> CounterValues:
            nonlocal clamped
            viewer = None
            for scope in selected:
                if not definition.tracks(scope) or (scope.startswith("viewer_") and not user_id) or ("stream" in scope and not stream_id):
                    skipped.append(scope)
                    continue
                if scope.startswith("viewer_"):
                    viewer = self._viewer(payload, user_id, create=True)
                    viewer["login"] = login.strip() or viewer.get("login", "")
                    viewer["display_name"] = display_name.strip() or viewer.get("display_name", "") or login.strip()
                clamped = self._adjust(payload, viewer, scope, amount, stream_id, definition.minimum) or clamped
                updated.append(scope)
            return self._values_from_payload(payload, user_id, stream_id)

        # Do not write a counter file when every requested scope is unavailable.
        for scope in selected:
            if not definition.tracks(scope) or (scope.startswith("viewer_") and not user_id) or ("stream" in scope and not stream_id):
                skipped.append(scope)
        if len(skipped) == len(selected):
            status = "missing_viewer" if any(scope.startswith("viewer_") for scope in skipped) and not user_id else "stream_unavailable" if any("stream" in scope for scope in skipped) and not stream_id else "invalid_configuration"
            return CounterOperation(status, before, skipped_scopes=selected, detail=self._skipped_detail(skipped, user_id, stream_id), viewer_id=user_id, viewer_login=login, viewer_display_name=display_name, stream_id=stream_id)
        skipped.clear()
        try:
            values = self.store.mutate_data(counter_id, mutate)
        except (OSError, ValueError) as error:
            return CounterOperation("persistence_failed", before, skipped_scopes=selected, detail=f"Counter values were not saved: {error}", viewer_id=user_id, viewer_login=login, viewer_display_name=display_name, stream_id=stream_id)
        status = "minimum_reached" if clamped else "success" if updated and not skipped else "partial_success"
        detail = ""
        if skipped:
            detail = self._skipped_detail(skipped, user_id, stream_id)
        elif clamped:
            detail = "One or more values reached the configured minimum."
        return CounterOperation(status, values, tuple(updated), tuple(skipped), amount if updated else 0, detail, user_id, login, display_name, stream_id)

    @staticmethod
    def _adjust(payload: dict[str, Any], viewer: dict[str, Any] | None, scope: str, amount: int, stream_id: str, minimum: int) -> bool:
        if scope == "channel_total":
            requested = int(payload.get("channel_total", 0)) + amount
            payload["channel_total"] = max(minimum, requested)
            return requested < minimum
        elif scope == "viewer_total" and viewer is not None:
            requested = int(viewer.get("total", 0)) + amount
            viewer["total"] = max(minimum, requested)
            return requested < minimum
        else:
            container = payload if scope == "stream_total" else viewer
            if container is not None:
                current = container.get("current_stream", {})
                previous = int(current.get("value", 0)) if current.get("stream_id") == stream_id else 0
                requested = previous + amount
                container["current_stream"] = {"stream_id": stream_id, "value": max(minimum, requested)}
                return requested < minimum
        return False

    def set_value(self, counter_id: str, scope: str, value: int, *, user_id: str = "", login: str = "", display_name: str = "", stream_id: str = "") -> CounterOperation:
        definition = self.get_counter(counter_id)
        if definition is None:
            return CounterOperation("missing_counter", detail=f'Counter "{counter_id}" does not exist.')
        try:
            selected = self._validate_scopes((scope,))
            value = int(value)
        except (TypeError, ValueError) as error:
            return CounterOperation("invalid_value", detail=str(error))
        user_id = self._clean_identity(user_id); login = self._clean_identity(login)
        display_name = self._clean_identity(display_name); stream_id = self._clean_identity(stream_id)
        before = self.get_values(counter_id, user_id=user_id, stream_id=stream_id)
        identity = {"viewer_id": user_id, "viewer_login": login, "viewer_display_name": display_name, "stream_id": stream_id}
        if not definition.enabled:
            return CounterOperation("disabled_counter", before, skipped_scopes=selected, detail="Counter is disabled.", **identity)
        if not definition.tracks(scope):
            return CounterOperation("invalid_configuration", before, skipped_scopes=selected, detail="The selected scope is not tracked by this counter.", **identity)
        if scope.startswith("viewer_") and not user_id:
            return CounterOperation("missing_viewer", before, skipped_scopes=selected, detail="A Twitch viewer ID is required for this scope.", **identity)
        if "stream" in scope and not stream_id:
            return CounterOperation("stream_unavailable", before, skipped_scopes=selected, detail="No confirmed active Twitch stream is available.", **identity)
        if value < definition.minimum:
            return CounterOperation("invalid_value", before, skipped_scopes=selected, detail=f"Value cannot be below {definition.minimum}.", **identity)

        def mutate(payload: dict[str, Any]) -> CounterValues:
            viewer = self._viewer(payload, user_id, create=scope.startswith("viewer_")) if user_id else None
            if viewer is not None:
                viewer["login"] = login.strip() or viewer.get("login", "")
                viewer["display_name"] = display_name.strip() or viewer.get("display_name", "") or login.strip()
            if scope == "channel_total": payload["channel_total"] = value
            elif scope == "viewer_total" and viewer is not None: viewer["total"] = value
            else:
                container = payload if scope == "stream_total" else viewer
                if container is not None: container["current_stream"] = {"stream_id": stream_id, "value": value}
            return self._values_from_payload(payload, user_id, stream_id)
        try:
            values = self.store.mutate_data(counter_id, mutate)
        except (OSError, ValueError) as error:
            return CounterOperation("persistence_failed", before, skipped_scopes=selected, detail=f"Counter value was not saved: {error}", **identity)
        return CounterOperation("success", values, selected, amount_changed=value, **identity)

    def reset(self, counter_id: str, scopes: Iterable[str], *, user_id: str = "", stream_id: str = "", all_viewers: bool = False, all_viewer_scopes: Iterable[str] = ()) -> CounterOperation:
        definition = self.get_counter(counter_id)
        if definition is None:
            return CounterOperation("missing_counter", detail=f'Counter "{counter_id}" does not exist.')
        raw_scopes = tuple(scopes)
        try:
            selected = self._validate_scopes(raw_scopes) if raw_scopes else ()
            broad = tuple(dict.fromkeys(str(scope) for scope in all_viewer_scopes))
            if all_viewers:
                broad = ("viewer_total", "viewer_stream_total")
            if any(scope not in {"viewer_total", "viewer_stream_total"} for scope in broad):
                raise ValueError("Broad resets support viewer lifetime and viewer current-stream scopes only.")
            if not selected and not broad:
                raise ValueError("Select at least one counter scope.")
        except ValueError as error:
            return CounterOperation("invalid_configuration", detail=str(error))
        user_id = self._clean_identity(user_id); stream_id = self._clean_identity(stream_id)
        requested = tuple(dict.fromkeys((*selected, *broad)))
        before = self.get_values(counter_id, user_id=user_id, stream_id=stream_id)
        identity = {"viewer_id": user_id, "stream_id": stream_id}
        if not definition.enabled:
            return CounterOperation("disabled_counter", before, skipped_scopes=requested, detail="Counter is disabled.", **identity)
        skipped: list[str] = []
        valid_items: list[str] = []
        for scope in requested:
            requires_individual_viewer = scope in selected and scope not in broad and scope.startswith("viewer_")
            if not definition.tracks(scope):
                skipped.append(scope)
            elif requires_individual_viewer and not user_id:
                skipped.append(scope)
            elif "stream" in scope and not stream_id:
                skipped.append(scope)
            else:
                valid_items.append(scope)
        valid = tuple(valid_items)
        if not valid:
            if any(scope.startswith("viewer_") for scope in skipped) and not user_id and any(scope in selected and scope not in broad for scope in skipped):
                status = "missing_viewer"
            elif any("stream" in scope for scope in skipped) and not stream_id:
                status = "stream_unavailable"
            else:
                status = "invalid_configuration"
            return CounterOperation(status, before, skipped_scopes=tuple(skipped), detail=self._skipped_detail(skipped, user_id, stream_id), **identity)
        def mutate(payload: dict[str, Any]) -> CounterValues:
            viewer = self._viewer(payload, user_id) if user_id else None
            if any(scope in valid and scope not in broad and scope.startswith("viewer_") for scope in selected) and viewer is None:
                raise KeyError(user_id)
            for scope in selected:
                if scope not in valid: continue
                if scope == "channel_total": payload["channel_total"] = definition.minimum
                elif scope == "stream_total": payload["current_stream"] = {"stream_id": stream_id, "value": definition.minimum}
                elif scope == "viewer_total" and viewer is not None: viewer["total"] = definition.minimum
                elif scope == "viewer_stream_total" and viewer is not None: viewer["current_stream"] = {"stream_id": stream_id, "value": definition.minimum}
            for item in payload.get("viewers", {}).values():
                if "viewer_total" in broad and "viewer_total" in valid: item["total"] = definition.minimum
                if "viewer_stream_total" in broad and "viewer_stream_total" in valid: item["current_stream"] = {"stream_id": stream_id, "value": definition.minimum}
            return self._values_from_payload(payload, user_id, stream_id)
        try:
            values = self.store.mutate_data(counter_id, mutate)
        except KeyError:
            return CounterOperation("missing_viewer", before, skipped_scopes=requested, detail="This viewer has no stored value for the counter.", **identity)
        except (OSError, ValueError) as error:
            return CounterOperation("persistence_failed", before, skipped_scopes=requested, detail=f"Counter reset was not saved: {error}", **identity)
        status = "partial_success" if skipped else "success"
        detail = self._skipped_detail(skipped, user_id, stream_id) if skipped else ""
        return CounterOperation(status, values, valid, tuple(skipped), detail=detail, **identity)

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
        definition = self._require(counter_id)
        rows = self.viewer_rows(counter_id, stream_id=stream_id)
        if definition.exclude_known_bots:
            rows = [row for row in rows if not self.bot_checker(row["user_id"])]
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
        return CounterValues(int(payload.get("channel_total", 0)), self._stream_value(payload, stream_id), score, self._stream_value(viewer, stream_id), rank, str(viewer.get("display_name", "")) if viewer else "", str(viewer.get("login", "")) if viewer else "")

    @staticmethod
    def _skipped_detail(skipped: Iterable[str], user_id: str, stream_id: str) -> str:
        scopes = tuple(skipped)
        reasons: list[str] = []
        if any(scope.startswith("viewer_") for scope in scopes) and not user_id:
            reasons.append("viewer scopes require a Twitch user ID")
        if any("stream" in scope for scope in scopes) and not stream_id:
            reasons.append("stream scopes require a confirmed active Twitch stream")
        if not reasons:
            reasons.append("one or more scopes are not tracked by the counter")
        return "Skipped " + ", ".join(scopes) + ": " + "; ".join(reasons) + "."
