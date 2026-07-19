import tempfile
import unittest
from pathlib import Path

from ai.test_report import AITestReportStore


class AITestReportStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / "report.json"
        self.store = AITestReportStore(self.path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_records_only_anonymous_diagnostic_fields(self) -> None:
        self.store.record(
            outcome="sent",
            reason="twitch sent",
            latency_ms=1250,
            response_expected=True,
            confidence=0.91,
            response_source="rivescript",
        )

        event = self.store.events[0]
        self.assertEqual(event["outcome"], "sent")
        self.assertEqual(event["reason"], "twitch_sent")
        self.assertEqual(event["response_source"], "rivescript")
        self.assertNotIn("message", event)
        self.assertNotIn("viewer", event)
        self.assertNotIn("user_id", event)

        loaded = AITestReportStore(self.path)
        loaded.load()
        self.assertEqual(len(loaded.events), 1)

    def test_summary_can_limit_results_to_current_session(self) -> None:
        self.store.record(
            outcome="missed",
            reason="model_ignored_required_message",
            latency_ms=1000,
            response_expected=True,
            confidence=0.2,
        )
        self.store.start_new_session()
        self.store.record(
            outcome="sent",
            reason="sent",
            latency_ms=500,
            response_expected=True,
            confidence=0.9,
        )

        current = self.store.summary(True)
        all_events = self.store.summary(False)

        self.assertEqual(current["total"], 1)
        self.assertEqual(current["sent"], 1)
        self.assertEqual(all_events["total"], 2)
        self.assertEqual(all_events["missed"], 1)
        self.assertEqual(all_events["llm"], 2)
        self.assertEqual(all_events["rivescript"], 0)
        self.assertEqual(all_events["average_latency_ms"], 750)

    def test_clear_removes_saved_events(self) -> None:
        self.store.record(
            outcome="ignored",
            reason="not_addressed",
            latency_ms=10,
            response_expected=False,
            confidence=0.9,
        )

        self.assertEqual(self.store.clear(), 1)
        loaded = AITestReportStore(self.path)
        loaded.load()
        self.assertEqual(loaded.events, [])


if __name__ == "__main__":
    unittest.main()
