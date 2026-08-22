from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4

from products.hub.automation.models import RoutineDefinition, RoutineGroup, TaskDefinition
from shared.streamhouse_runtime.json_store import atomic_write_json, load_json_with_backup
from shared.streamhouse_runtime.paths import user_data_root


class RoutineStore:
    """Persistent routine editor model.

    An empty ``group_id`` is the built-in Ungrouped section. Custom groups use
    stable IDs, so their names and display order can change without rewriting
    routine relationships.
    """

    VERSION = 4

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_root() / "automation" / "routines.json"
        self.groups: list[RoutineGroup] = []
        self.routines: list[RoutineDefinition] = []

    def load(self) -> list[RoutineDefinition]:
        if not self.path.exists():
            self.groups = []
            self.routines = []
            return []
        payload = load_json_with_backup(self.path)
        if not isinstance(payload, dict):
            raise ValueError("Routines must contain a JSON object.")
        version = int(payload.get("version", 0))
        if version != self.VERSION:
            raise ValueError(
                "Routine data uses a discarded pre-alpha schema and must be reset."
            )
        raw_groups = payload.get("groups", [])
        values = payload.get("routines", [])
        if not isinstance(raw_groups, list):
            raise ValueError("Routines must contain a group list.")
        if not isinstance(values, list):
            raise ValueError("Routines must contain a routine list.")
        groups = [
            RoutineGroup.from_dict(value)
            for value in raw_groups
            if isinstance(value, dict)
        ]
        routines = [
            RoutineDefinition.from_dict(value)
            for value in values
            if isinstance(value, dict)
        ]
        self._validate_state(groups, routines)
        self.groups = groups
        self.routines = routines
        return list(self.routines)

    def save(self) -> None:
        self._validate_state(self.groups, self.routines)
        self._write(self.groups, self.routines)

    def _write(
        self,
        groups: Iterable[RoutineGroup],
        routines: Iterable[RoutineDefinition],
    ) -> None:
        atomic_write_json(
            self.path,
            {
                "version": self.VERSION,
                "groups": [asdict(group) for group in groups],
                "routines": [asdict(routine) for routine in routines],
            },
        )

    def _commit(
        self,
        groups: list[RoutineGroup],
        routines: list[RoutineDefinition],
    ) -> None:
        self._validate_state(groups, routines)
        self._write(groups, routines)
        self.groups = groups
        self.routines = routines

    def get(self, routine_id: str) -> RoutineDefinition | None:
        return next(
            (
                routine
                for routine in self.routines
                if routine.routine_id == routine_id
            ),
            None,
        )

    def get_group(self, group_id: str) -> RoutineGroup | None:
        return next(
            (group for group in self.groups if group.group_id == group_id),
            None,
        )

    def grouped(self, group_id: str = "") -> tuple[RoutineDefinition, ...]:
        return tuple(
            routine for routine in self.routines if routine.group_id == group_id
        )

    def matching(self, trigger_id: str) -> tuple[RoutineDefinition, ...]:
        return tuple(
            routine
            for routine in self.routines
            if routine.enabled and trigger_id in routine.trigger_ids
        )

    def link_trigger(self, routine_id: str, trigger_id: str) -> RoutineDefinition:
        clean_trigger_id = trigger_id.strip()
        if not clean_trigger_id:
            raise ValueError("A trigger ID is required.")
        routines = deepcopy(self.routines)
        routine = self._find_routine(routines, routine_id)
        if clean_trigger_id in routine.trigger_ids:
            raise ValueError("That trigger is already linked to the routine.")
        if not routine.trigger_id:
            routine.trigger_id = clean_trigger_id
        else:
            routine.additional_trigger_ids.append(clean_trigger_id)
        self._commit(deepcopy(self.groups), routines)
        return self.get(routine_id)  # type: ignore[return-value]

    def unlink_trigger(self, routine_id: str, trigger_id: str) -> RoutineDefinition:
        routines = deepcopy(self.routines)
        routine = self._find_routine(routines, routine_id)
        if routine.trigger_id == trigger_id:
            routine.trigger_id = (
                routine.additional_trigger_ids.pop(0)
                if routine.additional_trigger_ids
                else ""
            )
        elif trigger_id in routine.additional_trigger_ids:
            routine.additional_trigger_ids.remove(trigger_id)
        else:
            raise ValueError("That trigger is not linked to the routine.")
        self._commit(deepcopy(self.groups), routines)
        return self.get(routine_id)  # type: ignore[return-value]

    # Groups -----------------------------------------------------------------

    def add_group(self, name: str, *, collapsed: bool = False) -> RoutineGroup:
        group = RoutineGroup(
            group_id=uuid4().hex,
            name=self._clean_group_name(name),
            collapsed=bool(collapsed),
        )
        groups = deepcopy(self.groups)
        groups.append(group)
        self._commit(groups, deepcopy(self.routines))
        return self.get_group(group.group_id)  # type: ignore[return-value]

    def update_group(
        self,
        group_id: str,
        *,
        name: str | None = None,
        collapsed: bool | None = None,
    ) -> RoutineGroup:
        groups = deepcopy(self.groups)
        group = self._find_group(groups, group_id)
        if name is not None:
            group.name = self._clean_group_name(name)
        if collapsed is not None:
            group.collapsed = bool(collapsed)
        self._commit(groups, deepcopy(self.routines))
        return self.get_group(group_id)  # type: ignore[return-value]

    def reorder_group(self, group_id: str, index: int) -> RoutineGroup:
        groups = deepcopy(self.groups)
        group = self._find_group(groups, group_id)
        groups.remove(group)
        groups.insert(max(0, min(int(index), len(groups))), group)
        self._commit(groups, deepcopy(self.routines))
        return self.get_group(group_id)  # type: ignore[return-value]

    def delete_group(self, group_id: str) -> bool:
        if not group_id:
            return False
        groups = deepcopy(self.groups)
        try:
            group = self._find_group(groups, group_id)
        except ValueError:
            return False
        groups.remove(group)
        routines = deepcopy(self.routines)
        for routine in routines:
            if routine.group_id == group_id:
                routine.group_id = ""
        self._commit(groups, routines)
        return True

    # Routines ---------------------------------------------------------------

    def add(
        self,
        name: str,
        *,
        trigger_id: str = "",
        group_id: str = "",
        description: str = "",
        enabled: bool = True,
        queue_id: str = "",
        tasks: Iterable[TaskDefinition] = (),
    ) -> RoutineDefinition:
        routine = RoutineDefinition(
            routine_id=uuid4().hex,
            name=self._clean_name(name),
            trigger_id=trigger_id.strip(),
            tasks=deepcopy(list(tasks)),
            enabled=bool(enabled),
            group_id=group_id,
            description=description.strip()[:500],
            queue_id=queue_id.strip(),
        )
        routines = deepcopy(self.routines)
        routines.append(routine)
        self._commit(deepcopy(self.groups), routines)
        return self.get(routine.routine_id)  # type: ignore[return-value]

    def update(
        self,
        routine_id: str,
        *,
        name: str | None = None,
        trigger_id: str | None = None,
        group_id: str | None = None,
        description: str | None = None,
        enabled: bool | None = None,
        queue_id: str | None = None,
    ) -> RoutineDefinition:
        routines = deepcopy(self.routines)
        routine = self._find_routine(routines, routine_id)
        if name is not None:
            routine.name = self._clean_name(name)
        if trigger_id is not None:
            if routine.managed_by and trigger_id.strip() != routine.trigger_id:
                raise ValueError(
                    "Service-managed triggers must be edited through their service."
                )
            routine.trigger_id = trigger_id.strip()
        if group_id is not None:
            routine.group_id = group_id
        if description is not None:
            routine.description = description.strip()[:500]
        if enabled is not None:
            routine.enabled = bool(enabled)
        if queue_id is not None:
            routine.queue_id = queue_id.strip()
        self._commit(deepcopy(self.groups), routines)
        return self.get(routine_id)  # type: ignore[return-value]

    def move_routine(
        self,
        routine_id: str,
        group_id: str,
        index: int,
    ) -> RoutineDefinition:
        """Move a routine to a group and position it within that group."""
        if group_id and self.get_group(group_id) is None:
            raise ValueError("The destination routine group no longer exists.")
        routines = deepcopy(self.routines)
        routine = self._find_routine(routines, routine_id)
        routines.remove(routine)
        routine.group_id = group_id
        siblings = [value for value in routines if value.group_id == group_id]
        target_index = max(0, min(int(index), len(siblings)))
        if not siblings:
            insert_at = len(routines)
        elif target_index < len(siblings):
            insert_at = routines.index(siblings[target_index])
        else:
            insert_at = routines.index(siblings[-1]) + 1
        routines.insert(insert_at, routine)
        self._commit(deepcopy(self.groups), routines)
        return self.get(routine_id)  # type: ignore[return-value]

    def duplicate(
        self,
        routine_id: str,
        *,
        name: str | None = None,
        include_trigger: bool = False,
    ) -> RoutineDefinition:
        source = self.get(routine_id)
        if source is None:
            raise ValueError("The selected routine no longer exists.")
        tasks = deepcopy(source.tasks)
        for task in tasks:
            task.task_id = uuid4().hex
            task.managed_key = ""
        duplicate = RoutineDefinition(
            routine_id=uuid4().hex,
            name=self._clean_name(name or f"{source.name} Copy"),
            trigger_id=source.trigger_id if include_trigger else "",
            tasks=tasks,
            enabled=source.enabled,
            managed_by="",
            group_id=source.group_id,
            description=source.description,
            additional_trigger_ids=(
                list(source.additional_trigger_ids) if include_trigger else []
            ),
            queue_id=source.queue_id,
        )
        routines = deepcopy(self.routines)
        routines.append(duplicate)
        self._commit(deepcopy(self.groups), routines)
        return self.get(duplicate.routine_id)  # type: ignore[return-value]

    def delete(self, routine_id: str, *, allow_managed: bool = False) -> bool:
        routines = deepcopy(self.routines)
        try:
            routine = self._find_routine(routines, routine_id)
        except ValueError:
            return False
        if routine.managed_by and not allow_managed:
            raise ValueError(
                "Service-managed routines must be deleted through their trigger."
            )
        routines.remove(routine)
        self._commit(deepcopy(self.groups), routines)
        return True

    # Tasks ------------------------------------------------------------------

    def add_task(
        self,
        routine_id: str,
        *,
        task_type: str,
        name: str,
        config: dict[str, Any] | None = None,
        enabled: bool = True,
        index: int | None = None,
    ) -> TaskDefinition:
        routines = deepcopy(self.routines)
        routine = self._find_routine(routines, routine_id)
        task = TaskDefinition(
            task_id=uuid4().hex,
            task_type=task_type.strip().casefold(),
            name=self._clean_name(name),
            config=deepcopy(config or {}),
            enabled=bool(enabled),
        )
        insert_at = len(routine.tasks) if index is None else max(
            0, min(int(index), len(routine.tasks))
        )
        routine.tasks.insert(insert_at, task)
        self._commit(deepcopy(self.groups), routines)
        saved = self.get(routine_id)
        return self._find_task(saved, task.task_id)  # type: ignore[arg-type]

    def update_task(
        self,
        routine_id: str,
        task_id: str,
        *,
        task_type: str | None = None,
        name: str | None = None,
        config: dict[str, Any] | None = None,
        enabled: bool | None = None,
    ) -> TaskDefinition:
        routines = deepcopy(self.routines)
        routine = self._find_routine(routines, routine_id)
        task = self._find_task(routine, task_id)
        if task_type is not None:
            if task.managed_key and task_type.strip().casefold() != task.task_type:
                raise ValueError(
                    "A service-managed task cannot change task provider."
                )
            task.task_type = task_type.strip().casefold()
        if name is not None:
            task.name = self._clean_name(name)
        if config is not None:
            task.config = deepcopy(config)
        if enabled is not None:
            task.enabled = bool(enabled)
        self._commit(deepcopy(self.groups), routines)
        saved = self.get(routine_id)
        return self._find_task(saved, task_id)  # type: ignore[arg-type]

    def move_task(self, routine_id: str, task_id: str, index: int) -> TaskDefinition:
        routines = deepcopy(self.routines)
        routine = self._find_routine(routines, routine_id)
        task = self._find_task(routine, task_id)
        routine.tasks.remove(task)
        routine.tasks.insert(max(0, min(int(index), len(routine.tasks))), task)
        self._commit(deepcopy(self.groups), routines)
        saved = self.get(routine_id)
        return self._find_task(saved, task_id)  # type: ignore[arg-type]

    def reorder_tasks(self, routine_id: str, task_ids: Iterable[str]) -> None:
        routines = deepcopy(self.routines)
        routine = self._find_routine(routines, routine_id)
        ordered_ids = list(task_ids)
        existing_ids = [task.task_id for task in routine.tasks]
        if len(ordered_ids) != len(set(ordered_ids)) or set(ordered_ids) != set(
            existing_ids
        ):
            raise ValueError("The task order must contain every task exactly once.")
        tasks_by_id = {task.task_id: task for task in routine.tasks}
        routine.tasks = [tasks_by_id[task_id] for task_id in ordered_ids]
        self._commit(deepcopy(self.groups), routines)

    def duplicate_task(
        self,
        routine_id: str,
        task_id: str,
        *,
        index: int | None = None,
    ) -> TaskDefinition:
        routines = deepcopy(self.routines)
        routine = self._find_routine(routines, routine_id)
        source = self._find_task(routine, task_id)
        task = deepcopy(source)
        task.task_id = uuid4().hex
        task.name = self._clean_name(f"{task.name} Copy")
        task.managed_key = ""
        source_index = routine.tasks.index(source)
        insert_at = source_index + 1 if index is None else max(
            0, min(int(index), len(routine.tasks))
        )
        routine.tasks.insert(insert_at, task)
        self._commit(deepcopy(self.groups), routines)
        saved = self.get(routine_id)
        return self._find_task(saved, task.task_id)  # type: ignore[arg-type]

    def delete_task(self, routine_id: str, task_id: str) -> bool:
        routines = deepcopy(self.routines)
        routine = self._find_routine(routines, routine_id)
        try:
            task = self._find_task(routine, task_id)
        except ValueError:
            return False
        if task.managed_key:
            raise ValueError(
                "A service-managed task must be deleted through its trigger."
            )
        routine.tasks.remove(task)
        self._commit(deepcopy(self.groups), routines)
        return True

    # Managed service helpers ------------------------------------------------

    def create_managed(
        self,
        *,
        trigger_id: str,
        name: str,
        managed_by: str,
        task_type: str = "",
        task_name: str = "",
        task_config: dict[str, Any] | None = None,
    ) -> RoutineDefinition:
        tasks = []
        if task_type.strip() and task_config is not None:
            tasks.append(
                TaskDefinition(
                    task_id=uuid4().hex,
                    task_type=task_type.strip().casefold(),
                    name=self._clean_name(task_name),
                    config=deepcopy(task_config),
                    managed_key=managed_by,
                )
            )
        routine = RoutineDefinition(
            routine_id=uuid4().hex,
            name=self._clean_name(name),
            trigger_id=trigger_id.strip(),
            managed_by=managed_by,
            tasks=tasks,
        )
        routines = deepcopy(self.routines)
        routines.append(routine)
        self._commit(deepcopy(self.groups), routines)
        return self.get(routine.routine_id)  # type: ignore[return-value]

    def attach_managed(
        self,
        routine_id: str,
        *,
        trigger_id: str,
        managed_by: str,
        task_type: str = "",
        task_name: str = "",
        task_config: dict[str, Any] | None = None,
    ) -> RoutineDefinition:
        routines = deepcopy(self.routines)
        routine = self._find_routine(routines, routine_id)
        if routine.managed_by:
            raise ValueError("The routine already has a managed service trigger.")
        if routine.trigger_id:
            routine.additional_trigger_ids.insert(0, routine.trigger_id)
        routine.trigger_id = trigger_id.strip()
        routine.managed_by = managed_by
        if task_type.strip() and task_config is not None:
            routine.tasks.insert(
                0,
                TaskDefinition(
                    task_id=uuid4().hex,
                    task_type=task_type.strip().casefold(),
                    name=self._clean_name(task_name),
                    config=deepcopy(task_config),
                    managed_key=managed_by,
                ),
            )
        self._commit(deepcopy(self.groups), routines)
        return self.get(routine_id)  # type: ignore[return-value]

    def detach_managed(
        self,
        routine_id: str,
        managed_by: str,
    ) -> RoutineDefinition:
        routines = deepcopy(self.routines)
        routine = self._find_routine(routines, routine_id)
        if routine.managed_by != managed_by:
            raise ValueError("The routine is not managed by that service trigger.")
        routine.trigger_id = (
            routine.additional_trigger_ids.pop(0)
            if routine.additional_trigger_ids
            else ""
        )
        routine.managed_by = ""
        for task in routine.tasks:
            if task.managed_key == managed_by:
                task.managed_key = ""
        self._commit(deepcopy(self.groups), routines)
        return self.get(routine_id)  # type: ignore[return-value]

    def managed_task(
        self,
        routine: RoutineDefinition,
        managed_by: str,
        task_type: str,
    ) -> TaskDefinition | None:
        exact = next(
            (
                task
                for task in routine.tasks
                if task.managed_key == managed_by
                and task.task_type == task_type
            ),
            None,
        )
        if exact is not None:
            return exact
        # Version-one routines did not persist managed_key. Prefer their first
        # matching provider task and mark it during the next managed update.
        return next(
            (task for task in routine.tasks if task.task_type == task_type),
            None,
        )

    def update_managed_task(
        self,
        routine_id: str,
        *,
        name: str,
        managed_by: str,
        task_type: str,
        task_name: str,
        task_config: dict[str, Any] | None,
    ) -> RoutineDefinition:
        routines = deepcopy(self.routines)
        routine = self._find_routine(routines, routine_id)
        if routine.managed_by != managed_by:
            raise ValueError("The selected routine is not managed by this trigger.")
        routine.name = self._clean_name(name)
        task = self.managed_task(routine, managed_by, task_type)
        if task_config is None:
            if task is not None:
                routine.tasks.remove(task)
        elif task is None:
            task = TaskDefinition(
                task_id=uuid4().hex,
                task_type=task_type.strip().casefold(),
                name=self._clean_name(task_name),
                config=deepcopy(task_config),
                managed_key=managed_by,
            )
            routine.tasks.insert(0, task)
        else:
            task.task_type = task_type.strip().casefold()
            task.name = self._clean_name(task_name)
            task.config = deepcopy(task_config)
            task.managed_key = managed_by
        self._commit(deepcopy(self.groups), routines)
        return self.get(routine_id)  # type: ignore[return-value]

    def delete_managed(self, routine_id: str, managed_by: str) -> bool:
        routine = self.get(routine_id)
        if routine is None or routine.managed_by != managed_by:
            return False
        return self.delete(routine_id, allow_managed=True)

    # Validation -------------------------------------------------------------

    @classmethod
    def _validate_state(
        cls,
        groups: Iterable[RoutineGroup],
        routines: Iterable[RoutineDefinition],
    ) -> None:
        group_values = list(groups)
        routine_values = list(routines)
        group_ids = [group.group_id for group in group_values]
        if len(set(group_ids)) != len(group_ids):
            raise ValueError("Routine group IDs must be unique.")
        group_names: set[str] = set()
        for group in group_values:
            cls._validate_group(group)
            folded = group.name.casefold()
            if folded in group_names:
                raise ValueError("Routine group names must be unique.")
            group_names.add(folded)
        routine_ids = [routine.routine_id for routine in routine_values]
        if len(set(routine_ids)) != len(routine_ids):
            raise ValueError("Routine IDs must be unique.")
        valid_groups = set(group_ids)
        for routine in routine_values:
            cls._validate_routine(routine)
            if routine.group_id and routine.group_id not in valid_groups:
                raise ValueError("A routine references a missing group.")

    @staticmethod
    def _validate_group(group: RoutineGroup) -> None:
        if not group.group_id or not group.name.strip():
            raise ValueError("Routine groups require an ID and name.")
        if len(group.name.strip()) > 60:
            raise ValueError("Routine group names can contain at most 60 characters.")

    @staticmethod
    def _validate_routine(routine: RoutineDefinition) -> None:
        if not routine.routine_id or not routine.name.strip():
            raise ValueError("Routines require an ID and name.")
        if len(routine.name.strip()) > 100:
            raise ValueError("Routine names can contain at most 100 characters.")
        trigger_ids = list(routine.trigger_ids)
        if len(trigger_ids) != len(set(trigger_ids)):
            raise ValueError("Routine trigger IDs must be unique.")
        task_ids: set[str] = set()
        managed_keys: set[str] = set()
        for task in routine.tasks:
            if not task.task_id or not task.task_type or not task.name.strip():
                raise ValueError("Tasks require an ID, type, and name.")
            if task.task_id in task_ids:
                raise ValueError("Task IDs must be unique within a routine.")
            task_ids.add(task.task_id)
            if task.managed_key:
                if task.managed_key in managed_keys:
                    raise ValueError("Managed task keys must be unique within a routine.")
                managed_keys.add(task.managed_key)

    @staticmethod
    def _clean_name(name: str) -> str:
        value = name.strip()
        if not value:
            raise ValueError("A name is required.")
        return value[:100]

    @classmethod
    def _clean_group_name(cls, name: str) -> str:
        value = name.strip()
        if not value:
            raise ValueError("A group name is required.")
        if value.casefold() == "ungrouped":
            raise ValueError("Ungrouped is reserved for routines without a group.")
        return value[:60]

    @staticmethod
    def _find_group(groups: list[RoutineGroup], group_id: str) -> RoutineGroup:
        group = next((value for value in groups if value.group_id == group_id), None)
        if group is None:
            raise ValueError("The selected routine group no longer exists.")
        return group

    @staticmethod
    def _find_routine(
        routines: list[RoutineDefinition], routine_id: str
    ) -> RoutineDefinition:
        routine = next(
            (value for value in routines if value.routine_id == routine_id), None
        )
        if routine is None:
            raise ValueError("The selected routine no longer exists.")
        return routine

    @staticmethod
    def _find_task(routine: RoutineDefinition, task_id: str) -> TaskDefinition:
        task = next((value for value in routine.tasks if value.task_id == task_id), None)
        if task is None:
            raise ValueError("The selected task no longer exists.")
        return task
