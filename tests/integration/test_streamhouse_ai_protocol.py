from __future__ import annotations

import threading
import unittest
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from products.ai.engine.memory_extractor import BufferedChatMessage, ExtractedMemory
from products.ai.engine.response_engine import ResponseDecision, ResponseMessage
from products.hub.streamhouse_hub.ai_client import StreamhouseAIClient
from shared.streamhouse_shared.protocol import (
    PROTOCOL_HEADER,
    PROTOCOL_VERSION,
    response_message_to_dict,
)
from products.ai.streamhouse_ai.server import create_server


class FakeStreamhouseAIService:
    def ping(self, _body: dict) -> dict:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "available": True,
        }

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
                    "reply": "Hello from Streamhouse AI.",
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


class StreamhouseAIProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = create_server(port=0, service=FakeStreamhouseAIService())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.client = StreamhouseAIClient(f"http://{host}:{port}", timeout=2.0)

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2.0)

    def test_status_proves_protocol_and_model(self) -> None:
        self.assertEqual(PROTOCOL_VERSION, 3)
        self.assertEqual(self.server.server_address[0], "127.0.0.1")
        status = self.client.status("http://127.0.0.1:11434", "qwen3:14b")
        self.assertTrue(status.available)
        self.assertEqual(status.protocol_version, PROTOCOL_VERSION)
        self.assertEqual(status.models, ("qwen3:14b",))

    def test_response_message_wire_fields_are_product_neutral(self) -> None:
        payload = response_message_to_dict(
            ResponseMessage(
                "request-1",
                "message-1",
                "user-1",
                "Viewer",
                "hello",
                "now",
                previous_ai_reply="Previous answer",
                directed_at_ai=True,
                reply_to_ai=True,
            )
        )

        self.assertEqual(payload["previous_ai_reply"], "Previous answer")
        self.assertTrue(payload["directed_at_ai"])
        self.assertTrue(payload["reply_to_ai"])
        self.assertNotIn("previous_sally_reply", payload)
        self.assertNotIn("directed_at_sally", payload)
        self.assertNotIn("reply_to_sally", payload)

    def test_reply_decision_round_trip(self) -> None:
        message = ResponseMessage(
            "request-1", "message-1", "user-1", "Viewer", "hey sally", "now"
        )
        decisions = self.client.decide(
            (message,), (), "http://127.0.0.1:11434", "qwen3:14b", "Warm", False, False
        )
        self.assertEqual(len(decisions), 1)
        self.assertIsInstance(decisions[0], ResponseDecision)
        self.assertEqual(decisions[0].reply, "Hello from Streamhouse AI.")

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

    def test_closed_streamhouse_ai_is_reported_as_unavailable(self) -> None:
        client = StreamhouseAIClient("http://127.0.0.1:1", timeout=0.2)
        self.assertFalse(client.status("http://127.0.0.1:11434", "qwen3:14b").available)

    def test_unknown_protocol_version_has_clear_mismatch_error(self) -> None:
        request = Request(
            self.client.endpoint + "/v1/ping",
            data=b"{}",
            headers={
                "Content-Type": "application/json",
                PROTOCOL_HEADER: "999",
            },
            method="POST",
        )
        with self.assertRaises(HTTPError) as raised:
            urlopen(request, timeout=2.0)
        self.assertEqual(raised.exception.code, 409)
        payload = json.loads(raised.exception.read().decode("utf-8"))
        self.assertIn("Unsupported Streamhouse protocol", payload["error"])


if __name__ == "__main__":
    unittest.main()
