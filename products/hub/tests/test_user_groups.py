from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from products.hub.automation.logic_tasks import IfTask
from products.hub.automation.models import TaskDefinition, TriggerEvent
from products.hub.twitch.chatter_history import ChatterHistoryStore
from products.hub.twitch.tasks import TwitchAutomationTask
from products.hub.twitch.user_groups import (
    SYSTEM_BOTS_GROUP_ID,
    SYSTEM_REGULARS_GROUP_ID,
    UserGroupService,
    UserGroupStore,
)


class UserGroupStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.path = self.root / "memory" / "user_groups.json"
        self.store = UserGroupStore(self.path)
        self.service = UserGroupService(self.store)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_empty_first_run_and_schema_v1_round_trip(self) -> None:
        self.store.load()
        group = self.service.create_group("Auto Shoutout")
        self.service.assign_member(group.group_id, "123")
        self.service.assign_member(group.group_id, "456")
        second = self.service.create_group("Friends")
        self.service.assign_member(second.group_id, "123")

        restored = UserGroupStore(self.path)
        restored.load()

        self.assertEqual(restored.groups[group.group_id].name, "Auto Shoutout")
        self.assertEqual(restored.memberships["123"], {group.group_id, second.group_id})
        self.assertEqual(json.loads(self.path.read_text())["version"], 1)

    def test_create_validation_and_rename_preserve_identity_and_membership(self) -> None:
        with self.assertRaisesRegex(ValueError, "required"):
            self.service.create_group("  ")
        group = self.service.create_group("Friends")
        self.service.assign_member(group.group_id, "123")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.service.create_group(" friends ")

        renamed = self.service.rename_group(group.group_id, "Crew")

        self.assertEqual(renamed.group_id, group.group_id)
        self.assertTrue(self.service.is_member("123", group.group_id))

    def test_delete_removes_memberships_without_touching_chatter_schema(self) -> None:
        chatter_path = self.root / "memory" / "twitch_chatters.json"
        chatter = ChatterHistoryStore(chatter_path)
        chatter.observe_message("123", "Viewer")
        chatter.save()
        before = json.loads(chatter_path.read_text())
        group = self.service.create_group("Crew")
        self.service.assign_member(group.group_id, "123")

        self.service.delete_group(group.group_id)

        self.assertNotIn("123", self.store.memberships)
        self.assertEqual(json.loads(chatter_path.read_text()), before)
        self.assertEqual(before["version"], ChatterHistoryStore.VERSION)
        self.assertIn("123", chatter.records)

    def test_malformed_data_is_preserved_and_not_silently_reset(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"version":1,"groups":"bad","memberships":{}}')

        with self.assertRaises(ValueError):
            self.store.load()

        corrupt = self.path.parent / "corrupt"
        self.assertTrue(any(corrupt.glob("user_groups-*.json")))

    def test_failed_atomic_save_rolls_back_memory(self) -> None:
        with patch(
            "products.hub.twitch.user_groups.atomic_write_json",
            side_effect=OSError("disk full"),
        ):
            with self.assertRaises(OSError):
                self.service.create_group("Crew")
        self.assertEqual(
            {group.group_id for group in self.service.list_groups()},
            {SYSTEM_BOTS_GROUP_ID, SYSTEM_REGULARS_GROUP_ID},
        )

    def test_system_groups_are_deterministic_and_protected(self) -> None:
        groups = {group.group_id: group for group in self.service.list_groups()}
        self.assertEqual(groups[SYSTEM_BOTS_GROUP_ID].name, "Bots")
        self.assertEqual(groups[SYSTEM_REGULARS_GROUP_ID].name, "Regulars")
        with self.assertRaisesRegex(ValueError, "cannot be renamed"):
            self.service.rename_group(SYSTEM_BOTS_GROUP_ID, "Robots")
        with self.assertRaisesRegex(ValueError, "cannot be deleted"):
            self.service.delete_group(SYSTEM_REGULARS_GROUP_ID)
        with self.assertRaisesRegex(ValueError, "managed automatically"):
            self.service.assign_member(SYSTEM_REGULARS_GROUP_ID, "123")

    def test_one_user_can_join_multiple_groups_and_groups_can_share_users(self) -> None:
        friends = self.service.create_group("Friends")
        crew = self.service.create_group("Crew")
        self.service.assign_member(friends.group_id, "123")
        self.service.assign_member(crew.group_id, "123")
        self.service.assign_member(crew.group_id, "456")

        self.assertEqual(
            {group.group_id for group in self.service.groups_for_user("123")},
            {friends.group_id, crew.group_id},
        )
        self.assertEqual(self.service.member_ids(crew.group_id), ("123", "456"))
        self.service.remove_member(crew.group_id, "123")
        self.assertFalse(self.service.is_member("123", crew.group_id))

    def test_v8_chatter_migration_preserves_records_and_moves_useful_groups(self) -> None:
        chatter_path = self.root / "memory" / "twitch_chatters.json"
        chatter_path.parent.mkdir(parents=True, exist_ok=True)
        original = {
            "version": 8,
            "chatters": {
                "bot-1": {
                    "user_id": "bot-1",
                    "user_name": "Helper Bot",
                    "user_login": "helperbot",
                    "first_seen": "2026-01-01T00:00:00+00:00",
                    "last_seen": "2026-01-02T00:00:00+00:00",
                    "message_count": 7,
                    "roles": ["Moderator"],
                    "manual_group": "Bots",
                },
                "regular-1": {
                    "user_id": "regular-1",
                    "user_name": "Friend",
                    "first_seen": "",
                    "last_seen": "",
                    "manual_group": "Regulars",
                },
                "viewer-1": {
                    "user_id": "viewer-1",
                    "user_name": "Viewer",
                    "first_seen": "",
                    "last_seen": "",
                    "manual_group": "Viewers",
                },
            },
        }
        chatter_path.write_text(json.dumps(original), encoding="utf-8")
        chatter = ChatterHistoryStore(chatter_path)
        chatter.load()

        self.service.migrate_chatter_store(chatter)

        saved = json.loads(chatter_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["version"], ChatterHistoryStore.VERSION)
        self.assertNotIn("manual_group", json.dumps(saved))
        self.assertEqual(saved["chatters"]["bot-1"]["user_login"], "helperbot")
        self.assertEqual(saved["chatters"]["bot-1"]["message_count"], 7)
        self.assertEqual(saved["chatters"]["bot-1"]["roles"], ["Moderator"])
        self.assertTrue(self.service.is_bot("bot-1"))
        self.assertTrue(self.service.is_regular("regular-1"))
        self.assertEqual(self.service.groups_for_user("viewer-1"), ())
        safety = chatter_path.parent / "migrations" / "twitch_chatters-v8-safety.json"
        self.assertEqual(json.loads(safety.read_text(encoding="utf-8")), original)

        second_chatter = ChatterHistoryStore(chatter_path)
        second_chatter.load()
        second_store = UserGroupStore(self.path)
        second_store.load()
        self.assertEqual(second_chatter.loaded_schema_version, ChatterHistoryStore.VERSION)
        self.assertEqual(second_chatter.legacy_manual_groups, {})
        self.assertIn(SYSTEM_BOTS_GROUP_ID, second_store.memberships["bot-1"])

    def test_v8_without_manual_assignments_still_migrates_once(self) -> None:
        chatter_path = self.root / "memory" / "twitch_chatters.json"
        chatter_path.parent.mkdir(parents=True, exist_ok=True)
        chatter_path.write_text(
            json.dumps(
                {
                    "version": 8,
                    "chatters": {
                        "1": {
                            "user_id": "1",
                            "user_name": "Viewer",
                            "first_seen": "",
                            "last_seen": "",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        chatter = ChatterHistoryStore(chatter_path)
        chatter.load()

        self.service.migrate_chatter_store(chatter)

        self.assertEqual(json.loads(chatter_path.read_text())["version"], 9)
        self.assertTrue(self.path.exists())

    def test_failed_group_publication_leaves_v8_source_recoverable(self) -> None:
        chatter_path = self.root / "memory" / "twitch_chatters.json"
        chatter_path.parent.mkdir(parents=True, exist_ok=True)
        original = {
            "version": 8,
            "chatters": {
                "1": {
                    "user_id": "1",
                    "user_name": "Bot",
                    "first_seen": "",
                    "last_seen": "",
                    "manual_group": "Bots",
                }
            },
        }
        chatter_path.write_text(json.dumps(original), encoding="utf-8")
        chatter = ChatterHistoryStore(chatter_path)
        chatter.load()

        with patch.object(self.store, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.service.migrate_chatter_store(chatter)

        self.assertEqual(json.loads(chatter_path.read_text()), original)
        safety = chatter_path.parent / "migrations" / "twitch_chatters-v8-safety.json"
        self.assertEqual(json.loads(safety.read_text()), original)

    def test_failed_chatter_publication_rolls_back_group_file(self) -> None:
        existing = self.service.create_group("Friends")
        self.service.assign_member(existing.group_id, "friend-1")
        group_bytes = self.path.read_bytes()
        chatter_path = self.root / "memory" / "twitch_chatters.json"
        chatter_path.parent.mkdir(parents=True, exist_ok=True)
        original = {
            "version": 8,
            "chatters": {
                "bot-1": {
                    "user_id": "bot-1",
                    "user_name": "Bot",
                    "first_seen": "",
                    "last_seen": "",
                    "manual_group": "Bots",
                }
            },
        }
        chatter_path.write_text(json.dumps(original), encoding="utf-8")
        chatter = ChatterHistoryStore(chatter_path)
        chatter.load()

        with patch.object(chatter, "save", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.service.migrate_chatter_store(chatter)

        self.assertEqual(self.path.read_bytes(), group_bytes)
        self.assertEqual(json.loads(chatter_path.read_text()), original)
        self.assertFalse(self.service.is_bot("bot-1"))
        self.assertTrue(self.service.is_member("friend-1", existing.group_id))


class UserGroupConditionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = TemporaryDirectory()
        store = UserGroupStore(Path(self.temporary.name) / "user_groups.json")
        self.groups = UserGroupService(store)
        self.group = self.groups.create_group("Auto Shoutout")
        self.groups.assign_member(self.group.group_id, "123")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run(self, user_id: str, group_id: str | None = None):
        calls: list[str] = []

        class TwitchService:
            def send_shoutout(self, target: str) -> str:
                calls.append(target)
                return target

        def branch_runner(tasks, trigger):
            return tuple(
                TwitchAutomationTask(TwitchService(), child.task_type).execute(
                    child, trigger
                )
                for child in tasks
            )

        task = TaskDefinition(
            "if-1",
            "core.if",
            "Auto shoutout group",
            {"operator": "user_in_group", "group_id": group_id or self.group.group_id},
            then_tasks=[
                TaskDefinition(
                    "yes",
                    "twitch.shoutout_user",
                    "Shoutout",
                    {"target": "{user.id}"},
                )
            ],
        )
        result = IfTask(branch_runner, self.groups).execute(
            task,
            TriggerEvent("first-message", "twitch", "first_message", {"user.id": user_id}),
        )
        return result, calls

    def test_member_uses_then_branch_with_stable_trigger_user(self) -> None:
        result, calls = self._run("123")
        self.assertTrue(result.succeeded)
        self.assertEqual(result.selected_branch, "then")
        self.assertEqual(calls, ["123"])

    def test_non_member_and_missing_user_use_else_branch(self) -> None:
        for user_id in ("456", ""):
            with self.subTest(user_id=user_id):
                result, calls = self._run(user_id)
                self.assertTrue(result.succeeded)
                self.assertEqual(result.selected_branch, "else")
                self.assertEqual(calls, [])

    def test_rename_keeps_condition_valid_and_delete_fails_safely(self) -> None:
        self.groups.rename_group(self.group.group_id, "Crew")
        result, _selected = self._run("123")
        self.assertTrue(result.succeeded)
        self.groups.delete_group(self.group.group_id)
        result, calls = self._run("123", self.group.group_id)
        self.assertFalse(result.succeeded)
        self.assertIn("no longer exists", result.detail)
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
