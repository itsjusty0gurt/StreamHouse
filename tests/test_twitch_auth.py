import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import Mock, call, patch

from twitch.auth import TwitchAuthService, TwitchAuthState, TwitchToken
from twitch.token_store import TwitchTokenStore


class TwitchTokenStoreTests(unittest.TestCase):
    def test_windows_encrypted_token_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = TwitchTokenStore(Path(directory) / "token.dat")
            expected = TwitchToken(
                access_token="access",
                refresh_token="refresh",
                expires_at=123.0,
                scopes=["user:read:chat"],
                user_id="42",
                login="sallybot",
            )

            store.save(expected)

            self.assertEqual(store.load(), expected)
            self.assertNotIn(b"access", store.path.read_bytes())
            store.clear()
            self.assertFalse(store.path.exists())


class TwitchAuthServiceTests(unittest.TestCase):
    @staticmethod
    def _device_error(reason: str, description: str = "") -> HTTPError:
        payload = {"status": 400, "message": reason}
        if description:
            payload = {
                "error": reason,
                "error_description": description,
            }
        return HTTPError(
            "https://id.twitch.tv/oauth2/token",
            400,
            "Bad Request",
            {},
            io.BytesIO(json.dumps(payload).encode("utf-8")),
        )

    @staticmethod
    def _device_service(*responses):
        client = Mock()
        client.start_device_flow.return_value = {
            "user_code": "TEST-CODE",
            "verification_uri": "https://www.twitch.tv/activate",
            "interval": 1,
            "expires_in": 600,
            "device_code": "device-code",
        }
        client.exchange_device_code.side_effect = responses
        store = Mock()
        service = TwitchAuthService(client=client, store=store)
        service._cancel = Mock()
        service._cancel.wait.return_value = False
        service._cancel.is_set.return_value = False
        return service, client, store

    def test_sign_out_clears_token_and_store(self) -> None:
        store = Mock()
        service = TwitchAuthService(client=Mock(), store=store)
        service.token = TwitchToken("a", "r", 1, [])

        service.sign_out()

        self.assertIsNone(service.token)
        self.assertIs(service.state, TwitchAuthState.SIGNED_OUT)
        store.clear.assert_called_once_with()

    def test_new_optional_scopes_do_not_discard_valid_login(self) -> None:
        token = TwitchToken(
            "access",
            "refresh",
            999,
            ["user:read:chat"],
            user_id="42",
            login="sallybot",
        )
        client = Mock()
        client.validate.return_value = token
        store = Mock()
        service = TwitchAuthService(client=client, store=store)

        service._validate_saved(token)

        self.assertIs(service.token, token)
        self.assertIs(service.state, TwitchAuthState.SIGNED_IN)
        store.save.assert_called_once_with(token)
        store.clear.assert_not_called()

    def test_unreadable_saved_token_is_cleared_without_crashing(self) -> None:
        store = Mock()
        store.load.side_effect = OSError("unreadable")
        service = TwitchAuthService(client=Mock(), store=store)

        service.restore()

        store.clear.assert_called_once_with()
        self.assertIs(service.state, TwitchAuthState.SIGNED_OUT)

    def test_unauthorized_recovery_refreshes_and_saves_token(self) -> None:
        old_token = TwitchToken("old", "refresh", 999, [])
        new_token = TwitchToken(
            "new",
            "new-refresh",
            9999,
            ["user:read:chat"],
            user_id="42",
            login="sallybot",
        )
        client = Mock()
        client.refresh.return_value = new_token
        client.validate.return_value = new_token
        store = Mock()
        service = TwitchAuthService(client=client, store=store)
        service.token = old_token
        service._run_async = lambda function, *args: function(*args)

        self.assertTrue(service.recover_unauthorized())

        self.assertIs(service.token, new_token)
        store.save.assert_called_once_with(new_token)
        self.assertFalse(service.recover_unauthorized())

    @patch("twitch.auth.webbrowser.open")
    def test_device_flow_keeps_polling_while_authorization_is_pending(
        self,
        _open: Mock,
    ) -> None:
        token = TwitchToken("access", "refresh", 9999, [], login="sallybot")
        service, client, store = self._device_service(
            self._device_error("authorization_pending"),
            token,
        )
        client.validate.return_value = token

        service._device_flow()

        self.assertIs(service.state, TwitchAuthState.SIGNED_IN)
        self.assertEqual(service._cancel.wait.call_args_list, [call(1), call(1)])
        store.save.assert_called_once_with(token)

    @patch("twitch.auth.webbrowser.open")
    def test_device_flow_slow_down_adds_five_seconds_to_polling(
        self,
        _open: Mock,
    ) -> None:
        token = TwitchToken("access", "refresh", 9999, [], login="sallybot")
        service, client, _store = self._device_service(
            self._device_error("slow_down"),
            token,
        )
        client.validate.return_value = token

        service._device_flow()

        self.assertIs(service.state, TwitchAuthState.SIGNED_IN)
        self.assertEqual(service._cancel.wait.call_args_list, [call(1), call(6)])

    @patch("twitch.auth.webbrowser.open")
    def test_device_flow_reports_denial_immediately(self, _open: Mock) -> None:
        service, client, _store = self._device_service(
            self._device_error("access_denied")
        )
        service._set_state = Mock(wraps=service._set_state)

        service._device_flow()

        self.assertIs(service.state, TwitchAuthState.ERROR)
        self.assertEqual(client.exchange_device_code.call_count, 1)
        service._set_state.assert_called_with(
            TwitchAuthState.ERROR,
            "Twitch authorization was denied.",
        )

    @patch("twitch.auth.webbrowser.open")
    def test_device_flow_reports_expired_code_immediately(self, _open: Mock) -> None:
        service, client, _store = self._device_service(
            self._device_error("expired_token")
        )
        service._set_state = Mock(wraps=service._set_state)

        service._device_flow()

        self.assertIs(service.state, TwitchAuthState.ERROR)
        self.assertEqual(client.exchange_device_code.call_count, 1)
        service._set_state.assert_called_with(
            TwitchAuthState.ERROR,
            "The Twitch activation code expired.",
        )


if __name__ == "__main__":
    unittest.main()
