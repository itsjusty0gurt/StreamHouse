from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from products.hub.twitch.channel_information import (
    ChannelInformation,
    ChannelInformationStore,
    SocialLink,
    normalize_social_url,
)
from shared.streamhouse_runtime.json_store import JsonStoreCorruptionError


class ChannelInformationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "twitch" / "channel-information.json"
        self.store = ChannelInformationStore(self.path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_first_load_without_a_file_returns_empty_information(self) -> None:
        information = self.store.load()

        self.assertEqual(information.schedule, "")
        self.assertEqual(len(information.social_links), 8)
        self.assertFalse(self.path.exists())

    def test_save_reload_normalizes_links_and_preserves_multiline_fields(self) -> None:
        information = ChannelInformation(
            schedule="  Monday 7 PM  \r\nFriday 8 PM\n",
            rules="Be kind.\nNo spoilers.",
            server_info="Example Realm\nplay.example.com",
        )
        information.social_links["discord"] = SocialLink(True, " discord.gg/example ")
        information.social_links["youtube"] = SocialLink(False, "https://youtube.com/@example")

        self.store.save(information)
        loaded = ChannelInformationStore(self.path).load()

        self.assertEqual(loaded.social_links["discord"].url, "https://discord.gg/example")
        self.assertTrue(loaded.social_links["discord"].enabled_in_socials)
        self.assertFalse(loaded.social_links["youtube"].enabled_in_socials)
        self.assertEqual(loaded.schedule, "Monday 7 PM\nFriday 8 PM")
        self.assertEqual(loaded.rules, "Be kind.\nNo spoilers.")
        self.assertEqual(loaded.server_info, "Example Realm\nplay.example.com")
        self.assertFalse(self.path.with_suffix(".json.tmp").exists())

    def test_atomic_save_retains_the_previous_version_as_backup(self) -> None:
        first = ChannelInformation(schedule="First")
        second = ChannelInformation(schedule="Second")
        self.store.save(first)
        self.store.save(second)

        backup = json.loads(self.path.with_suffix(".json.bak").read_text(encoding="utf-8"))
        self.assertEqual(backup["schedule"], "First")
        self.assertEqual(ChannelInformationStore(self.path).load().schedule, "Second")

    def test_invalid_payload_and_newer_versions_are_rejected(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "JSON object"):
            self.store.load()

        self.path.write_text(json.dumps({"version": 999}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Unsupported"):
            self.store.load()

        self.path.write_text(json.dumps({"version": 1}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "expected 3"):
            self.store.load()

    def test_startup_missing_store_publishes_current_schema(self) -> None:
        information = self.store.load_for_startup()

        self.assertEqual(information.schedule, "")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["version"], 3)
        self.assertEqual(ChannelInformationStore(self.path).load().schedule, "")

    def test_startup_replaces_obsolete_live_and_recovery_with_current_defaults(self) -> None:
        self.path.parent.mkdir(parents=True)
        obsolete = json.dumps({"version": 1, "schedule": "Old schedule"})
        self.path.write_text(obsolete, encoding="utf-8")
        self.path.with_suffix(".json.bak").write_text(obsolete, encoding="utf-8")

        information = self.store.load_for_startup()

        self.assertEqual(information.schedule, "")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["version"], 3)
        self.assertEqual(
            json.loads(self.path.with_suffix(".json.bak").read_text(encoding="utf-8"))["version"],
            3,
        )

    def test_startup_prefers_valid_current_recovery_over_obsolete_live(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({"version": 1}), encoding="utf-8")
        recovery = {
            "version": 3,
            "social_links": {},
            "schedule": "Recovered schedule",
            "rules": "Recovered rules",
            "server_info": "Recovered server",
        }
        self.path.with_suffix(".json.bak").write_text(
            json.dumps(recovery), encoding="utf-8"
        )

        information = self.store.load_for_startup()

        self.assertEqual(information.schedule, "Recovered schedule")
        self.assertEqual(ChannelInformationStore(self.path).load().rules, "Recovered rules")

    def test_startup_repairs_obsolete_recovery_without_rewriting_current_live(self) -> None:
        current = ChannelInformation(schedule="Current schedule")
        self.store.save(current)
        live_before = self.path.read_bytes()
        self.path.with_suffix(".json.bak").write_text(
            json.dumps({"version": 1}), encoding="utf-8"
        )

        loaded = ChannelInformationStore(self.path).load_for_startup()

        self.assertEqual(loaded.schedule, "Current schedule")
        self.assertEqual(self.path.read_bytes(), live_before)
        self.assertEqual(
            json.loads(self.path.with_suffix(".json.bak").read_text(encoding="utf-8"))["version"],
            3,
        )

    def test_startup_recovers_corrupt_current_live_from_valid_current_backup(self) -> None:
        self.store.save(ChannelInformation(schedule="Recovered"))
        self.store.save(ChannelInformation(schedule="Current"))
        self.path.write_text('{"version": 3, "social_links":', encoding="utf-8")

        information = ChannelInformationStore(self.path).load_for_startup()

        self.assertEqual(information.schedule, "Recovered")
        self.assertEqual(ChannelInformationStore(self.path).load().schedule, "Recovered")

    def test_startup_rejects_corrupt_current_live_with_obsolete_backup(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"version": 3, "social_links":', encoding="utf-8")
        self.path.with_suffix(".json.bak").write_text(
            json.dumps({"version": 1}), encoding="utf-8"
        )

        with self.assertRaises(JsonStoreCorruptionError):
            self.store.load_for_startup()

    def test_link_validation_is_permissive_but_blocks_spaces_and_line_breaks(self) -> None:
        self.assertEqual(normalize_social_url("discord.gg/example"), "https://discord.gg/example")
        self.assertEqual(
            normalize_social_url("https://uncommon.example/invite?q=1"),
            "https://uncommon.example/invite?q=1",
        )
        with self.assertRaisesRegex(ValueError, "one line"):
            normalize_social_url("https://example.com\nhttps://other.example")
        with self.assertRaisesRegex(ValueError, "spaces"):
            normalize_social_url("https://example.com/my invite")

    def test_social_message_uses_checked_valid_unique_links_in_stable_order(self) -> None:
        information = ChannelInformation()
        information.social_links["discord"] = SocialLink(True, "https://same.example")
        information.social_links["youtube"] = SocialLink(False, "https://youtube.example")
        information.social_links["tiktok"] = SocialLink(True, "https://same.example/")
        information.social_links["website"] = SocialLink(True, "https://website.example")
        self.store.save(information)

        self.assertEqual(
            self.store.build_social_links_message(),
            "Discord: https://same.example | Website: https://website.example",
        )
        self.assertLessEqual(len(self.store.build_social_links_message(50)), 50)


if __name__ == "__main__":
    unittest.main()
