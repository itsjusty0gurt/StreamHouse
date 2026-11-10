from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QUrl, Signal, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from products.hub.twitch.auth import TwitchAuthService, TwitchAuthState, TwitchToken
from products.hub.twitch.service import TwitchConnectionState, TwitchService


@dataclass(slots=True)
class _AccountPresentation:
    state: TwitchAuthState = TwitchAuthState.SIGNED_OUT
    detail: str = "Not signed in"
    missing_scopes: frozenset[str] = frozenset()
    token: TwitchToken | None = None


class _ProfileWorkerSignals(QObject):
    loaded = Signal(str, int, object)
    failed = Signal(str, int)


class _ProfileWorker(QRunnable):
    def __init__(
        self,
        account: str,
        generation: int,
        token: TwitchToken,
        loader: Callable[[TwitchToken], dict],
    ) -> None:
        super().__init__()
        self.account = account
        self.generation = generation
        self.token = token
        self.loader = loader
        self.signals = _ProfileWorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            profile = self.loader(self.token)
        except (OSError, TypeError, ValueError):
            try:
                self.signals.failed.emit(self.account, self.generation)
            except RuntimeError:
                # The page may have been destroyed while the request was in flight.
                pass
            return
        try:
            self.signals.loaded.emit(self.account, self.generation, profile)
        except RuntimeError:
            # The page may have been destroyed while the request was in flight.
            pass


class _AccountCard(QFrame):
    def __init__(self, title: str, *, prominent: bool, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("twitchAccountCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        title_label = QLabel(title, self)
        title_label.setStyleSheet(
            f"font-size: {'20px' if prominent else '16px'}; font-weight: 650;"
        )
        layout.addWidget(title_label)

        identity_row = QHBoxLayout()
        identity_row.setSpacing(16)
        self.avatar_label = QLabel(self)
        avatar_size = 104 if prominent else 72
        self.avatar_label.setFixedSize(avatar_size, avatar_size)
        self.avatar_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar_label.setStyleSheet(
            "background: #6441a5; border-radius: 8px; color: white; "
            f"font-size: {'30px' if prominent else '22px'}; font-weight: 700;"
        )
        identity_row.addWidget(self.avatar_label)

        identity = QVBoxLayout()
        identity.setSpacing(4)
        self.display_name_label = QLabel("Not connected", self)
        self.display_name_label.setStyleSheet(
            f"font-size: {'18px' if prominent else '15px'}; font-weight: 600;"
        )
        self.login_label = QLabel("", self)
        self.login_label.setStyleSheet("color: #a7a7b3;")
        self.status_label = QLabel("Needs Setup", self)
        self.status_label.setObjectName("twitchAccountStatus")
        self.status_label.setStyleSheet("font-weight: 600;")
        identity.addStretch()
        identity.addWidget(self.display_name_label)
        identity.addWidget(self.login_label)
        identity.addWidget(self.status_label)
        identity.addStretch()
        identity_row.addLayout(identity, 1)
        layout.addLayout(identity_row)

        actions = QHBoxLayout()
        self.connect_button = QPushButton(self)
        self.disconnect_button = QPushButton(self)
        self.disconnect_button.setEnabled(False)
        actions.addWidget(self.connect_button)
        actions.addWidget(self.disconnect_button)
        actions.addStretch()
        layout.addLayout(actions)

    def show_placeholder(self, letter: str) -> None:
        self.avatar_label.setPixmap(QPixmap())
        self.avatar_label.setText(letter[:1].upper() or "T")

    def show_avatar(self, pixmap: QPixmap) -> None:
        size = self.avatar_label.size()
        self.avatar_label.setText("")
        self.avatar_label.setPixmap(
            pixmap.scaled(
                size,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


class TwitchAccountPage(QWidget):
    """Twitch-owned broadcaster and optional bot account setup surface."""

    def __init__(
        self,
        auth: TwitchAuthService,
        bot_auth: TwitchAuthService,
        service: TwitchService,
        parent: QWidget | None = None,
        *,
        thread_pool: QThreadPool | None = None,
    ) -> None:
        super().__init__(parent)
        self.auth = auth
        self.bot_auth = bot_auth
        self.service = service
        self._thread_pool = thread_pool or QThreadPool.globalInstance()
        self._network = QNetworkAccessManager(self)
        self._network.finished.connect(self._avatar_finished)
        self._active = False
        self._connection_state = TwitchConnectionState.DISCONNECTED
        self._presentations = {
            "main": _AccountPresentation(),
            "bot": _AccountPresentation(),
        }
        self._profile_generation = {"main": 0, "bot": 0}
        self._profile_user_id = {"main": "", "bot": ""}
        self._workers: set[_ProfileWorker] = set()
        self._avatar_cache: dict[str, QPixmap] = {}
        self._avatar_requests: dict[QNetworkReply, tuple[str, int, str]] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget(scroll)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(20, 20, 20, 20)
        content_layout.setSpacing(16)

        intro = QLabel(
            "Connect your Twitch channel and, optionally, a separate bot account.",
            content,
        )
        intro.setWordWrap(True)
        content_layout.addWidget(intro)
        self.main_card = _AccountCard(
            "Main Twitch Account", prominent=True, parent=content
        )
        self.bot_card = _AccountCard("Bot Account", prominent=False, parent=content)
        self.bot_help_label = QLabel(
            "Optional. Chat can use a separate bot identity while channel controls "
            "continue using the main Twitch account.",
            self.bot_card,
        )
        self.bot_help_label.setWordWrap(True)
        self.bot_card.layout().addWidget(self.bot_help_label)
        content_layout.addWidget(self.main_card)
        content_layout.addWidget(self.bot_card)
        content_layout.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

        self.main_card.connect_button.clicked.connect(lambda: self.auth.sign_in())
        self.main_card.disconnect_button.clicked.connect(lambda: self.auth.sign_out())
        self.bot_card.connect_button.clicked.connect(lambda: self.bot_auth.sign_in())
        self.bot_card.disconnect_button.clicked.connect(
            lambda: self.bot_auth.sign_out()
        )
        self._render("main")
        self._render("bot")

    def set_active(self, active: bool) -> None:
        self._active = active
        if active:
            self._maybe_load_profile("main")
            self._maybe_load_profile("bot")

    def set_connection_state(self, state: TwitchConnectionState) -> None:
        self._connection_state = state
        self._render("main")

    def set_main_auth_state(
        self,
        state: TwitchAuthState,
        detail: str,
        missing_scopes: set[str],
        token: TwitchToken | None,
    ) -> None:
        self._set_auth_state("main", state, detail, missing_scopes, token)

    def set_bot_auth_state(
        self,
        state: TwitchAuthState,
        detail: str,
        missing_scopes: set[str],
        token: TwitchToken | None,
    ) -> None:
        self._set_auth_state("bot", state, detail, missing_scopes, token)

    def _set_auth_state(
        self,
        account: str,
        state: TwitchAuthState,
        detail: str,
        missing_scopes: set[str],
        token: TwitchToken | None,
    ) -> None:
        previous = self._presentations[account]
        self._presentations[account] = _AccountPresentation(
            state=state,
            detail=detail,
            missing_scopes=frozenset(missing_scopes),
            token=token,
        )
        if token is None or previous.token is not token:
            self._profile_generation[account] += 1
            self._profile_user_id[account] = ""
        self._render(account)
        self._maybe_load_profile(account)

    def _render(self, account: str) -> None:
        card = self.main_card if account == "main" else self.bot_card
        presentation = self._presentations[account]
        token = presentation.token
        signed_in = presentation.state is TwitchAuthState.SIGNED_IN and token is not None
        waiting = presentation.state is TwitchAuthState.WAITING
        needs_reauthorization = signed_in and bool(presentation.missing_scopes)

        if presentation.state is TwitchAuthState.ERROR:
            status = "Needs Reauthorization"
        elif waiting:
            status = "Reconnecting"
        elif not signed_in:
            status = "Needs Setup"
        elif needs_reauthorization:
            status = "Needs Reauthorization"
        elif account == "main" and self._connection_state is TwitchConnectionState.RECONNECTING:
            status = "Reconnecting"
        elif account == "main" and self._connection_state is TwitchConnectionState.ERROR:
            status = "Error"
        else:
            status = "Connected"

        token_login = getattr(token, "login", "") if token is not None else ""
        login = token_login if isinstance(token_login, str) else ""
        login = login or (presentation.detail if signed_in else "")
        if not self._profile_user_id[account]:
            card.display_name_label.setText(login or "Not connected")
            card.login_label.setText(f"@{login}" if login else "")
            card.show_placeholder(login or ("B" if account == "bot" else "T"))
        card.status_label.setText(status)
        if signed_in and needs_reauthorization:
            card.connect_button.setText(
                "Reauthorize Bot" if account == "bot" else "Reauthorize"
            )
        elif presentation.state is TwitchAuthState.ERROR:
            card.connect_button.setText(
                "Reconnect Bot" if account == "bot" else "Reconnect"
            )
        else:
            card.connect_button.setText(
                "Connect Bot" if account == "bot" else "Connect / Sign In"
            )
        card.connect_button.setEnabled(
            (not signed_in or needs_reauthorization) and not waiting
        )
        card.disconnect_button.setText(
            "Disconnect Bot" if account == "bot" else "Disconnect / Sign Out"
        )
        card.disconnect_button.setEnabled(signed_in or waiting)

    def _maybe_load_profile(self, account: str) -> None:
        if not self._active:
            return
        presentation = self._presentations[account]
        token = presentation.token
        if (
            presentation.state is not TwitchAuthState.SIGNED_IN
            or not isinstance(token, TwitchToken)
            or not isinstance(token.user_id, str)
            or not token.user_id
            or self._profile_user_id[account] == token.user_id
        ):
            return
        generation = self._profile_generation[account]
        self._profile_user_id[account] = token.user_id
        worker = _ProfileWorker(
            account,
            generation,
            token,
            self.service.get_account_profile,
        )
        worker.signals.loaded.connect(self._profile_loaded)
        worker.signals.failed.connect(self._profile_failed)
        worker.signals.loaded.connect(
            lambda *_args, item=worker: self._workers.discard(item)
        )
        worker.signals.failed.connect(
            lambda *_args, item=worker: self._workers.discard(item)
        )
        self._workers.add(worker)
        self._thread_pool.start(worker)

    @Slot(str, int, object)
    def _profile_loaded(self, account: str, generation: int, value: object) -> None:
        if generation != self._profile_generation.get(account) or not isinstance(
            value, dict
        ):
            return
        card = self.main_card if account == "main" else self.bot_card
        display_name = str(value.get("display_name") or value.get("login") or "").strip()
        login = str(value.get("login") or "").strip()
        if display_name:
            card.display_name_label.setText(display_name)
        if login:
            card.login_label.setText(f"@{login}")
        avatar_url = str(value.get("profile_image_url") or "").strip()
        if not avatar_url:
            card.show_placeholder(display_name or login or "T")
            return
        cached = self._avatar_cache.get(avatar_url)
        if cached is not None:
            card.show_avatar(cached)
            return
        reply = self._network.get(QNetworkRequest(QUrl(avatar_url)))
        self._avatar_requests[reply] = (account, generation, avatar_url)

    @Slot(str, int)
    def _profile_failed(self, account: str, generation: int) -> None:
        if generation != self._profile_generation.get(account):
            return
        self._profile_user_id[account] = ""

    @Slot(QNetworkReply)
    def _avatar_finished(self, reply: QNetworkReply) -> None:
        request = self._avatar_requests.pop(reply, None)
        try:
            if request is None or reply.error() is not QNetworkReply.NetworkError.NoError:
                return
            account, generation, avatar_url = request
            if generation != self._profile_generation.get(account):
                return
            pixmap = QPixmap()
            if not pixmap.loadFromData(bytes(reply.readAll())):
                return
            self._avatar_cache[avatar_url] = pixmap
            card = self.main_card if account == "main" else self.bot_card
            card.show_avatar(pixmap)
        finally:
            reply.deleteLater()
