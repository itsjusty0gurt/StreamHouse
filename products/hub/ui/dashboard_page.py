from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable
from urllib.parse import quote

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from products.hub.config.product import (
    ISSUE_TRACKER_URL,
    PRODUCT_NAME,
    PRODUCT_TAGLINE,
    PROJECT_URL,
)
from products.hub.core.resources import resource_path
from products.hub.obs_service.models import ObsConnectionState
from products.hub.twitch.auth import TwitchAuthState
from products.hub.twitch.service import TwitchConnectionState
from shared.streamhouse_runtime.version import VERSION


class DashboardStatus(StrEnum):
    CONNECTED = "Connected"
    PARTIAL = "Partial"
    DISCONNECTED = "Disconnected"
    NEEDS_ATTENTION = "Needs Attention"


@dataclass(frozen=True, slots=True)
class ConnectionSummary:
    status: DashboardStatus
    detail: str


def summarize_twitch_connection(
    auth_state: TwitchAuthState,
    connection_state: TwitchConnectionState,
    *,
    broadcaster_missing_scopes: set[str] | frozenset[str] = frozenset(),
    bot_auth_state: TwitchAuthState = TwitchAuthState.SIGNED_OUT,
    bot_missing_scopes: set[str] | frozenset[str] = frozenset(),
) -> ConnectionSummary:
    if auth_state in {TwitchAuthState.SIGNED_OUT, TwitchAuthState.ERROR}:
        return ConnectionSummary(
            DashboardStatus.NEEDS_ATTENTION,
            "Sign in or repair the broadcaster authorization.",
        )
    if auth_state is TwitchAuthState.WAITING:
        return ConnectionSummary(
            DashboardStatus.PARTIAL,
            "Broadcaster authorization is in progress.",
        )
    if broadcaster_missing_scopes:
        return ConnectionSummary(
            DashboardStatus.NEEDS_ATTENTION,
            "Broadcaster permissions need to be updated.",
        )
    if connection_state is TwitchConnectionState.ERROR:
        return ConnectionSummary(
            DashboardStatus.NEEDS_ATTENTION,
            "The Twitch connection reported an error.",
        )
    if connection_state is TwitchConnectionState.CONNECTING:
        return ConnectionSummary(
            DashboardStatus.PARTIAL,
            "Chat and EventSub are connecting.",
        )
    if connection_state is not TwitchConnectionState.CONNECTED:
        return ConnectionSummary(
            DashboardStatus.DISCONNECTED,
            "Chat and EventSub are not connected.",
        )
    if (
        bot_auth_state is TwitchAuthState.SIGNED_IN
        and bot_missing_scopes
    ):
        return ConnectionSummary(
            DashboardStatus.PARTIAL,
            "Broadcaster services are connected; bot permissions need attention.",
        )
    return ConnectionSummary(
        DashboardStatus.CONNECTED,
        "Broadcaster services are ready.",
    )


def summarize_obs_connection(state: ObsConnectionState) -> ConnectionSummary:
    if state is ObsConnectionState.CONNECTED:
        return ConnectionSummary(
            DashboardStatus.CONNECTED,
            "OBS WebSocket is ready.",
        )
    if state is ObsConnectionState.CONNECTING:
        return ConnectionSummary(
            DashboardStatus.PARTIAL,
            "OBS WebSocket is connecting.",
        )
    if state is ObsConnectionState.ERROR:
        return ConnectionSummary(
            DashboardStatus.NEEDS_ATTENTION,
            "The OBS connection reported an error.",
        )
    return ConnectionSummary(
        DashboardStatus.DISCONNECTED,
        "OBS WebSocket is not connected.",
    )


def current_build_description() -> str:
    return (
        "Packaged Windows build"
        if getattr(sys, "frozen", False)
        else "Development build"
    )


class DashboardPage(QWidget):
    """Lightweight Alpha landing page backed by existing service state."""

    connections_requested = Signal()

    _STATUS_COLORS = {
        DashboardStatus.CONNECTED: "#37c98b",
        DashboardStatus.PARTIAL: "#f4c95d",
        DashboardStatus.DISCONNECTED: "#9b9ba6",
        DashboardStatus.NEEDS_ATTENTION: "#ff7b72",
    }

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        version: str = VERSION,
        build_description: str | None = None,
        project_url: str = PROJECT_URL,
        issue_tracker_url: str = ISSUE_TRACKER_URL,
        url_opener: Callable[[QUrl], object] = QDesktopServices.openUrl,
    ) -> None:
        super().__init__(parent)
        self.version = version
        self.build_description = build_description or current_build_description()
        self.project_url = project_url.strip()
        self.issue_tracker_url = issue_tracker_url.strip()
        self._url_opener = url_opener
        self._twitch_summary = ConnectionSummary(
            DashboardStatus.NEEDS_ATTENTION,
            "Sign in or repair the broadcaster authorization.",
        )
        self._obs_summary = summarize_obs_connection(
            ObsConnectionState.DISCONNECTED
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setObjectName("dashboardScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(scroll)

        content = QWidget(scroll)
        content.setObjectName("dashboardContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(14)
        scroll.setWidget(content)

        layout.addWidget(self._build_branding(content))
        layout.addWidget(self._build_connection_summary(content))
        layout.addWidget(self._build_attention_area(content))
        layout.addWidget(self._build_help(content))
        layout.addStretch()
        self._refresh_attention()

    def _build_branding(self, parent: QWidget) -> QWidget:
        panel = QFrame(parent)
        panel.setObjectName("dashboardBranding")
        panel.setStyleSheet(
            "QFrame#dashboardBranding {"
            "background:#242427; border:1px solid #3c3c42; border-radius:8px;"
            "}"
        )
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(16)

        self.logo_label = QLabel(panel)
        self.logo_label.setObjectName("dashboardLogo")
        self.logo_label.setFixedSize(72, 72)
        self.logo_label.setScaledContents(True)
        logo = QPixmap(
            str(resource_path("assets/streamhouse-icons/streamhouse-hub.png"))
        )
        if logo.isNull():
            self.logo_label.setText("H")
            self.logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        else:
            self.logo_label.setPixmap(logo)
        layout.addWidget(self.logo_label)

        text = QVBoxLayout()
        self.product_name_label = QLabel(PRODUCT_NAME, panel)
        self.product_name_label.setObjectName("dashboardProductName")
        self.product_name_label.setStyleSheet(
            "font-size:22px; font-weight:700; color:#efeff1;"
        )
        self.tagline_label = QLabel(PRODUCT_TAGLINE, panel)
        self.tagline_label.setWordWrap(True)
        self.tagline_label.setStyleSheet("color:#b8b8c2;")
        self.version_label = QLabel(f"Version {self.version}", panel)
        self.version_label.setObjectName("dashboardVersion")
        self.build_label = QLabel(self.build_description, panel)
        self.build_label.setObjectName("dashboardBuild")
        self.build_label.setStyleSheet("color:#9b9ba6;")
        text.addWidget(self.product_name_label)
        text.addWidget(self.tagline_label)
        text.addSpacing(2)
        text.addWidget(self.version_label)
        text.addWidget(self.build_label)
        layout.addLayout(text, 1)
        return panel

    def _build_connection_summary(self, parent: QWidget) -> QWidget:
        group = QGroupBox("Connection Summary", parent)
        layout = QGridLayout(group)
        layout.setHorizontalSpacing(14)
        layout.setVerticalSpacing(9)

        layout.addWidget(QLabel("Twitch", group), 0, 0)
        self.twitch_status_label = QLabel(group)
        self.twitch_status_label.setObjectName("dashboardTwitchStatus")
        self.twitch_detail_label = QLabel(group)
        self.twitch_detail_label.setWordWrap(True)
        layout.addWidget(self.twitch_status_label, 0, 1)
        layout.addWidget(self.twitch_detail_label, 0, 2)

        layout.addWidget(QLabel("OBS", group), 1, 0)
        self.obs_status_label = QLabel(group)
        self.obs_status_label.setObjectName("dashboardObsStatus")
        self.obs_detail_label = QLabel(group)
        self.obs_detail_label.setWordWrap(True)
        layout.addWidget(self.obs_status_label, 1, 1)
        layout.addWidget(self.obs_detail_label, 1, 2)

        self.connections_button = QPushButton("Open Connections", group)
        self.connections_button.setObjectName("dashboardConnectionsButton")
        self.connections_button.clicked.connect(self.connections_requested.emit)
        layout.addWidget(self.connections_button, 2, 0, 1, 3)
        layout.setColumnStretch(2, 1)
        self._apply_summary(
            self.twitch_status_label,
            self.twitch_detail_label,
            self._twitch_summary,
        )
        self._apply_summary(
            self.obs_status_label,
            self.obs_detail_label,
            self._obs_summary,
        )
        return group

    def _build_attention_area(self, parent: QWidget) -> QWidget:
        self.attention_frame = QFrame(parent)
        self.attention_frame.setObjectName("dashboardAttention")
        self.attention_frame.setStyleSheet(
            "QFrame#dashboardAttention {"
            "background:#2b2924; border:1px solid #5e5435; border-radius:6px;"
            "}"
        )
        layout = QHBoxLayout(self.attention_frame)
        self.attention_label = QLabel(self.attention_frame)
        self.attention_label.setObjectName("dashboardAttentionText")
        self.attention_label.setWordWrap(True)
        attention_button = QPushButton("View Connections", self.attention_frame)
        attention_button.clicked.connect(self.connections_requested.emit)
        layout.addWidget(self.attention_label, 1)
        layout.addWidget(attention_button)
        return self.attention_frame

    def _build_help(self, parent: QWidget) -> QWidget:
        group = QGroupBox("Help & About", parent)
        layout = QVBoxLayout(group)
        explanation = QLabel(
            "Found a problem or have an idea for the Alpha? Use the project issue "
            "tracker so the details stay together.",
            group,
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        actions = QHBoxLayout()
        self.report_bug_button = QPushButton("Report a Bug", group)
        self.feedback_button = QPushButton("Feedback & Ideas", group)
        self.project_button = QPushButton("Project on GitHub", group)
        self.about_button = QPushButton("About", group)
        self.report_bug_button.clicked.connect(
            lambda: self._open_issue("[Bug] ")
        )
        self.feedback_button.clicked.connect(
            lambda: self._open_issue("[Idea] ")
        )
        self.project_button.clicked.connect(
            lambda: self._open_url(self.project_url)
        )
        self.about_button.clicked.connect(self.show_about)
        self.report_bug_button.setVisible(bool(self.issue_tracker_url))
        self.feedback_button.setVisible(bool(self.issue_tracker_url))
        self.project_button.setVisible(bool(self.project_url))
        for button in (
            self.report_bug_button,
            self.feedback_button,
            self.project_button,
            self.about_button,
        ):
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        return group

    def update_twitch(
        self,
        auth_state: TwitchAuthState,
        connection_state: TwitchConnectionState,
        *,
        broadcaster_missing_scopes: set[str] | frozenset[str] = frozenset(),
        bot_auth_state: TwitchAuthState = TwitchAuthState.SIGNED_OUT,
        bot_missing_scopes: set[str] | frozenset[str] = frozenset(),
    ) -> None:
        self._twitch_summary = summarize_twitch_connection(
            auth_state,
            connection_state,
            broadcaster_missing_scopes=broadcaster_missing_scopes,
            bot_auth_state=bot_auth_state,
            bot_missing_scopes=bot_missing_scopes,
        )
        self._apply_summary(
            self.twitch_status_label,
            self.twitch_detail_label,
            self._twitch_summary,
        )
        self._refresh_attention()

    def update_obs(self, state: ObsConnectionState) -> None:
        self._obs_summary = summarize_obs_connection(state)
        self._apply_summary(
            self.obs_status_label,
            self.obs_detail_label,
            self._obs_summary,
        )
        self._refresh_attention()

    def _apply_summary(
        self,
        status_label: QLabel,
        detail_label: QLabel,
        summary: ConnectionSummary,
    ) -> None:
        status_label.setText(summary.status.value)
        status_label.setStyleSheet(
            f"color:{self._STATUS_COLORS[summary.status]}; font-weight:700;"
        )
        detail_label.setText(summary.detail)
        detail_label.setStyleSheet("color:#b8b8c2;")

    def _refresh_attention(self) -> None:
        notices: list[str] = []
        if self._twitch_summary.status is DashboardStatus.NEEDS_ATTENTION:
            notices.append("Twitch needs attention.")
        elif self._twitch_summary.status is DashboardStatus.DISCONNECTED:
            notices.append("Twitch is disconnected.")
        if self._obs_summary.status is DashboardStatus.NEEDS_ATTENTION:
            notices.append("OBS needs attention.")
        elif self._obs_summary.status is DashboardStatus.DISCONNECTED:
            notices.append("OBS is disconnected.")
        self.attention_label.setText(" ".join(notices))
        self.attention_frame.setVisible(bool(notices))

    def _open_issue(self, title_prefix: str) -> None:
        if not self.issue_tracker_url:
            return
        self._open_url(
            f"{self.issue_tracker_url}/new?title={quote(title_prefix)}"
        )

    def _open_url(self, url: str) -> None:
        if url:
            self._url_opener(QUrl(url))

    def show_about(self) -> None:
        QMessageBox.information(
            self,
            f"About {PRODUCT_NAME}",
            (
                f"{PRODUCT_NAME}\nVersion {self.version}\n"
                f"{self.build_description}\n\n{PRODUCT_TAGLINE}"
            ),
        )
