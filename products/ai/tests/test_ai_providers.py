import json
import unittest
from unittest.mock import Mock, patch

from products.ai.engine.providers import OllamaProvider


class OllamaProviderTests(unittest.TestCase):
    @staticmethod
    def response(payload: dict) -> Mock:
        response = Mock()
        response.read.return_value = json.dumps(payload).encode("utf-8")
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    @patch("products.ai.engine.providers.urlopen")
    def test_status_lists_local_models(self, urlopen: Mock) -> None:
        urlopen.return_value = self.response(
            {"models": [{"name": "qwen3:14b"}]}
        )
        status = OllamaProvider().status()
        self.assertTrue(status.available)
        self.assertEqual(status.models, ("qwen3:14b",))

    @patch("products.ai.engine.providers.urlopen")
    def test_chat_uses_selected_model(self, urlopen: Mock) -> None:
        urlopen.return_value = self.response(
            {"message": {"role": "assistant", "content": "Hello"}}
        )
        provider = OllamaProvider(model="qwen3:14b")
        result = provider.chat([{"role": "user", "content": "Hi"}])
        request = urlopen.call_args.args[0]
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body["model"], "qwen3:14b")
        self.assertFalse(body["stream"])
        self.assertEqual(result["message"]["content"], "Hello")


if __name__ == "__main__":
    unittest.main()
