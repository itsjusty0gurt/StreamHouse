from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import Event
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4
import weakref

from PySide6.QtCore import QObject, QEventLoop, QThread, QTimer, Qt, QUrl, Signal

from shared.streamhouse_runtime.logger import Logger


CLIP_MEDIA_RESOLUTION_TIMEOUT_MS = 5_000
CLIP_MEDIA_POLL_INTERVAL_MS = 100


def direct_clip_media_url(value: object) -> str:
    """Return a validated Twitch CDN MP4 URL, or an empty string."""

    candidate = str(value or "").strip()
    if not candidate:
        return ""
    try:
        parsed = urlsplit(candidate)
        hostname = (parsed.hostname or "").casefold()
        _port = parsed.port
    except (TypeError, ValueError):
        return ""
    if (
        parsed.scheme.casefold() != "https"
        or not hostname
        or not (hostname == "twitchcdn.net" or hostname.endswith(".twitchcdn.net"))
        or not parsed.path.casefold().endswith(".mp4")
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    return candidate


def resolver_embed_url(clip_id: str, embed_url: str) -> str:
    """Build the muted, non-autoplay Twitch embed used only for resolution."""

    clean_id = str(clip_id).strip()
    candidate = str(embed_url).strip()
    if not clean_id or not candidate:
        return ""
    try:
        parsed = urlsplit(candidate)
        parameters = parse_qsl(parsed.query, keep_blank_values=True)
    except ValueError:
        return ""
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold() != "clips.twitch.tv"
        or parsed.path != "/embed"
    ):
        return ""
    embedded_ids = [
        value for key, value in parameters if key.casefold() == "clip"
    ]
    if embedded_ids != [clean_id]:
        return ""
    controlled = {"clip", "parent", "autoplay", "muted"}
    normalized = [
        (key, value)
        for key, value in parameters
        if key.casefold() not in controlled
    ]
    normalized.extend(
        (
            ("clip", clean_id),
            ("parent", "localhost"),
            ("autoplay", "false"),
            ("muted", "true"),
        )
    )
    return urlunsplit(parsed._replace(query=urlencode(normalized)))


@dataclass(slots=True)
class _ResolutionRequest:
    token: str
    clip_id: str
    embed_url: str
    callback: Callable[[str], None]
    cancelled: bool = False


class TwitchClipMediaResolver(QObject):
    """Serialize best-effort Twitch clip media discovery in one hidden page."""

    _request_from_worker = Signal(object)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        timeout_ms: int = CLIP_MEDIA_RESOLUTION_TIMEOUT_MS,
        page_factory: Callable[[], object] | None = None,
    ) -> None:
        super().__init__(parent)
        self._timeout_ms = max(int(timeout_ms), 100)
        self._page_factory = page_factory
        self._page = None
        self._profile = None
        self._queue: deque[_ResolutionRequest] = deque()
        self._current: _ResolutionRequest | None = None
        self._load_slot = None
        self._javascript_pending = False
        self._closed = False
        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._timeout_current)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(CLIP_MEDIA_POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_current)
        self._request_from_worker.connect(
            self._enqueue_request,
            Qt.ConnectionType.QueuedConnection,
        )

    def resolve(self, clip_id: str, embed_url: str) -> str:
        """Resolve one clip while allowing Qt's event loop to remain responsive."""

        if self._closed:
            return ""
        result = ""
        completed = Event()
        loop = QEventLoop() if QThread.currentThread() == self.thread() else None

        def finish(value: str) -> None:
            nonlocal result
            result = value
            completed.set()
            if loop is not None and loop.isRunning():
                loop.quit()

        request = _ResolutionRequest(
            token=uuid4().hex,
            clip_id=str(clip_id).strip(),
            embed_url=str(embed_url).strip(),
            callback=finish,
        )
        if loop is not None:
            self._enqueue_request(request)
            if not completed.is_set():
                loop.exec()
        else:
            self._request_from_worker.emit(request)
            if not completed.wait((self._timeout_ms / 1000) + 1.0):
                request.cancelled = True
                return ""
        return result

    def resolve_async(
        self,
        clip_id: str,
        embed_url: str,
        callback: Callable[[str], None],
    ) -> None:
        """Queue a resolution without blocking its caller."""

        request = _ResolutionRequest(
            token=uuid4().hex,
            clip_id=str(clip_id).strip(),
            embed_url=str(embed_url).strip(),
            callback=callback,
        )
        if QThread.currentThread() == self.thread():
            self._enqueue_request(request)
        else:
            self._request_from_worker.emit(request)

    def shutdown(self) -> None:
        """Stop navigation and release every waiter before QObject teardown."""

        if self._closed:
            return
        self._closed = True
        self._timeout_timer.stop()
        self._poll_timer.stop()
        self._disconnect_load_slot()
        page = self._page
        if page is not None:
            try:
                from PySide6.QtWebEngineCore import QWebEnginePage

                page.triggerAction(QWebEnginePage.WebAction.Stop)
            except (RuntimeError, TypeError):
                pass
        requests = ([self._current] if self._current is not None else []) + list(
            self._queue
        )
        self._current = None
        self._queue.clear()
        for request in requests:
            request.cancelled = True
            request.callback("")

    def _enqueue_request(self, request: _ResolutionRequest) -> None:
        if self._closed or request.cancelled:
            request.callback("")
            return
        self._queue.append(request)
        self._start_next()

    def _start_next(self) -> None:
        if self._closed or self._current is not None:
            return
        while self._queue and self._queue[0].cancelled:
            self._queue.popleft().callback("")
        if not self._queue:
            return
        request = self._queue.popleft()
        target = resolver_embed_url(request.clip_id, request.embed_url)
        if not target:
            request.callback("")
            self._start_next()
            return
        self._current = request
        try:
            page = self._ensure_page()
            token = request.token
            self._load_slot = (
                lambda succeeded, current_token=token: self._load_finished(
                    current_token, succeeded
                )
            )
            page.loadFinished.connect(self._load_slot)
            self._timeout_timer.start(self._timeout_ms)
            page.load(QUrl(target))
        except (ImportError, RuntimeError, TypeError) as error:
            Logger.debug(
                f"Twitch clip direct media resolution is unavailable ({type(error).__name__}).",
                source="TWITCH",
            )
            self._complete_current("")

    def _ensure_page(self):
        if self._page is not None:
            return self._page
        if self._page_factory is not None:
            self._page = self._page_factory()
            return self._page
        from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile

        self._profile = QWebEngineProfile(self)
        self._page = QWebEnginePage(self._profile, self)
        return self._page

    def _load_finished(self, token: str, succeeded: bool) -> None:
        if self._closed or self._current is None or self._current.token != token:
            return
        if not succeeded:
            self._complete_current("")
            return
        self._poll_timer.start()
        self._poll_current()

    def _poll_current(self) -> None:
        request = self._current
        page = self._page
        if (
            self._closed
            or request is None
            or page is None
            or self._javascript_pending
        ):
            return
        self._javascript_pending = True
        resolver_ref = weakref.ref(self)
        token = request.token

        def javascript_finished(value) -> None:
            resolver = resolver_ref()
            if resolver is None or resolver._closed:
                return
            resolver._javascript_finished(token, value)

        try:
            page.runJavaScript(
                'document.querySelector("video")?.src || ""',
                javascript_finished,
            )
        except (RuntimeError, TypeError):
            self._javascript_pending = False
            self._complete_current("")

    def _javascript_finished(self, token: str, value: object) -> None:
        if self._current is None or self._current.token != token:
            return
        self._javascript_pending = False
        resolved = direct_clip_media_url(value)
        if resolved:
            self._complete_current(resolved)

    def _timeout_current(self) -> None:
        if self._current is None:
            return
        Logger.debug(
            "Twitch clip direct media resolution timed out; using embed fallback.",
            source="TWITCH",
        )
        self._complete_current("")

    def _disconnect_load_slot(self) -> None:
        if self._page is None or self._load_slot is None:
            self._load_slot = None
            return
        try:
            self._page.loadFinished.disconnect(self._load_slot)
        except (RuntimeError, TypeError):
            pass
        self._load_slot = None

    def _complete_current(self, value: str) -> None:
        request = self._current
        if request is None:
            return
        self._timeout_timer.stop()
        self._poll_timer.stop()
        self._disconnect_load_slot()
        self._javascript_pending = False
        self._current = None
        request.callback(value if not request.cancelled else "")
        self._start_next()
