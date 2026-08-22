from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any, Callable

from products.hub.counters.models import (
    CounterDefinition,
    counter_number_to_storage,
    parse_counter_number,
    validate_counter_id,
)
from shared.streamhouse_runtime.json_store import atomic_write_json, load_json_with_backup

INDEX_VERSION = 2
COUNTER_VERSION = 2


def empty_counter_payload(
    counter_id: str,
    reset_value: Any = "0",
    numeric_type: str = "integer",
) -> dict[str, Any]:
    stored = counter_number_to_storage(reset_value, numeric_type)
    return {
        "version": COUNTER_VERSION,
        "counter_id": validate_counter_id(counter_id),
        "channel_total": stored,
        "current_stream": {"stream_id": "", "value": stored},
        "viewers": {},
    }


class CounterStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.index_path = self.directory / "index.json"
        self._index_lock = RLock()
        self._locks_guard = RLock()
        self._counter_locks: dict[str, RLock] = {}

    def _lock_for(self, counter_id: str) -> RLock:
        counter_id = validate_counter_id(counter_id)
        with self._locks_guard:
            return self._counter_locks.setdefault(counter_id, RLock())

    def _counter_path(self, counter_id: str) -> Path:
        return self.directory / f"{validate_counter_id(counter_id)}.json"

    @staticmethod
    def _check_version(payload: dict[str, Any], expected: int, label: str) -> None:
        if payload.get("version") != expected:
            raise ValueError(f"Unsupported {label} version {payload.get('version')!r}; expected {expected}.")

    @classmethod
    def _definitions_from_payload(cls, payload: Any) -> list[CounterDefinition]:
        if not isinstance(payload, dict):
            raise ValueError("Counter index must be a JSON object.")
        cls._check_version(payload, INDEX_VERSION, "counter index")
        raw = payload.get("counters", [])
        if not isinstance(raw, list):
            raise ValueError("Counter index counters must be a list.")
        if any(not isinstance(item, dict) for item in raw):
            raise ValueError("Every counter definition must be a JSON object.")
        definitions = [CounterDefinition.from_dict(item) for item in raw]
        ids = [item.counter_id for item in definitions]
        if len(ids) != len(set(ids)):
            raise ValueError("Counter index contains duplicate IDs.")
        return sorted(definitions, key=lambda item: item.counter_id)

    def list_definitions(self) -> list[CounterDefinition]:
        with self._index_lock:
            if not self.index_path.exists() and not self.index_path.with_suffix(".json.bak").exists():
                return []
            payload = load_json_with_backup(self.index_path)
            try:
                return self._definitions_from_payload(payload)
            except (TypeError, ValueError):
                backup = self.index_path.with_suffix(".json.bak")
                if not backup.exists():
                    raise
                return self._definitions_from_payload(load_json_with_backup(backup))

    def save_definitions(self, definitions: list[CounterDefinition]) -> None:
        with self._index_lock:
            ordered = sorted(definitions, key=lambda item: item.counter_id)
            ids = [item.counter_id for item in ordered]
            if len(ids) != len(set(ids)):
                raise ValueError("Counter definitions contain duplicate IDs.")
            atomic_write_json(self.index_path, {"version": INDEX_VERSION, "counters": [item.to_dict() for item in ordered]})

    def create(self, definition: CounterDefinition) -> None:
        with self._index_lock:
            definitions = self.list_definitions()
            if any(item.counter_id == definition.counter_id for item in definitions):
                raise ValueError(f'Counter ID "{definition.counter_id}" already exists.')
            path = self._counter_path(definition.counter_id)
            with self._lock_for(definition.counter_id):
                if path.exists():
                    raise ValueError(f'Counter data file "{path.name}" already exists.')
                atomic_write_json(
                    path,
                    empty_counter_payload(
                        definition.counter_id,
                        definition.reset_value,
                        definition.numeric_type,
                    ),
                )
                try:
                    self.save_definitions([*definitions, definition])
                except Exception:
                    path.unlink(missing_ok=True)
                    raise

    def update_definition(self, definition: CounterDefinition) -> None:
        with self._index_lock:
            definitions = self.list_definitions()
            if not any(item.counter_id == definition.counter_id for item in definitions):
                raise KeyError(definition.counter_id)
            self.save_definitions([definition if item.counter_id == definition.counter_id else item for item in definitions])

    def _read_data_unlocked(self, counter_id: str) -> dict[str, Any]:
        path = self._counter_path(counter_id)
        if not path.exists() and not path.with_suffix(".json.bak").exists():
            definition = next(
                (item for item in self.list_definitions() if item.counter_id == counter_id),
                None,
            )
            return empty_counter_payload(
                counter_id,
                definition.reset_value if definition else "0",
                definition.numeric_type if definition else "integer",
            )
        payload = load_json_with_backup(path)
        try:
            return self._validate_counter_payload(payload, counter_id)
        except (TypeError, ValueError):
            backup = path.with_suffix(".json.bak")
            if not backup.exists():
                raise
            return self._validate_counter_payload(load_json_with_backup(backup), counter_id)

    @classmethod
    def _validate_counter_payload(cls, payload: Any, counter_id: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValueError(f"Counter {counter_id} data must be a JSON object.")
        cls._check_version(payload, COUNTER_VERSION, f"counter {counter_id}")
        viewers = payload.get("viewers", {})
        current_stream = payload.get("current_stream", {})
        if payload.get("counter_id") != counter_id or not isinstance(viewers, dict) or not isinstance(current_stream, dict):
            raise ValueError(f"Counter {counter_id} data is invalid.")
        try:
            parse_counter_number(payload.get("channel_total", "0"))
            parse_counter_number(current_stream.get("value", "0"))
            for user_id, viewer in viewers.items():
                if not str(user_id).strip() or not isinstance(viewer, dict) or not isinstance(viewer.get("current_stream", {}), dict):
                    raise ValueError
                parse_counter_number(viewer.get("total", "0"))
                parse_counter_number(viewer.get("current_stream", {}).get("value", "0"))
        except (TypeError, ValueError) as error:
            raise ValueError(f"Counter {counter_id} data is invalid.") from error
        return payload

    def read_data(self, counter_id: str) -> dict[str, Any]:
        with self._lock_for(counter_id):
            return self._read_data_unlocked(counter_id)

    def mutate_data(self, counter_id: str, mutation: Callable[[dict[str, Any]], Any]) -> Any:
        with self._lock_for(counter_id):
            original = self._read_data_unlocked(counter_id)
            working = {**original, "current_stream": dict(original.get("current_stream", {})), "viewers": {str(key): {**value, "current_stream": dict(value.get("current_stream", {}))} for key, value in original.get("viewers", {}).items() if isinstance(value, dict)}}
            result = mutation(working)
            atomic_write_json(self._counter_path(counter_id), working)
            return result

    def delete(self, counter_id: str) -> None:
        counter_id = validate_counter_id(counter_id)
        with self._index_lock, self._lock_for(counter_id):
            definitions = self.list_definitions()
            if not any(item.counter_id == counter_id for item in definitions):
                raise KeyError(counter_id)
            path = self._counter_path(counter_id)
            backup = path.with_suffix(".json.bak")
            staged: list[tuple[Path, Path]] = []
            try:
                for source in (path, backup):
                    if source.exists():
                        destination = source.with_suffix(source.suffix + ".deleting")
                        source.replace(destination)
                        staged.append((source, destination))
            except OSError:
                for source, destination in reversed(staged):
                    if destination.exists():
                        destination.replace(source)
                raise
            try:
                self.save_definitions([item for item in definitions if item.counter_id != counter_id])
            except Exception:
                for source, destination in reversed(staged):
                    if destination.exists():
                        destination.replace(source)
                raise
            for _source, destination in staged:
                destination.unlink(missing_ok=True)
