import unittest
from unittest.mock import Mock

from PySide6.QtWidgets import QApplication

from products.hub.config.twitch import TWITCH_BOT_SCOPES, TWITCH_SCOPES
from products.hub.modules.bundled.twitch.account_page import TwitchAccountPage
from products.hub.twitch.auth import TwitchAuthState, TwitchToken
from products.hub.twitch.service import TwitchConnectionState


class _InlineThreadPool:
    def start(self, worker) -> None:
        worker.run()


class TwitchAccountPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.auth = Mock()
        self.bot_auth = Mock()
        self.service = Mock()
        self.service.get_account_profile.side_effect = lambda token: {
            "id": token.user_id,
            "login": token.login,
            "display_name": "Streamer" if token.user_id == "main-1" else "Helper Bot",
            "profile_image_url": "",
        }
        self.page = TwitchAccountPage(
            self.auth,
            self.bot_auth,
            self.service,
            thread_pool=_InlineThreadPool(),
        )

    def tearDown(self) -> None:
        self.page.deleteLater()
        self.application.processEvents()

    @staticmethod
    def _token(user_id: str, login: str, scopes: tuple[str, ...]) -> TwitchToken:
        return TwitchToken("access", "refresh", 999, list(scopes), user_id, login)

    def test_no_credentials_offer_main_and_optional_bot_connection(self) -> None:
        self.assertEqual(self.page.main_card.status_label.text(), "Needs Setup")
        self.assertEqual(
            self.page.main_card.connect_button.text(), "Connect / Sign In"
        )
        self.assertEqual(self.page.bot_card.status_label.text(), "Needs Setup")
        self.assertEqual(self.page.bot_card.connect_button.text(), "Connect Bot")
        self.assertFalse(self.page.main_card.disconnect_button.isEnabled())
        self.assertFalse(self.page.bot_card.disconnect_button.isEnabled())

    def test_connected_account_loads_identity_and_handles_missing_avatar(self) -> None:
        token = self._token("main-1", "streamer", TWITCH_SCOPES)
        self.page.set_active(True)
        self.page.set_connection_state(TwitchConnectionState.CONNECTED)
        self.page.set_main_auth_state(TwitchAuthState.SIGNED_IN, "streamer", set(), token)

        self.assertEqual(self.page.main_card.status_label.text(), "Connected")
        self.assertEqual(self.page.main_card.display_name_label.text(), "Streamer")
        self.assertEqual(self.page.main_card.login_label.text(), "@streamer")
        self.assertEqual(self.page.main_card.avatar_label.text(), "S")
        self.service.get_account_profile.assert_called_once_with(token)

    def test_profile_avatar_is_requested_without_exposing_its_url(self) -> None:
        reply = Mock()
        self.page._network.get = Mock(return_value=reply)
        self.page._profile_generation["main"] = 3

        self.page._profile_loaded(
            "main",
            3,
            {
                "id": "main-1",
                "login": "streamer",
                "display_name": "Streamer",
                "profile_image_url": "https://static.example/avatar.png",
            },
        )

        self.page._network.get.assert_called_once()
        visible_text = " ".join(
            label.text()
            for label in self.page.findChildren(
                type(self.page.main_card.status_label)
            )
        )
        self.assertNotIn("static.example", visible_text)

    def test_error_and_missing_scopes_require_reauthorization(self) -> None:
        token = self._token("main-1", "streamer", TWITCH_SCOPES)
        self.page.set_main_auth_state(
            TwitchAuthState.SIGNED_IN,
            "streamer",
            {"missing:scope"},
            token,
        )
        self.assertEqual(
            self.page.main_card.status_label.text(), "Needs Reauthorization"
        )
        self.assertEqual(self.page.main_card.connect_button.text(), "Reauthorize")

        self.page.set_main_auth_state(TwitchAuthState.ERROR, "Expired", set(), None)
        self.assertNotEqual(self.page.main_card.status_label.text(), "Connected")
        self.assertEqual(self.page.main_card.connect_button.text(), "Reconnect")

    def test_existing_auth_actions_reuse_independent_services(self) -> None:
        main_token = self._token("main-1", "streamer", TWITCH_SCOPES)
        bot_token = self._token("bot-1", "helperbot", TWITCH_BOT_SCOPES)
        self.page.set_main_auth_state(
            TwitchAuthState.SIGNED_IN, "streamer", set(), main_token
        )
        self.page.set_bot_auth_state(
            TwitchAuthState.SIGNED_IN, "helperbot", set(), bot_token
        )

        self.page.bot_card.disconnect_button.click()
        self.bot_auth.sign_out.assert_called_once_with()
        self.auth.sign_out.assert_not_called()

        self.page.main_card.disconnect_button.click()
        self.auth.sign_out.assert_called_once_with()
        self.bot_auth.sign_out.assert_called_once_with()

    def test_bot_profile_is_loaded_and_rendered_independently(self) -> None:
        main_token = self._token("main-1", "streamer", TWITCH_SCOPES)
        bot_token = self._token("bot-1", "helperbot", TWITCH_BOT_SCOPES)
        self.page.set_active(True)
        self.page.set_main_auth_state(
            TwitchAuthState.SIGNED_IN, "streamer", set(), main_token
        )
        self.page.set_bot_auth_state(
            TwitchAuthState.SIGNED_IN, "helperbot", set(), bot_token
        )

        self.assertEqual(self.page.main_card.display_name_label.text(), "Streamer")
        self.assertEqual(self.page.bot_card.display_name_label.text(), "Helper Bot")
        self.assertEqual(self.page.bot_card.login_label.text(), "@helperbot")
        self.assertEqual(self.page.bot_card.status_label.text(), "Connected")
        self.assertEqual(self.service.get_account_profile.call_count, 2)

    def test_connect_and_reauthorize_buttons_use_existing_auth_flow(self) -> None:
        self.page.main_card.connect_button.click()
        self.page.bot_card.connect_button.click()
        self.auth.sign_in.assert_called_once_with()
        self.bot_auth.sign_in.assert_called_once_with()

        token = self._token("main-1", "streamer", TWITCH_SCOPES)
        self.page.set_main_auth_state(
            TwitchAuthState.SIGNED_IN, "streamer", {"missing:scope"}, token
        )
        self.page.main_card.connect_button.click()
        self.assertEqual(self.auth.sign_in.call_count, 2)


if __name__ == "__main__":
    unittest.main()
