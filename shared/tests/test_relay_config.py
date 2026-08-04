from __future__ import annotations

import unittest

from shared.streamhouse_runtime.relay_config import (
    STREAMHOUSE_RELAY_BASE_DEFAULT,
    load_relay_environment,
)


class RelayEnvironmentTests(unittest.TestCase):
    def test_modern_values_are_authoritative(self) -> None:
        resolved = load_relay_environment(
            {
                "STREAMHOUSE_RELAY_BASE": "https://modern.example",
                "STREAMHOUSE_RELAY_KEYS": "modern-secret",
                "STREAMHOUSE_RELAY_DB": "modern.sqlite3",
            }
        )
        self.assertEqual(resolved.base.value, "https://modern.example")
        self.assertEqual(resolved.keys.source, "STREAMHOUSE_RELAY_KEYS")
        self.assertFalse(resolved.keys.used_legacy)

    def test_legacy_values_are_temporary_fallbacks(self) -> None:
        resolved = load_relay_environment(
            {
                "SALLY_RELAY_BASE": "https://legacy.example",
                "SALLY_RELAY_KEYS": "legacy-secret",
                "SALLY_RELAY_DB": "legacy.sqlite3",
            }
        )
        self.assertEqual(resolved.base.value, "https://legacy.example")
        self.assertTrue(resolved.keys.used_legacy)
        self.assertTrue(resolved.database.used_legacy)

    def test_both_equal_values_do_not_report_a_conflict(self) -> None:
        resolved = load_relay_environment(
            {
                "STREAMHOUSE_RELAY_KEYS": "same",
                "SALLY_RELAY_KEYS": "same",
            }
        )
        self.assertEqual(resolved.keys.value, "same")
        self.assertFalse(resolved.keys.conflict)

    def test_conflicting_values_use_modern_and_report_metadata_only(self) -> None:
        resolved = load_relay_environment(
            {
                "STREAMHOUSE_RELAY_KEYS": "modern-secret",
                "SALLY_RELAY_KEYS": "legacy-secret",
            }
        )
        self.assertEqual(resolved.keys.value, "modern-secret")
        self.assertTrue(resolved.keys.conflict)
        representation = repr(resolved.keys)
        self.assertNotIn("modern-secret", representation)
        self.assertNotIn("legacy-secret", representation)

    def test_missing_values_return_safe_base_and_empty_required_values(self) -> None:
        resolved = load_relay_environment({})
        self.assertEqual(resolved.base.value, STREAMHOUSE_RELAY_BASE_DEFAULT)
        self.assertEqual(resolved.keys.value, "")
        self.assertEqual(resolved.database.value, "")


if __name__ == "__main__":
    unittest.main()
