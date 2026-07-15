import unittest
from unittest.mock import Mock

from ai.memory_extractor import BufferedChatMessage, MemoryExtractor


class MemoryExtractorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.messages = (
            BufferedChatMessage(
                buffer_id="one",
                message_id="twitch-one",
                user_id="1",
                user_name="Viewer",
                text="I have been building a puzzle game this year.",
                timestamp="2026-07-13T00:00:00+00:00",
            ),
            BufferedChatMessage(
                buffer_id="two",
                message_id="twitch-two",
                user_id="1",
                user_name="Viewer",
                text="My puzzle game is called Switchboard.",
                timestamp="2026-07-13T00:01:00+00:00",
            ),
        )

    def test_extracts_valid_evidence_backed_proposal(self) -> None:
        provider = Mock()
        provider.chat.return_value = {
            "message": {
                "content": """
                {"memories":[{"text":"Viewer is building a puzzle game called Switchboard","category":"Project","key":"current-project","confidence":0.9,"evidence_ids":["one","two"]}]}
                """
            }
        }

        proposals = MemoryExtractor().extract(
            provider,
            "Viewer",
            self.messages,
        )

        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0].category, "Project")
        self.assertEqual(proposals[0].key, "current-project")
        self.assertEqual(
            [item["message_id"] for item in proposals[0].evidence],
            ["twitch-one", "twitch-two"],
        )

    def test_rejects_hallucinated_evidence_and_low_confidence(self) -> None:
        provider = Mock()
        provider.chat.return_value = {
            "message": {
                "content": """
                {"memories":[
                  {"text":"Viewer owns a spaceship","category":"Personal","key":"vehicle","confidence":0.99,"evidence_ids":["invented"]},
                  {"text":"Viewer likes puzzles","category":"Preference","key":"game-genre","confidence":0.2,"evidence_ids":["one"]}
                ]}
                """
            }
        }

        self.assertEqual(
            MemoryExtractor().extract(provider, "Viewer", self.messages),
            (),
        )

    def test_invalid_json_is_rejected(self) -> None:
        provider = Mock()
        provider.chat.return_value = {
            "message": {"content": "I think this viewer likes games."}
        }

        with self.assertRaisesRegex(ValueError, "valid memory JSON"):
            MemoryExtractor().extract(provider, "Viewer", self.messages)

    def test_sensitive_proposal_is_rejected_even_with_valid_evidence(self) -> None:
        provider = Mock()
        provider.chat.return_value = {
            "message": {
                "content": """
                {"memories":[{"text":"Viewer's home address is 123 Example Street","category":"Personal","key":"home-address","confidence":0.99,"evidence_ids":["one"]}]}
                """
            }
        }

        self.assertEqual(
            MemoryExtractor().extract(provider, "Viewer", self.messages),
            (),
        )


if __name__ == "__main__":
    unittest.main()
