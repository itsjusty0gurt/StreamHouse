import json
import csv
import logging
import re
from dataclasses import asdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from PySide6.QtCore import QThreadPool, QTimer, Qt, Slot
from PySide6.QtGui import QCloseEvent, QColor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QCheckBox,
    QDockWidget,
    QFormLayout,
    QFileDialog,
    QGroupBox,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.events import Events
from core.logger import Logger
from core.settings import AppSettings, SettingsStore
from core.window_state import WindowStateStore
from config.twitch import TWITCH_COMPANION_SCOPES, TWITCH_SCOPES
from twitch.catalog import EVENTSUB_SUBSCRIPTIONS, EventSubSubscription
from twitch.auth import TwitchAuthService, TwitchAuthState
from twitch.activity import format_twitch_activity
from twitch.activity_history import (
    ActivityHistoryStore,
    PersistedActivity,
)
from twitch.chatter_history import ChatterHistoryStore
from twitch.models import (
    TwitchChatNotice,
    TwitchEvent,
    TwitchEventDiagnostic,
    TwitchMessage,
)
from twitch.service import TwitchConnectionState, TwitchService
from twitch.session_history import StreamSessionStore, StreamSessionTracker
from twitch.health import TwitchHealth
from twitch.analytics import AnalyticsSnapshot, build_analytics
from twitch.simulator import create_eventsub_notification
from ui.generated.ui_mainwindow import Ui_MainWindow
from ui.log_handler import QtLogHandler
from ui.twitch_bridge import TwitchEventBridge
from ui.twitch_assets import twitch_emote_url
from ui.twitch_chat_view import TwitchChatView
from ui.companion_worker import (
    CompanionRefreshResult,
    CompanionRefreshWorker,
)
from ui.controllers.release_controller import ReleaseController


class MainWindow(QMainWindow):
    def __init__(
        self,
        twitch_service: TwitchService | None = None,
        twitch_auth: TwitchAuthService | None = None,
        window_state_store: WindowStateStore | None = None,
        chatter_history_store: ChatterHistoryStore | None = None,
        activity_history_store: ActivityHistoryStore | None = None,
        session_store: StreamSessionStore | None = None,
        release_controller: ReleaseController | None = None,
        auto_upgrade_permissions: bool = True,
    ) -> None:
        super().__init__()

        self.twitch_service = twitch_service or TwitchService()
        self.twitch_auth = twitch_auth or TwitchAuthService()
        self.window_state_store = window_state_store or WindowStateStore()
        self.chatter_history = (
            chatter_history_store or ChatterHistoryStore()
        )
        self.activity_history = (
            activity_history_store or ActivityHistoryStore()
        )
        self.session_store = session_store or StreamSessionStore()
        self.session_tracker = StreamSessionTracker(self.session_store)
        self.twitch_health = TwitchHealth()
        self.release_controller = release_controller or ReleaseController()
        try:
            self.chatter_history.load()
        except (OSError, ValueError, json.JSONDecodeError) as error:
            Logger.warning(
                f"Could not load Twitch chatter history: {error}",
                source="TWITCH",
            )
        try:
            self.session_store.load()
        except (OSError, ValueError, json.JSONDecodeError) as error:
            Logger.warning(
                f"Could not load stream sessions: {error}",
                source="TWITCH",
            )
        try:
            self.activity_history.load()
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self.activity_history.entries = []
            Logger.warning(
                f"Could not load Twitch activity history: {error}",
                source="TWITCH",
            )
        self.auto_upgrade_permissions = auto_upgrade_permissions
        self.permission_upgrade_started = False
        self.companion_thread_pool = QThreadPool(self)
        self.companion_thread_pool.setMaxThreadCount(1)
        self.companion_refresh_request_id = 0
        self.companion_refresh_in_flight = False
        self.companion_warning_cache: set[str] = set()
        self.followers_backfilled = False
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        for page_title in (
            self.ui.dashboardTitleLabel,
            self.ui.twitchTitleLabel,
            self.ui.logsTitleLabel,
            self.ui.settingsTitleLabel,
        ):
            page_title.hide()
        old_chat_output = self.ui.twitchChatOutput
        self.ui.twitchChatOutput = TwitchChatView(self.ui.twitchChatTab)
        self.ui.twitchChatTabLayout.replaceWidget(
            old_chat_output,
            self.ui.twitchChatOutput,
        )
        old_chat_output.deleteLater()
        self.ui.twitchConnectButton.hide()
        self.ui.twitchDisconnectButton.hide()
        self.ui.twitchChannelEdit.setReadOnly(True)
        self._build_developer_dock()
        self._build_stream_companion()
        self._build_ai_page()
        self._build_release_tools()
        self.twitch_status_bar_label = QLabel("Twitch: Signed out")
        self.statusBar().addPermanentWidget(self.twitch_status_bar_label)

        self.navigation_group = QButtonGroup(self)
        self.navigation_group.setExclusive(True)
        for button in (
            self.ui.dashboardButton,
            self.ui.twitchButton,
            self.ai_button,
            self.connections_button,
            self.ui.logsButton,
            self.ui.settingsButton,
        ):
            self.navigation_group.addButton(button)

        self.settings_store = SettingsStore()
        self.settings = self._load_settings()

        self.log_handler = QtLogHandler()
        self.log_handler.setLevel(logging.DEBUG)
        self.log_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s  [%(levelname)s]  "
                "[%(source)s]  %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        self.log_handler.emitter.message_ready.connect(
            self.ui.logOutput.appendPlainText
        )
        Logger.add_handler(self.log_handler)

        self.twitch_bridge = TwitchEventBridge(self)
        self.twitch_bridge.status_changed.connect(
            self.handle_twitch_status_changed
        )
        self.twitch_bridge.message_received.connect(
            self.handle_twitch_message
        )
        self.twitch_bridge.error_received.connect(self.handle_twitch_error)
        self.twitch_bridge.diagnostic_received.connect(
            self.handle_twitch_diagnostic
        )
        self.twitch_bridge.auth_changed.connect(self.handle_twitch_auth_changed)
        self.twitch_bridge.notice_received.connect(self.handle_twitch_notice)
        self.twitch_bridge.activity_received.connect(self.handle_twitch_activity)
        Events.subscribe(
            "twitch_status_changed",
            self.twitch_bridge.handle_status_changed,
        )
        Events.subscribe(
            "twitch_message_received",
            self.twitch_bridge.handle_message_received,
        )
        Events.subscribe(
            "twitch_error",
            self.twitch_bridge.handle_error,
        )
        Events.subscribe(
            "twitch_event_received",
            self.twitch_bridge.handle_diagnostic,
        )
        Events.subscribe(
            "twitch_auth_changed",
            self.twitch_bridge.handle_auth_changed,
        )
        Events.subscribe(
            "twitch_notice_received",
            self.twitch_bridge.handle_notice_received,
        )
        Events.subscribe(
            "twitch_event",
            self.twitch_bridge.handle_activity_received,
        )

        self.ui.dashboardButton.clicked.connect(self.show_dashboard)
        self.ui.twitchButton.clicked.connect(self.show_twitch)
        self.ai_button.clicked.connect(self.show_ai)
        self.connections_button.clicked.connect(self.show_connections)
        self.ui.logsButton.clicked.connect(self.show_logs)
        self.ui.settingsButton.clicked.connect(self.show_settings)
        self.ui.testInfoButton.clicked.connect(self.test_info_log)
        self.ui.testWarningButton.clicked.connect(self.test_warning_log)
        self.ui.testErrorButton.clicked.connect(self.test_error_log)
        self.ui.saveSettingsButton.clicked.connect(self.save_settings)
        self.ui.resetSettingsButton.clicked.connect(self.reset_settings)
        self.ui.toggleDeveloperToolsButton.clicked.connect(
            self.toggle_developer_tools
        )
        self.ui.showDeveloperToolsCheck.toggled.connect(
            self._developer_enabled_changed
        )
        self.developer_dock.visibilityChanged.connect(
            self._developer_visibility_changed
        )
        self.ui.twitchConnectButton.clicked.connect(self.connect_twitch)
        self.ui.twitchSignInButton.clicked.connect(self.twitch_auth.sign_in)
        self.ui.twitchSignOutButton.clicked.connect(self.twitch_auth.sign_out)
        self.ui.twitchDisconnectButton.clicked.connect(
            self.disconnect_twitch
        )
        self.ui.twitchSendButton.clicked.connect(self.send_twitch_message)
        self.ui.twitchSendEdit.returnPressed.connect(self.send_twitch_message)
        self.ui.simulateTwitchMessageButton.clicked.connect(
            self.simulate_twitch_message
        )
        self.ui.clearTwitchChatButton.clicked.connect(self.clear_twitch_chat)
        self.ui.clearTwitchEventsButton.clicked.connect(
            self.clear_twitch_events
        )
        self.ui.copyTwitchEventButton.clicked.connect(
            self.copy_twitch_event_details
        )
        self.ui.twitchEventSearchEdit.textChanged.connect(
            self._rebuild_twitch_event_table
        )
        self.ui.twitchEventResultCombo.currentTextChanged.connect(
            self._rebuild_twitch_event_table
        )
        self.ui.twitchEventTable.itemSelectionChanged.connect(
            self.show_selected_twitch_event
        )
        self.ui.twitchEventTypeCombo.currentIndexChanged.connect(
            self.reset_twitch_event_payload
        )
        self.ui.resetTwitchEventPayloadButton.clicked.connect(
            self.reset_twitch_event_payload
        )
        self.ui.sendTwitchEventButton.clicked.connect(
            self.send_simulated_twitch_event
        )

        self.twitch_message_count = 0
        self.twitch_chat_has_content = False
        self.known_bot_user_ids: set[str] = set()
        self.ui.twitchChatOutput.document().setMaximumBlockCount(1000)
        self.twitch_event_diagnostics: list[TwitchEventDiagnostic] = []
        self.ui.twitchEventResultCombo.addItems(
            (
                "All results",
                "Processed",
                "Accepted",
                "Ignored",
                "Rejected",
                "Error",
            )
        )
        self.ui.twitchEventTable.setColumnCount(4)
        self.ui.twitchEventTable.setHorizontalHeaderLabels(
            ("Time", "Event", "Summary", "Result")
        )
        event_header = self.ui.twitchEventTable.horizontalHeader()
        event_header.setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        event_header.setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        event_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        event_header.setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents
        )
        for subscription in EVENTSUB_SUBSCRIPTIONS:
            self.ui.twitchEventTypeCombo.addItem(
                subscription.display_name,
                subscription,
            )
        chat_index = next(
            (
                index
                for index, subscription in enumerate(EVENTSUB_SUBSCRIPTIONS)
                if subscription.type == "channel.chat.message"
            ),
            0,
        )
        self.ui.twitchEventTypeCombo.setCurrentIndex(chat_index)
        self.reset_twitch_event_payload()

        self._populate_settings_controls()
        self._apply_settings(self.settings)
        self._show_empty_twitch_chat()
        self._show_startup_page()
        self.window_state_store.restore(self)
        self.auth_maintenance_timer = QTimer(self)
        self.auth_maintenance_timer.setInterval(30 * 60 * 1000)
        self.auth_maintenance_timer.timeout.connect(self.twitch_auth.maintain)
        self.auth_maintenance_timer.start()
        self.companion_refresh_timer = QTimer(self)
        self.companion_refresh_timer.setInterval(60_000)
        self.companion_refresh_timer.timeout.connect(self.refresh_stream_companion)
        self.companion_refresh_timer.start()
        self.chatter_history_save_timer = QTimer(self)
        self.chatter_history_save_timer.setInterval(10_000)
        self.chatter_history_save_timer.timeout.connect(
            self._save_chatter_history
        )
        self.chatter_history_save_timer.start()
        self.activity_age_timer = QTimer(self)
        self.activity_age_timer.timeout.connect(self._rebuild_activity_feed)
        self._schedule_activity_age_refresh()
        QTimer.singleShot(2_000, self._create_automatic_backup)
        Logger.info("UI log viewer connected.", source="UI")

    def _build_release_tools(self) -> None:
        group = QGroupBox("Data Safety & Diagnostics")
        layout = QVBoxLayout(group)
        explanation = QLabel(
            "Create or restore local data backups, or export a sanitized "
            "diagnostic bundle for troubleshooting."
        )
        explanation.setWordWrap(True)
        actions = QHBoxLayout()
        self.create_backup_button = QPushButton("Create Backup")
        self.restore_backup_button = QPushButton("Restore Latest")
        self.export_diagnostics_button = QPushButton("Export Diagnostics")
        actions.addWidget(self.create_backup_button)
        actions.addWidget(self.restore_backup_button)
        actions.addWidget(self.export_diagnostics_button)
        actions.addStretch()
        self.release_tools_status = QLabel("")
        self.release_tools_status.setWordWrap(True)
        layout.addWidget(explanation)
        layout.addLayout(actions)
        layout.addWidget(self.release_tools_status)
        self.ui.settingsLayout.insertWidget(
            max(self.ui.settingsLayout.count() - 1, 0),
            group,
        )
        self.create_backup_button.clicked.connect(self._create_manual_backup)
        self.restore_backup_button.clicked.connect(self._restore_latest_backup)
        self.export_diagnostics_button.clicked.connect(
            self._export_diagnostic_bundle
        )

    def _build_developer_dock(self) -> None:
        self.developer_dock = QDockWidget("Developer Tools", self)
        self.developer_dock.setObjectName("developerToolsDock")
        self.developer_dock.setAllowedAreas(
            Qt.DockWidgetArea.RightDockWidgetArea
        )
        self.developer_dock.setFeatures(
            QDockWidget.DockWidgetFeature.DockWidgetClosable
            | QDockWidget.DockWidgetFeature.DockWidgetFloatable
            | QDockWidget.DockWidgetFeature.DockWidgetMovable
        )

        tabs = QTabWidget(self.developer_dock)
        tabs.setObjectName("developerToolsTabs")

        message_page = QWidget(tabs)
        message_layout = QVBoxLayout(message_page)
        message_layout.addWidget(self.ui.twitchSimulationGroup)
        message_layout.addStretch()
        tabs.addTab(message_page, "Message")

        event_index = self.ui.twitchDetailTabs.indexOf(
            self.ui.twitchEventsTab
        )
        if event_index >= 0:
            self.ui.twitchDetailTabs.removeTab(event_index)

        developer_events_page = QWidget(tabs)
        developer_events_layout = QVBoxLayout(developer_events_page)
        listener_group = QGroupBox("Listener", developer_events_page)
        listener_layout = QFormLayout(listener_group)
        listener_layout.addRow(
            self.ui.twitchListenerNameLabel,
            self.ui.twitchListenerUrlLabel,
        )
        developer_events_layout.addWidget(listener_group)
        developer_events_layout.addWidget(self.ui.twitchEventSimulatorGroup)
        developer_events_layout.addWidget(self.ui.twitchEventsTab)
        tabs.addTab(developer_events_page, "Events")

        channel_splitter = QSplitter(Qt.Orientation.Horizontal, self.ui.twitchPage)
        channel_splitter.setObjectName("twitchChannelSplitter")
        self.ui.twitchPageLayout.replaceWidget(
            self.ui.twitchDetailTabs,
            channel_splitter,
        )
        channel_splitter.addWidget(self.ui.twitchDetailTabs)
        channel_splitter.setStretchFactor(0, 3)
        channel_splitter.setSizes([1080])
        self.twitch_channel_splitter = channel_splitter

        logs_page = QWidget(tabs)
        logs_layout = QVBoxLayout(logs_page)
        logs_layout.addWidget(QLabel("Generate test entries in the app log:"))
        logs_layout.addWidget(self.ui.testInfoButton)
        logs_layout.addWidget(self.ui.testWarningButton)
        logs_layout.addWidget(self.ui.testErrorButton)
        logs_layout.addStretch()
        tabs.addTab(logs_page, "Logs")

        self.developer_dock.setWidget(tabs)
        self.addDockWidget(
            Qt.DockWidgetArea.RightDockWidgetArea,
            self.developer_dock,
        )
        self.developer_dock.hide()

    def _build_stream_companion(self) -> None:
        self.connections_button = QPushButton("Connections")
        self.connections_button.setCheckable(True)
        self.ui.verticalLayout.insertWidget(2, self.connections_button)
        self.connections_page = QWidget()
        connections_layout = QVBoxLayout(self.connections_page)
        connections_layout.addWidget(self.ui.twitchConnectionGroup)
        connections_layout.addWidget(self.ui.twitchErrorLabel)
        health_group = QGroupBox("Connection Health")
        health_layout = QFormLayout(health_group)
        self.health_auth_label = QLabel("Signed out")
        self.health_token_label = QLabel("Not available")
        self.health_eventsub_label = QLabel("Stopped")
        self.health_companion_label = QLabel("Never")
        self.health_permissions_label = QLabel("Unknown")
        self.health_permissions_label.setWordWrap(True)
        self.health_error_label = QLabel("None")
        self.health_error_label.setWordWrap(True)
        self.health_retry_button = QPushButton("Retry Now")
        self.health_retry_button.clicked.connect(self._retry_twitch_health)
        health_layout.addRow("Authentication", self.health_auth_label)
        health_layout.addRow("Token expiry", self.health_token_label)
        health_layout.addRow("EventSub", self.health_eventsub_label)
        health_layout.addRow("Companion refresh", self.health_companion_label)
        health_layout.addRow("Missing permissions", self.health_permissions_label)
        health_layout.addRow("Last issue", self.health_error_label)
        health_layout.addRow("", self.health_retry_button)
        connections_layout.addWidget(health_group)
        connections_layout.addStretch()
        self.ui.mainStack.addWidget(self.connections_page)

        stats = QGroupBox("Stream Overview", self.ui.twitchPage)
        stats.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Fixed,
        )
        stats.setMaximumHeight(72)
        stats_layout = QHBoxLayout(stats)
        self.stream_live_label = QLabel("Offline")
        self.stream_time_label = QLabel("00:00:00")
        self.stream_viewers_label = QLabel("0 viewers")
        self.stream_followers_label = QLabel("0 followers")
        self.stream_subscribers_label = QLabel("-- subscribers")
        for label in (
            self.stream_live_label,
            self.stream_time_label,
            self.stream_viewers_label,
            self.stream_followers_label,
            self.stream_subscribers_label,
        ):
            stats_layout.addWidget(label)
        stats_layout.addStretch()
        self.ad_length_combo = QComboBox()
        for seconds in (30, 60, 90, 120, 150, 180):
            self.ad_length_combo.addItem(f"{seconds}s ad", seconds)
        self.run_ad_button = QPushButton("Run Ad")
        self.snooze_ad_button = QPushButton("Snooze Ad")
        self.update_companion_permissions_button = QPushButton(
            "Enable Stats & Chatters"
        )
        self.update_companion_permissions_button.clicked.connect(
            self.twitch_auth.sign_in
        )
        self.update_companion_permissions_button.hide()
        self.run_ad_button.setEnabled(False)
        self.snooze_ad_button.setEnabled(False)
        self.run_ad_button.setToolTip(
            "Requires channel:edit:commercial and an eligible live channel."
        )
        self.snooze_ad_button.setToolTip(
            "Requires channel:manage:ads and an upcoming scheduled ad."
        )
        self.run_ad_button.clicked.connect(self.run_commercial)
        self.snooze_ad_button.clicked.connect(self.snooze_next_ad)
        stats_layout.addWidget(self.ad_length_combo)
        stats_layout.addWidget(self.run_ad_button)
        stats_layout.addWidget(self.snooze_ad_button)
        stats_layout.addWidget(self.update_companion_permissions_button)
        self.ui.twitchPageLayout.insertWidget(1, stats)

        chatter_panel = QWidget()
        chatter_layout = QVBoxLayout(chatter_panel)
        chatter_title = QLabel("Chatters")
        chatter_title.setStyleSheet("font-weight:bold;")
        self.chatter_list = QTreeWidget()
        self.chatter_list.setHeaderHidden(True)
        chatter_panel.setMinimumWidth(120)
        chatter_panel.setMaximumWidth(210)
        chatter_layout.addWidget(chatter_title)
        chatter_layout.addWidget(self.chatter_list)
        self.twitch_channel_splitter.insertWidget(1, chatter_panel)
        activity_panel = QWidget()
        activity_layout = QVBoxLayout(activity_panel)
        activity_header = QHBoxLayout()
        activity_title = QLabel("Activity Feed")
        activity_title.setStyleSheet("font-weight:bold;")
        self.activity_filter_combo = QComboBox()
        self.activity_filter_combo.setObjectName("activityFilterCombo")
        self.activity_filter_combo.addItems(
            ("All activity", "Follows", "Subscriptions", "Raids", "Cheers", "Rewards")
        )
        activity_header.addWidget(activity_title)
        activity_header.addStretch()
        activity_header.addWidget(self.activity_filter_combo)
        self.activity_feed_list = QListWidget()
        self.activity_feed_list.setWordWrap(True)
        activity_layout.addLayout(activity_header)
        activity_layout.addWidget(self.activity_feed_list)
        self.activity_entries = self.activity_history.entries
        self.activity_filter_combo.currentTextChanged.connect(
            lambda _text: self._rebuild_activity_feed()
        )
        self._rebuild_activity_feed()
        self.twitch_channel_splitter.addWidget(activity_panel)
        self.twitch_channel_splitter.setStretchFactor(0, 4)
        self.twitch_channel_splitter.setStretchFactor(1, 0)
        self.twitch_channel_splitter.setStretchFactor(2, 2)
        self.twitch_channel_splitter.setCollapsible(0, False)
        self.twitch_channel_splitter.setCollapsible(1, False)
        self.twitch_channel_splitter.setCollapsible(2, False)
        activity_panel.setMinimumWidth(300)

    def _build_ai_page(self) -> None:
        self.ai_button = QPushButton("AI")
        self.ai_button.setCheckable(True)
        self.ui.verticalLayout.insertWidget(2, self.ai_button)
        self.ai_page = QWidget()
        ai_page_layout = QVBoxLayout(self.ai_page)
        self.ai_tabs = QTabWidget()
        ai_page_layout.addWidget(self.ai_tabs)
        self.memories_page = QWidget()
        page_layout = QHBoxLayout(self.memories_page)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("memoriesSplitter")

        browser = QWidget()
        browser_layout = QVBoxLayout(browser)
        self.memory_search_edit = QLineEdit()
        self.memory_search_edit.setPlaceholderText("Search viewers")
        self.memory_viewer_list = QListWidget()
        self.memory_viewer_list.setMinimumWidth(220)
        browser_layout.addWidget(self.memory_search_edit)
        browser_layout.addWidget(self.memory_viewer_list)

        profile = QWidget()
        profile_layout = QVBoxLayout(profile)
        self.memory_name_label = QLabel("Select a viewer")
        self.memory_name_label.setStyleSheet(
            "font-size: 20px; font-weight: 600;"
        )
        self.memory_id_label = QLabel("")
        stats = QGroupBox("Viewer Profile")
        stats_layout = QFormLayout(stats)
        self.memory_groups_label = QLabel("--")
        self.memory_follow_age_label = QLabel("Not available")
        self.memory_first_seen_label = QLabel("--")
        self.memory_last_seen_label = QLabel("--")
        self.memory_active_days_label = QLabel("0")
        self.memory_snapshot_days_label = QLabel("0")
        self.memory_messages_label = QLabel("0")
        self.memory_regular_progress_label = QLabel("--")
        self.memory_sessions_label = QLabel("0")
        self.memory_streak_label = QLabel("0 days")
        stats_layout.addRow("Groups", self.memory_groups_label)
        stats_layout.addRow("Follow age", self.memory_follow_age_label)
        stats_layout.addRow("First seen", self.memory_first_seen_label)
        stats_layout.addRow("Last seen", self.memory_last_seen_label)
        stats_layout.addRow("Active days", self.memory_active_days_label)
        stats_layout.addRow("Days seen in chat", self.memory_snapshot_days_label)
        stats_layout.addRow("Observed messages", self.memory_messages_label)
        stats_layout.addRow(
            "Regular progress",
            self.memory_regular_progress_label,
        )
        stats_layout.addRow("Sessions attended", self.memory_sessions_label)
        stats_layout.addRow("Engagement streak", self.memory_streak_label)

        recent_activity = QGroupBox("Recent Activity")
        recent_activity_layout = QVBoxLayout(recent_activity)
        self.memory_recent_activity_list = QListWidget()
        recent_activity_layout.addWidget(self.memory_recent_activity_list)

        timeline_group = QGroupBox("Viewer Timeline")
        timeline_layout = QVBoxLayout(timeline_group)
        self.memory_timeline_list = QListWidget()
        self.memory_timeline_filter = QComboBox()
        self.memory_timeline_filter.addItems(
            (
                "All events",
                "Follows",
                "Subscriptions",
                "Cheers",
                "Raids",
                "Rewards",
                "Role changes",
            )
        )
        timeline_layout.addWidget(self.memory_timeline_filter)
        timeline_layout.addWidget(self.memory_timeline_list)

        notes_group = QGroupBox("Private Profile")
        notes_layout = QFormLayout(notes_group)
        self.memory_tags_edit = QLineEdit()
        self.memory_tags_edit.setPlaceholderText(
            "friend, collaborator, community member"
        )
        self.memory_private_notes_edit = QTextEdit()
        self.memory_private_notes_edit.setPlaceholderText(
            "Private streamer notes (not generated by AI)"
        )
        self.save_viewer_profile_button = QPushButton("Save Tags & Notes")
        self.merge_viewer_button = QPushButton("Merge Duplicate Viewer")
        self.export_timeline_csv_button = QPushButton("Export Timeline CSV")
        profile_actions = QHBoxLayout()
        profile_actions.addWidget(self.save_viewer_profile_button)
        profile_actions.addWidget(self.merge_viewer_button)
        profile_actions.addWidget(self.export_timeline_csv_button)
        notes_layout.addRow("Tags", self.memory_tags_edit)
        notes_layout.addRow("Notes", self.memory_private_notes_edit)
        notes_layout.addRow("", profile_actions)

        ai_memories = QGroupBox("AI Memories")
        ai_layout = QVBoxLayout(ai_memories)
        self.memory_ai_list = QListWidget()
        ai_layout.addWidget(self.memory_ai_list)
        memory_actions = QHBoxLayout()
        self.add_memory_button = QPushButton("Add")
        self.edit_memory_button = QPushButton("Edit")
        self.pin_memory_button = QPushButton("Pin")
        self.archive_memory_button = QPushButton("Archive")
        self.delete_memory_button = QPushButton("Delete")
        self.export_memory_button = QPushButton("Export Viewer")
        self.erase_memories_button = QPushButton("Erase Memories")
        self.show_archived_memories_check = QCheckBox("Show archived")
        for button in (
            self.add_memory_button,
            self.edit_memory_button,
            self.pin_memory_button,
            self.archive_memory_button,
            self.delete_memory_button,
            self.export_memory_button,
            self.erase_memories_button,
        ):
            memory_actions.addWidget(button)
        memory_actions.addStretch()
        ai_layout.addLayout(memory_actions)
        ai_layout.addWidget(self.show_archived_memories_check)

        profile_layout.addWidget(self.memory_name_label)
        profile_layout.addWidget(self.memory_id_label)
        self.viewer_profile_tabs = QTabWidget()
        overview_page = QWidget()
        overview_layout = QVBoxLayout(overview_page)
        overview_layout.addWidget(stats)
        overview_layout.addWidget(recent_activity, 1)
        timeline_page = QWidget()
        timeline_page_layout = QVBoxLayout(timeline_page)
        timeline_page_layout.addWidget(timeline_group)
        notes_page = QWidget()
        notes_page_layout = QVBoxLayout(notes_page)
        notes_page_layout.addWidget(notes_group)
        memories_detail_page = QWidget()
        memories_detail_layout = QVBoxLayout(memories_detail_page)
        memories_detail_layout.addWidget(ai_memories)
        viewer_sessions_page = QWidget()
        viewer_sessions_layout = QVBoxLayout(viewer_sessions_page)
        self.memory_sessions_table = QTableWidget(0, 3)
        self.memory_sessions_table.setHorizontalHeaderLabels(
            ("Session", "Observed messages", "Attendance")
        )
        self.memory_sessions_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.memory_sessions_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        viewer_sessions_layout.addWidget(self.memory_sessions_table)
        self.viewer_profile_tabs.addTab(overview_page, "Overview")
        self.viewer_profile_tabs.addTab(timeline_page, "Timeline")
        self.viewer_profile_tabs.addTab(viewer_sessions_page, "Sessions")
        self.viewer_profile_tabs.addTab(notes_page, "Tags & Notes")
        self.viewer_profile_tabs.addTab(memories_detail_page, "Memories")
        profile_layout.addWidget(self.viewer_profile_tabs, 1)
        splitter.addWidget(browser)
        splitter.addWidget(profile)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 3)
        page_layout.addWidget(splitter)
        self.ai_tabs.addTab(self.memories_page, "Memories")
        sessions_page = QWidget()
        sessions_layout = QVBoxLayout(sessions_page)
        self.session_summary_label = QLabel("No active stream session")
        self.session_table = QTableWidget(0, 8)
        self.session_table.setHorizontalHeaderLabels(
            (
                "Started",
                "Duration",
                "Peak",
                "Messages",
                "Follows",
                "Subs",
                "Cheers",
                "Raids",
            )
        )
        self.session_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.session_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        sessions_layout.addWidget(self.session_summary_label)
        sessions_layout.addWidget(self.session_table)
        self.ai_tabs.addTab(sessions_page, "Stream Sessions")
        analytics_page = QWidget()
        analytics_layout = QVBoxLayout(analytics_page)
        analytics_toolbar = QHBoxLayout()
        self.analytics_range_combo = QComboBox()
        self.analytics_range_combo.addItem("All time", None)
        self.analytics_range_combo.addItem("Last 7 days", 7)
        self.analytics_range_combo.addItem("Last 30 days", 30)
        self.analytics_range_combo.addItem("Last 90 days", 90)
        self.analytics_export_csv_button = QPushButton("Export CSV")
        self.analytics_export_json_button = QPushButton("Export JSON")
        analytics_toolbar.addWidget(QLabel("Range"))
        analytics_toolbar.addWidget(self.analytics_range_combo)
        analytics_toolbar.addStretch()
        analytics_toolbar.addWidget(self.analytics_export_csv_button)
        analytics_toolbar.addWidget(self.analytics_export_json_button)
        analytics_layout.addLayout(analytics_toolbar)

        analytics_summary = QGroupBox("Overview")
        summary_layout = QGridLayout(analytics_summary)
        self.analytics_labels: dict[str, QLabel] = {}
        summary_fields = (
            ("Sessions", "sessions"),
            ("Stream hours", "hours"),
            ("Average peak", "average_peak"),
            ("Highest peak", "highest_peak"),
            ("Messages", "messages"),
            ("Messages/hour", "messages_hour"),
            ("Follows", "follows"),
            ("Subscriptions", "subscriptions"),
            ("Cheers", "cheers"),
            ("Raids", "raids"),
            ("Known viewers", "known_viewers"),
            ("Returning viewers", "returning_viewers"),
            ("New viewers", "new_viewers"),
            ("Regular viewers", "regular_viewers"),
        )
        for index, (title, key) in enumerate(summary_fields):
            title_label = QLabel(title)
            value_label = QLabel("0")
            value_label.setStyleSheet("font-size: 18px; font-weight: 600;")
            column = (index % 4) * 2
            row = index // 4
            summary_layout.addWidget(title_label, row, column)
            summary_layout.addWidget(value_label, row, column + 1)
            self.analytics_labels[key] = value_label
        analytics_layout.addWidget(analytics_summary)

        analytics_tables = QTabWidget()
        self.analytics_sessions_table = QTableWidget(0, 6)
        self.analytics_sessions_table.setHorizontalHeaderLabels(
            ("Started", "Peak", "Messages", "Follows", "Subs", "Engagement")
        )
        self.analytics_viewers_table = QTableWidget(0, 5)
        self.analytics_viewers_table.setHorizontalHeaderLabels(
            ("Viewer", "Messages", "Active days", "First seen", "Last seen")
        )
        for table in (
            self.analytics_sessions_table,
            self.analytics_viewers_table,
        ):
            table.horizontalHeader().setSectionResizeMode(
                QHeaderView.ResizeMode.Stretch
            )
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        analytics_tables.addTab(self.analytics_sessions_table, "Sessions")
        analytics_tables.addTab(self.analytics_viewers_table, "Top Viewers")
        analytics_layout.addWidget(analytics_tables, 1)

        retention_layout = QHBoxLayout()
        self.analytics_retention_spin = QSpinBox()
        self.analytics_retention_spin.setRange(30, 3650)
        self.analytics_retention_spin.setSuffix(" days")
        self.analytics_retention_spin.setValue(
            self.session_store.retention_days
        )
        self.analytics_cleanup_button = QPushButton("Apply Retention & Clean Up")
        retention_layout.addWidget(QLabel("Session retention"))
        retention_layout.addWidget(self.analytics_retention_spin)
        retention_layout.addWidget(self.analytics_cleanup_button)
        retention_layout.addStretch()
        analytics_layout.addLayout(retention_layout)
        self.ai_tabs.addTab(analytics_page, "Analytics")
        self.ui.mainStack.addWidget(self.ai_page)
        self.memory_search_edit.textChanged.connect(
            self._refresh_memory_viewer_list
        )
        self.memory_viewer_list.currentItemChanged.connect(
            self._show_memory_viewer
        )
        self.add_memory_button.clicked.connect(self._add_viewer_memory)
        self.edit_memory_button.clicked.connect(self._edit_viewer_memory)
        self.pin_memory_button.clicked.connect(self._toggle_viewer_memory_pin)
        self.archive_memory_button.clicked.connect(
            self._archive_viewer_memory
        )
        self.delete_memory_button.clicked.connect(self._delete_viewer_memory)
        self.export_memory_button.clicked.connect(self._export_viewer_memory)
        self.erase_memories_button.clicked.connect(self._erase_viewer_memories)
        self.show_archived_memories_check.toggled.connect(
            lambda _checked: self._show_memory_viewer(
                self.memory_viewer_list.currentItem(),
                None,
            )
        )
        self.memory_timeline_filter.currentTextChanged.connect(
            lambda _text: self._refresh_memory_timeline()
        )
        self.save_viewer_profile_button.clicked.connect(
            self._save_viewer_profile
        )
        self.merge_viewer_button.clicked.connect(self._merge_viewer_record)
        self.export_timeline_csv_button.clicked.connect(
            self._export_viewer_timeline_csv
        )
        self._refresh_memory_viewer_list()
        self._refresh_session_history()
        self.analytics_range_combo.currentIndexChanged.connect(
            lambda _index: self._refresh_analytics()
        )
        self.analytics_export_csv_button.clicked.connect(
            self._export_analytics_csv
        )
        self.analytics_export_json_button.clicked.connect(
            self._export_analytics_json
        )
        self.analytics_cleanup_button.clicked.connect(
            self._apply_analytics_retention
        )
        self._refresh_analytics()
        self.twitch_channel_splitter.setSizes([1000, 170, 420])

    @Slot()
    def toggle_developer_tools(self) -> None:
        self.developer_dock.setVisible(not self.developer_dock.isVisible())

    @Slot(bool)
    def _developer_enabled_changed(self, enabled: bool) -> None:
        self.ui.toggleDeveloperToolsButton.setEnabled(enabled)
        if not enabled:
            self.developer_dock.hide()

    @Slot(bool)
    def _developer_visibility_changed(self, visible: bool) -> None:
        self.ui.toggleDeveloperToolsButton.setText(
            "Close Developer Tools" if visible else "Open Developer Tools"
        )

    @Slot()
    def connect_twitch(self) -> None:
        self.ui.twitchErrorLabel.clear()
        if self.twitch_service.connect(self.ui.twitchChannelEdit.text()):
            self.reset_twitch_event_payload()

    @Slot(object, str)
    def handle_twitch_auth_changed(
        self,
        state: TwitchAuthState,
        detail: str,
    ) -> None:
        self.companion_refresh_request_id += 1
        self.companion_refresh_in_flight = False
        signed_in = state is TwitchAuthState.SIGNED_IN
        waiting = state is TwitchAuthState.WAITING
        missing_scopes = (
            self.twitch_auth.missing_scopes(set(TWITCH_SCOPES))
            if signed_in
            else set()
        )
        missing_companion_scopes = (
            self.twitch_auth.missing_scopes(TWITCH_COMPANION_SCOPES)
            if signed_in
            else set()
        )
        self.twitch_health.auth_state = state.value
        self.twitch_health.missing_scopes = set(missing_scopes)
        self._refresh_twitch_health()
        self.update_companion_permissions_button.setVisible(
            signed_in and bool(missing_companion_scopes)
        )
        self.ui.twitchAccountStatusLabel.setText(detail)
        self.ui.twitchSignInButton.setEnabled(
            (not signed_in or bool(missing_scopes)) and not waiting
        )
        self.ui.twitchSignInButton.setText(
            "Update Permissions"
            if signed_in and missing_scopes
            else "Sign in with Twitch"
        )
        if (
            signed_in
            and missing_scopes
            and self.auto_upgrade_permissions
            and not self.permission_upgrade_started
        ):
            self.permission_upgrade_started = True
            QTimer.singleShot(100, self.twitch_auth.sign_in)
        self.ui.twitchSignOutButton.setEnabled(signed_in or waiting)
        if state is TwitchAuthState.ERROR:
            self.handle_twitch_error(f"Twitch sign-in failed: {detail}")
            self.twitch_service.disconnect()
        elif state is TwitchAuthState.SIGNED_OUT:
            self.permission_upgrade_started = False
            self.twitch_service.disconnect()
            self.twitch_status_bar_label.setText("Twitch: Signed out")
            self.run_ad_button.setEnabled(False)
            self.snooze_ad_button.setEnabled(False)
        elif signed_in:
            scopes = set(self.twitch_auth.token.scopes) if self.twitch_auth.token else set()
            self.run_ad_button.setEnabled("channel:edit:commercial" in scopes)
            self.snooze_ad_button.setEnabled("channel:manage:ads" in scopes)
            self.ui.twitchChannelEdit.setText(detail)
            self.twitch_status_bar_label.setText(f"Twitch: @{detail}")
            if self.twitch_service.state is not TwitchConnectionState.CONNECTED:
                self.connect_twitch()
            self.refresh_stream_companion()

    @Slot()
    def disconnect_twitch(self) -> None:
        self.ui.twitchErrorLabel.clear()
        self.twitch_service.disconnect()

    @Slot()
    def send_twitch_message(self) -> None:
        self.ui.twitchErrorLabel.clear()
        if self.twitch_service.send_message(self.ui.twitchSendEdit.text()):
            self.ui.twitchSendEdit.clear()

    @Slot()
    def simulate_twitch_message(self) -> None:
        self.ui.twitchErrorLabel.clear()
        sent = self.twitch_service.simulate_message(
            self.ui.simulationUsernameEdit.text(),
            self.ui.simulationMessageEdit.text(),
        )
        if sent:
            self.ui.simulationMessageEdit.clear()

    @Slot()
    def reset_twitch_event_payload(self) -> None:
        subscription = self.ui.twitchEventTypeCombo.currentData()
        if not isinstance(subscription, EventSubSubscription):
            return

        self.ui.twitchEventVersionEdit.setText(subscription.version)
        channel = (
            self.ui.twitchChannelEdit.text().strip().lstrip("#").lower()
            or "test_channel"
        )
        payload = create_eventsub_notification(
            subscription.type,
            subscription.version,
            channel,
        )
        self.ui.twitchEventPayloadEdit.setPlainText(
            json.dumps(payload, indent=2, ensure_ascii=False)
        )

    @Slot()
    def send_simulated_twitch_event(self) -> None:
        self.ui.twitchErrorLabel.clear()
        subscription = self.ui.twitchEventTypeCombo.currentData()
        if not isinstance(subscription, EventSubSubscription):
            return

        try:
            payload = json.loads(self.ui.twitchEventPayloadEdit.toPlainText())
        except json.JSONDecodeError as error:
            self.handle_twitch_error(f"Invalid event JSON: {error}")
            return

        if not isinstance(payload, dict):
            self.handle_twitch_error("Event JSON must contain an object.")
            return

        self.twitch_service.simulate_event(
            subscription.type,
            self.ui.twitchEventVersionEdit.text(),
            payload,
        )

    @Slot(object, str)
    def handle_twitch_status_changed(
        self,
        state: TwitchConnectionState,
        channel: str,
    ) -> None:
        connected = state is TwitchConnectionState.CONNECTED
        connecting = state is TwitchConnectionState.CONNECTING

        page_status = state.value
        dashboard_status = state.value
        if connected and channel:
            page_status = f"Connected to #{channel}"
            dashboard_status = f"Connected (#{channel})"

        self.ui.twitchConnectionStatusLabel.setText(page_status)
        self.twitch_status_bar_label.setText(f"Twitch: {page_status}")
        self.ui.twitchStatusLabel.setText(dashboard_status)
        self.ui.twitchChannelEdit.setEnabled(not connected and not connecting)
        self.ui.twitchConnectButton.setEnabled(not connected and not connecting)
        self.ui.twitchDisconnectButton.setEnabled(connected or connecting)
        self.ui.simulateTwitchMessageButton.setEnabled(connected)
        self.ui.sendTwitchEventButton.setEnabled(connected)
        self.ui.twitchSendEdit.setEnabled(connected)
        self.ui.twitchSendButton.setEnabled(connected)
        self.ui.twitchListenerUrlLabel.setText(
            self.twitch_service.listener_url if connected else "Stopped"
        )
        self.twitch_health.connection_state = state.value
        self.twitch_health.eventsub_state = (
            "Connected" if connected else "Stopped"
        )
        self._refresh_twitch_health()

    @Slot(object)
    def handle_twitch_message(self, chat_message: TwitchMessage) -> None:
        is_bot = any(
            badge.set_id in {"bot", "verified-bot"}
            for badge in chat_message.badges
        )
        if chat_message.user_id:
            new_viewer = (
                chat_message.user_id not in self.chatter_history.records
            )
            self.chatter_history.observe_message(
                chat_message.user_id,
                chat_message.username,
                chat_message.received_at,
                is_bot=is_bot,
                session_id=(
                    self.session_store.current.started_at
                    if self.session_store.current is not None
                    else ""
                ),
            )
            if new_viewer:
                self._refresh_memory_viewer_list()
        if is_bot and chat_message.user_id:
            self.known_bot_user_ids.add(chat_message.user_id)
        received_time = chat_message.received_at.astimezone().strftime(
            "%H:%M:%S"
        )
        username = escape(chat_message.username)
        message_parts: list[str] = []
        emote_size = max(20, round(self.settings.twitch_chat_font_size * 1.8))
        for fragment in chat_message.fragments:
            if fragment.emote is None:
                message_parts.append(self._linkify(fragment.text))
                continue
            animated = "animated" in fragment.emote.formats
            url = twitch_emote_url(fragment.emote.id, animated)
            message_parts.append(
                f"<img src='{escape(url)}' width='{emote_size}' height='{emote_size}' "
                f"alt='{escape(fragment.text)}' />"
            )
        message = "".join(message_parts) or escape(chat_message.text).replace("\n", "<br>")
        badge_parts: list[str] = []
        for badge in chat_message.badges:
            url = self.twitch_service.badge_url(badge.set_id, badge.id)
            if not url:
                continue
            badge_parts.append(
                f"<img src='{escape(url)}' width='18' height='18' "
                f"alt='{escape(badge.set_id)}' /> "
            )
        badges_html = "".join(badge_parts)
        username_color = self._twitch_username_color(chat_message.username)

        timestamp_html = ""
        if self.settings.twitch_chat_show_timestamps:
            timestamp_html = (
                "<span style='color: #adadb8;'>"
                f"{received_time}</span> "
            )

        if not self.twitch_chat_has_content:
            self.ui.twitchChatOutput.clear()

        self.ui.twitchChatOutput.append(
            "<div style='margin: 4px 2px 7px 2px;'>"
            f"{timestamp_html}"
            f"{badges_html}"
            f"<span style='color: {username_color}; font-weight: 600;'>"
            f"{username}:</span>"
            f" <span style='color: #efeff1;'>{message}</span>"
            "</div>"
        )
        self.twitch_message_count += 1
        if self.session_tracker.observe_message():
            self._refresh_session_history()
        self.twitch_chat_has_content = True
        self._update_twitch_chat_count()

        scroll_bar = self.ui.twitchChatOutput.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())

    @staticmethod
    def _linkify(text: str) -> str:
        escaped = escape(text).replace("\n", "<br>")
        return re.sub(
            r"(https?://[^\s<]+)",
            r"<a href='\1'>\1</a>",
            escaped,
        )

    @Slot(object)
    def handle_twitch_notice(self, notice: TwitchChatNotice) -> None:
        if notice.kind == "clear":
            self.clear_twitch_chat()
        if not self.twitch_chat_has_content:
            self.ui.twitchChatOutput.clear()
        timestamp = notice.received_at.astimezone().strftime("%H:%M:%S")
        self.ui.twitchChatOutput.append(
            "<div style='color:#adadb8; margin:4px 2px;'>"
            f"[{timestamp}] {escape(notice.text)}</div>"
        )
        self.twitch_chat_has_content = True
        scroll_bar = self.ui.twitchChatOutput.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())

    @Slot(object)
    def handle_twitch_activity(self, twitch_event: TwitchEvent) -> None:
        entry = format_twitch_activity(twitch_event)
        if entry is None:
            return
        if self.session_tracker.observe_event(
            twitch_event.subscription_type
        ):
            self._refresh_session_history()
        if twitch_event.subscription_type == "channel.follow":
            event = twitch_event.payload.get("event", {})
            if isinstance(event, dict):
                self.chatter_history.record_follow(
                    str(event.get("user_id", "")),
                    str(event.get("user_name", "")),
                    str(event.get("followed_at", "")),
                )
                self._refresh_memory_viewer_list()
        event_payload = twitch_event.payload.get("event", {})
        if isinstance(event_payload, dict):
            viewer_id = str(
                event_payload.get("user_id")
                or event_payload.get("from_broadcaster_user_id")
                or ""
            )
            viewer_name = str(
                event_payload.get("user_name")
                or event_payload.get("from_broadcaster_user_name")
                or ""
            )
            if viewer_id:
                self.chatter_history.record_event(
                    viewer_id,
                    viewer_name,
                    twitch_event.subscription_type,
                    entry.text,
                    twitch_event.received_at,
                    (
                        self.session_store.current.started_at
                        if self.session_store.current is not None
                        else ""
                    ),
                )
        persisted = PersistedActivity(
            category=entry.category,
            text=entry.text,
            color=entry.color,
            occurred_at=twitch_event.received_at.isoformat(),
        )
        try:
            self.activity_history.add(persisted)
        except OSError as error:
            Logger.warning(
                f"Could not save Twitch activity history: {error}",
                source="TWITCH",
            )
            self.activity_entries.insert(0, persisted)
            del self.activity_entries[ActivityHistoryStore.LIMIT :]
        self._rebuild_activity_feed()

    @Slot()
    def _rebuild_activity_feed(self) -> None:
        selected = self.activity_filter_combo.currentText()
        self.activity_feed_list.clear()
        for entry in self.activity_entries:
            if selected == "All activity" or selected == entry.category:
                item = QListWidgetItem(entry.display_text())
                item.setForeground(QColor(entry.color))
                self.activity_feed_list.addItem(item)
        self._schedule_activity_age_refresh()

    def _schedule_activity_age_refresh(self) -> None:
        timer = getattr(self, "activity_age_timer", None)
        if timer is None:
            return
        interval = self.activity_history.refresh_interval_ms()
        if interval is None:
            timer.stop()
            return
        if timer.interval() != interval or not timer.isActive():
            timer.start(interval)

    @staticmethod
    def _twitch_username_color(username: str) -> str:
        colors = (
            "#bf94ff",
            "#ff75e6",
            "#00c7ac",
            "#ffb31a",
            "#5cafff",
            "#ff8280",
            "#7ee787",
        )
        color_index = sum(ord(character) for character in username) % len(
            colors
        )
        return colors[color_index]

    def _update_twitch_chat_count(self) -> None:
        noun = "message" if self.twitch_message_count == 1 else "messages"
        self.ui.twitchChatCountLabel.setText(
            f"Chat - {self.twitch_message_count} {noun}"
        )

    def _show_empty_twitch_chat(self) -> None:
        self.twitch_chat_has_content = False
        self.ui.twitchChatOutput.clear()
        self.ui.twitchChatOutput.setHtml(
            "<div style='color: #7f7f8b; margin: 8px;'>"
            "No chat messages yet. Connect and simulate a message to begin."
            "</div>"
        )

    @Slot()
    def clear_twitch_chat(self) -> None:
        self.twitch_message_count = 0
        self._update_twitch_chat_count()
        self._show_empty_twitch_chat()

    @Slot(object)
    def handle_twitch_diagnostic(
        self,
        diagnostic: TwitchEventDiagnostic,
    ) -> None:
        self.twitch_event_diagnostics.append(diagnostic)
        if len(self.twitch_event_diagnostics) > 1000:
            del self.twitch_event_diagnostics[:-1000]
        self._rebuild_twitch_event_table()

    @Slot()
    def _rebuild_twitch_event_table(self) -> None:
        table = self.ui.twitchEventTable
        selected_row = table.currentRow()
        selected_item = table.item(selected_row, 0) if selected_row >= 0 else None
        selected_diagnostic = (
            selected_item.data(Qt.ItemDataRole.UserRole)
            if selected_item is not None
            else None
        )
        result_filter = self.ui.twitchEventResultCombo.currentText()
        search_text = self.ui.twitchEventSearchEdit.text().strip().lower()
        matching_events = []

        for diagnostic in self.twitch_event_diagnostics:
            if (
                result_filter != "All results"
                and diagnostic.result != result_filter
            ):
                continue

            searchable_text = " ".join(
                (
                    diagnostic.message_type,
                    diagnostic.subscription_type,
                    diagnostic.summary,
                    diagnostic.result,
                    diagnostic.message_id,
                )
            ).lower()
            if search_text and search_text not in searchable_text:
                continue
            matching_events.append(diagnostic)

        table.setRowCount(0)
        preserved_row = -1
        for diagnostic in matching_events:
            row = table.rowCount()
            table.insertRow(row)
            time_item = QTableWidgetItem(
                diagnostic.received_at.astimezone().strftime("%H:%M:%S")
            )
            time_item.setData(Qt.ItemDataRole.UserRole, diagnostic)
            table.setItem(row, 0, time_item)
            table.setItem(
                row,
                1,
                QTableWidgetItem(
                    diagnostic.subscription_type
                    if diagnostic.subscription_type != "unknown"
                    else diagnostic.message_type
                ),
            )
            table.setItem(row, 2, QTableWidgetItem(diagnostic.summary))
            table.setItem(row, 3, QTableWidgetItem(diagnostic.result))
            if diagnostic is selected_diagnostic:
                preserved_row = row

        if self.ui.pauseTwitchEventsCheck.isChecked() and preserved_row >= 0:
            table.selectRow(preserved_row)
        elif table.rowCount() and not self.ui.pauseTwitchEventsCheck.isChecked():
            table.selectRow(table.rowCount() - 1)
            table.scrollToBottom()

    @Slot()
    def show_selected_twitch_event(self) -> None:
        row = self.ui.twitchEventTable.currentRow()
        item = self.ui.twitchEventTable.item(row, 0) if row >= 0 else None
        diagnostic = (
            item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        )
        if not isinstance(diagnostic, TwitchEventDiagnostic):
            self.ui.twitchEventDetails.clear()
            self.ui.copyTwitchEventButton.setEnabled(False)
            return

        details = {
            "received_at": diagnostic.received_at.isoformat(),
            "message_id": diagnostic.message_id,
            "message_type": diagnostic.message_type,
            "subscription_type": diagnostic.subscription_type,
            "result": diagnostic.result,
            "summary": diagnostic.summary,
            "status_code": diagnostic.status_code,
            "headers": diagnostic.headers,
            "payload": diagnostic.payload,
        }
        self.ui.twitchEventDetails.setPlainText(
            json.dumps(details, indent=2, ensure_ascii=False)
        )
        self.ui.copyTwitchEventButton.setEnabled(True)

    @Slot()
    def clear_twitch_events(self) -> None:
        self.twitch_event_diagnostics.clear()
        self.ui.twitchEventTable.setRowCount(0)
        self.ui.twitchEventDetails.clear()
        self.ui.copyTwitchEventButton.setEnabled(False)

    @Slot()
    def copy_twitch_event_details(self) -> None:
        details = self.ui.twitchEventDetails.toPlainText()
        if details:
            QApplication.clipboard().setText(details)

    @Slot(str)
    def handle_twitch_error(self, message: str) -> None:
        self.ui.twitchErrorLabel.setText(message)

    def _load_settings(self) -> AppSettings:
        try:
            return self.settings_store.load()
        except (OSError, ValueError) as error:
            Logger.warning(
                f"Could not load settings; using defaults: {error}",
                source="SETTINGS",
            )
            return AppSettings()

    def _populate_settings_controls(self) -> None:
        self.ui.startupPageCombo.addItems(AppSettings.STARTUP_PAGES)
        self.ui.logLevelCombo.addItems(AppSettings.LOG_LEVELS)
        self._settings_to_controls(self.settings)

    def _settings_to_controls(self, settings: AppSettings) -> None:
        self.ui.startupPageCombo.setCurrentText(settings.startup_page)
        self.ui.logLevelCombo.setCurrentText(settings.log_level)
        self.ui.uiLogLimitSpin.setValue(settings.ui_log_limit)
        self.ui.showDeveloperToolsCheck.setChecked(
            settings.show_developer_tools
        )
        self.ui.twitchChatTimestampCheck.setChecked(
            settings.twitch_chat_show_timestamps
        )
        self.ui.twitchChatFontCombo.setCurrentFont(
            QFont(settings.twitch_chat_font_family)
        )
        self.ui.twitchChatFontSizeSpin.setValue(
            settings.twitch_chat_font_size
        )

    def _settings_from_controls(self) -> AppSettings:
        return AppSettings(
            startup_page=self.ui.startupPageCombo.currentText(),
            log_level=self.ui.logLevelCombo.currentText(),
            ui_log_limit=self.ui.uiLogLimitSpin.value(),
            show_developer_tools=(
                self.ui.showDeveloperToolsCheck.isChecked()
            ),
            twitch_chat_show_timestamps=(
                self.ui.twitchChatTimestampCheck.isChecked()
            ),
            twitch_chat_font_family=(
                self.ui.twitchChatFontCombo.currentFont().family()
            ),
            twitch_chat_font_size=self.ui.twitchChatFontSizeSpin.value(),
        )

    def _apply_settings(self, settings: AppSettings) -> None:
        Logger.set_level(getattr(logging, settings.log_level))
        self.ui.logOutput.setMaximumBlockCount(settings.ui_log_limit)
        self.ui.toggleDeveloperToolsButton.setEnabled(
            settings.show_developer_tools
        )
        if not settings.show_developer_tools:
            self.developer_dock.hide()
        chat_font = QFont(
            settings.twitch_chat_font_family,
            settings.twitch_chat_font_size,
        )
        self.ui.twitchChatOutput.setFont(chat_font)
        self.ui.twitchChatOutput.document().setDefaultFont(chat_font)

    def _show_startup_page(self) -> None:
        page_actions = {
            "Dashboard": self.show_dashboard,
            "Twitch": self.show_twitch,
            "AI": self.show_ai,
            "Logs": self.show_logs,
            "Settings": self.show_settings,
        }
        page_actions[self.settings.startup_page]()

    @Slot()
    def show_dashboard(self) -> None:
        self.ui.mainStack.setCurrentWidget(self.ui.dashboardPage)
        self.ui.dashboardButton.setChecked(True)

    @Slot()
    def show_twitch(self) -> None:
        self.ui.mainStack.setCurrentWidget(self.ui.twitchPage)
        self.ui.twitchButton.setChecked(True)

    @Slot()
    def show_ai(self) -> None:
        self._refresh_memory_viewer_list()
        self.ui.mainStack.setCurrentWidget(self.ai_page)
        self.ai_button.setChecked(True)

    def show_memories(self) -> None:
        self.show_ai()
        self.ai_tabs.setCurrentWidget(self.memories_page)

    @Slot(str)
    def _refresh_memory_viewer_list(self, _text: str = "") -> None:
        if not hasattr(self, "memory_viewer_list"):
            return
        selected_item = self.memory_viewer_list.currentItem()
        selected_id = (
            str(selected_item.data(Qt.ItemDataRole.UserRole))
            if selected_item is not None
            else ""
        )
        query = self.memory_search_edit.text().strip().casefold()
        self.memory_viewer_list.clear()
        selected_row = -1
        records = sorted(
            self.chatter_history.records.values(),
            key=lambda record: record.user_name.casefold(),
        )
        for record in records:
            if query and query not in record.user_name.casefold():
                continue
            item = QListWidgetItem(record.user_name or record.user_id)
            item.setData(Qt.ItemDataRole.UserRole, record.user_id)
            self.memory_viewer_list.addItem(item)
            if record.user_id == selected_id:
                selected_row = self.memory_viewer_list.count() - 1
        if selected_row >= 0:
            self.memory_viewer_list.setCurrentRow(selected_row)

    @Slot(object, object)
    def _show_memory_viewer(self, current: object, _previous: object) -> None:
        if not isinstance(current, QListWidgetItem):
            return
        user_id = str(current.data(Qt.ItemDataRole.UserRole))
        record = self.chatter_history.records.get(user_id)
        if record is None:
            return
        self.memory_name_label.setText(record.user_name or "Unknown viewer")
        self.memory_id_label.setText(f"Twitch user ID: {record.user_id}")
        groups = list(record.roles)
        if self.chatter_history.is_bot(user_id) and "Bot" not in groups:
            groups.append("Bot")
        if self.chatter_history.is_regular(user_id):
            groups.append("Regular")
        self.memory_groups_label.setText(", ".join(groups) or "Viewer")
        self.memory_first_seen_label.setText(
            self._format_memory_timestamp(record.first_seen)
        )
        self.memory_last_seen_label.setText(
            self._format_memory_timestamp(record.last_seen)
        )
        self.memory_active_days_label.setText(str(len(record.active_days)))
        self.memory_snapshot_days_label.setText(str(record.snapshot_days))
        self.memory_messages_label.setText(str(record.message_count))
        self.memory_sessions_label.setText(str(len(record.session_messages)))
        self.memory_streak_label.setText(
            f"{self.chatter_history.engagement_streak(record.active_days)} day(s)"
        )
        self.memory_regular_progress_label.setText(
            f"{min(len(record.active_days), self.chatter_history.REGULAR_ACTIVE_DAYS)}"
            f"/{self.chatter_history.REGULAR_ACTIVE_DAYS} active days; "
            f"{min(record.message_count, self.chatter_history.REGULAR_MESSAGES)}"
            f"/{self.chatter_history.REGULAR_MESSAGES} messages; "
            f"{min(record.snapshot_days, self.chatter_history.REGULAR_SNAPSHOT_DAYS)}"
            f"/{self.chatter_history.REGULAR_SNAPSHOT_DAYS} chat days"
        )
        self.memory_follow_age_label.setText(
            self._format_follow_age(record.followed_at)
        )
        self.memory_tags_edit.setText(", ".join(record.tags))
        self.memory_private_notes_edit.setPlainText(record.private_notes)
        session_rows = sorted(
            record.session_messages.items(),
            reverse=True,
        )
        self.memory_sessions_table.setRowCount(len(session_rows))
        for row, (session_id, message_count) in enumerate(session_rows):
            session_values = (
                self._format_memory_timestamp(session_id),
                str(message_count),
                "Chatted" if message_count else "Present",
            )
            for column, value in enumerate(session_values):
                self.memory_sessions_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(value),
                )
        self._refresh_memory_timeline()
        self.memory_recent_activity_list.clear()
        viewer_name = record.user_name.casefold()
        for activity in self.activity_entries:
            if viewer_name and viewer_name in activity.text.casefold():
                self.memory_recent_activity_list.addItem(
                    activity.display_text()
                )
            if self.memory_recent_activity_list.count() >= 20:
                break
        if self.memory_recent_activity_list.count() == 0:
            self.memory_recent_activity_list.addItem(
                "No recent Twitch activity recorded."
            )
        self.memory_ai_list.clear()
        visible_memories = [
            memory
            for memory in record.memories
            if self.show_archived_memories_check.isChecked()
            or not bool(memory.get("archived", False))
        ]
        if visible_memories:
            visible_memories.sort(
                key=lambda memory: (
                    not bool(memory.get("pinned", False)),
                    str(memory.get("created_at", "")),
                )
            )
            for memory in visible_memories:
                prefix = "★ " if memory.get("pinned") else ""
                category = str(memory.get("category", "General"))
                item = QListWidgetItem(
                    f"{prefix}[{category}] {memory.get('text', 'Memory')}"
                )
                item.setData(
                    Qt.ItemDataRole.UserRole,
                    str(memory.get("id", "")),
                )
                self.memory_ai_list.addItem(item)
        else:
            self.memory_ai_list.addItem(
                "No memories yet. Add a manual memory to begin."
            )

    @Slot()
    def _refresh_memory_timeline(self) -> None:
        if not hasattr(self, "memory_timeline_list"):
            return
        viewer_item = self.memory_viewer_list.currentItem()
        self.memory_timeline_list.clear()
        if viewer_item is None:
            return
        user_id = str(viewer_item.data(Qt.ItemDataRole.UserRole))
        record = self.chatter_history.records.get(user_id)
        if record is None:
            return
        selected = self.memory_timeline_filter.currentText()
        type_groups = {
            "Follows": {"channel.follow"},
            "Subscriptions": {
                "channel.subscribe",
                "channel.subscription.gift",
                "channel.subscription.message",
            },
            "Cheers": {"channel.cheer"},
            "Raids": {"channel.raid"},
            "Rewards": {
                "channel.channel_points_custom_reward_redemption.add"
            },
            "Role changes": {"role_change"},
        }
        allowed_types = type_groups.get(selected)
        for event in reversed(record.timeline):
            if allowed_types is not None and str(event.get("type")) not in allowed_types:
                continue
            timestamp = self._format_memory_timestamp(
                str(event.get("timestamp", ""))
            )
            self.memory_timeline_list.addItem(
                f"{timestamp}  -  {event.get('text', 'Activity')}"
            )
        if self.memory_timeline_list.count() == 0:
            self.memory_timeline_list.addItem("No matching viewer activity.")

    @Slot()
    def _save_viewer_profile(self) -> None:
        viewer_item = self.memory_viewer_list.currentItem()
        if viewer_item is None:
            return
        user_id = str(viewer_item.data(Qt.ItemDataRole.UserRole))
        self.chatter_history.update_profile(
            user_id,
            self.memory_tags_edit.text().split(","),
            self.memory_private_notes_edit.toPlainText(),
        )
        self._save_chatter_history()
        self.statusBar().showMessage("Viewer profile saved.", 5000)

    @Slot()
    def _merge_viewer_record(self) -> None:
        viewer_item = self.memory_viewer_list.currentItem()
        if viewer_item is None:
            return
        source_id = str(viewer_item.data(Qt.ItemDataRole.UserRole))
        targets = sorted(
            (
                record.user_name,
                record.user_id,
            )
            for record in self.chatter_history.records.values()
            if record.user_id != source_id
        )
        if not targets:
            return
        labels = [f"{name} ({user_id})" for name, user_id in targets]
        selected, accepted = QInputDialog.getItem(
            self,
            "Merge Duplicate Viewer",
            "Merge the selected viewer into",
            labels,
            0,
            False,
        )
        if not accepted:
            return
        target_id = targets[labels.index(selected)][1]
        if QMessageBox.question(
            self,
            "Confirm Viewer Merge",
            "This combines both histories and removes the selected duplicate. Continue?",
        ) is not QMessageBox.StandardButton.Yes:
            return
        self.chatter_history.merge_records(source_id, target_id)
        self._save_chatter_history()
        self._refresh_memory_viewer_list()
        for row in range(self.memory_viewer_list.count()):
            item = self.memory_viewer_list.item(row)
            if str(item.data(Qt.ItemDataRole.UserRole)) == target_id:
                self.memory_viewer_list.setCurrentRow(row)
                break

    @Slot()
    def _export_viewer_timeline_csv(self) -> None:
        viewer_item = self.memory_viewer_list.currentItem()
        if viewer_item is None:
            return
        user_id = str(viewer_item.data(Qt.ItemDataRole.UserRole))
        record = self.chatter_history.records.get(user_id)
        if record is None:
            return
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Viewer Timeline",
            f"{record.user_name or user_id}-timeline.csv",
            "CSV files (*.csv)",
        )
        if not filename:
            return
        with Path(filename).open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(("timestamp", "type", "text", "session_id"))
            for event in record.timeline:
                writer.writerow(
                    (
                        event.get("timestamp", ""),
                        event.get("type", ""),
                        event.get("text", ""),
                        event.get("session_id", ""),
                    )
                )
            writer.writerow(())
            writer.writerow(("session_id", "message_count"))
            for session_id, count in record.session_messages.items():
                writer.writerow((session_id, count))

    def _selected_memory_context(self) -> tuple[str, str] | None:
        viewer_item = self.memory_viewer_list.currentItem()
        memory_item = self.memory_ai_list.currentItem()
        if viewer_item is None or memory_item is None:
            return None
        user_id = str(viewer_item.data(Qt.ItemDataRole.UserRole) or "")
        memory_id = str(memory_item.data(Qt.ItemDataRole.UserRole) or "")
        if not user_id or not memory_id:
            return None
        return user_id, memory_id

    @Slot()
    def _add_viewer_memory(self) -> None:
        viewer_item = self.memory_viewer_list.currentItem()
        if viewer_item is None:
            return
        text, accepted = QInputDialog.getMultiLineText(
            self,
            "Add Viewer Memory",
            "Memory",
        )
        if not accepted or not text.strip():
            return
        categories = ("General", "Preference", "Game", "Relationship", "Personal")
        category, accepted = QInputDialog.getItem(
            self,
            "Memory Category",
            "Category",
            categories,
            0,
            False,
        )
        if not accepted:
            return
        user_id = str(viewer_item.data(Qt.ItemDataRole.UserRole))
        self.chatter_history.add_memory(user_id, text, category)
        self._save_chatter_history()
        self._show_memory_viewer(viewer_item, None)

    @Slot()
    def _edit_viewer_memory(self) -> None:
        context = self._selected_memory_context()
        if context is None:
            return
        user_id, memory_id = context
        memory = self.chatter_history.get_memory(user_id, memory_id)
        text, accepted = QInputDialog.getMultiLineText(
            self,
            "Edit Viewer Memory",
            "Memory",
            str(memory.get("text", "")),
        )
        if not accepted or not text.strip():
            return
        self.chatter_history.update_memory(user_id, memory_id, text=text)
        self._save_chatter_history()
        self._show_memory_viewer(self.memory_viewer_list.currentItem(), None)

    @Slot()
    def _toggle_viewer_memory_pin(self) -> None:
        context = self._selected_memory_context()
        if context is None:
            return
        user_id, memory_id = context
        memory = self.chatter_history.get_memory(user_id, memory_id)
        self.chatter_history.update_memory(
            user_id,
            memory_id,
            pinned=not bool(memory.get("pinned", False)),
        )
        self._save_chatter_history()
        self._show_memory_viewer(self.memory_viewer_list.currentItem(), None)

    @Slot()
    def _archive_viewer_memory(self) -> None:
        context = self._selected_memory_context()
        if context is None:
            return
        user_id, memory_id = context
        self.chatter_history.update_memory(
            user_id,
            memory_id,
            archived=True,
        )
        self._save_chatter_history()
        self._show_memory_viewer(self.memory_viewer_list.currentItem(), None)

    @Slot()
    def _delete_viewer_memory(self) -> None:
        context = self._selected_memory_context()
        if context is None:
            return
        if QMessageBox.question(
            self,
            "Delete Viewer Memory",
            "Permanently delete the selected memory?",
        ) is not QMessageBox.StandardButton.Yes:
            return
        user_id, memory_id = context
        self.chatter_history.delete_memory(user_id, memory_id)
        self._save_chatter_history()
        self._show_memory_viewer(self.memory_viewer_list.currentItem(), None)

    @Slot()
    def _export_viewer_memory(self) -> None:
        viewer_item = self.memory_viewer_list.currentItem()
        if viewer_item is None:
            return
        user_id = str(viewer_item.data(Qt.ItemDataRole.UserRole))
        record = self.chatter_history.records.get(user_id)
        if record is None:
            return
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Viewer Data",
            f"{record.user_name or user_id}-memories.json",
            "JSON files (*.json)",
        )
        if not filename:
            return
        Path(filename).write_text(
            json.dumps(asdict(record), indent=2),
            encoding="utf-8",
        )

    @Slot()
    def _erase_viewer_memories(self) -> None:
        viewer_item = self.memory_viewer_list.currentItem()
        if viewer_item is None:
            return
        if QMessageBox.question(
            self,
            "Erase Viewer Memories",
            "Erase every saved memory for this viewer? Participation statistics remain.",
        ) is not QMessageBox.StandardButton.Yes:
            return
        user_id = str(viewer_item.data(Qt.ItemDataRole.UserRole))
        self.chatter_history.clear_memories(user_id)
        self._save_chatter_history()
        self._show_memory_viewer(viewer_item, None)

    @staticmethod
    def _format_memory_timestamp(value: str) -> str:
        try:
            return datetime.fromisoformat(value).astimezone().strftime(
                "%b %d, %Y %H:%M"
            )
        except ValueError:
            return "Not available"

    @staticmethod
    def _format_follow_age(value: str) -> str:
        if not value:
            return "Not observed yet"
        try:
            followed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return "Not available"
        days = max(
            (datetime.now(timezone.utc) - followed).days,
            0,
        )
        if days < 30:
            return f"{days} day(s)"
        if days < 365:
            return f"{days // 30} month(s)"
        return f"{days // 365} year(s)"

    @Slot()
    def show_logs(self) -> None:
        self.ui.mainStack.setCurrentWidget(self.ui.logsPage)
        self.ui.logsButton.setChecked(True)

    @Slot()
    def show_connections(self) -> None:
        self.ui.mainStack.setCurrentWidget(self.connections_page)
        self.connections_button.setChecked(True)

    @Slot()
    def refresh_stream_companion(self) -> None:
        token = self.twitch_auth.token
        broadcaster_id = self.twitch_service.broadcaster_user_id
        if (
            token is None
            or not broadcaster_id
            or self.companion_refresh_in_flight
        ):
            return
        self.companion_refresh_request_id += 1
        request_id = self.companion_refresh_request_id
        worker = CompanionRefreshWorker(
            request_id,
            self.twitch_service.helix,
            broadcaster_id,
            token,
            fetch_followers=not self.followers_backfilled,
        )
        worker.signals.completed.connect(self._apply_companion_refresh)
        worker.signals.failed.connect(self._companion_refresh_failed)
        self.companion_refresh_in_flight = True
        self.companion_thread_pool.start(worker)

    @Slot(object)
    def _apply_companion_refresh(
        self,
        result: CompanionRefreshResult,
    ) -> None:
        if result.request_id != self.companion_refresh_request_id:
            return
        self.companion_refresh_in_flight = False
        snapshot = result.snapshot
        if self.session_tracker.observe_stream(snapshot.get("stream")):
            self._refresh_session_history()
        current_warnings = set(result.warnings)
        for warning in sorted(current_warnings - self.companion_warning_cache):
            Logger.warning(
                f"Stream companion section unavailable ({warning})",
                source="TWITCH",
            )
        self.companion_warning_cache = current_warnings
        self.twitch_health.companion_succeeded(result.warnings)
        self._refresh_twitch_health()
        if any("401" in warning for warning in current_warnings):
            self.twitch_auth.recover_unauthorized()
        stream = snapshot.get("stream")
        if isinstance(stream, dict):
            self.stream_live_label.setText("LIVE")
            self.stream_live_label.setStyleSheet(
                "color:#ff4f64; font-weight:bold;"
            )
            self.stream_viewers_label.setText(
                f"{int(stream.get('viewer_count', 0)):,} viewers"
            )
            started_at = datetime.fromisoformat(
                str(stream.get("started_at", "")).replace("Z", "+00:00")
            )
            elapsed = max(
                int(
                    (datetime.now(timezone.utc) - started_at).total_seconds()
                ),
                0,
            )
            self.stream_time_label.setText(
                f"{elapsed // 3600:02}:"
                f"{elapsed % 3600 // 60:02}:{elapsed % 60:02}"
            )
        else:
            self.stream_live_label.setText("Offline")
            self.stream_live_label.setStyleSheet("")
            self.stream_viewers_label.setText("0 viewers")
            self.stream_time_label.setText("00:00:00")
        followers = snapshot.get("followers")
        self.stream_followers_label.setText(
            "-- followers"
            if followers is None
            else f"{int(followers):,} followers"
        )
        subscribers = snapshot.get("subscribers")
        self.stream_subscribers_label.setText(
            "-- subscribers"
            if subscribers is None
            else f"{int(subscribers):,} subscribers"
        )
        self._apply_chatter_groups(result)
        if result.followers:
            for follower in result.followers:
                user_id = str(follower.get("user_id", ""))
                if user_id not in self.chatter_history.records:
                    continue
                self.chatter_history.record_follow(
                    user_id,
                    str(follower.get("user_name", "")),
                    str(follower.get("followed_at", "")),
                )
            self.followers_backfilled = True
            self._refresh_memory_viewer_list()

    def _apply_chatter_groups(
        self,
        result: CompanionRefreshResult,
    ) -> None:
        self.chatter_list.clear()
        if not result.can_read_chatters:
            self.chatter_list.addTopLevelItem(
                QTreeWidgetItem(["Connections > Update Permissions"])
            )
            return
        self.chatter_history.observe_snapshot(
            result.chatters,
            moderator_ids=result.moderator_ids,
            vip_ids=result.vip_ids,
            subscriber_ids=result.subscriber_ids,
            session_id=(
                self.session_store.current.started_at
                if self.session_store.current is not None
                else ""
            ),
        )
        self._refresh_memory_viewer_list()
        groups = {
            "Moderators": [],
            "VIPs": [],
            "Subscribers": [],
            "Bots": [],
            "Regulars": [],
            "Viewers": [],
        }
        for chatter in result.chatters:
            user_id = str(chatter.get("user_id", ""))
            user_name = str(chatter.get("user_name", ""))
            if user_id in result.moderator_ids:
                groups["Moderators"].append(user_name)
            elif user_id in result.vip_ids:
                groups["VIPs"].append(user_name)
            elif user_id in result.subscriber_ids:
                groups["Subscribers"].append(user_name)
            elif (
                user_id in self.known_bot_user_ids
                or self.chatter_history.is_bot(user_id)
            ):
                groups["Bots"].append(user_name)
            elif self.chatter_history.is_regular(user_id):
                groups["Regulars"].append(user_name)
            else:
                groups["Viewers"].append(user_name)
        for group_name, users in groups.items():
            group_item = QTreeWidgetItem([f"{group_name} ({len(users)})"])
            for user_name in sorted(users, key=str.casefold):
                group_item.addChild(QTreeWidgetItem([user_name]))
            self.chatter_list.addTopLevelItem(group_item)
            group_item.setExpanded(True)

    def _refresh_session_history(self) -> None:
        if not hasattr(self, "session_table"):
            return
        current = self.session_store.current
        if current is None:
            self.session_summary_label.setText("No active stream session")
        else:
            self.session_summary_label.setText(
                "Live session: "
                f"{current.peak_viewers:,} peak viewers, "
                f"{current.messages:,} messages, "
                f"{current.follows:,} follows, "
                f"{current.subscriptions:,} subscriptions"
            )
        self.session_table.setRowCount(len(self.session_store.sessions))
        for row, session in enumerate(self.session_store.sessions):
            try:
                started = datetime.fromisoformat(session.started_at)
                ended = datetime.fromisoformat(session.ended_at)
                duration_seconds = max(
                    int((ended - started).total_seconds()),
                    0,
                )
                started_text = started.astimezone().strftime(
                    "%b %d, %Y %H:%M"
                )
                duration_text = (
                    f"{duration_seconds // 3600:02}:"
                    f"{duration_seconds % 3600 // 60:02}"
                )
            except ValueError:
                started_text = session.started_at
                duration_text = "--"
            values = (
                started_text,
                duration_text,
                f"{session.peak_viewers:,}",
                f"{session.messages:,}",
                f"{session.follows:,}",
                f"{session.subscriptions:,}",
                f"{session.cheers:,}",
                f"{session.raids:,}",
            )
            for column, value in enumerate(values):
                self.session_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(value),
                )
        self._refresh_analytics()

    def _analytics_snapshot(self) -> AnalyticsSnapshot:
        days = self.analytics_range_combo.currentData()
        return build_analytics(
            self.session_store.sessions,
            self.chatter_history.records.values(),
            int(days) if days is not None else None,
        )

    def _refresh_analytics(self) -> None:
        if not hasattr(self, "analytics_sessions_table"):
            return
        snapshot = self._analytics_snapshot()
        values = {
            "sessions": f"{snapshot.session_count:,}",
            "hours": f"{snapshot.total_hours:,.1f}",
            "average_peak": f"{snapshot.average_peak_viewers:,.1f}",
            "highest_peak": f"{snapshot.highest_peak_viewers:,}",
            "messages": f"{snapshot.total_messages:,}",
            "messages_hour": f"{snapshot.messages_per_hour:,.1f}",
            "follows": f"{snapshot.follows:,}",
            "subscriptions": f"{snapshot.subscriptions:,}",
            "cheers": f"{snapshot.cheers:,}",
            "raids": f"{snapshot.raids:,}",
            "known_viewers": f"{snapshot.known_viewers:,}",
            "returning_viewers": f"{snapshot.returning_viewers:,}",
            "new_viewers": f"{snapshot.new_viewers:,}",
            "regular_viewers": f"{snapshot.regular_viewers:,}",
        }
        for key, value in values.items():
            self.analytics_labels[key].setText(value)
        self.analytics_sessions_table.setRowCount(len(snapshot.sessions))
        for row, session in enumerate(snapshot.sessions):
            engagement = (
                session.follows
                + session.subscriptions
                + session.cheers
                + session.raids
            )
            row_values = (
                self._format_memory_timestamp(session.started_at),
                f"{session.peak_viewers:,}",
                f"{session.messages:,}",
                f"{session.follows:,}",
                f"{session.subscriptions:,}",
                f"{engagement:,}",
            )
            for column, value in enumerate(row_values):
                self.analytics_sessions_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(value),
                )
        self.analytics_viewers_table.setRowCount(len(snapshot.top_viewers))
        for row, viewer in enumerate(snapshot.top_viewers):
            row_values = (
                viewer.user_name,
                f"{viewer.messages:,}",
                f"{viewer.active_days:,}",
                self._format_memory_timestamp(viewer.first_seen),
                self._format_memory_timestamp(viewer.last_seen),
            )
            for column, value in enumerate(row_values):
                self.analytics_viewers_table.setItem(
                    row,
                    column,
                    QTableWidgetItem(value),
                )

    @Slot()
    def _export_analytics_csv(self) -> None:
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Stream Analytics",
            "sally-stream-analytics.csv",
            "CSV files (*.csv)",
        )
        if not filename:
            return
        snapshot = self._analytics_snapshot()
        with Path(filename).open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output)
            writer.writerow(
                ("started_at", "ended_at", "peak_viewers", "messages", "follows", "subscriptions", "cheers", "raids")
            )
            for session in snapshot.sessions:
                writer.writerow(
                    (
                        session.started_at,
                        session.ended_at,
                        session.peak_viewers,
                        session.messages,
                        session.follows,
                        session.subscriptions,
                        session.cheers,
                        session.raids,
                    )
                )
            writer.writerow(())
            writer.writerow(
                ("viewer", "messages", "active_days", "first_seen", "last_seen")
            )
            for viewer in snapshot.top_viewers:
                writer.writerow(
                    (
                        viewer.user_name,
                        viewer.messages,
                        viewer.active_days,
                        viewer.first_seen,
                        viewer.last_seen,
                    )
                )

    @Slot()
    def _export_analytics_json(self) -> None:
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Stream Analytics",
            "sally-stream-analytics.json",
            "JSON files (*.json)",
        )
        if not filename:
            return
        snapshot = self._analytics_snapshot()
        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "range_days": self.analytics_range_combo.currentData(),
            "summary": {
                "session_count": snapshot.session_count,
                "total_hours": snapshot.total_hours,
                "average_peak_viewers": snapshot.average_peak_viewers,
                "highest_peak_viewers": snapshot.highest_peak_viewers,
                "total_messages": snapshot.total_messages,
                "messages_per_hour": snapshot.messages_per_hour,
                "follows": snapshot.follows,
                "subscriptions": snapshot.subscriptions,
                "cheers": snapshot.cheers,
                "raids": snapshot.raids,
                "known_viewers": snapshot.known_viewers,
                "new_viewers": snapshot.new_viewers,
                "returning_viewers": snapshot.returning_viewers,
                "regular_viewers": snapshot.regular_viewers,
            },
            "sessions": [asdict(session) for session in snapshot.sessions],
            "top_viewers": [
                asdict(viewer) for viewer in snapshot.top_viewers
            ],
        }
        Path(filename).write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )

    @Slot()
    def _apply_analytics_retention(self) -> None:
        removed = self.session_store.prune(
            self.analytics_retention_spin.value()
        )
        self._refresh_session_history()
        self.statusBar().showMessage(
            f"Session retention applied; removed {removed} old session(s).",
            8000,
        )

    @Slot(int, str)
    def _companion_refresh_failed(
        self,
        request_id: int,
        message: str,
    ) -> None:
        if request_id != self.companion_refresh_request_id:
            return
        self.companion_refresh_in_flight = False
        self.twitch_health.companion_failed(message)
        self._refresh_twitch_health()
        if "401" in message and self.twitch_auth.recover_unauthorized():
            Logger.info(
                "Recovering Twitch login after companion API authorization failed.",
                source="TWITCH",
            )
            return
        Logger.warning(
            f"Could not refresh stream companion: {message}",
            source="TWITCH",
        )

    @Slot()
    def _retry_twitch_health(self) -> None:
        self.companion_warning_cache.clear()
        self.twitch_auth.maintain()
        self.refresh_stream_companion()
        self.statusBar().showMessage("Retrying Twitch services...", 5000)

    def _refresh_twitch_health(self) -> None:
        if not hasattr(self, "health_auth_label"):
            return
        self.health_auth_label.setText(self.twitch_health.auth_state)
        token = self.twitch_auth.token
        if token is None or not isinstance(token.expires_at, (int, float)):
            self.health_token_label.setText("Not available")
        else:
            expires = datetime.fromtimestamp(
                token.expires_at,
                timezone.utc,
            )
            remaining = max(
                int((expires - datetime.now(timezone.utc)).total_seconds()),
                0,
            )
            self.health_token_label.setText(
                f"{remaining // 60} minutes ({expires.astimezone():%H:%M})"
            )
        self.health_eventsub_label.setText(
            self.twitch_health.eventsub_state
        )
        self.health_companion_label.setText(
            TwitchHealth.elapsed_text(
                self.twitch_health.last_companion_success
            )
        )
        missing = sorted(self.twitch_health.missing_scopes)
        self.health_permissions_label.setText(
            ", ".join(missing) if missing else "All requested permissions"
        )
        self.health_error_label.setText(
            self.twitch_health.last_companion_error or "None"
        )

    @Slot()
    def _save_chatter_history(self) -> None:
        try:
            self.chatter_history.save()
            self.session_store.save()
        except OSError as error:
            Logger.warning(
                f"Could not save Twitch chatter history: {error}",
                source="TWITCH",
            )

    @Slot()
    def run_commercial(self) -> None:
        token = self.twitch_auth.token
        if token is None or not self.twitch_service.broadcaster_user_id:
            return
        try:
            result = self.twitch_service.helix.start_commercial(
                self.twitch_service.broadcaster_user_id,
                int(self.ad_length_combo.currentData()),
                token,
            )
            self.statusBar().showMessage(str(result.get("message", "Commercial started.")), 8000)
        except Exception as error:
            self.handle_twitch_error(f"Could not start commercial: {error}")

    @Slot()
    def snooze_next_ad(self) -> None:
        token = self.twitch_auth.token
        if token is None or not self.twitch_service.broadcaster_user_id:
            return
        try:
            self.twitch_service.helix.snooze_ad(
                self.twitch_service.broadcaster_user_id,
                token,
            )
            self.statusBar().showMessage("Next ad snoozed by 5 minutes.", 8000)
        except Exception as error:
            self.handle_twitch_error(f"Could not snooze ad: {error}")

    @Slot()
    def show_settings(self) -> None:
        self.ui.mainStack.setCurrentWidget(self.ui.settingsPage)
        self.ui.settingsButton.setChecked(True)

    @Slot()
    def test_info_log(self) -> None:
        Logger.info("Developer test information message.", source="DEV")

    @Slot()
    def test_warning_log(self) -> None:
        Logger.warning("Developer test warning message.", source="DEV")

    @Slot()
    def test_error_log(self) -> None:
        Logger.error("Developer test error message.", source="DEV")

    @Slot()
    def save_settings(self) -> None:
        settings = self._settings_from_controls()

        try:
            self.settings_store.save(settings)
        except OSError as error:
            self.ui.settingsStatusLabel.setText(
                f"Could not save settings: {error}"
            )
            Logger.error(
                f"Could not save settings: {error}",
                source="SETTINGS",
            )
            return

        self.settings = settings
        self._apply_settings(settings)
        self.ui.settingsStatusLabel.setText("Settings saved.")
        Logger.info("Application settings saved.", source="SETTINGS")

    @Slot()
    def reset_settings(self) -> None:
        self._settings_to_controls(AppSettings())
        self.ui.settingsStatusLabel.setText(
            "Defaults restored. Select Save Settings to apply them."
        )

    @Slot()
    def _create_automatic_backup(self) -> None:
        try:
            archive = self.release_controller.automatic_backup()
            if archive is not None:
                Logger.info(
                    f"Created automatic data backup: {archive.name}",
                    source="DATA",
                )
        except OSError as error:
            Logger.warning(
                f"Could not create automatic data backup: {error}",
                source="DATA",
            )

    @Slot()
    def _create_manual_backup(self) -> None:
        try:
            archive = self.release_controller.create_backup()
            self.release_tools_status.setText(
                f"Backup created: {archive}"
            )
        except OSError as error:
            self.release_tools_status.setText(f"Backup failed: {error}")

    @Slot()
    def _restore_latest_backup(self) -> None:
        if QMessageBox.question(
            self,
            "Restore Latest Backup",
            "Current local data will be backed up, then replaced. "
            "Sally must be restarted afterward. Continue?",
        ) is not QMessageBox.StandardButton.Yes:
            return
        try:
            report = self.release_controller.restore_latest()
            if report is None:
                self.release_tools_status.setText("No backup is available.")
                return
            self.release_tools_status.setText(
                f"Restored {len(report.restored_files)} file(s). Restart Sally."
            )
        except (OSError, ValueError) as error:
            self.release_tools_status.setText(f"Restore failed: {error}")

    @Slot()
    def _export_diagnostic_bundle(self) -> None:
        filename, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Sally Diagnostics",
            "sally-diagnostics.zip",
            "ZIP archives (*.zip)",
        )
        if not filename:
            return
        health = {
            "authentication": self.twitch_health.auth_state,
            "connection": self.twitch_health.connection_state,
            "eventsub": self.twitch_health.eventsub_state,
            "last_companion_success": (
                self.twitch_health.last_companion_success.isoformat()
                if self.twitch_health.last_companion_success
                else None
            ),
            "last_companion_error": self.twitch_health.last_companion_error,
            "missing_scopes": sorted(self.twitch_health.missing_scopes),
        }
        try:
            destination = self.release_controller.export_diagnostics(
                Path(filename),
                asdict(self.settings),
                health,
            )
            self.release_tools_status.setText(
                f"Diagnostics exported: {destination}"
            )
        except OSError as error:
            self.release_tools_status.setText(
                f"Diagnostic export failed: {error}"
            )

    def closeEvent(self, event: QCloseEvent) -> None:
        self.window_state_store.save(self)
        self._save_chatter_history()
        self.companion_refresh_request_id += 1
        self.companion_thread_pool.clear()
        self.companion_thread_pool.waitForDone(2_000)
        self.twitch_service.disconnect()
        Events.unsubscribe(
            "twitch_status_changed",
            self.twitch_bridge.handle_status_changed,
        )
        Events.unsubscribe(
            "twitch_message_received",
            self.twitch_bridge.handle_message_received,
        )
        Events.unsubscribe(
            "twitch_error",
            self.twitch_bridge.handle_error,
        )
        Events.unsubscribe(
            "twitch_event_received",
            self.twitch_bridge.handle_diagnostic,
        )
        Events.unsubscribe(
            "twitch_auth_changed",
            self.twitch_bridge.handle_auth_changed,
        )
        Events.unsubscribe(
            "twitch_notice_received",
            self.twitch_bridge.handle_notice_received,
        )
        Events.unsubscribe(
            "twitch_event",
            self.twitch_bridge.handle_activity_received,
        )
        Logger.remove_handler(self.log_handler)
        super().closeEvent(event)
