import unittest
from threading import Event
from unittest.mock import Mock

from PySide6.QtCore import QCoreApplication, QEvent, QThread, QThreadPool
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from products.hub.config.twitch import TWITCH_BOT_SCOPES, TWITCH_SCOPES
from products.hub.modules.bundled.twitch.account_page import TwitchAccountPage
from products.hub.twitch.auth import TwitchAuthState, TwitchToken
from products.hub.twitch.service import (
    TwitchConnectionState,
    TwitchTransportHealth,
)


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
        self._destroy_page()

    def _destroy_page(self) -> None:
        if not isValid(self.page):
            return
        self.page.deleteLater()
        QCoreApplication.sendPostedEvents(self.page, QEvent.Type.DeferredDelete)

    def _use_async_profiles(self, loader):
        pool = QThreadPool()
        self._destroy_page()
        self.page = TwitchAccountPage(self.auth, self.bot_auth, self.service, thread_pool=pool)
        self.service.get_account_profile.side_effect = loader
        self.page.set_active(True)
        return pool

    def _load_profile(self, login: str) -> None:
        self.page.set_main_auth_state(
            TwitchAuthState.SIGNED_IN, login, set(),
            self._token(login, login, TWITCH_SCOPES),
        )

    def test_async_profile_completion_does_not_touch_destroyed_page(self) -> None:
        for timing, fails in (
            ("before", False), ("during", True), ("after", False), ("after", True),
        ):
            with self.subTest(timing=timing, fails=fails):
                entered, release = Event(), Event()
                threads = []

                def load(_token):
                    threads.append(QThread.currentThread())
                    entered.set()
                    if not release.wait(5):
                        raise OSError("Profile test gate timed out")
                    if fails:
                        raise OSError("Synthetic profile failure")
                    return {"login": "fixture"}

                pool = self._use_async_profiles(load)
                tracked = self.page._workers
                try:
                    self._load_profile("fixture")
                    self.assertTrue(entered.wait(5))
                    self.assertNotEqual(threads, [self.page.thread()])
                    self.assertEqual(len(tracked), 1)
                    if timing == "before":
                        release.set()
                        self.assertTrue(pool.waitForDone(5000))
                    elif timing == "during":
                        def finish_during_destruction():
                            release.set()
                            completed_during_destruction.append(pool.waitForDone(5000))

                        completed_during_destruction = []
                        self.page.destroyed.connect(finish_during_destruction)
                    self._destroy_page()
                    self.assertFalse(isValid(self.page))
                    if timing == "during":
                        self.assertEqual(completed_during_destruction, [True])
                    release.set()
                    self.assertTrue(pool.waitForDone(5000))
                    QCoreApplication.sendPostedEvents(None, QEvent.Type.MetaCall)
                    # Our test holds the original collection alive. A stray
                    # completion must not mutate it after native destruction.
                    self.assertEqual(len(tracked), 1)
                finally:
                    release.set()
                    pool.waitForDone(5000)

    def test_async_out_of_order_profile_results_keep_current_identity(self) -> None:
        first_entered, finish_first = Event(), Event()
        second_finished = Event()

        def load(token):
            if token.login == "first":
                first_entered.set()
                if not finish_first.wait(5):
                    raise OSError("Profile test gate timed out")
            else:
                second_finished.set()
            return {"login": token.login, "display_name": token.login.upper()}

        pool = self._use_async_profiles(load)
        pool.setMaxThreadCount(2)
        try:
            self._load_profile("first")
            self.assertTrue(first_entered.wait(5))
            self._load_profile("second")
            self.assertTrue(second_finished.wait(5))
            # Complete all workers before delivering GUI slots; generation
            # checks must reject an obsolete result regardless of queue order.
            finish_first.set()
            self.assertTrue(pool.waitForDone(5000))
            QCoreApplication.sendPostedEvents(self.page, QEvent.Type.MetaCall)
            self.assertEqual(self.page.main_card.display_name_label.text(), "SECOND")
            self.assertEqual(self.page.main_card.login_label.text(), "@second")
            self.assertFalse(self.page._workers)
        finally:
            finish_first.set()
            pool.waitForDone(5000)

    def test_destruction_releases_pending_avatar_reply(self) -> None:
        self.service.get_account_profile.side_effect = None
        self.service.get_account_profile.return_value = {
            "login": "fixture",
            "profile_image_url": "data:text/plain,fixture",
        }
        finished = QSignalSpy(self.page._network.finished)
        self.page.set_active(True)
        self._load_profile("fixture")
        reply, = self.page._avatar_requests
        self.assertTrue(isValid(reply))
        # A data URL can be internally finished already, but its finished
        # signal has not been delivered to the manager/page yet.
        self.assertEqual(finished.count(), 0)

        self._destroy_page()
        self.application.processEvents()

        self.assertFalse(isValid(reply))
        self.assertEqual(finished.count(), 0)

    @staticmethod
    def _token(user_id: str, login: str, scopes: tuple[str, ...]) -> TwitchToken:
        return TwitchToken("access", "refresh", 999, list(scopes), user_id, login)

    def test_no_credentials_offer_main_and_optional_bot_connection(self) -> None:
        self.assertEqual(self.page.main_card.status_label.text(), "Needs Setup")
        self.assertEqual(
            self.page.main_card.connect_button.text(), "Connect / Sign In"
        )
        self.assertEqual(
            self.page.main_card.sign_in_guidance_label.text(),
            "Make sure you're already signed in to the Twitch account you want "
            "to use in your browser before continuing.",
        )
        self.assertTrue(self.page.main_card.sign_in_guidance_label.wordWrap())
        self.assertEqual(self.page.bot_card.status_label.text(), "Needs Setup")
        self.assertEqual(self.page.bot_card.connect_button.text(), "Connect Bot")
        self.assertEqual(
            self.page.bot_card.sign_in_guidance_label.text(),
            "Make sure you're already signed in to the bot's Twitch account in "
            "your browser before connecting.",
        )
        self.assertTrue(self.page.bot_card.sign_in_guidance_label.wordWrap())
        self.assertFalse(self.page.main_card.disconnect_button.isEnabled())
        self.assertFalse(self.page.bot_card.disconnect_button.isEnabled())
        self.assertEqual(
            self.page.main_card.connection_health.rows,
            ("Auth", "API", "EventSub", "Chat"),
        )
        self.assertEqual(
            self.page.bot_card.connection_health.rows,
            ("Auth", "API", "Chat"),
        )
        self.assertNotIn("EventSub", self.page.bot_card.connection_health.rows)
        self.assertEqual(
            self.page.main_card.connection_health.status("Auth"), "Disconnected"
        )
        self.assertEqual(
            self.page.main_card.connection_health.status("API"), "Unavailable"
        )

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
        self.assertEqual(
            self.page.main_card.connection_health.status("Auth"), "Connected"
        )
        self.assertEqual(
            self.page.main_card.connection_health.status("API"), "Connected"
        )

    def test_broadcaster_only_chat_uses_live_transport_state(self) -> None:
        main_token = self._token("main-1", "streamer", TWITCH_SCOPES)
        self.page.set_active(True)
        self.page.set_main_auth_state(
            TwitchAuthState.SIGNED_IN, "streamer", set(), main_token
        )
        self.page.set_transport_health(
            TwitchTransportHealth(
                chat=TwitchConnectionState.CONNECTED,
                eventsub=TwitchConnectionState.CONNECTED,
                chat_accounts=frozenset({"main"}),
            )
        )

        self.assertEqual(
            self.page.main_card.connection_health.status("Chat"), "Connected"
        )
        self.assertEqual(
            self.page.bot_card.connection_health.status("Chat"), "Unavailable"
        )

    def test_separate_bot_keeps_broadcaster_and_bot_chat_participation(self) -> None:
        main_token = self._token("main-1", "streamer", TWITCH_SCOPES)
        bot_token = self._token("bot-1", "helperbot", TWITCH_BOT_SCOPES)
        self.page.set_active(True)
        self.page.set_main_auth_state(
            TwitchAuthState.SIGNED_IN, "streamer", set(), main_token
        )
        self.page.set_bot_auth_state(
            TwitchAuthState.SIGNED_IN, "helperbot", set(), bot_token
        )

        self.page.set_transport_health(
            TwitchTransportHealth(
                chat=TwitchConnectionState.DISCONNECTED,
                eventsub=TwitchConnectionState.DISCONNECTED,
                chat_accounts=frozenset({"main", "bot"}),
            )
        )
        self.assertEqual(
            self.page.main_card.connection_health.status("EventSub"),
            "Disconnected",
        )
        self.assertEqual(
            self.page.bot_card.connection_health.status("Chat"), "Disconnected"
        )

        self.page.set_transport_health(
            TwitchTransportHealth(
                chat=TwitchConnectionState.CONNECTED,
                eventsub=TwitchConnectionState.CONNECTED,
                chat_accounts=frozenset({"main", "bot"}),
            )
        )
        self.assertEqual(
            self.page.main_card.connection_health.status("EventSub"), "Connected"
        )
        self.assertEqual(
            self.page.main_card.connection_health.status("Chat"), "Connected"
        )
        self.assertEqual(
            self.page.bot_card.connection_health.status("Chat"), "Connected"
        )

        self.page.set_transport_health(
            TwitchTransportHealth(
                chat=TwitchConnectionState.RECONNECTING,
                eventsub=TwitchConnectionState.RECONNECTING,
                chat_accounts=frozenset({"main", "bot"}),
            )
        )
        self.assertEqual(
            self.page.main_card.connection_health.status("EventSub"), "Connecting"
        )
        self.assertEqual(
            self.page.bot_card.connection_health.status("Chat"), "Connecting"
        )
        self.assertEqual(
            self.page.main_card.connection_health.status("Chat"), "Connecting"
        )

    def test_broadcaster_and_bot_disconnect_states_follow_participation(self) -> None:
        main_token = self._token("main-1", "streamer", TWITCH_SCOPES)
        bot_token = self._token("bot-1", "helperbot", TWITCH_BOT_SCOPES)
        self.page.set_main_auth_state(
            TwitchAuthState.SIGNED_IN, "streamer", set(), main_token
        )
        self.page.set_bot_auth_state(
            TwitchAuthState.SIGNED_OUT, "Signed out", set(), None
        )
        self.page.set_transport_health(
            TwitchTransportHealth(
                chat=TwitchConnectionState.CONNECTED,
                eventsub=TwitchConnectionState.CONNECTED,
                chat_accounts=frozenset({"main"}),
            )
        )
        self.assertEqual(
            self.page.main_card.connection_health.status("Chat"), "Connected"
        )
        self.assertEqual(
            self.page.bot_card.connection_health.status("Chat"), "Unavailable"
        )

        self.page.set_main_auth_state(
            TwitchAuthState.SIGNED_OUT, "Signed out", set(), None
        )
        self.page.set_bot_auth_state(
            TwitchAuthState.SIGNED_IN, "helperbot", set(), bot_token
        )
        self.page.set_transport_health(
            TwitchTransportHealth(
                chat=TwitchConnectionState.DISCONNECTED,
                eventsub=TwitchConnectionState.DISCONNECTED,
                chat_accounts=frozenset({"bot"}),
            )
        )
        self.assertEqual(
            self.page.main_card.connection_health.status("Chat"), "Unavailable"
        )
        self.assertEqual(
            self.page.bot_card.connection_health.status("Chat"), "Disconnected"
        )

    def test_sign_out_clears_healthy_states_without_exposing_token(self) -> None:
        token = self._token("main-1", "streamer", TWITCH_SCOPES)
        self.page.set_active(True)
        self.page.set_main_auth_state(
            TwitchAuthState.SIGNED_IN, "streamer", set(), token
        )
        self.page.set_transport_health(
            TwitchTransportHealth(
                chat=TwitchConnectionState.CONNECTED,
                eventsub=TwitchConnectionState.CONNECTED,
                chat_accounts=frozenset({"main"}),
            )
        )
        self.page.set_main_auth_state(
            TwitchAuthState.SIGNED_OUT, "Signed out", set(), None
        )

        health = self.page.main_card.connection_health
        self.assertEqual(health.status("Auth"), "Disconnected")
        self.assertEqual(health.status("API"), "Unavailable")
        self.assertEqual(health.status("EventSub"), "Unavailable")
        self.assertEqual(health.status("Chat"), "Unavailable")
        visible_text = " ".join(
            label.text() for label in self.page.findChildren(type(health._status_labels["Auth"]))
        )
        self.assertNotIn("access", visible_text)
        self.assertNotIn("refresh", visible_text)

    def test_bot_profile_failure_marks_only_bot_api_as_error(self) -> None:
        main_token = self._token("main-1", "streamer", TWITCH_SCOPES)
        bot_token = self._token("bot-1", "helperbot", TWITCH_BOT_SCOPES)

        def profile(token: TwitchToken) -> dict:
            if token.user_id == "bot-1":
                raise OSError("offline")
            return {
                "id": token.user_id,
                "login": token.login,
                "display_name": "Streamer",
                "profile_image_url": "",
            }

        self.service.get_account_profile.side_effect = profile
        self.page.set_active(True)
        self.page.set_main_auth_state(
            TwitchAuthState.SIGNED_IN, "streamer", set(), main_token
        )
        self.page.set_bot_auth_state(
            TwitchAuthState.SIGNED_IN, "helperbot", set(), bot_token
        )

        self.assertEqual(
            self.page.main_card.connection_health.status("API"), "Connected"
        )
        self.assertEqual(
            self.page.bot_card.connection_health.status("API"), "Error"
        )

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
