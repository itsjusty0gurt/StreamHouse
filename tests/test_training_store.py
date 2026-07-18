import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai.response_engine import ResponseDecision
from ai.training_store import TrainingStore


class TrainingStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / "examples.json"
        self.store = TrainingStore(self.path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    @staticmethod
    def decision() -> ResponseDecision:
        return ResponseDecision(
            request_id="request-1",
            message_id="message-1",
            user_id="12345",
            user_name="VisibleViewer",
            source_text="@VisibleViewer see https://example.com Sally?",
            received_at=datetime.now(timezone.utc).isoformat(),
            decision="reply",
            reply="Sure.",
            reason="Direct.",
            confidence=0.9,
            engagement_type="direct",
            conversation_state="start",
        )

    def test_capture_is_pseudonymous_and_sanitized(self) -> None:
        example_id = self.store.capture("12345", self.decision())

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        example = payload["examples"][0]
        self.assertEqual(example["id"], example_id)
        self.assertNotIn("12345", json.dumps(example))
        self.assertNotIn("VisibleViewer", json.dumps(example))
        self.assertIn("@<user>", example["message"])
        self.assertIn("<url>", example["message"])
        self.assertFalse(example["reviewed"])

    def test_label_and_participant_delete_round_trip(self) -> None:
        example_id = self.store.capture("12345", self.decision())
        self.assertTrue(self.store.label(example_id, "conversation"))

        loaded = TrainingStore(self.path)
        loaded.load()
        self.assertEqual(loaded.examples[0]["label"], "conversation")
        self.assertTrue(loaded.examples[0]["reviewed"])
        self.assertEqual(loaded.delete_participant("12345"), 1)
        self.assertEqual(loaded.examples, [])

    def test_old_unreviewed_examples_expire_but_reviewed_remain(self) -> None:
        old = (
            datetime.now(timezone.utc) - timedelta(days=31)
        ).isoformat()
        self.store.examples = [
            {"id": "pending", "message": "one", "captured_at": old},
            {
                "id": "reviewed",
                "message": "two",
                "captured_at": old,
                "reviewed": True,
            },
        ]

        self.assertEqual(self.store.prune(save=False), 1)
        self.assertEqual(self.store.examples[0]["id"], "reviewed")


if __name__ == "__main__":
    unittest.main()
