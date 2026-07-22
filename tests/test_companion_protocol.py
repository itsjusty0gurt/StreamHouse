from __future__ import annotations

import threading
import unittest

from ai.memory_extractor import BufferedChatMessage, ExtractedMemory
from ai.response_engine import ResponseDecision, ResponseMessage
from sally_companion.client import CompanionClient
from sally_companion.protocol import PROTOCOL_VERSION
from sally_companion.server import create_server


class FakeCompanionService:
    def status(self, body: dict) -> dict:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "available": True,
            "models": [body["model"]],
            "error": "",
        }

    def decisions(self, body: dict) -> dict:
        source = body["messages"][0]
        return {
            "protocol_version": PROTOCOL_VERSION,
            "decisions": [
                {
                    "request_id": source["request_id"],
                    "message_id": source["message_id"],
                    "user_id": source["user_id"],
                    "user_name": source["user_name"],
                    "source_text": source["text"],
                    "received_at": source["received_at"],
                    "decision": "reply",
                    "reply": "Hello from the companion.",
                    "reason": "direct invocation",
                    "confidence": 0.9,
                    "solicited": True,
                }
            ],
        }

    def memories(self, body: dict) -> dict:
        source = body["messages"][0]
        return {
            "protocol_version": PROTOCOL_VERSION,
            "memories": [
                {
                    "text": "Viewer likes puzzle games",
                    "category": "Preference",
                    "key": "game-genre",
                    "confidence": 0.8,
                    "evidence": [
                        {
                            "text": source["text"],
                            "timestamp": source["timestamp"],
                            "message_id": source["message_id"],
                        }
                    ],
                }
            ],
        }


class CompanionProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(port=0, service=FakeCompanionService())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.client = CompanionClient(f"http://{host}:{port}", timeout=2.0)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)

    def test_status_proves_protocol_and_model(self) -> None:
        status = self.client.status("http://127.0.0.1:11434", "qwen3:14b")
        self.assertTrue(status.available)
        self.assertEqual(status.protocol_version, PROTOCOL_VERSION)
        self.assertEqual(status.models, ("qwen3:14b",))

    def test_reply_decision_round_trip(self) -> None:
        message = ResponseMessage(
            "request-1", "message-1", "user-1", "Viewer", "hey sally", "now"
        )
        decisions = self.client.decide(
            (message,), (), "http://127.0.0.1:11434", "qwen3:14b", "Warm", False, False
        )
        self.assertEqual(len(decisions), 1)
        self.assertIsInstance(decisions[0], ResponseDecision)
        self.assertEqual(decisions[0].reply, "Hello from the companion.")

    def test_memory_extraction_round_trip(self) -> None:
        message = BufferedChatMessage(
            "buffer-1", "message-1", "user-1", "Viewer", "I like puzzles", "now"
        )
        memories = self.client.extract_memories(
            "Viewer", (message,), (), "http://127.0.0.1:11434", "qwen3:14b"
        )
        self.assertEqual(len(memories), 1)
        self.assertIsInstance(memories[0], ExtractedMemory)
        self.assertEqual(memories[0].key, "game-genre")

    def test_closed_companion_is_reported_as_unavailable(self) -> None:
        client = CompanionClient("http://127.0.0.1:1", timeout=0.2)
        self.assertFalse(client.status("http://127.0.0.1:11434", "qwen3:14b").available)


if __name__ == "__main__":
    unittest.main()
