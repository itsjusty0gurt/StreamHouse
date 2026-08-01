from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic
from typing import Iterable
from uuid import uuid4

from products.hub.automation.models import TriggerEvent
from shared.streamhouse_runtime.json_store import atomic_write_json, load_json_with_backup
from shared.streamhouse_runtime.paths import user_data_root


DUPLICATE_POLICIES = frozenset({"allow", "ignore", "replace"})


@dataclass(slots=True)
class AutomationQueueDefinition:
    queue_id: str
    name: str
    paused: bool = False
    max_length: int = 100
    duplicate_policy: str = "allow"
    delay_seconds: float = 0.0

    @classmethod
    def from_dict(cls, values: dict) -> AutomationQueueDefinition:
        return cls(
            queue_id=str(values.get("queue_id", "")) or uuid4().hex,
            name=str(values.get("name", "")),
            paused=bool(values.get("paused", False)),
            max_length=int(values.get("max_length", 100)),
            duplicate_policy=str(values.get("duplicate_policy", "allow")),
            delay_seconds=float(values.get("delay_seconds", 0)),
        )


@dataclass(frozen=True, slots=True)
class QueuedRoutine:
    item_id: str
    queue_id: str
    routine_id: str
    routine_name: str
    trigger: TriggerEvent


@dataclass(frozen=True, slots=True)
class QueueAddResult:
    accepted: bool
    item: QueuedRoutine | None = None
    detail: str = ""


class AutomationQueueStore:
    VERSION = 1

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_root() / "automation" / "queues.json"
        self.queues: list[AutomationQueueDefinition] = []

    def load(self) -> list[AutomationQueueDefinition]:
        if not self.path.exists():
            self.queues = []
            return []
        payload = load_json_with_backup(self.path)
        if not isinstance(payload, dict):
            raise ValueError("Automation queues must contain a JSON object.")
        if int(payload.get("version", 1)) > self.VERSION:
            raise ValueError("Automation queue data is newer than this app.")
        raw = payload.get("queues", [])
        if not isinstance(raw, list):
            raise ValueError("Automation queues must contain a queue list.")
        queues = [
            AutomationQueueDefinition.from_dict(value)
            for value in raw
            if isinstance(value, dict)
        ]
        self._validate(queues)
        self.queues = queues
        return list(self.queues)

    def save(self) -> None:
        self._validate(self.queues)
        atomic_write_json(
            self.path,
            {"version": self.VERSION, "queues": [asdict(queue) for queue in self.queues]},
        )

    def get(self, queue_id: str) -> AutomationQueueDefinition | None:
        return next((queue for queue in self.queues if queue.queue_id == queue_id), None)

    def add(
        self,
        name: str,
        *,
        max_length: int = 100,
        duplicate_policy: str = "allow",
        delay_seconds: float = 0,
    ) -> AutomationQueueDefinition:
        queue = AutomationQueueDefinition(
            uuid4().hex,
            name,
            max_length=max_length,
            duplicate_policy=duplicate_policy,
            delay_seconds=delay_seconds,
        )
        values = [*self.queues, queue]
        self._validate(values)
        self.queues = values
        self.save()
        return queue

    def update(self, queue_id: str, **changes) -> AutomationQueueDefinition:
        queue = self.get(queue_id)
        if queue is None:
            raise ValueError("The selected queue no longer exists.")
        for key in ("name", "paused", "max_length", "duplicate_policy", "delay_seconds"):
            if key in changes:
                setattr(queue, key, changes[key])
        self._validate(self.queues)
        self.save()
        return queue

    def delete(self, queue_id: str) -> bool:
        queue = self.get(queue_id)
        if queue is None:
            return False
        self.queues.remove(queue)
        self.save()
        return True

    @staticmethod
    def _validate(queues: Iterable[AutomationQueueDefinition]) -> None:
        values = list(queues)
        ids = [queue.queue_id for queue in values]
        names: set[str] = set()
        if len(ids) != len(set(ids)):
            raise ValueError("Automation queue IDs must be unique.")
        for queue in values:
            queue.name = queue.name.strip()
            if not queue.name:
                raise ValueError("Queue names cannot be empty.")
            folded = queue.name.casefold()
            if folded in names:
                raise ValueError("Queue names must be unique.")
            names.add(folded)
            queue.max_length = max(1, min(int(queue.max_length), 10_000))
            queue.delay_seconds = max(0.0, min(float(queue.delay_seconds), 3600.0))
            queue.duplicate_policy = queue.duplicate_policy.strip().casefold()
            if queue.duplicate_policy not in DUPLICATE_POLICIES:
                raise ValueError("Choose a valid duplicate policy.")


class AutomationQueueManager:
    def __init__(self, store: AutomationQueueStore) -> None:
        self.store = store
        self.pending: dict[str, list[QueuedRoutine]] = {}
        self.current: dict[str, QueuedRoutine] = {}
        self.ready_at: dict[str, float] = {}

    def enqueue(
        self,
        queue_id: str,
        routine_id: str,
        routine_name: str,
        trigger: TriggerEvent,
    ) -> QueueAddResult:
        queue = self.store.get(queue_id)
        if queue is None:
            return QueueAddResult(False, detail="The assigned automation queue no longer exists.")
        items = self.pending.setdefault(queue_id, [])
        duplicate = any(item.routine_id == routine_id for item in items) or (
            self.current.get(queue_id) is not None
            and self.current[queue_id].routine_id == routine_id
        )
        if duplicate and queue.duplicate_policy == "ignore":
            return QueueAddResult(False, detail=f'Ignored duplicate routine "{routine_name}".')
        if duplicate and queue.duplicate_policy == "replace":
            items[:] = [item for item in items if item.routine_id != routine_id]
        if len(items) >= queue.max_length:
            return QueueAddResult(False, detail=f'Queue "{queue.name}" is full.')
        item = QueuedRoutine(
            uuid4().hex,
            queue_id,
            routine_id,
            routine_name,
            trigger,
        )
        items.append(item)
        return QueueAddResult(True, item, f'Queued in "{queue.name}".')

    def take_ready(self, queue_id: str, *, now: float | None = None) -> QueuedRoutine | None:
        queue = self.store.get(queue_id)
        current_time = monotonic() if now is None else now
        if (
            queue is None
            or queue.paused
            or queue_id in self.current
            or current_time < self.ready_at.get(queue_id, 0)
        ):
            return None
        items = self.pending.get(queue_id, [])
        if not items:
            return None
        item = items.pop(0)
        self.current[queue_id] = item
        return item

    def complete(self, queue_id: str, *, now: float | None = None) -> None:
        queue = self.store.get(queue_id)
        self.current.pop(queue_id, None)
        if queue is not None:
            self.ready_at[queue_id] = (monotonic() if now is None else now) + queue.delay_seconds

    def remove(self, queue_id: str, item_id: str) -> bool:
        items = self.pending.get(queue_id, [])
        item = next((value for value in items if value.item_id == item_id), None)
        if item is None:
            return False
        items.remove(item)
        return True

    def clear(self, queue_id: str) -> int:
        items = self.pending.get(queue_id, [])
        count = len(items)
        items.clear()
        return count

    def reorder(self, queue_id: str, item_ids: Iterable[str]) -> None:
        items = self.pending.get(queue_id, [])
        ordered = list(item_ids)
        existing = [item.item_id for item in items]
        if len(ordered) != len(set(ordered)) or set(ordered) != set(existing):
            raise ValueError("The queue order must contain every pending item exactly once.")
        by_id = {item.item_id: item for item in items}
        self.pending[queue_id] = [by_id[item_id] for item_id in ordered]

    def count(self, queue_id: str) -> int:
        return len(self.pending.get(queue_id, []))
