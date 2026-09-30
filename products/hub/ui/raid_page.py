from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Callable

from PySide6.QtCore import (
    QEvent,
    QObject,
    QThreadPool,
    QRunnable,
    Qt,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QPixmap, QResizeEvent
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from products.hub.core.events import Events
from products.hub.twitch.models import TwitchEvent
from products.hub.twitch.raid_contract import RAID_COUNTDOWN_SECONDS
from products.hub.ui.automation_task_cards import ElidingLabel
from products.hub.ui.page_header import PageHeader
from products.hub.ui.raid_landing_window import RaidLandingWindow
from shared.streamhouse_shared.responsive import responsive_grid_columns
from shared.streamhouse_runtime.logger import Logger


FOLLOWED_STREAMS_SCOPE = "user:read:follows"
RAID_SCOPE = "channel:manage:raids"
RAID_SECONDARY_TEXT_COLOR = "#adadb8"


def format_raid_uptime(
    started_at: datetime | None,
    *,
    now: datetime | None = None,
) -> str:
    if started_at is None:
        return "—"
    current = now or datetime.now(timezone.utc)
    elapsed = max(int((current - started_at).total_seconds()), 0)
    hours, remainder = divmod(elapsed, 3600)
    minutes = remainder // 60
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


@dataclass(frozen=True, slots=True)
class RaidCandidate:
    user_id: str
    login: str
    display_name: str
    category: str
    title: str
    viewer_count: int
    started_at: datetime | None
    thumbnail_url: str

    @classmethod
    def from_twitch(cls, value: dict) -> RaidCandidate | None:
        if str(value.get("type", "")).casefold() != "live":
            return None
        user_id = str(value.get("user_id", "")).strip()
        if not user_id:
            return None
        started_at = None
        raw_started_at = str(value.get("started_at", "")).strip()
        if raw_started_at:
            try:
                started_at = datetime.fromisoformat(
                    raw_started_at.replace("Z", "+00:00")
                )
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=timezone.utc)
            except ValueError:
                started_at = None
        login = str(value.get("user_login", "")).strip()
        display_name = str(value.get("user_name", "")).strip()
        thumbnail_url = str(value.get("thumbnail_url", "")).strip()
        try:
            viewer_count = max(int(value.get("viewer_count", 0) or 0), 0)
        except (TypeError, ValueError):
            viewer_count = 0
        return cls(
            user_id=user_id,
            login=login,
            display_name=display_name or login or user_id,
            category=str(value.get("game_name", "")).strip() or "No category",
            title=str(value.get("title", "")).strip() or "Untitled stream",
            viewer_count=viewer_count,
            started_at=started_at,
            thumbnail_url=thumbnail_url.replace("{width}", "320").replace(
                "{height}", "180"
            ),
        )

    def matches(self, query: str) -> bool:
        search = query.strip().casefold()
        if not search:
            return True
        return any(
            search in value.casefold()
            for value in (
                self.display_name,
                self.login,
                self.category,
                self.title,
            )
        )


@dataclass(frozen=True, slots=True)
class ActiveRaid:
    candidate: RaidCandidate
    created_at: datetime
    deadline: datetime


class _LoadSignals(QObject):
    completed = Signal(object, int, object)
    failed = Signal(object, int, str, str)


class RaidCandidatesWorker(QRunnable):
    def __init__(self, service, generation: int) -> None:
        super().__init__()
        self.service = service
        self.generation = generation
        self.signals = _LoadSignals()

    def run(self) -> None:
        try:
            channels = self.service.get_followed_live_channels()
            self.signals.completed.emit(self, self.generation, channels)
        except PermissionError as error:
            self.signals.failed.emit(
                self, self.generation, "permission", str(error)
            )
        except Exception as error:
            Logger.warning(
                f"Could not load followed live channels: {error}",
                source="TWITCH",
            )
            self.signals.failed.emit(
                self, self.generation, "network", str(error)
            )


class _RaidSignals(QObject):
    completed = Signal(object, object, object, str)


class RaidActionWorker(QRunnable):
    def __init__(self, service, candidate: RaidCandidate) -> None:
        super().__init__()
        self.service = service
        self.candidate = candidate
        self.signals = _RaidSignals()

    def run(self) -> None:
        try:
            created_at = self.service.start_raid(self.candidate.user_id)
            self.signals.completed.emit(
                self,
                self.candidate,
                created_at,
                "" if created_at is not None else "network",
            )
        except PermissionError as error:
            self.signals.completed.emit(
                self, self.candidate, None, str(error)
            )
        except Exception as error:
            Logger.warning(
                f"Could not start Twitch raid: {error}",
                source="TWITCH",
            )
            self.signals.completed.emit(
                self, self.candidate, None, "network"
            )


class _SimpleActionSignals(QObject):
    completed = Signal(object, bool, str)


class RaidCancelWorker(QRunnable):
    def __init__(self, service) -> None:
        super().__init__()
        self.service = service
        self.signals = _SimpleActionSignals()

    def run(self) -> None:
        try:
            success = bool(self.service.cancel_raid())
            self.signals.completed.emit(
                self,
                success,
                "" if success else "network",
            )
        except PermissionError as error:
            self.signals.completed.emit(self, False, str(error))
        except Exception as error:
            Logger.warning(
                f"Could not cancel Twitch raid: {error}",
                source="TWITCH",
            )
            self.signals.completed.emit(self, False, "network")


class RaidMessageWorker(QRunnable):
    def __init__(self, service, message: str) -> None:
        super().__init__()
        self.service = service
        self.message = message
        self.signals = _SimpleActionSignals()

    def run(self) -> None:
        try:
            success = bool(
                self.service.send_message(self.message, as_bot=False)
            )
            self.signals.completed.emit(
                self,
                success,
                "" if success else "network",
            )
        except Exception as error:
            Logger.warning(
                f"Could not send raid message to Twitch chat: {error}",
                source="TWITCH",
            )
            self.signals.completed.emit(self, False, "network")


class RaidChannelCard(QFrame):
    raid_requested = Signal(object)

    def __init__(
        self,
        candidate: RaidCandidate,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.candidate = candidate
        self.setObjectName("raidChannelCard")
        self.setMinimumWidth(280)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.setStyleSheet(
            "QFrame#raidChannelCard {"
            "background:palette(base); border:1px solid palette(mid);"
            "border-radius:6px;"
            "}"
            "QFrame#raidChannelCard[activeRaidTarget=\"true\"] {"
            "border:2px solid palette(highlight);"
            "}"
            "QFrame#raidChannelCard:hover { border-color:palette(highlight); }"
            "QFrame#raidChannelCard QLabel { border:none; background:transparent; }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)
        self.thumbnail_label = QLabel("LIVE", self)
        self.thumbnail_label.setObjectName("raidChannelThumbnail")
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_label.setFixedSize(128, 72)
        self.thumbnail_label.setStyleSheet(
            "background:palette(dark); color:palette(highlighted-text);"
            "border-radius:4px; font-weight:700;"
        )
        layout.addWidget(self.thumbnail_label)

        details = QVBoxLayout()
        details.setSpacing(2)
        self.name_label = ElidingLabel(candidate.display_name, self)
        name_font = self.name_label.font()
        name_font.setBold(True)
        name_font.setPointSize(max(name_font.pointSize(), 11))
        self.name_label.setFont(name_font)
        self.category_label = ElidingLabel(candidate.category, self)
        self.category_label.setStyleSheet("color:palette(highlight);")
        self.stats_label = QLabel(
            f"{candidate.viewer_count:,} viewers  •  Live "
            f"{format_raid_uptime(candidate.started_at)}",
            self,
        )
        self.stats_label.setObjectName("raidChannelStats")
        self.stats_label.setStyleSheet(f"color:{RAID_SECONDARY_TEXT_COLOR};")
        self.title_label = ElidingLabel(candidate.title, self)
        self.title_label.setObjectName("raidChannelTitle")
        self.title_label.setStyleSheet(f"color:{RAID_SECONDARY_TEXT_COLOR};")
        self.raid_button = QPushButton("Raid", self)
        self.raid_button.setObjectName("raidChannelButton")
        self.raid_button.setMaximumWidth(90)
        self.raid_button.clicked.connect(
            lambda: self.raid_requested.emit(self.candidate)
        )
        details.addWidget(self.name_label)
        details.addWidget(self.category_label)
        details.addWidget(self.stats_label)
        details.addWidget(self.title_label)
        action_row = QHBoxLayout()
        action_row.addStretch()
        action_row.addWidget(self.raid_button)
        details.addLayout(action_row)
        layout.addLayout(details, 1)

    def set_thumbnail(self, data: bytes) -> None:
        pixmap = QPixmap()
        if not data or not pixmap.loadFromData(data):
            return
        self.thumbnail_label.setPixmap(
            pixmap.scaled(
                self.thumbnail_label.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.thumbnail_label.setText("")

    def set_raid_pending(self, pending: bool) -> None:
        self.raid_button.setEnabled(not pending)
        self.raid_button.setText("Starting…" if pending else "Raid")

    def set_active_target(self, active: bool) -> None:
        self.setProperty("activeRaidTarget", active)
        self.raid_button.setText("Raiding" if active else "Raid")
        style = self.style()
        style.unpolish(self)
        style.polish(self)


class RaidPage(QWidget):
    MIN_CARD_WIDTH = 330
    GRID_GAP = 10
    GRID_HYSTERESIS = 24
    raid_event_received = Signal(object)

    @classmethod
    def columns_for_width(cls, width: int, *, current: int = 0) -> int:
        return responsive_grid_columns(
            width,
            cls.MIN_CARD_WIDTH,
            cls.GRID_GAP,
            current=current,
            hysteresis=cls.GRID_HYSTERESIS,
        )

    def __init__(
        self,
        service,
        auth,
        parent: QWidget | None = None,
        *,
        clock: Callable[[], datetime] | None = None,
        landing_window_factory=RaidLandingWindow,
    ) -> None:
        super().__init__(parent)
        self.service = service
        self.auth = auth
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._landing_window_factory = landing_window_factory
        self._generation = 0
        self._loading = False
        self._shutting_down = False
        self._columns = 0
        self._candidates: tuple[RaidCandidate, ...] = ()
        self._visible_candidates: tuple[RaidCandidate, ...] = ()
        self._cards: dict[str, RaidChannelCard] = {}
        self._active_raid: ActiveRaid | None = None
        self._landing_window: RaidLandingWindow | None = None
        self._load_workers: set[RaidCandidatesWorker] = set()
        self._raid_workers: set[RaidActionWorker] = set()
        self._cancel_workers: set[RaidCancelWorker] = set()
        self._message_workers: set[RaidMessageWorker] = set()
        self._thumbnail_replies: dict[QNetworkReply, RaidChannelCard] = {}

        self.load_pool = QThreadPool(self)
        self.load_pool.setMaxThreadCount(1)
        self.raid_pool = QThreadPool(self)
        self.raid_pool.setMaxThreadCount(1)
        self.network = QNetworkAccessManager(self)
        self.countdown_timer = QTimer(self)
        self.countdown_timer.setInterval(250)
        self.countdown_timer.timeout.connect(self._update_countdown)

        root = QVBoxLayout(self)
        self.refresh_button = QPushButton("Refresh", self)
        self.page_header = PageHeader(
            "Raid",
            "Find followed channels that are live and start a Twitch raid.",
            self,
        )
        self.page_header.add_action(self.refresh_button)
        root.addWidget(self.page_header)

        self.raid_message_frame = QFrame(self)
        self.raid_message_frame.setObjectName("raidMessagePanel")
        self.raid_message_frame.setStyleSheet(
            "QFrame#raidMessagePanel {"
            "background:palette(base); border:1px solid palette(mid);"
            "border-radius:6px;"
            "}"
            "QFrame#raidMessagePanel QLabel { border:none; background:transparent; }"
        )
        message_layout = QHBoxLayout(self.raid_message_frame)
        message_layout.setContentsMargins(8, 6, 8, 6)
        message_layout.setSpacing(6)
        message_label = QLabel("Raid Message", self.raid_message_frame)
        message_label.setStyleSheet("font-weight:600; border:none;")
        self.raid_message_edit = QLineEdit(self.raid_message_frame)
        self.raid_message_edit.setObjectName("raidMessageEdit")
        self.raid_message_edit.setPlaceholderText(
            "Message to send in your Twitch chat before the raid…"
        )
        self.copy_message_button = QPushButton("Copy", self.raid_message_frame)
        self.send_message_button = QPushButton(
            "Send to Chat", self.raid_message_frame
        )
        message_layout.addWidget(message_label)
        message_layout.addWidget(self.raid_message_edit, 1)
        message_layout.addWidget(self.copy_message_button)
        message_layout.addWidget(self.send_message_button)
        root.addWidget(self.raid_message_frame)

        self.open_raid_landing_checkbox = QCheckBox(
            "Open Raid Landing after raid",
            self,
        )
        self.open_raid_landing_checkbox.setObjectName(
            "openRaidLandingAfterRaid"
        )
        self.open_raid_landing_checkbox.setToolTip(
            "After Twitch confirms the outgoing raid, open a local companion "
            "window with the target chat."
        )
        self.open_raid_landing_checkbox.setChecked(False)
        root.addWidget(self.open_raid_landing_checkbox)

        self.active_raid_frame = QFrame(self)
        self.active_raid_frame.setObjectName("activeRaidPanel")
        self.active_raid_frame.setStyleSheet(
            "QFrame#activeRaidPanel {"
            "background:palette(base); border:1px solid palette(highlight);"
            "border-radius:6px;"
            "}"
            "QFrame#activeRaidPanel QLabel {"
            "border:none; background:transparent;"
            "}"
        )
        active_layout = QHBoxLayout(self.active_raid_frame)
        active_layout.setContentsMargins(10, 7, 10, 7)
        active_text = QVBoxLayout()
        active_text.setSpacing(1)
        self.active_raid_label = QLabel(self.active_raid_frame)
        self.active_raid_label.setStyleSheet("font-weight:700;")
        self.countdown_label = QLabel(self.active_raid_frame)
        self.countdown_label.setStyleSheet(
            f"color:{RAID_SECONDARY_TEXT_COLOR};"
        )
        self.countdown_note_label = QLabel(
            "Raid will start automatically when the countdown ends.",
            self.active_raid_frame,
        )
        self.countdown_note_label.setWordWrap(True)
        self.countdown_note_label.setSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Preferred,
        )
        self.countdown_note_label.setStyleSheet(
            f"color:{RAID_SECONDARY_TEXT_COLOR};"
        )
        active_text.addWidget(self.active_raid_label)
        active_text.addWidget(self.countdown_label)
        active_text.addWidget(self.countdown_note_label)
        self.cancel_raid_button = QPushButton(
            "Cancel Raid", self.active_raid_frame
        )
        active_layout.addLayout(active_text, 1)
        active_layout.addWidget(self.cancel_raid_button)
        self.active_raid_frame.hide()
        root.addWidget(self.active_raid_frame)

        controls = QHBoxLayout()
        self.search_edit = QLineEdit(self)
        self.search_edit.setObjectName("raidSearch")
        self.search_edit.setPlaceholderText("Search live channels…")
        self.search_edit.setClearButtonEnabled(True)
        self.sort_combo = QComboBox(self)
        self.sort_combo.setObjectName("raidSort")
        self.sort_combo.addItem("Viewers: High to Low", "viewers_desc")
        self.sort_combo.addItem("Viewers: Low to High", "viewers_asc")
        self.sort_combo.addItem("Channel Name", "name")
        controls.addWidget(self.search_edit, 1)
        controls.addWidget(self.sort_combo)
        root.addLayout(controls)

        self.permission_label = QLabel(self)
        self.permission_label.setObjectName("raidPermissionStatus")
        self.permission_label.setWordWrap(True)
        self.permission_label.setStyleSheet("color:palette(highlight);")
        self.permission_label.hide()
        root.addWidget(self.permission_label)

        self.status_label = QLabel(self)
        self.status_label.setObjectName("raidStatus")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setWordWrap(True)
        self.status_label.setContentsMargins(12, 24, 12, 24)
        root.addWidget(self.status_label)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setObjectName("raidChannelScroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.grid_widget = QWidget(self.scroll_area)
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(self.GRID_GAP)
        self.grid.setVerticalSpacing(10)
        self.grid.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self.scroll_area.setWidget(self.grid_widget)
        self.scroll_area.viewport().installEventFilter(self)
        root.addWidget(self.scroll_area, 1)
        self.scroll_area.hide()

        self.refresh_button.clicked.connect(self.refresh)
        self.copy_message_button.clicked.connect(self._copy_raid_message)
        self.send_message_button.clicked.connect(self._send_raid_message)
        self.cancel_raid_button.clicked.connect(self._cancel_active_raid)
        self.search_edit.textChanged.connect(self._apply_filter)
        self.sort_combo.currentIndexChanged.connect(self._apply_filter)
        self.raid_event_received.connect(self._handle_raid_event)
        self._raid_event_callback = self._forward_raid_event
        Events.subscribe(
            "twitch_event.channel.raid",
            self._raid_event_callback,
        )
        self._show_state("Open Raid to load followed channels that are live.")

    def activate(self) -> None:
        self.refresh()

    @Slot()
    def refresh(self) -> None:
        if self._loading or self._shutting_down:
            return
        token = self.auth.token if self.auth is not None else None
        if token is None or not token.user_id:
            self._show_state("Connect Twitch to find channels to raid.")
            return
        if FOLLOWED_STREAMS_SCOPE not in set(token.scopes):
            self._show_state(
                "Additional Twitch permission is required to view followed "
                "live channels."
            )
            return
        self._generation += 1
        self._loading = True
        self.refresh_button.setEnabled(False)
        self.search_edit.setEnabled(False)
        if self._candidates:
            self._set_status("Refreshing followed channels that are live…")
        else:
            self._show_state("Loading followed channels that are live…")
        worker = RaidCandidatesWorker(self.service, self._generation)
        self._load_workers.add(worker)
        worker.signals.completed.connect(self._load_completed)
        worker.signals.failed.connect(self._load_failed)
        self.load_pool.start(worker)

    @Slot(object, int, object)
    def _load_completed(
        self,
        worker: RaidCandidatesWorker,
        generation: int,
        values: object,
    ) -> None:
        self._load_workers.discard(worker)
        if self._shutting_down or generation != self._generation:
            return
        self._finish_loading()
        candidates = []
        if isinstance(values, list):
            for value in values:
                if isinstance(value, dict):
                    candidate = RaidCandidate.from_twitch(value)
                    if candidate is not None:
                        candidates.append(candidate)
        self._candidates = tuple(candidates)
        self._replace_cards()
        self._apply_filter()

    @Slot(object, int, str, str)
    def _load_failed(
        self,
        worker: RaidCandidatesWorker,
        generation: int,
        kind: str,
        _detail: str,
    ) -> None:
        self._load_workers.discard(worker)
        if self._shutting_down or generation != self._generation:
            return
        self._finish_loading()
        if kind == "permission":
            message = (
                "Additional Twitch permission is required to view followed "
                "live channels."
            )
        else:
            message = "Couldn’t load live channels. Try again."
        if self._candidates:
            self._set_status(message)
        else:
            self._show_state(message)

    def _finish_loading(self) -> None:
        self._loading = False
        self.refresh_button.setEnabled(True)
        self.search_edit.setEnabled(True)

    def _replace_cards(self) -> None:
        self._cancel_thumbnails()
        for card in self._cards.values():
            self.grid.removeWidget(card)
            card.deleteLater()
        self._cards.clear()
        for candidate in self._candidates:
            card = RaidChannelCard(candidate, self.grid_widget)
            card.raid_requested.connect(self._confirm_raid)
            self._cards[candidate.user_id] = card
            if candidate.thumbnail_url:
                self._request_thumbnail(candidate, card)
        can_raid = self._can_raid()
        self.permission_label.setVisible(not can_raid and bool(self._cards))
        if not can_raid:
            self.permission_label.setText(self._raid_unavailable_message())
        self._update_card_raid_state()

    def _update_card_raid_state(self) -> None:
        can_start = (
            self._can_raid()
            and self._active_raid is None
            and not self._raid_workers
        )
        active_target_id = (
            self._active_raid.candidate.user_id
            if self._active_raid is not None
            else ""
        )
        for user_id, card in self._cards.items():
            active = user_id == active_target_id
            card.set_active_target(active)
            card.raid_button.setEnabled(can_start)
            if active:
                card.raid_button.setToolTip("This raid is currently pending.")
            elif self._active_raid is not None:
                card.raid_button.setToolTip(
                    "Cancel or complete the current raid before starting another."
                )
            elif not can_start:
                card.raid_button.setToolTip(
                    "Reconnect the Main / Broadcaster Account with raid permission."
                )
            else:
                card.raid_button.setToolTip("")

    def _can_raid(self) -> bool:
        token = self.auth.token if self.auth is not None else None
        return bool(
            token is not None
            and RAID_SCOPE in set(token.scopes)
            and token.user_id == self.service.broadcaster_user_id
        )

    def _raid_unavailable_message(self) -> str:
        token = self.auth.token if self.auth is not None else None
        if (
            token is None
            or not token.user_id
            or not self.service.broadcaster_user_id
        ):
            return "Connect Twitch to start raids."
        if RAID_SCOPE not in set(token.scopes):
            return "Additional Twitch permission is required to start raids."
        return "The Main / Broadcaster Account is required to start raids."

    def _request_thumbnail(
        self,
        candidate: RaidCandidate,
        card: RaidChannelCard,
    ) -> None:
        reply = self.network.get(
            QNetworkRequest(QUrl(candidate.thumbnail_url))
        )
        self._thumbnail_replies[reply] = card
        reply.finished.connect(lambda current=reply: self._thumbnail_finished(current))

    def _thumbnail_finished(self, reply: QNetworkReply) -> None:
        card = self._thumbnail_replies.pop(reply, None)
        if (
            card is not None
            and reply.error() == QNetworkReply.NetworkError.NoError
            and not self._shutting_down
        ):
            card.set_thumbnail(bytes(reply.readAll()))
        reply.deleteLater()

    def _cancel_thumbnails(self) -> None:
        for reply in tuple(self._thumbnail_replies):
            reply.abort()
            reply.deleteLater()
        self._thumbnail_replies.clear()

    @Slot()
    def _copy_raid_message(self) -> None:
        message = self.raid_message_edit.text().strip()
        if not message:
            self._set_status("Enter a raid message first.")
            return
        QApplication.clipboard().setText(message)
        self._set_status("Raid message copied.")

    @Slot()
    def _send_raid_message(self) -> None:
        if self._shutting_down or self._message_workers:
            return
        message = self.raid_message_edit.text().strip()
        if not message:
            self._set_status("Enter a raid message first.")
            return
        self.send_message_button.setEnabled(False)
        self.send_message_button.setText("Sending…")
        worker = RaidMessageWorker(self.service, message)
        self._message_workers.add(worker)
        worker.signals.completed.connect(self._raid_message_completed)
        self.raid_pool.start(worker)

    @Slot(object, bool, str)
    def _raid_message_completed(
        self,
        worker: RaidMessageWorker,
        success: bool,
        detail: str,
    ) -> None:
        self._message_workers.discard(worker)
        if self._shutting_down:
            return
        self.send_message_button.setEnabled(True)
        self.send_message_button.setText("Send to Chat")
        if success:
            self._set_status("Raid message sent to chat.")
        elif detail and detail != "network":
            self._set_status(detail)
        else:
            self._set_status(
                "Couldn’t send the raid message. Check Twitch chat."
            )

    @Slot()
    def _apply_filter(self) -> None:
        query = self.search_edit.text()
        candidates = [
            candidate
            for candidate in self._candidates
            if candidate.matches(query)
        ]
        sort_key = self.sort_combo.currentData()
        if sort_key == "viewers_asc":
            candidates.sort(
                key=lambda item: (item.viewer_count, item.display_name.casefold())
            )
        elif sort_key == "name":
            candidates.sort(key=lambda item: item.display_name.casefold())
        else:
            candidates.sort(
                key=lambda item: (-item.viewer_count, item.display_name.casefold())
            )
        visible_ids = {candidate.user_id for candidate in candidates}
        self._visible_candidates = tuple(candidates)
        for user_id, card in self._cards.items():
            card.setVisible(user_id in visible_ids)
            self.grid.removeWidget(card)
        self._reflow(candidates, force=True)
        if candidates:
            self.status_label.hide()
            self.scroll_area.show()
        elif self._candidates and query.strip():
            self._show_state("No live channels match your search.")
        elif not self._loading:
            self._show_state("No channels you follow are live right now.")

    def _reflow(
        self,
        candidates: list[RaidCandidate] | None = None,
        *,
        force: bool = False,
    ) -> None:
        columns = self.columns_for_width(
            self.scroll_area.viewport().width(),
            current=self._columns,
        )
        if not force and columns == self._columns:
            return
        previous_columns = self._columns
        self._columns = columns
        current = (
            list(candidates)
            if candidates is not None
            else list(self._visible_candidates)
        )
        for position, candidate in enumerate(current):
            self.grid.addWidget(
                self._cards[candidate.user_id],
                position // columns,
                position % columns,
            )
        for column in range(max(previous_columns, columns)):
            self.grid.setColumnStretch(column, 0)
        for column in range(columns):
            self.grid.setColumnStretch(column, 1)

    @Slot(object)
    def _confirm_raid(self, candidate: RaidCandidate) -> None:
        if (
            self._shutting_down
            or self._raid_workers
            or self._active_raid is not None
        ):
            return
        answer = QMessageBox.question(
            self,
            "Confirm Raid",
            f"Raid {candidate.display_name}?",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        card = self._cards.get(candidate.user_id)
        self._set_status(f"Starting raid for {candidate.display_name}…")
        worker = RaidActionWorker(self.service, candidate)
        self._raid_workers.add(worker)
        self._update_card_raid_state()
        if card is not None:
            card.set_raid_pending(True)
        worker.signals.completed.connect(self._raid_completed)
        self.raid_pool.start(worker)

    @Slot(object, object, bool, str)
    def _raid_completed(
        self,
        worker: RaidActionWorker,
        candidate: RaidCandidate,
        created_at: object,
        detail: str,
    ) -> None:
        self._raid_workers.discard(worker)
        if self._shutting_down:
            return
        card = self._cards.get(candidate.user_id)
        if card is not None:
            card.set_raid_pending(False)
        if isinstance(created_at, datetime):
            self._set_active_raid(candidate, created_at)
            message = f"Raid countdown started for {candidate.display_name}."
        elif detail and detail != "network":
            message = detail
        else:
            message = "Couldn’t start the raid. Try again."
        self._update_card_raid_state()
        self._set_status(message)

    def _set_active_raid(
        self,
        candidate: RaidCandidate,
        created_at: datetime,
    ) -> None:
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        created_at = created_at.astimezone(timezone.utc)
        self._active_raid = ActiveRaid(
            candidate=candidate,
            created_at=created_at,
            deadline=created_at + timedelta(seconds=RAID_COUNTDOWN_SECONDS),
        )
        self.active_raid_label.setText(f"Raiding {candidate.display_name}")
        self.active_raid_frame.show()
        self.countdown_timer.start()
        self._update_countdown()
        self._update_card_raid_state()

    @Slot()
    def _update_countdown(self) -> None:
        active = self._active_raid
        if active is None:
            self.countdown_timer.stop()
            return
        remaining = max(
            0,
            ceil((active.deadline - self._clock()).total_seconds()),
        )
        minutes, seconds = divmod(remaining, 60)
        self.countdown_label.setText(f"Starting in {minutes:02d}:{seconds:02d}")
        if remaining == 0:
            target = active.candidate.display_name
            self._clear_active_raid(f"Raid sent to {target}.")

    @Slot()
    def _cancel_active_raid(self) -> None:
        if (
            self._shutting_down
            or self._active_raid is None
            or self._cancel_workers
        ):
            return
        self.cancel_raid_button.setEnabled(False)
        self.cancel_raid_button.setText("Cancelling…")
        worker = RaidCancelWorker(self.service)
        self._cancel_workers.add(worker)
        worker.signals.completed.connect(self._cancel_completed)
        self.raid_pool.start(worker)

    @Slot(object, bool, str)
    def _cancel_completed(
        self,
        worker: RaidCancelWorker,
        success: bool,
        detail: str,
    ) -> None:
        self._cancel_workers.discard(worker)
        if self._shutting_down:
            return
        self.cancel_raid_button.setEnabled(True)
        self.cancel_raid_button.setText("Cancel Raid")
        if success:
            self._clear_active_raid("Raid cancelled.")
        elif detail and detail != "network":
            self._set_status(detail)
        else:
            self._set_status(
                "Couldn’t cancel the raid. It may have already started."
            )

    def _clear_active_raid(self, message: str = "") -> None:
        self.countdown_timer.stop()
        self._active_raid = None
        self.active_raid_frame.hide()
        self.cancel_raid_button.setEnabled(True)
        self.cancel_raid_button.setText("Cancel Raid")
        self._update_card_raid_state()
        if message:
            self._set_status(message)

    def _forward_raid_event(self, twitch_event: TwitchEvent) -> None:
        if not self._shutting_down:
            self.raid_event_received.emit(twitch_event)

    @Slot(object)
    def _handle_raid_event(self, twitch_event: object) -> None:
        active = self._active_raid
        if active is None or not isinstance(twitch_event, TwitchEvent):
            return
        event = twitch_event.payload.get("event", {})
        if not isinstance(event, dict):
            return
        from_id = str(event.get("from_broadcaster_user_id", ""))
        to_id = str(event.get("to_broadcaster_user_id", ""))
        if (
            from_id == self.service.broadcaster_user_id
            and to_id == active.candidate.user_id
        ):
            candidate = active.candidate
            self._clear_active_raid(
                f"Raid sent to {candidate.display_name}."
            )
            if self.open_raid_landing_checkbox.isChecked():
                self._open_raid_landing(candidate)

    def _open_raid_landing(self, candidate: RaidCandidate) -> None:
        if self._shutting_down:
            return
        previous = self._landing_window
        if previous is not None:
            self._landing_window = None
            previous.shutdown()
            previous.close()
            previous.deleteLater()
        landing = self._landing_window_factory(
            candidate,
            self.service,
            self.window(),
        )
        self._landing_window = landing
        landing.dismissed.connect(self._landing_dismissed)
        landing.show()
        landing.raise_()
        landing.activateWindow()
        Logger.info("Raid Landing opened.", source="TWITCH")

    @Slot(object)
    def _landing_dismissed(self, landing: object) -> None:
        if landing is not self._landing_window:
            return
        self._landing_window = None
        landing.deleteLater()
        Logger.info("Raid Landing closed.", source="TWITCH")

    def _set_status(self, message: str) -> None:
        self.status_label.setText(message)
        self.status_label.show()

    def _show_state(self, message: str) -> None:
        self.status_label.setText(message)
        self.status_label.show()
        self.scroll_area.hide()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._cards:
            self._reflow()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if (
            watched is self.scroll_area.viewport()
            and event.type() == QEvent.Type.Resize
            and self._cards
        ):
            self._reflow()
        return super().eventFilter(watched, event)

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._generation += 1
        Events.unsubscribe(
            "twitch_event.channel.raid",
            self._raid_event_callback,
        )
        self.countdown_timer.stop()
        self._active_raid = None
        landing = self._landing_window
        self._landing_window = None
        if landing is not None:
            landing.shutdown()
            landing.close()
            landing.deleteLater()
        self._cancel_thumbnails()
        self.load_pool.clear()
        self.raid_pool.clear()
        if self.load_pool.waitForDone(2_000):
            self._load_workers.clear()
        if self.raid_pool.waitForDone(2_000):
            self._raid_workers.clear()
            self._cancel_workers.clear()
            self._message_workers.clear()
