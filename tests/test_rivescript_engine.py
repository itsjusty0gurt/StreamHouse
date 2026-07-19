import tempfile
import unittest
from pathlib import Path

from ai.rivescript_engine import RiveScriptRuleStore, SallyRiveScriptEngine


class RiveScriptEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / "rules.json"
        self.store = RiveScriptRuleStore(self.path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_add_match_disable_and_reload_rule(self) -> None:
        rule_id = self.store.add("hello *", "Hello, <star>!", name="Greeting")
        engine = SallyRiveScriptEngine(self.store)

        self.assertEqual(engine.match("viewer", "hello Bob"), (rule_id, "Hello, bob!"))
        self.assertIsNone(engine.match("viewer", "something else"))

        self.store.set_enabled(rule_id, False)
        engine.rebuild()
        self.assertIsNone(engine.match("viewer", "hello Bob"))

        loaded = RiveScriptRuleStore(self.path)
        loaded.load()
        self.assertEqual(loaded.rules[0]["name"], "Greeting")
        self.assertFalse(loaded.rules[0]["enabled"])

    def test_duplicate_and_invalid_rules_are_rejected(self) -> None:
        self.store.add("hello", "Hi")
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.store.add("HELLO", "Another reply")
        with self.assertRaises(ValueError):
            self.store.add("", "Reply")

    def test_suggest_trigger_removes_links_mentions_and_punctuation(self) -> None:
        self.assertEqual(
            self.store.suggest_trigger("@Viewer Hey, Sally! https://example.com"),
            "hey sally",
        )


if __name__ == "__main__":
    unittest.main()
