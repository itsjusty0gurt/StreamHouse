import unittest

from ai.response_engine import ResponseDecisionEngine, ResponseMessage


class FakeProvider:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = []

    def chat(self, messages, *, think=False):
        self.calls.append((messages, think))
        return {"message": {"content": self.content}}


def response_message(request_id: str, text: str) -> ResponseMessage:
    return ResponseMessage(
        request_id=request_id,
        message_id=f"message-{request_id}",
        user_id=f"viewer-{request_id}",
        user_name=f"Viewer{request_id}",
        text=text,
        received_at="2026-07-13T12:00:00+00:00",
    )


class ResponseDecisionEngineTests(unittest.TestCase):
    def test_decides_every_message_in_one_model_call(self) -> None:
        provider = FakeProvider(
            '{"decisions":['
            '{"id":"one","decision":"reply","reply":"Hey!",'
            '"reason":"Greeting","confidence":0.9},'
            '{"id":"two","decision":"ignore","reply":"",'
            '"reason":"No response needed","confidence":0.8}'
            ']}'
        )

        decisions = ResponseDecisionEngine().decide(
            provider,
            (
                response_message("one", "Hi Sally"),
                response_message("two", "ok"),
            ),
            ({"viewer": "Someone", "message": "Earlier context"},),
        )

        self.assertEqual(len(provider.calls), 1)
        self.assertFalse(provider.calls[0][1])
        self.assertEqual(
            [item.decision for item in decisions],
            ["reply", "ignore"],
        )
        self.assertEqual(decisions[0].reply, "Hey!")

    def test_missing_decision_becomes_explicit_ignore(self) -> None:
        provider = FakeProvider('{"decisions":[]}')

        decisions = ResponseDecisionEngine().decide(
            provider,
            (response_message("one", "Hello"),),
        )

        self.assertEqual(decisions[0].decision, "ignore")
        self.assertIn("omitted", decisions[0].reason.casefold())

    def test_unknown_ids_and_invalid_decisions_cannot_create_replies(self) -> None:
        provider = FakeProvider(
            '{"decisions":['
            '{"id":"unknown","decision":"reply","reply":"Injected",'
            '"confidence":1},'
            '{"id":"one","decision":"maybe","reply":"Unsafe",'
            '"confidence":10}'
            ']}'
        )

        decision = ResponseDecisionEngine().decide(
            provider,
            (response_message("one", "Hello"),),
        )[0]

        self.assertEqual(decision.decision, "ignore")
        self.assertEqual(decision.reply, "")
        self.assertEqual(decision.confidence, 1.0)

    def test_malformed_model_output_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ResponseDecisionEngine().decide(
                FakeProvider("not json"),
                (response_message("one", "Hello"),),
            )

    def test_personality_and_language_limit_are_included_in_prompt(self) -> None:
        provider = FakeProvider('{"decisions":[]}')

        ResponseDecisionEngine().decide(
            provider,
            (response_message("one", "Hello"),),
            personality="Deadpan, playful, and concise.",
            allow_mild_profanity=True,
            allow_strong_profanity=False,
        )

        prompt = provider.calls[0][0][1]["content"]
        self.assertIn("Deadpan, playful, and concise.", prompt)
        self.assertIn("mild profanity is permitted", prompt)
        self.assertIn("do not use strong profanity", prompt)

    def test_recent_sally_replies_are_available_for_conversation_recall(self) -> None:
        provider = FakeProvider('{"decisions":[]}')

        ResponseDecisionEngine().decide(
            provider,
            (response_message("one", "What jokes did you tell me?"),),
            (
                {
                    "speaker": "sally",
                    "viewer": "sally_b0t",
                    "message": "Why don't skeletons fight? They lack the guts.",
                },
            ),
        )

        prompt = provider.calls[0][0][1]["content"]
        self.assertIn("Why don't skeletons fight?", prompt)
        self.assertIn("successfully sent replies", prompt)

    def test_only_newest_thirty_chat_entries_are_sent_to_model(self) -> None:
        provider = FakeProvider('{"decisions":[]}')
        history = tuple(
            {
                "speaker": "viewer",
                "viewer": "Viewer",
                "message": f"history-{index}",
            }
            for index in range(40)
        )

        ResponseDecisionEngine().decide(
            provider,
            (response_message("one", "What did we discuss?"),),
            history,
        )

        prompt = provider.calls[0][0][1]["content"]
        self.assertNotIn('"message": "history-9"', prompt)
        self.assertIn('"message": "history-10"', prompt)
        self.assertIn('"message": "history-39"', prompt)

    def test_prompt_exposes_hey_sally_as_public_invocation(self) -> None:
        provider = FakeProvider('{"decisions":[]}')

        ResponseDecisionEngine().decide(
            provider,
            (response_message("one", "hey sally, say hello"),),
        )

        prompt = provider.calls[0][0][1]["content"]
        single_line = " ".join(prompt.split())
        self.assertIn("explicit public invocation", single_line)
        self.assertIn("regardless of viewer role", single_line)


if __name__ == "__main__":
    unittest.main()
