from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from PySide6.QtCore import QObject, QThreadPool, QRunnable, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QPixmap, QResizeEvent
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
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

from products.hub.twitch.slash_commands import TwitchSlashRequest
from products.hub.ui.automation_task_cards import ElidingLabel
from products.hub.ui.page_header import PageHeader
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
    completed = Signal(object, object, bool, str)


class RaidActionWorker(QRunnable):
    def __init__(self, service, candidate: RaidCandidate) -> None:
        super().__init__()
        self.service = service
        self.candidate = candidate
        self.signals = _RaidSignals()

    def run(self) -> None:
        try:
            success, _target = self.service.execute_slash_action(
                TwitchSlashRequest(
                    action="raid",
                    user_reference=self.candidate.display_name,
                    user_id=self.candidate.user_id,
                )
            )
            self.signals.completed.emit(self, self.candidate, success, "")
        except PermissionError as error:
            self.signals.completed.emit(
                self, self.candidate, False, str(error)
            )
        except Exception as error:
            Logger.warning(
                f"Could not start Twitch raid: {error}",
                source="TWITCH",
            )
            self.signals.completed.emit(
                self, self.candidate, False, "network"
            )


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


class RaidPage(QWidget):
    MIN_CARD_WIDTH = 330

    @classmethod
    def columns_for_width(cls, width: int) -> int:
        return max(1, max(width, cls.MIN_CARD_WIDTH) // cls.MIN_CARD_WIDTH)

    def __init__(self, service, auth, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.service = service
        self.auth = auth
        self._generation = 0
        self._requested_once = False
        self._loading = False
        self._shutting_down = False
        self._columns = 0
        self._candidates: tuple[RaidCandidate, ...] = ()
        self._visible_candidates: tuple[RaidCandidate, ...] = ()
        self._cards: dict[str, RaidChannelCard] = {}
        self._load_workers: set[RaidCandidatesWorker] = set()
        self._raid_workers: set[RaidActionWorker] = set()
        self._thumbnail_replies: dict[QNetworkReply, RaidChannelCard] = {}

        self.load_pool = QThreadPool(self)
        self.load_pool.setMaxThreadCount(1)
        self.raid_pool = QThreadPool(self)
        self.raid_pool.setMaxThreadCount(1)
        self.network = QNetworkAccessManager(self)

        root = QVBoxLayout(self)
        self.refresh_button = QPushButton("Refresh", self)
        self.page_header = PageHeader(
            "Raid",
            "Find followed channels that are live and start a Twitch raid.",
            self,
        )
        self.page_header.add_action(self.refresh_button)
        root.addWidget(self.page_header)

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
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(10)
        self.grid.setAlignment(
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        self.scroll_area.setWidget(self.grid_widget)
        root.addWidget(self.scroll_area, 1)
        self.scroll_area.hide()

        self.refresh_button.clicked.connect(self.refresh)
        self.search_edit.textChanged.connect(self._apply_filter)
        self.sort_combo.currentIndexChanged.connect(self._apply_filter)
        self._show_state("Open Raid to load followed channels that are live.")

    def activate(self) -> None:
        if not self._requested_once:
            self.refresh()

    @Slot()
    def refresh(self) -> None:
        if self._loading or self._shutting_down:
            return
        self._requested_once = True
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
        self._candidates = ()
        self._visible_candidates = ()
        self._replace_cards()
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
            self._show_state(
                "Additional Twitch permission is required to view followed "
                "live channels."
            )
        else:
            self._show_state("Couldn’t load live channels. Try again.")

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
        for card in self._cards.values():
            card.raid_button.setEnabled(can_raid)
            if not can_raid:
                card.raid_button.setToolTip(
                    "Reconnect the Main / Broadcaster Account with raid permission."
                )

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
        columns = self.columns_for_width(self.scroll_area.viewport().width())
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
        if self._shutting_down or self._raid_workers:
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
        for existing_card in self._cards.values():
            existing_card.raid_button.setEnabled(False)
        card = self._cards.get(candidate.user_id)
        if card is not None:
            card.set_raid_pending(True)
        self.status_label.setText(f"Starting raid for {candidate.display_name}…")
        self.status_label.show()
        worker = RaidActionWorker(self.service, candidate)
        self._raid_workers.add(worker)
        worker.signals.completed.connect(self._raid_completed)
        self.raid_pool.start(worker)

    @Slot(object, object, bool, str)
    def _raid_completed(
        self,
        worker: RaidActionWorker,
        candidate: RaidCandidate,
        success: bool,
        detail: str,
    ) -> None:
        self._raid_workers.discard(worker)
        if self._shutting_down:
            return
        card = self._cards.get(candidate.user_id)
        if card is not None:
            card.set_raid_pending(False)
        can_raid = self._can_raid()
        for existing_card in self._cards.values():
            existing_card.raid_button.setEnabled(can_raid)
        if success:
            message = f"Raid started for {candidate.display_name}."
        elif detail and detail != "network":
            message = detail
        else:
            message = "Couldn’t start the raid. Try again."
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

    def shutdown(self) -> None:
        self._shutting_down = True
        self._generation += 1
        self._cancel_thumbnails()
        self.load_pool.clear()
        self.raid_pool.clear()
        if self.load_pool.waitForDone(2_000):
            self._load_workers.clear()
        if self.raid_pool.waitForDone(2_000):
            self._raid_workers.clear()
