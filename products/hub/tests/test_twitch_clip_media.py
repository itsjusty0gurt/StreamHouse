from __future__ import annotations

import os
import unittest
from urllib.parse import parse_qsl, urlsplit

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from products.hub.twitch.clip_media import (
    TwitchClipMediaResolver,
    direct_clip_media_url,
    resolver_embed_url,
)


class _FakeSignal:
    def __init__(self) -> None:
        self.callbacks: list[object] = []

    def connect(self, callback) -> None:
        self.callbacks.append(callback)

    def disconnect(self, callback) -> None:
        if callback in self.callbacks:
            self.callbacks.remove(callback)

    def emit(self, *args) -> None:
        for callback in tuple(self.callbacks):
            callback(*args)


class _FakePage:
    def __init__(self) -> None:
        self.loadFinished = _FakeSignal()
        self.loaded: list[str] = []
        self.javascript: list[tuple[str, object]] = []
        self.actions: list[object] = []

    def load(self, url) -> None:
        self.loaded.append(url.toString())

    def runJavaScript(self, script: str, callback) -> None:
        self.javascript.append((script, callback))

    def triggerAction(self, action) -> None:
        self.actions.append(action)


class TwitchClipMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_direct_media_validation_accepts_only_https_twitch_cdn_mp4(self) -> None:
        valid = (
            "https://production.assets.clips.twitchcdn.net/AT-cm%7Cclip.mp4"
            "?sig=temporary"
        )
        self.assertEqual(direct_clip_media_url(valid), valid)
        for value in (
            "",
            "blob:https://clips.twitch.tv/value",
            "data:video/mp4;base64,AAAA",
            "http://production.assets.clips.twitchcdn.net/clip.mp4",
            "https://twitchcdn.net.evil.test/clip.mp4",
            "https://production.assets.clips.twitchcdn.net/clip.webm",
            "https://[invalid/clip.mp4",
        ):
            with self.subTest(value=value):
                self.assertEqual(direct_clip_media_url(value), "")

    def test_resolver_embed_is_muted_non_autoplay_and_deduplicated(self) -> None:
        result = resolver_embed_url(
            "clip-1",
            "https://clips.twitch.tv/embed?theme=dark&parent=old.test"
            "&autoplay=true&muted=false&clip=clip-1&parent=second.test",
        )
        parameters = parse_qsl(urlsplit(result).query, keep_blank_values=True)
        self.assertEqual(parameters.count(("clip", "clip-1")), 1)
        self.assertEqual(parameters.count(("parent", "localhost")), 1)
        self.assertEqual(parameters.count(("autoplay", "false")), 1)
        self.assertEqual(parameters.count(("muted", "true")), 1)
        self.assertEqual(parameters.count(("theme", "dark")), 1)
        self.assertEqual(
            resolver_embed_url("clip-1", "https://example.test/embed?clip=clip-1"),
            "",
        )

    def test_requests_are_serialized_and_complete_in_order(self) -> None:
        page = _FakePage()
        resolver = TwitchClipMediaResolver(page_factory=lambda: page)
        completed: list[tuple[str, str]] = []
        try:
            resolver.resolve_async(
                "first",
                "https://clips.twitch.tv/embed?clip=first",
                lambda value: completed.append(("first", value)),
            )
            resolver.resolve_async(
                "second",
                "https://clips.twitch.tv/embed?clip=second",
                lambda value: completed.append(("second", value)),
            )

            self.assertEqual(len(page.loaded), 1)
            self.assertIn("clip=first", page.loaded[0])
            page.loadFinished.emit(True)
            first_callback = page.javascript[-1][1]
            first_callback(
                "https://production.assets.clips.twitchcdn.net/first.mp4?sig=1"
            )

            self.assertEqual(completed[0][0], "first")
            self.assertEqual(len(page.loaded), 2)
            self.assertIn("clip=second", page.loaded[1])
            page.loadFinished.emit(True)
            second_callback = page.javascript[-1][1]
            second_callback(
                "https://production.assets.clips.twitchcdn.net/second.mp4?sig=2"
            )

            self.assertEqual(
                completed,
                [
                    (
                        "first",
                        "https://production.assets.clips.twitchcdn.net/first.mp4?sig=1",
                    ),
                    (
                        "second",
                        "https://production.assets.clips.twitchcdn.net/second.mp4?sig=2",
                    ),
                ],
            )
        finally:
            resolver.shutdown()

    def test_timeout_returns_empty_and_advances_queue(self) -> None:
        page = _FakePage()
        resolver = TwitchClipMediaResolver(page_factory=lambda: page)
        completed: list[str] = []
        try:
            resolver.resolve_async(
                "first",
                "https://clips.twitch.tv/embed?clip=first",
                completed.append,
            )
            resolver.resolve_async(
                "second",
                "https://clips.twitch.tv/embed?clip=second",
                completed.append,
            )
            resolver._timeout_current()

            self.assertEqual(completed, [""])
            self.assertEqual(len(page.loaded), 2)
            self.assertIn("clip=second", page.loaded[-1])
        finally:
            resolver.shutdown()

    def test_late_javascript_result_cannot_change_the_next_request(self) -> None:
        page = _FakePage()
        resolver = TwitchClipMediaResolver(page_factory=lambda: page)
        completed: list[tuple[str, str]] = []
        try:
            resolver.resolve_async(
                "first",
                "https://clips.twitch.tv/embed?clip=first",
                lambda value: completed.append(("first", value)),
            )
            resolver.resolve_async(
                "second",
                "https://clips.twitch.tv/embed?clip=second",
                lambda value: completed.append(("second", value)),
            )
            page.loadFinished.emit(True)
            first_callback = page.javascript[-1][1]
            resolver._timeout_current()
            page.loadFinished.emit(True)

            self.assertTrue(resolver._javascript_pending)
            first_callback(
                "https://production.assets.clips.twitchcdn.net/first.mp4?sig=late"
            )
            self.assertTrue(resolver._javascript_pending)
            self.assertEqual(completed, [("first", "")])

            second_callback = page.javascript[-1][1]
            second_callback(
                "https://production.assets.clips.twitchcdn.net/second.mp4?sig=2"
            )
            self.assertEqual(
                completed,
                [
                    ("first", ""),
                    (
                        "second",
                        "https://production.assets.clips.twitchcdn.net/second.mp4?sig=2",
                    ),
                ],
            )
        finally:
            resolver.shutdown()

    def test_shutdown_releases_active_and_queued_callbacks(self) -> None:
        page = _FakePage()
        resolver = TwitchClipMediaResolver(page_factory=lambda: page)
        completed: list[str] = []
        resolver.resolve_async(
            "first",
            "https://clips.twitch.tv/embed?clip=first",
            completed.append,
        )
        resolver.resolve_async(
            "second",
            "https://clips.twitch.tv/embed?clip=second",
            completed.append,
        )

        resolver.shutdown()

        self.assertEqual(completed, ["", ""])
        self.assertEqual(resolver.resolve("third", "https://clips.twitch.tv/embed?clip=third"), "")


if __name__ == "__main__":
    unittest.main()
