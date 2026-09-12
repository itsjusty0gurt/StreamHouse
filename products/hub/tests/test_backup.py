from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zipfile import ZIP_DEFLATED, ZipFile

from products.hub.automation.core_triggers import CoreTriggerStore
from products.hub.automation.custom_variables import CustomVariableStore
from products.hub.automation.queues import AutomationQueueStore
from products.hub.automation.models import DEFAULT_AUTOMATION_QUEUE_ID
from products.hub.automation.routines import RoutineStore
from products.hub.core.backup import (
    BackupComponent,
    BackupError,
    BackupManager,
    BackupPreset,
)
from products.hub.core.settings import SettingsStore
from products.hub.counters.store import COUNTER_VERSION, INDEX_VERSION
from products.hub.obs_service.triggers import ObsTriggerStore
from products.hub.twitch.automation_triggers import TwitchEventTriggerStore
from products.hub.twitch.channel_information import ChannelInformationStore
from products.hub.twitch.chatter_history import ChatterHistoryStore
from products.hub.twitch.commands import TwitchCommandTriggerStore
from shared.streamhouse_runtime.version import VERSION


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


class BackupManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "data"
        self.backups = Path(self.temporary.name) / "archives"
        self.now = datetime(2026, 9, 11, 16, 4, tzinfo=timezone.utc)
        self.manager = BackupManager(
            self.root, self.backups, now=lambda: self.now
        )
        self._write_complete_setup()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_complete_setup(self) -> None:
        write_json(
            self.root / "automation/routines.json",
            {
                "version": RoutineStore.VERSION,
                "groups": [
                    {"group_id": "alerts", "name": "Alerts", "collapsed": False}
                ],
                "routines": [
                    {
                        "routine_id": "parent",
                        "name": "Parent",
                        "trigger_id": "raid-trigger",
                        "additional_trigger_ids": [],
                        "tasks": [
                            {
                                "task_id": "nested-task",
                                "task_type": "core.run_routine",
                                "name": "Run child",
                                "config": {"routine_id": "child"},
                                "enabled": True,
                                "managed_key": "",
                                "then_tasks": [],
                                "else_tasks": [],
                            }
                        ],
                        "enabled": True,
                        "managed_by": "",
                        "group_id": "alerts",
                        "description": "",
                        "queue_id": "alerts-queue",
                    },
                    {
                        "routine_id": "child",
                        "name": "Child",
                        "trigger_id": "",
                        "additional_trigger_ids": [],
                        "tasks": [
                            {
                                "task_id": "message-task",
                                "task_type": "twitch.send_chat_message",
                                "name": "Send message",
                                "config": {"message": "Count {custom.greeting}"},
                                "enabled": True,
                                "managed_key": "",
                                "then_tasks": [],
                                "else_tasks": [],
                            }
                        ],
                        "enabled": True,
                        "managed_by": "",
                        "group_id": "",
                        "description": "",
                        "queue_id": DEFAULT_AUTOMATION_QUEUE_ID,
                    },
                ],
            },
        )
        write_json(
            self.root / "twitch/event_triggers.json",
            {
                "version": TwitchEventTriggerStore.VERSION,
                "triggers": [
                    {
                        "trigger_id": "raid-trigger",
                        "routine_id": "parent",
                        "event_key": "channel.raid.incoming",
                        "enabled": True,
                        "filters": {},
                    }
                ],
                "first_message": {
                    "raid_suppression_enabled": True,
                    "raid_suppression_minutes": 3,
                },
            },
        )
        write_json(
            self.root / "twitch/commands.json",
            {"version": TwitchCommandTriggerStore.VERSION, "triggers": []},
        )
        write_json(
            self.root / "automation/core_triggers.json",
            {"version": CoreTriggerStore.VERSION, "triggers": []},
        )
        write_json(
            self.root / "obs/triggers.json",
            {"version": ObsTriggerStore.VERSION, "triggers": []},
        )
        write_json(
            self.root / "automation/queues.json",
            {
                "version": AutomationQueueStore.VERSION,
                "queues": [
                    {
                        "queue_id": DEFAULT_AUTOMATION_QUEUE_ID,
                        "name": "Default Queue",
                        "paused": False,
                        "max_length": 100,
                        "duplicate_policy": "allow",
                        "delay_seconds": 0,
                    },
                    {
                        "queue_id": "alerts-queue",
                        "name": "Alerts",
                        "paused": False,
                        "max_length": 20,
                        "duplicate_policy": "allow",
                        "delay_seconds": 0,
                    },
                    {
                        "queue_id": "unrelated",
                        "name": "Unrelated",
                        "paused": False,
                        "max_length": 20,
                        "duplicate_policy": "allow",
                        "delay_seconds": 0,
                    },
                ],
            },
        )
        write_json(
            self.root / "automation/variables.json",
            {
                "version": CustomVariableStore.VERSION,
                "global": {"greeting": "hello", "unused": "ignore"},
                "metadata": {
                    "greeting": {"type": "text", "description": "Greeting"},
                    "unused": {"type": "text", "description": "Unused"},
                },
            },
        )
        write_json(
            self.root / "counters/index.json",
            {
                "version": INDEX_VERSION,
                "counters": [
                    {
                        "counter_id": "farts",
                        "display_name": "Farts",
                        "singular": "fart",
                        "plural": "farts",
                        "enabled": True,
                        "track_channel_total": True,
                        "track_stream_total": True,
                        "track_viewer_total": True,
                        "track_viewer_stream_total": True,
                        "exclude_known_bots": True,
                        "allow_negative": False,
                        "minimum": "0",
                        "numeric_type": "decimal",
                        "reset_value": "0",
                        "display_precision": 2,
                    }
                ],
            },
        )
        write_json(
            self.root / "counters/farts.json",
            {
                "version": COUNTER_VERSION,
                "counter_id": "farts",
                "channel_total": "12.50",
                "current_stream": {"stream_id": "stream-1", "value": "2.25"},
                "viewers": {
                    "viewer-1": {
                        "total": "3.75",
                        "display_name": "Viewer",
                        "login": "viewer",
                        "current_stream": {
                            "stream_id": "stream-1",
                            "value": "1.25",
                        },
                    }
                },
            },
        )
        write_json(
            self.root / "memory/twitch_chatters.json",
            {
                "version": ChatterHistoryStore.VERSION,
                "chatters": {
                    "viewer-1": {
                        "user_id": "viewer-1",
                        "user_name": "Viewer",
                        "user_login": "viewer",
                        "first_seen": "2026-09-01T00:00:00+00:00",
                        "last_seen": "2026-09-11T00:00:00+00:00",
                        "is_bot": False,
                        "manual_group": "Regulars",
                        "twitch_status": {"Subscriber": True},
                        "daily_memory": [
                            {"message": "private viewer message"}
                        ],
                        "memories": [{"text": "private memory"}],
                        "private_notes": "private note",
                    }
                },
            },
        )
        write_json(
            self.root / "config/settings.json",
            {
                "_version": SettingsStore.VERSION,
                "startup_page": "Dashboard",
                "log_level": "INFO",
                "automatic_backups_enabled": True,
                "streamhouse_ai_endpoint": "http://private-machine:8765",
                "ai_personality": "private prompt",
            },
        )
        write_json(
            self.root / "obs/connection.json",
            {
                "version": 1,
                "host": "127.0.0.1",
                "port": 4455,
                "auto_connect": True,
                "default_mute_input": "Mic",
                "password": "must-not-appear",
            },
        )
        (self.root / "obs/password.dat").write_bytes(b"encrypted-secret")
        (self.root / "logs").mkdir(parents=True)
        (self.root / "logs/session.log").write_text("private log", encoding="utf-8")
        (self.root / "support").mkdir()
        (self.root / "support/report.zip").write_text("support", encoding="utf-8")

    def test_presets_are_explicit_and_users_are_only_in_everything(self) -> None:
        recommended = self.manager.components_for(BackupPreset.RECOMMENDED)
        configuration = self.manager.components_for(
            BackupPreset.CONFIGURATION_ONLY
        )
        everything = self.manager.components_for(
            BackupPreset.EVERYTHING_ELIGIBLE
        )
        self.assertIn(BackupComponent.COUNTER_VALUES, recommended)
        self.assertNotIn(BackupComponent.COUNTER_VALUES, configuration)
        self.assertNotIn(BackupComponent.USERS, recommended)
        self.assertEqual(everything, frozenset(BackupComponent))
        custom = self.manager.components_for(
            BackupPreset.CUSTOM, [BackupComponent.COUNTER_VALUES]
        )
        self.assertEqual(
            custom,
            frozenset(
                {
                    BackupComponent.COUNTER_VALUES,
                    BackupComponent.COUNTER_DEFINITIONS,
                }
            ),
        )

    def test_manifest_dependency_closure_and_privacy(self) -> None:
        archive = self.manager.create(
            "manual", preset=BackupPreset.EVERYTHING_ELIGIBLE
        )
        self.assertEqual(archive.suffix, ".streamhousebackup")
        with ZipFile(archive) as source:
            names = set(source.namelist())
            manifest = json.loads(source.read("manifest.json"))
            routines = json.loads(source.read("components/routines.json"))
            users = json.loads(source.read("components/users.json"))
            settings = json.loads(source.read("components/hub_settings.json"))
            obs = json.loads(source.read("components/obs_configuration.json"))
            combined = b"\n".join(source.read(name) for name in names)
        self.assertEqual(manifest["backup_format_version"], 1)
        self.assertEqual(manifest["components"]["routines"]["schema"], 1)
        self.assertEqual(
            {item["routine_id"] for item in routines["routines"]["routines"]},
            {"parent", "child"},
        )
        self.assertEqual(
            {item["queue_id"] for item in routines["queues"]["queues"]},
            {DEFAULT_AUTOMATION_QUEUE_ID, "alerts-queue"},
        )
        self.assertEqual(
            set(routines["custom_variables"]["global"]), {"greeting"}
        )
        user = users["chatters"]["viewer-1"]
        self.assertEqual(user["manual_group"], "Regulars")
        self.assertNotIn("daily_memory", user)
        self.assertNotIn("memories", user)
        self.assertNotIn("private_notes", user)
        self.assertNotIn("streamhouse_ai_endpoint", settings)
        self.assertNotIn("ai_personality", settings)
        self.assertNotIn("password", obs)
        for forbidden in (
            b"private viewer message",
            b"private memory",
            b"private note",
            b"must-not-appear",
            b"encrypted-secret",
            b"private log",
            b"support",
        ):
            self.assertNotIn(forbidden, combined)

    def test_counter_values_preserve_exact_decimal_and_viewer_identity(self) -> None:
        archive = self.manager.create(
            "manual",
            preset=BackupPreset.CUSTOM,
            components=[BackupComponent.COUNTER_VALUES],
        )
        with ZipFile(archive) as source:
            values = json.loads(source.read("components/counter_values.json"))
        counter = values["values"]["farts"]
        self.assertEqual(counter["channel_total"], "12.50")
        self.assertEqual(counter["viewers"]["viewer-1"]["total"], "3.75")
        self.assertEqual(counter["current_stream"]["stream_id"], "stream-1")

    def test_viewer_scrub_removes_management_and_counter_records_from_archives(self) -> None:
        archive = self.manager.create(
            "manual", preset=BackupPreset.EVERYTHING_ELIGIBLE
        )

        self.assertEqual(self.manager.scrub_viewer("viewer-1"), 1)

        with ZipFile(archive) as source:
            users = json.loads(source.read("components/users.json"))
            values = json.loads(source.read("components/counter_values.json"))
        self.assertNotIn("viewer-1", users["chatters"])
        self.assertNotIn("viewer-1", values["values"]["farts"]["viewers"])

    def test_restore_is_selective_replacement_and_creates_safety_backup(self) -> None:
        archive = self.manager.create(
            "manual",
            preset=BackupPreset.CUSTOM,
            components=[BackupComponent.CHANNEL_INFORMATION],
        )
        original = {"version": ChannelInformationStore.VERSION, "social_links": {}, "schedule": "Original", "rules": "", "server_info": ""}
        write_json(self.root / "twitch/channel-information.json", original)
        settings_before = (self.root / "config/settings.json").read_bytes()

        report = self.manager.restore(archive)

        restored = json.loads(
            (self.root / "twitch/channel-information.json").read_text(encoding="utf-8")
        )
        self.assertNotEqual(restored["schedule"], "Original")
        self.assertEqual((self.root / "config/settings.json").read_bytes(), settings_before)
        self.assertTrue(report.safety_backup.exists())
        self.assertEqual(report.restored_components, ("channel_information",))

    def test_invalid_or_corrupt_backup_cannot_change_current_data(self) -> None:
        archive = self.manager.create(
            "manual",
            preset=BackupPreset.CUSTOM,
            components=[BackupComponent.HUB_SETTINGS],
        )
        before = (self.root / "config/settings.json").read_bytes()
        corrupt = archive.with_name("corrupt.streamhousebackup")
        with ZipFile(archive) as source, ZipFile(corrupt, "w", ZIP_DEFLATED) as target:
            for name in source.namelist():
                target.writestr(
                    name,
                    b"{}" if name == "components/hub_settings.json" else source.read(name),
                )
        with self.assertRaises(BackupError):
            self.manager.restore(corrupt)
        self.assertEqual((self.root / "config/settings.json").read_bytes(), before)

    def test_command_restore_replaces_commands_and_keeps_unrelated_routine_triggers(self) -> None:
        routines = RoutineStore(self.root / "automation/routines.json")
        routines.load()
        commands = TwitchCommandTriggerStore(
            self.root / "twitch/commands.json", routines
        )
        commands.load()
        command = commands.add(
            "hello",
            "Hello!",
            aliases=("hi",),
            global_cooldown_seconds=7,
            user_cooldown_seconds=11,
        )
        command_routine = routines.get(command.routine_id)
        self.assertIsNotNone(command_routine)
        archive = self.manager.create(
            "manual",
            preset=BackupPreset.CUSTOM,
            components=(BackupComponent.COMMANDS,),
        )
        commands.delete(command.trigger_id)

        report = self.manager.restore(
            archive,
            components=(BackupComponent.COMMANDS,),
            create_safety=False,
        )

        reloaded_routines = RoutineStore(self.root / "automation/routines.json")
        reloaded_routines.load()
        reloaded_commands = TwitchCommandTriggerStore(
            self.root / "twitch/commands.json", reloaded_routines
        )
        reloaded_commands.load()
        restored = reloaded_commands.resolve("hi")
        self.assertIsNotNone(restored)
        self.assertEqual(restored.global_cooldown_seconds, 7)
        self.assertEqual(restored.user_cooldown_seconds, 11)
        self.assertIsNotNone(reloaded_routines.get("parent"))
        event_payload = json.loads(
            (self.root / "twitch/event_triggers.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {item["trigger_id"] for item in event_payload["triggers"]},
            {"raid-trigger"},
        )
        self.assertEqual(report.restored_components, ("commands",))

    def test_stream_values_do_not_overwrite_a_different_current_stream(self) -> None:
        archive = self.manager.create(
            "manual",
            preset=BackupPreset.CUSTOM,
            components=(BackupComponent.COUNTER_VALUES,),
        )
        current = json.loads(
            (self.root / "counters/farts.json").read_text(encoding="utf-8")
        )
        current["channel_total"] = "99.00"
        current["current_stream"] = {"stream_id": "stream-2", "value": "8.00"}
        current["viewers"]["viewer-1"]["current_stream"] = {
            "stream_id": "stream-2",
            "value": "4.00",
        }
        write_json(self.root / "counters/farts.json", current)

        self.manager.restore(archive, create_safety=False)

        restored = json.loads(
            (self.root / "counters/farts.json").read_text(encoding="utf-8")
        )
        self.assertEqual(restored["channel_total"], "12.50")
        self.assertEqual(restored["current_stream"]["stream_id"], "stream-2")
        self.assertEqual(restored["current_stream"]["value"], "8.00")
        self.assertEqual(
            restored["viewers"]["viewer-1"]["current_stream"]["value"],
            "4.00",
        )

    def test_transaction_rolls_back_every_target_after_mid_commit_failure(self) -> None:
        first = self.root / "transaction/first.json"
        second = self.root / "transaction/second.json"
        write_json(first, {"value": "before-one"})
        write_json(second, {"value": "before-two"})
        real_replace = __import__("os").replace
        committed = 0

        def fail_second_restore(source, destination):
            nonlocal committed
            if str(source).endswith(".restore"):
                committed += 1
                if committed == 2:
                    raise OSError("simulated commit failure")
            return real_replace(source, destination)

        with patch("products.hub.core.backup.os.replace", side_effect=fail_second_restore):
            with self.assertRaises(OSError):
                self.manager._commit_transaction(
                    {first: b'{"value":"after-one"}', second: b'{"value":"after-two"}'},
                    set(),
                )
        self.assertEqual(json.loads(first.read_text())["value"], "before-one")
        self.assertEqual(json.loads(second.read_text())["value"], "before-two")

    def test_safety_backup_failure_aborts_restore(self) -> None:
        archive = self.manager.create(
            "manual",
            preset=BackupPreset.CUSTOM,
            components=[BackupComponent.HUB_SETTINGS],
        )
        before = (self.root / "config/settings.json").read_bytes()
        with patch.object(self.manager, "create", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(BackupError, "safety backup failed"):
                self.manager.restore(archive)
        self.assertEqual((self.root / "config/settings.json").read_bytes(), before)

    def test_invalid_format_schema_and_dependency_are_rejected_before_restore(self) -> None:
        invalid = self.backups / "invalid.streamhousebackup"
        invalid.parent.mkdir(parents=True, exist_ok=True)
        invalid.write_bytes(b"not a zip")
        with self.assertRaisesRegex(BackupError, "not a valid Streamhouse backup"):
            self.manager.restore(invalid)

        archive = self.manager.create(
            "manual",
            preset=BackupPreset.CUSTOM,
            components=(BackupComponent.COUNTER_VALUES,),
        )
        with ZipFile(archive) as source:
            original = {name: source.read(name) for name in source.namelist()}

        def rewritten(name: str, mutate) -> Path:
            destination = self.backups / f"{name}.streamhousebackup"
            manifest = json.loads(original["manifest.json"])
            mutate(manifest)
            with ZipFile(destination, "w", ZIP_DEFLATED) as target:
                for member, content in original.items():
                    target.writestr(
                        member,
                        json.dumps(manifest).encode("utf-8")
                        if member == "manifest.json"
                        else content,
                    )
            return destination

        future = rewritten(
            "future-format",
            lambda manifest: manifest.update(backup_format_version=99),
        )
        with self.assertRaisesRegex(BackupError, "format is not supported"):
            self.manager.restore(future)

        wrong_schema = rewritten(
            "wrong-schema",
            lambda manifest: manifest["components"]["counter_values"].update(
                schema=99
            ),
        )
        with self.assertRaisesRegex(BackupError, "unsupported schema"):
            self.manager.restore(wrong_schema)

        def remove_dependency(manifest):
            manifest["components"].pop("counter_definitions")
            manifest["included_components"].remove("counter_definitions")

        missing_dependency = rewritten("missing-dependency", remove_dependency)
        with self.assertRaisesRegex(BackupError, "missing dependency"):
            self.manager.restore(missing_dependency)

    def test_automatic_backup_requires_change_and_rotates_only_automatic(self) -> None:
        first = self.manager.create_daily_if_needed()
        self.assertIsNotNone(first)
        self.assertIsNone(self.manager.create_daily_if_needed())
        manual = self.manager.create("manual", preset=BackupPreset.CONFIGURATION_ONLY)
        for day in range(1, 8):
            self.now += timedelta(days=1)
            settings = json.loads(
                (self.root / "config/settings.json").read_text(encoding="utf-8")
            )
            settings["ui_log_limit"] = 1000 + day
            write_json(self.root / "config/settings.json", settings)
            self.assertIsNotNone(self.manager.create_daily_if_needed())
        self.assertEqual(
            len(list(self.manager.automatic_directory.glob("*.streamhousebackup"))),
            self.manager.AUTOMATIC_RETENTION,
        )
        self.assertTrue(manual.exists())

    def test_secret_like_eligible_content_is_rejected_not_redacted(self) -> None:
        settings = json.loads(
            (self.root / "config/settings.json").read_text(encoding="utf-8")
        )
        settings["startup_page"] = "Authorization: Bearer secret-token"
        write_json(self.root / "config/settings.json", settings)
        with self.assertRaisesRegex(BackupError, "credential-like"):
            self.manager.create(
                "manual",
                preset=BackupPreset.CUSTOM,
                components=[BackupComponent.HUB_SETTINGS],
            )

    def test_manifest_schema_and_checksum_are_validated(self) -> None:
        archive = self.manager.create(
            "manual",
            preset=BackupPreset.CUSTOM,
            components=[BackupComponent.HUB_SETTINGS],
        )
        inspection = self.manager.inspect(archive)
        self.assertEqual(inspection.schemas["hub_settings"], SettingsStore.VERSION)
        self.assertEqual(inspection.hub_version, VERSION)


if __name__ == "__main__":
    unittest.main()
