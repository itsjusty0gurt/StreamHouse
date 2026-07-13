import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

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


if __name__ == "__main__":
    unittest.main()
