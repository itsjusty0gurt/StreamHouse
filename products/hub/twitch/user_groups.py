from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from shared.streamhouse_runtime.json_store import (
    UnsupportedJsonSchemaError,
    atomic_write_bytes,
    atomic_write_json,
    json_store_exists,
    load_validated_json,
)
from shared.streamhouse_runtime.paths import user_data_root

if TYPE_CHECKING:
    from products.hub.twitch.chatter_history import ChatterHistoryStore


SYSTEM_BOTS_GROUP_ID = "streamhouse.system.bots"
SYSTEM_REGULARS_GROUP_ID = "streamhouse.system.regulars"


@dataclass(frozen=True, slots=True)
class UserGroup:
    group_id: str
    name: str
    kind: str
    protected: bool
    membership_editable: bool


SYSTEM_GROUPS = (
    UserGroup(SYSTEM_BOTS_GROUP_ID, "Bots", "system", True, True),
    UserGroup(SYSTEM_REGULARS_GROUP_ID, "Regulars", "system", True, False),
)


class UserGroupStore:
    """One durable source of system/custom groups and user memberships."""

    VERSION = 1

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or user_data_root() / "memory" / "user_groups.json"
        self.groups: dict[str, UserGroup] = {
            group.group_id: group for group in SYSTEM_GROUPS
        }
        self.memberships: dict[str, set[str]] = {}

    def load(self) -> None:
        if not json_store_exists(self.path):
            return
        groups, memberships = load_validated_json(self.path, self._parse_payload)
        self.groups = groups
        self.memberships = memberships

    def _parse_payload(
        self, payload: object
    ) -> tuple[dict[str, UserGroup], dict[str, set[str]]]:
        if not isinstance(payload, dict):
            raise ValueError("User groups must contain a JSON object.")
        if set(payload) != {"version", "groups", "memberships"}:
            raise ValueError("User groups contain unexpected fields.")
        if payload.get("version") != self.VERSION:
            raise UnsupportedJsonSchemaError("User groups use an unsupported schema version.")
        raw_groups = payload.get("groups")
        raw_memberships = payload.get("memberships")
        if not isinstance(raw_groups, list):
            raise ValueError("User group definitions must be a list.")
        if not isinstance(raw_memberships, dict):
            raise ValueError("User group memberships must be an object.")

        groups: dict[str, UserGroup] = {}
        names: set[str] = set()
        expected = {"id", "name", "kind", "protected", "membership_editable"}
        for raw in raw_groups:
            if not isinstance(raw, dict) or set(raw) != expected:
                raise ValueError("Each user group has an invalid definition.")
            group = UserGroup(
                str(raw.get("id", "")).strip(),
                self.normalize_name(raw.get("name", "")),
                str(raw.get("kind", "")).strip(),
                bool(raw.get("protected")),
                bool(raw.get("membership_editable")),
            )
            if not group.group_id or not group.name or group.kind not in {"system", "custom"}:
                raise ValueError("Every user group requires a stable ID, name, and valid kind.")
            if group.group_id in groups or group.name.casefold() in names:
                raise ValueError("User group IDs and names must be unique.")
            groups[group.group_id] = group
            names.add(group.name.casefold())
        for required in SYSTEM_GROUPS:
            if groups.get(required.group_id) != required:
                raise ValueError(f'The protected system group "{required.name}" is invalid or missing.')

        memberships: dict[str, set[str]] = {}
        for raw_user_id, raw_ids in raw_memberships.items():
            user_id = str(raw_user_id).strip()
            if not user_id or not isinstance(raw_ids, list):
                raise ValueError("Each user membership requires a stable Twitch ID.")
            group_ids = {str(value).strip() for value in raw_ids}
            if "" in group_ids or any(group_id not in groups for group_id in group_ids):
                raise ValueError("User membership references an unknown group.")
            if len(group_ids) != len(raw_ids):
                raise ValueError("User membership contains duplicate group IDs.")
            if group_ids:
                memberships[user_id] = group_ids
        return groups, memberships

    @staticmethod
    def normalize_name(value: object) -> str:
        return " ".join(str(value).split())

    def save(self) -> None:
        atomic_write_json(self.path, self.to_payload())

    def to_payload(self) -> dict[str, object]:
        return {
            "version": self.VERSION,
            "groups": [
                {
                    "id": group.group_id,
                    "name": group.name,
                    "kind": group.kind,
                    "protected": group.protected,
                    "membership_editable": group.membership_editable,
                }
                for group in sorted(
                    self.groups.values(),
                    key=lambda item: (item.kind != "system", item.name.casefold()),
                )
            ],
            "memberships": {
                user_id: sorted(group_ids)
                for user_id, group_ids in sorted(self.memberships.items())
                if group_ids
            },
        }


class UserGroupService:
    """Validated mutation and lookup boundary for the unified group domain."""

    def __init__(self, store: UserGroupStore) -> None:
        self.store = store

    def list_groups(self) -> tuple[UserGroup, ...]:
        return tuple(
            sorted(
                self.store.groups.values(),
                key=lambda item: (item.kind != "system", item.name.casefold()),
            )
        )

    def get_group(self, group_id: str) -> UserGroup | None:
        return self.store.groups.get(str(group_id).strip())

    def create_group(self, name: str) -> UserGroup:
        group = UserGroup(uuid4().hex, self._available_name(name), "custom", False, True)
        self.store.groups[group.group_id] = group
        self._save_or_rollback(lambda: self.store.groups.pop(group.group_id, None))
        return group

    def rename_group(self, group_id: str, name: str) -> UserGroup:
        current = self._require_group(group_id)
        if current.protected:
            raise ValueError("Protected system groups cannot be renamed.")
        updated = UserGroup(
            current.group_id,
            self._available_name(name, excluding=current.group_id),
            current.kind,
            current.protected,
            current.membership_editable,
        )
        self.store.groups[current.group_id] = updated
        self._save_or_rollback(lambda: self.store.groups.__setitem__(current.group_id, current))
        return updated

    def delete_group(self, group_id: str) -> None:
        current = self._require_group(group_id)
        if current.protected:
            raise ValueError("Protected system groups cannot be deleted.")
        previous = {user_id: set(ids) for user_id, ids in self.store.memberships.items()}
        self.store.groups.pop(current.group_id)
        for user_id, group_ids in tuple(self.store.memberships.items()):
            group_ids.discard(current.group_id)
            if not group_ids:
                self.store.memberships.pop(user_id, None)

        def rollback() -> None:
            self.store.groups[current.group_id] = current
            self.store.memberships = previous

        self._save_or_rollback(rollback)

    def assign_member(self, group_id: str, user_id: str) -> None:
        group = self._require_group(group_id)
        if not group.membership_editable:
            raise ValueError(f'{group.name} membership is managed automatically by Hub.')
        self._set_membership(group.group_id, user_id, True)

    def remove_member(self, group_id: str, user_id: str) -> None:
        group = self._require_group(group_id)
        if not group.membership_editable:
            raise ValueError(f'{group.name} membership is managed automatically by Hub.')
        self._set_membership(group.group_id, user_id, False)

    def sync_system_membership(self, group_id: str, user_id: str, present: bool) -> bool:
        if self._require_group(group_id).kind != "system":
            raise ValueError("Only system-group membership can be synchronized.")
        return self._set_membership(group_id, user_id, present, save=False)

    def save_synced_memberships(self) -> None:
        self.store.save()

    def migrate_chatter_store(self, chatter: ChatterHistoryStore) -> None:
        """Convert released chatter v8 group state, then publish only v9 state."""
        legacy = dict(chatter.legacy_manual_groups)
        is_legacy_schema = chatter.loaded_schema_version == chatter.LEGACY_VERSION
        previous_memberships = {
            user_id: set(group_ids)
            for user_id, group_ids in self.store.memberships.items()
        }
        previous_group_bytes = (
            self.store.path.read_bytes() if self.store.path.exists() else None
        )
        changed = self.sync_all_chatter_records(chatter, save=False)
        for user_id, old_group in legacy.items():
            if old_group == "Bots":
                changed |= self.sync_system_membership(
                    SYSTEM_BOTS_GROUP_ID, user_id, True
                )
            elif old_group == "Regulars":
                changed |= self.sync_system_membership(
                    SYSTEM_REGULARS_GROUP_ID, user_id, True
                )

        if is_legacy_schema:
            chatter.create_v8_migration_backup()
        if changed or is_legacy_schema or not self.store.path.exists():
            try:
                self.store.save()
            except Exception:
                self.store.memberships = previous_memberships
                raise
        if is_legacy_schema:
            try:
                chatter.save()
            except Exception:
                self.store.memberships = previous_memberships
                if previous_group_bytes is None:
                    self.store.path.unlink(missing_ok=True)
                else:
                    atomic_write_bytes(self.store.path, previous_group_bytes)
                raise
            chatter.legacy_manual_groups.clear()

    def sync_chatter_record(
        self,
        chatter: ChatterHistoryStore,
        user_id: str,
        *,
        observed_bot: bool = False,
        save: bool = True,
    ) -> bool:
        record = chatter.records.get(str(user_id).strip())
        if record is None:
            return False
        previous_memberships = (
            {
                existing_id: set(group_ids)
                for existing_id, group_ids in self.store.memberships.items()
            }
            if save
            else None
        )
        changed = False
        if observed_bot or record.is_bot:
            changed |= self.sync_system_membership(
                SYSTEM_BOTS_GROUP_ID, record.user_id, True
            )
        if chatter.qualifies_as_regular(record.user_id) is True:
            changed |= self.sync_system_membership(
                SYSTEM_REGULARS_GROUP_ID, record.user_id, True
            )
        if changed and save:
            try:
                self.store.save()
            except Exception:
                self.store.memberships = previous_memberships or {}
                raise
        return changed

    def sync_all_chatter_records(
        self, chatter: ChatterHistoryStore, *, save: bool = True
    ) -> bool:
        previous_memberships = (
            {
                user_id: set(group_ids)
                for user_id, group_ids in self.store.memberships.items()
            }
            if save
            else None
        )
        changed = False
        for user_id in tuple(chatter.records):
            changed |= self.sync_chatter_record(
                chatter, user_id, save=False
            )
        if changed and save:
            try:
                self.store.save()
            except Exception:
                self.store.memberships = previous_memberships or {}
                raise
        return changed

    def remove_user(self, user_id: str) -> None:
        stable_user_id = str(user_id).strip()
        previous = self.store.memberships.pop(stable_user_id, None)
        if previous is not None:
            self._save_or_rollback(
                lambda: self.store.memberships.__setitem__(stable_user_id, previous)
            )

    def merge_users(self, source_user_id: str, target_user_id: str) -> None:
        source = str(source_user_id).strip()
        target = str(target_user_id).strip()
        if not source or not target or source == target:
            return
        old_source = set(self.store.memberships.get(source, set()))
        old_target = set(self.store.memberships.get(target, set()))
        if not old_source:
            return
        self.store.memberships[target] = old_target | old_source
        self.store.memberships.pop(source, None)

        def rollback() -> None:
            self.store.memberships[source] = old_source
            if old_target:
                self.store.memberships[target] = old_target
            else:
                self.store.memberships.pop(target, None)

        self._save_or_rollback(rollback)

    def is_member(self, user_id: str, group_id: str) -> bool:
        return str(group_id).strip() in self.store.memberships.get(str(user_id).strip(), set())

    def is_bot(self, user_id: str) -> bool:
        return self.is_member(user_id, SYSTEM_BOTS_GROUP_ID)

    def is_regular(self, user_id: str) -> bool:
        return self.is_member(user_id, SYSTEM_REGULARS_GROUP_ID)

    def groups_for_user(self, user_id: str) -> tuple[UserGroup, ...]:
        group_ids = self.store.memberships.get(str(user_id).strip(), set())
        return tuple(group for group in self.list_groups() if group.group_id in group_ids)

    def member_ids(self, group_id: str) -> tuple[str, ...]:
        self._require_group(group_id)
        return tuple(
            sorted(
                user_id
                for user_id, group_ids in self.store.memberships.items()
                if str(group_id).strip() in group_ids
            )
        )

    def _set_membership(
        self, group_id: str, user_id: str, present: bool, *, save: bool = True
    ) -> bool:
        stable_user_id = str(user_id).strip()
        if not stable_user_id:
            raise ValueError("A stable Twitch user ID is required.")
        previous = set(self.store.memberships.get(stable_user_id, set()))
        values = self.store.memberships.setdefault(stable_user_id, set())
        if present:
            values.add(group_id)
        else:
            values.discard(group_id)
        if not values:
            self.store.memberships.pop(stable_user_id, None)
        changed = previous != self.store.memberships.get(stable_user_id, set())
        if save and changed:

            def rollback() -> None:
                if previous:
                    self.store.memberships[stable_user_id] = previous
                else:
                    self.store.memberships.pop(stable_user_id, None)
            self._save_or_rollback(rollback)
        return changed

    def _save_or_rollback(self, rollback) -> None:
        try:
            self.store.save()
        except Exception:
            rollback()
            raise

    def _require_group(self, group_id: str) -> UserGroup:
        group = self.get_group(group_id)
        if group is None:
            raise ValueError("The selected user group no longer exists.")
        return group

    def _available_name(self, name: str, *, excluding: str = "") -> str:
        clean_name = self.store.normalize_name(name)
        if not clean_name:
            raise ValueError("Group name is required.")
        if len(clean_name) > 80:
            raise ValueError("Group name cannot exceed 80 characters.")
        if any(
            group.group_id != excluding and group.name.casefold() == clean_name.casefold()
            for group in self.store.groups.values()
        ):
            raise ValueError("A user group with that name already exists.")
        return clean_name
