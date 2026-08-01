from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from products.ai.engine.test_report import AITestReportStore
from products.ai.engine.training_store import TrainingStore
from products.hub.streamhouse_hub.ai_remote_stores import (
    StreamhouseAITestReportStore,
    StreamhouseAITrainingStore,
)
from products.ai.streamhouse_ai.server import StreamhouseAIService, create_server
from products.hub.streamhouse_hub.ai_client import StreamhouseAIClient
from products.ai.streamhouse_ai.settings import StreamhouseAISettingsStore
from shared.streamhouse_shared.models import ResponseDecision


class CompanionRemoteStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        service = StreamhouseAIService(
            TrainingStore(root / "training.json"),
            AITestReportStore(root / "report.json"),
            settings_store=StreamhouseAISettingsStore(root / "settings.json"),
        )
        self.server = create_server(port=0, service=service)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.endpoint = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)
        self.temporary_directory.cleanup()

    def test_training_records_are_owned_by_companion(self) -> None:
        store = StreamhouseAITrainingStore(self.endpoint)
        store.connect()
        example_id = store.capture(
            "viewer-1",
            ResponseDecision(
                "request-1",
                "message-1",
                "viewer-1",
                "Viewer",
                "hey sally",
                "now",
                "reply",
                "Hello",
                "direct",
                0.9,
                engagement_type="direct",
            ),
        )
        self.assertTrue(example_id)
        self.assertEqual(len(store.examples), 1)
        self.assertTrue(store.label(example_id, "direct"))

    def test_test_report_is_owned_by_companion(self) -> None:
        store = StreamhouseAITestReportStore(self.endpoint)
        store.connect()
        store.record(
            outcome="sent",
            reason="direct",
            latency_ms=150,
            response_expected=True,
            confidence=0.9,
            save=True,
        )
        summary = store.summary(False)
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["sent"], 1)

    def test_model_and_personality_settings_are_owned_by_companion(self) -> None:
        client = StreamhouseAIClient(self.endpoint)
        updated = client.update_settings(
            {
                "ollama_endpoint": "http://localhost:11434",
                "model": "qwen3:8b",
                "personality": "Dry and concise.",
                "allow_mild_profanity": True,
                "allow_strong_profanity": False,
            }
        )
        self.assertEqual(updated["model"], "qwen3:8b")
        self.assertEqual(client.get_settings()["personality"], "Dry and concise.")


if __name__ == "__main__":
    unittest.main()
