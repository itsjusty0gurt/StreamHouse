import json
import csv
import logging
import re
from collections import deque
from time import monotonic
from urllib.parse import urlencode
from uuid import uuid4
from dataclasses import asdict
from datetime import datetime, time, timezone
from html import escape
from pathlib import Path

from PySide6.QtCore import QThreadPool, QTime, QTimer, Qt, Slot
from PySide6.QtGui import QCloseEvent, QColor, QCursor, QFont
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QCheckBox,
    QDockWidget,
    QFormLayout,
    QFileDialog,
    QFrame,
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
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QScrollArea,
    QSplitter,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QTimeEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.events import Events
from core.logger import Logger
from core.settings import AppSettings, SettingsStore
from ai.providers import OllamaProvider
from ai.memory_extractor import BufferedChatMessage
from ai.memory import build_viewer_context
from ai.response_engine import (
    ResponseDecision,
    ResponseDecisionEngine,
    ResponseMessage,
)
from ai.training_store import TrainingStore
from ai.test_report import AITestReportStore
from ai.rivescript_engine import RiveScriptRuleStore, SallyRiveScriptEngine
from core.window_state import WindowStateStore
from config.twitch import (
    TWITCH_BOT_SCOPES,
    TWITCH_COMPANION_SCOPES,
    TWITCH_SCOPES,
)
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
from twitch.token_store import TwitchTokenStore
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
from ui.memory_worker import MemoryExtractionResult, MemoryExtractionWorker
from ui.response_worker import ResponseBatchResult, ResponseDecisionWorker


class MainWindow(QMainWindow):
    def __init__(
        self,
        twitch_service: TwitchService | None = None,
        twitch_auth: TwitchAuthService | None = None,
        twitch_bot_auth: TwitchAuthService | None = None,
        window_state_store: WindowStateStore | None = None,
        chatter_history_store: ChatterHistoryStore | None = None,
        activity_history_store: ActivityHistoryStore | None = None,
        session_store: StreamSessionStore | None = None,
        release_controller: ReleaseController | None = None,
        training_store: TrainingStore | None = None,
        test_report_store: AITestReportStore | None = None,
        rivescript_store: RiveScriptRuleStore | None = None,
        auto_upgrade_permissions: bool = True,
    ) -> None:
        super().__init__()

        self.twitch_service = twitch_service or TwitchService()
        self.twitch_auth = twitch_auth or TwitchAuthService()
        self.twitch_bot_auth = twitch_bot_auth or TwitchAuthService(
            store=TwitchTokenStore.bot_account(),
            scopes=TWITCH_BOT_SCOPES,
            event_name="twitch_bot_auth_changed",
            account_label="Sally bot",
        )
        self.twitch_service.bot_auth = self.twitch_bot_auth
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
        self.training_store = training_store or TrainingStore()
        self.test_report_store = test_report_store or AITestReportStore()
        self.rivescript_store = rivescript_store or RiveScriptRuleStore()
        self.training_opted_in_users: set[str] = set()
        self.training_notice_attempt_context = ""
        try:
            self.training_store.load()
        except (OSError, ValueError, json.JSONDecodeError) as error:
            Logger.warning(
                f"Could not load local training examples: {error}",
                source="AI",
            )
        try:
            self.test_report_store.load()
        except (OSError, ValueError, json.JSONDecodeError) as error:
            Logger.warning(
                f"Could not load anonymous AI test diagnostics: {error}",
                source="AI",
            )
        try:
            self.rivescript_store.load()
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self.rivescript_store.rules = []
            Logger.warning(
                f"Could not load local RiveScript rules: {error}",
                source="AI",
            )
        self.rivescript_engine = SallyRiveScriptEngine(self.rivescript_store)
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
        self.last_companion_result: CompanionRefreshResult | None = None
        self.memory_reasoning_thread_pool = QThreadPool(self)
        self.memory_reasoning_thread_pool.setMaxThreadCount(1)
        self.memory_message_buffers: dict[
            str, deque[BufferedChatMessage]
        ] = {}
        self.memory_extraction_in_flight: set[str] = set()
        self.memory_extraction_retry_after: dict[str, float] = {}
        self.response_decision_thread_pool = QThreadPool(self)
        self.response_decision_thread_pool.setMaxThreadCount(1)
        self.response_decision_queue: deque[ResponseMessage] = deque(
            maxlen=100
        )
        self.response_decision_in_flight = False
        self.auto_send_diagnostic_reasons: dict[str, str] = {}
        self.ai_test_report_flush_timer = QTimer(self)
        self.ai_test_report_flush_timer.setSingleShot(True)
        self.ai_test_report_flush_timer.setInterval(750)
        self.ai_test_report_flush_timer.timeout.connect(
            self._flush_ai_test_report
        )
        self.recent_ai_chat: deque[dict[str, str]] = deque(maxlen=100)
        self.last_auto_reply_at = 0.0
        self.last_interjection_at = 0.0
        self.viewer_messages_since_sally_reply = 0
        self.closed_ai_conversations: dict[str, datetime] = {}
        self.current_memory_stream_id = ""
        self.memory_promo_message_count = 0
        self.last_memory_promo_at = 0.0
        self.pending_memory_deletions: dict[str, float] = {}
        self.daily_memory_expiry_pending = True
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
        self._build_ai_settings()
        self._build_settings_tabs()
        self._make_layout_responsive()
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
        self.twitch_bridge.bot_auth_changed.connect(
            self.handle_twitch_bot_auth_changed
        )
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
            "twitch_bot_auth_changed",
            self.twitch_bridge.handle_bot_auth_changed,
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
        self.twitch_bot_sign_in_button.clicked.connect(
            self.twitch_bot_auth.sign_in
        )
        self.twitch_bot_sign_out_button.clicked.connect(
            self.twitch_bot_auth.sign_out
        )
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
        self.auth_maintenance_timer.timeout.connect(
            self.twitch_bot_auth.maintain
        )
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
        self.daily_memory_timer = QTimer(self)
        self.daily_memory_timer.setInterval(60_000)
        self.daily_memory_timer.timeout.connect(self._expire_daily_memory)
        self.daily_memory_timer.start()
        self.activity_age_timer = QTimer(self)
        self.activity_age_timer.timeout.connect(self._rebuild_activity_feed)
        self._schedule_activity_age_refresh()
        QTimer.singleShot(2_000, self._create_automatic_backup)
        Logger.info("UI log viewer connected.", source="UI")

    def _build_release_tools(self) -> None:
        group = QGroupBox("Data Safety & Diagnostics")
        self.release_tools_group = group
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

    def _build_settings_tabs(self) -> None:
        self.settings_tabs = QTabWidget(self.ui.settingsPage)
        self.settings_tabs.setObjectName("settingsTabs")
        tab_groups = (
            (
                "Application",
                (
                    self.ui.generalSettingsGroup,
                    self.ui.loggingSettingsGroup,
                    self.release_tools_group,
                ),
            ),
            ("Chat", (self.ui.twitchChatSettingsGroup,)),
            ("AI", (self.local_ai_settings_group,)),
            ("Developer", (self.ui.developerSettingsGroup,)),
        )
        for title, groups in tab_groups:
            page = QWidget(self.settings_tabs)
            page_layout = QVBoxLayout(page)
            for group in groups:
                self.ui.settingsLayout.removeWidget(group)
                page_layout.addWidget(group)
            page_layout.addStretch()
            self.settings_tabs.addTab(page, title)
        self.ui.settingsLayout.insertWidget(1, self.settings_tabs, 1)

    def _make_layout_responsive(self) -> None:
        """Keep hidden pages from imposing their full size on the main window."""
        self.setMinimumSize(520, 360)
        ignored = QSizePolicy(
            QSizePolicy.Policy.Ignored,
            QSizePolicy.Policy.Ignored,
        )
        self.ui.mainStack.setSizePolicy(ignored)
        for index in range(self.ui.mainStack.count()):
            self.ui.mainStack.widget(index).setSizePolicy(ignored)

        settings_index = self.ui.mainStack.indexOf(self.ui.settingsPage)
        self.settings_container = QScrollArea()
        self.settings_container.setObjectName("settingsScrollArea")
        self.settings_container.setWidgetResizable(True)
        self.settings_container.setFrameShape(QFrame.Shape.NoFrame)
        self.settings_container.setSizePolicy(ignored)
        self.ui.mainStack.removeWidget(self.ui.settingsPage)
        self.settings_container.setWidget(self.ui.settingsPage)
        self.ui.mainStack.insertWidget(settings_index, self.settings_container)

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
        self.ui.twitchDetailTabs.tabBar().hide()
        self.ui.twitchChatCountLabel.hide()

        self.ui.twitchEventsTabLayout.removeWidget(
            self.ui.twitchEventSimulatorGroup
        )
        self.logs_tabs = QTabWidget(self.ui.logsPage)
        self.logs_tabs.setObjectName("logsTabs")
        application_log_page = QWidget(self.logs_tabs)
        application_log_layout = QVBoxLayout(application_log_page)
        self.ui.logsLayout.removeWidget(self.ui.logOutput)
        application_log_layout.addWidget(self.ui.logOutput)
        self.logs_tabs.addTab(application_log_page, "Application")
        self.logs_tabs.addTab(self.ui.twitchEventsTab, "Twitch Events")
        self.ui.logsLayout.insertWidget(1, self.logs_tabs)

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
        developer_events_layout.addStretch()
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
        bot_account_group = QGroupBox("Sally Chat Account")
        bot_account_layout = QFormLayout(bot_account_group)
        self.twitch_bot_account_status_label = QLabel("Not signed in")
        self.twitch_bot_account_status_label.setWordWrap(True)
        self.twitch_bot_sign_in_button = QPushButton(
            "Sign in with a Bot Account"
        )
        self.twitch_bot_sign_out_button = QPushButton("Sign Out Bot")
        self.twitch_bot_sign_out_button.setEnabled(False)
        bot_actions = QHBoxLayout()
        bot_actions.addWidget(self.twitch_bot_sign_in_button)
        bot_actions.addWidget(self.twitch_bot_sign_out_button)
        bot_account_help = QLabel(
            "Optional. When connected, Sally reads and sends chat as this "
            "separate Twitch account. Channel controls continue using your "
            "broadcaster account. On Twitch's activation page, make sure the "
            "browser is signed into the bot account."
        )
        bot_account_help.setWordWrap(True)
        bot_account_layout.addRow("Bot identity", self.twitch_bot_account_status_label)
        bot_account_layout.addRow("", bot_actions)
        bot_account_layout.addRow("", bot_account_help)
        connections_layout.addWidget(bot_account_group)
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
        self.chatter_title_label = QLabel("Chatters (0)")
        self.chatter_title_label.setStyleSheet("font-weight:bold;")
        self.chatter_list = QTreeWidget()
        self.chatter_list.setHeaderHidden(True)
        chatter_panel.setMinimumWidth(80)
        chatter_panel.setMaximumWidth(180)
        chatter_layout.addWidget(self.chatter_title_label)
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
        self.twitch_channel_splitter.setCollapsible(1, True)
        self.twitch_channel_splitter.setCollapsible(2, True)
        activity_panel.setMinimumWidth(140)

        self.channel_tabs = QTabWidget(self.ui.twitchPage)
        self.channel_tabs.setObjectName("channelTabs")
        self.ui.twitchPageLayout.replaceWidget(
            self.twitch_channel_splitter,
            self.channel_tabs,
        )
        self.channel_tabs.addTab(self.twitch_channel_splitter, "Chat")
        self.chatter_list.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.chatter_list.customContextMenuRequested.connect(
            self._show_chatter_tree_context_menu
        )
        self.ui.twitchChatOutput.chatter_context_requested.connect(
            self._show_chatter_context_menu
        )

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
        self.memory_viewer_list.setMinimumWidth(140)
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
        self.memory_summary_label = QLabel("Select a viewer to build a summary.")
        self.memory_summary_label.setWordWrap(True)
        self.memory_summary_label.setStyleSheet(
            "background:#18181b; padding:8px; border:1px solid #3a3a3d;"
        )
        ai_layout.addWidget(self.memory_summary_label)
        memory_controls = QHBoxLayout()
        self.memory_enabled_check = QCheckBox("Allow AI memory for this viewer")
        self.memory_status_filter = QComboBox()
        self.memory_status_filter.addItems(("All", "Pending review", "Approved"))
        memory_controls.addWidget(self.memory_enabled_check)
        memory_controls.addStretch()
        memory_controls.addWidget(QLabel("Show"))
        memory_controls.addWidget(self.memory_status_filter)
        ai_layout.addLayout(memory_controls)
        self.memory_ai_list = QListWidget()
        self.memory_reasoning_status_label = QLabel(
            "Local memory reasoning is waiting for enough chat context."
        )
        self.memory_reasoning_status_label.setWordWrap(True)
        ai_layout.addWidget(self.memory_reasoning_status_label)
        ai_layout.addWidget(self.memory_ai_list)
        self.memory_detail_label = QLabel("Select a memory to inspect its evidence.")
        self.memory_detail_label.setWordWrap(True)
        ai_layout.addWidget(self.memory_detail_label)
        memory_actions = QGridLayout()
        self.add_memory_button = QPushButton("Add")
        self.edit_memory_button = QPushButton("Edit")
        self.pin_memory_button = QPushButton("Pin")
        self.archive_memory_button = QPushButton("Archive")
        self.delete_memory_button = QPushButton("Delete")
        self.export_memory_button = QPushButton("Export Viewer")
        self.erase_memories_button = QPushButton("Erase Memories")
        self.approve_memory_button = QPushButton("Approve")
        self.reject_memory_button = QPushButton("Reject")
        self.show_archived_memories_check = QCheckBox("Show archived")
        for index, button in enumerate((
            self.add_memory_button,
            self.edit_memory_button,
            self.pin_memory_button,
            self.archive_memory_button,
            self.delete_memory_button,
            self.export_memory_button,
            self.erase_memories_button,
            self.approve_memory_button,
            self.reject_memory_button,
        )):
            memory_actions.addWidget(button, index // 3, index % 3)
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
        self._build_reply_review_tab()
        self._build_rivescript_tab()
        self._build_ai_test_report_tab()
        self._build_training_tab()
        self._build_personality_tab()
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
        self.channel_tabs.addTab(sessions_page, "Stream Sessions")
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
        self.channel_tabs.addTab(analytics_page, "Analytics")
        self.ui.mainStack.addWidget(self.ai_page)
        self.memory_search_edit.textChanged.connect(
            self._refresh_memory_viewer_list
        )
        self.memory_viewer_list.currentItemChanged.connect(
            self._show_memory_viewer
        )
        self.memory_ai_list.currentItemChanged.connect(
            self._show_memory_details
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
        self.approve_memory_button.clicked.connect(self._approve_viewer_memory)
        self.reject_memory_button.clicked.connect(self._reject_viewer_memory)
        self.memory_enabled_check.toggled.connect(self._set_viewer_memory_enabled)
        self.memory_status_filter.currentTextChanged.connect(
            lambda _text: self._show_memory_viewer(
                self.memory_viewer_list.currentItem(), None
            )
        )
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
        self._update_memory_action_states()
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

    def _build_reply_review_tab(self) -> None:
        page = QWidget(self.ai_tabs)
        layout = QVBoxLayout(page)
        self.reply_decision_status_label = QLabel(
            "Waiting for live Twitch messages. Approved replies send automatically."
        )
        self.reply_decision_status_label.setWordWrap(True)
        self.reply_review_table = QTableWidget(0, 6)
        self.reply_review_table.setHorizontalHeaderLabels(
            ("Age", "Viewer", "Message", "Decision", "Draft reply", "Reason")
        )
        self.reply_review_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.reply_review_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.reply_review_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        header = self.reply_review_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        actions = QHBoxLayout()
        self.send_reply_draft_button = QPushButton("Send Selected")
        self.edit_send_reply_button = QPushButton("Edit & Send")
        self.dismiss_reply_button = QPushButton("Dismiss")
        self.teach_rivescript_button = QPushButton("Teach RiveScript")
        self.teach_rivescript_button.setEnabled(False)
        self.teach_rivescript_button.setToolTip(
            "Create a reviewed local RiveScript rule from the selected reply."
        )
        self.clear_reply_decisions_button = QPushButton("Clear History")
        actions.addWidget(self.send_reply_draft_button)
        actions.addWidget(self.edit_send_reply_button)
        actions.addWidget(self.dismiss_reply_button)
        actions.addWidget(self.teach_rivescript_button)
        actions.addStretch()
        actions.addWidget(self.clear_reply_decisions_button)
        layout.addWidget(self.reply_decision_status_label)
        layout.addWidget(self.reply_review_table, 1)
        layout.addLayout(actions)
        self.ai_tabs.addTab(page, "Reply Review")
        self.send_reply_draft_button.clicked.connect(
            self._send_selected_reply_draft
        )
        self.edit_send_reply_button.clicked.connect(
            self._edit_and_send_reply_draft
        )
        self.dismiss_reply_button.clicked.connect(
            self._dismiss_reply_decision
        )
        self.teach_rivescript_button.clicked.connect(
            self._teach_selected_rivescript_reply
        )
        self.reply_review_table.itemSelectionChanged.connect(
            self._update_reply_review_actions
        )
        self.clear_reply_decisions_button.clicked.connect(
            lambda: self.reply_review_table.setRowCount(0)
        )
        self._update_reply_review_actions()

    def _build_rivescript_tab(self) -> None:
        page = QWidget(self.ai_tabs)
        self.rivescript_rules_page = page
        layout = QVBoxLayout(page)
        explanation = QLabel(
            "Reasoning still decides whether Sally should speak. When it "
            "approves a reply and an enabled rule matches, the streamer-written "
            "RiveScript response is used instead of an LLM draft."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.rivescript_rules_table = QTableWidget(0, 5)
        self.rivescript_rules_table.setHorizontalHeaderLabels(
            ("Enabled", "Name", "Trigger", "Reply", "Updated")
        )
        self.rivescript_rules_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.rivescript_rules_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.rivescript_rules_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        rule_header = self.rivescript_rules_table.horizontalHeader()
        rule_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        rule_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        rule_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        rule_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        rule_header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.rivescript_rules_table, 1)

        actions = QHBoxLayout()
        self.add_rivescript_rule_button = QPushButton("Add Rule")
        self.edit_rivescript_rule_button = QPushButton("Edit Selected")
        self.toggle_rivescript_rule_button = QPushButton("Disable Selected")
        self.delete_rivescript_rule_button = QPushButton("Delete Selected")
        actions.addWidget(self.add_rivescript_rule_button)
        actions.addWidget(self.edit_rivescript_rule_button)
        actions.addWidget(self.toggle_rivescript_rule_button)
        actions.addWidget(self.delete_rivescript_rule_button)
        actions.addStretch()
        layout.addLayout(actions)

        test_row = QHBoxLayout()
        self.rivescript_test_edit = QLineEdit()
        self.rivescript_test_edit.setPlaceholderText(
            "Test rule matching without sending to Twitch"
        )
        self.rivescript_test_button = QPushButton("Test Match")
        test_row.addWidget(self.rivescript_test_edit, 1)
        test_row.addWidget(self.rivescript_test_button)
        layout.addLayout(test_row)
        self.rivescript_status_label = QLabel()
        self.rivescript_status_label.setWordWrap(True)
        layout.addWidget(self.rivescript_status_label)
        self.ai_tabs.addTab(page, "RiveScript Rules")

        self.add_rivescript_rule_button.clicked.connect(
            self._add_rivescript_rule
        )
        self.edit_rivescript_rule_button.clicked.connect(
            self._edit_rivescript_rule
        )
        self.toggle_rivescript_rule_button.clicked.connect(
            self._toggle_rivescript_rule
        )
        self.delete_rivescript_rule_button.clicked.connect(
            self._delete_rivescript_rule
        )
        self.rivescript_test_button.clicked.connect(self._test_rivescript_rule)
        self.rivescript_test_edit.returnPressed.connect(
            self._test_rivescript_rule
        )
        self.rivescript_rules_table.itemSelectionChanged.connect(
            self._update_rivescript_rule_actions
        )
        self._refresh_rivescript_rules()

    def _refresh_rivescript_rules(self, selected_rule_id: str = "") -> None:
        table = self.rivescript_rules_table
        if not selected_rule_id:
            selected = self._selected_rivescript_rule()
            selected_rule_id = str(selected.get("id", "")) if selected else ""
        table.setRowCount(0)
        selected_row = -1
        for rule in self.rivescript_store.rules:
            row = table.rowCount()
            table.insertRow(row)
            updated = str(rule.get("updated_at") or rule.get("created_at") or "")
            try:
                updated = datetime.fromisoformat(
                    updated.replace("Z", "+00:00")
                ).astimezone().strftime("%Y-%m-%d %H:%M")
            except ValueError:
                updated = "--"
            values = (
                "Yes" if bool(rule.get("enabled", True)) else "No",
                str(rule.get("name", "")),
                str(rule.get("trigger", "")),
                str(rule.get("reply", "")),
                updated,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, str(rule.get("id", "")))
                table.setItem(row, column, item)
            if rule.get("id") == selected_rule_id:
                selected_row = row
        if selected_row >= 0:
            table.selectRow(selected_row)
        self.rivescript_status_label.setText(
            f"{len(self.rivescript_store.rules)} local rule(s)."
        )
        self._update_rivescript_rule_actions()

    def _selected_rivescript_rule(self) -> dict[str, object] | None:
        row = self.rivescript_rules_table.currentRow()
        item = self.rivescript_rules_table.item(row, 0) if row >= 0 else None
        rule_id = str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""
        return self.rivescript_store.get(rule_id) if rule_id else None

    def _update_rivescript_rule_actions(self) -> None:
        rule = self._selected_rivescript_rule()
        enabled = rule is not None
        self.edit_rivescript_rule_button.setEnabled(enabled)
        self.toggle_rivescript_rule_button.setEnabled(enabled)
        self.delete_rivescript_rule_button.setEnabled(enabled)
        self.toggle_rivescript_rule_button.setText(
            "Disable Selected"
            if rule is None or bool(rule.get("enabled", True))
            else "Enable Selected"
        )

    def _prompt_rivescript_rule(
        self,
        *,
        trigger: str = "",
        reply: str = "",
        name: str = "",
    ) -> tuple[str, str, str] | None:
        trigger, accepted = QInputDialog.getText(
            self,
            "RiveScript Trigger",
            "Trigger (RiveScript patterns such as 'hello *' are supported)",
            QLineEdit.EchoMode.Normal,
            trigger,
        )
        if not accepted or not trigger.strip():
            return None
        reply, accepted = QInputDialog.getMultiLineText(
            self, "RiveScript Reply", "Reply", reply
        )
        if not accepted or not reply.strip():
            return None
        name, accepted = QInputDialog.getText(
            self,
            "Rule Name",
            "Name",
            QLineEdit.EchoMode.Normal,
            name,
        )
        if not accepted:
            return None
        return trigger, reply, name

    def _add_rivescript_rule(self) -> None:
        values = self._prompt_rivescript_rule()
        if values is None:
            return
        self._save_new_rivescript_rule(*values)

    def _save_new_rivescript_rule(
        self, trigger: str, reply: str, name: str
    ) -> bool:
        try:
            rule_id = self.rivescript_store.add(trigger, reply, name=name)
            self.rivescript_engine.rebuild()
        except (OSError, ValueError) as error:
            self.rivescript_status_label.setText(f"Could not save rule: {error}")
            return False
        self._refresh_rivescript_rules(rule_id)
        self.rivescript_status_label.setText("RiveScript rule saved and enabled.")
        return True

    def _edit_rivescript_rule(self) -> None:
        rule = self._selected_rivescript_rule()
        if rule is None:
            return
        values = self._prompt_rivescript_rule(
            trigger=str(rule.get("trigger", "")),
            reply=str(rule.get("reply", "")),
            name=str(rule.get("name", "")),
        )
        if values is None:
            return
        try:
            self.rivescript_store.update(
                str(rule.get("id", "")),
                trigger=values[0],
                reply=values[1],
                name=values[2],
            )
            self.rivescript_engine.rebuild()
        except (OSError, ValueError) as error:
            self.rivescript_status_label.setText(f"Could not update rule: {error}")
            return
        self._refresh_rivescript_rules(str(rule.get("id", "")))

    def _toggle_rivescript_rule(self) -> None:
        rule = self._selected_rivescript_rule()
        if rule is None:
            return
        rule_id = str(rule.get("id", ""))
        try:
            self.rivescript_store.set_enabled(
                rule_id, not bool(rule.get("enabled", True))
            )
            self.rivescript_engine.rebuild()
        except OSError as error:
            self.rivescript_status_label.setText(f"Could not update rule: {error}")
            return
        self._refresh_rivescript_rules(rule_id)

    def _delete_rivescript_rule(self) -> None:
        rule = self._selected_rivescript_rule()
        if rule is None:
            return
        if QMessageBox.question(
            self,
            "Delete RiveScript Rule",
            f"Delete the rule '{rule.get('name', 'Rule')}'?",
        ) is not QMessageBox.StandardButton.Yes:
            return
        try:
            self.rivescript_store.delete(str(rule.get("id", "")))
            self.rivescript_engine.rebuild()
        except OSError as error:
            self.rivescript_status_label.setText(f"Could not delete rule: {error}")
            return
        self._refresh_rivescript_rules()

    def _test_rivescript_rule(self) -> None:
        text = self.rivescript_test_edit.text().strip()
        if not text:
            return
        match = self.rivescript_engine.match("rivescript-test", text)
        self.rivescript_status_label.setText(
            f"Matched: {match[1]}" if match else "No enabled rule matched."
        )

    def _build_ai_test_report_tab(self) -> None:
        page = QWidget(self.ai_tabs)
        layout = QVBoxLayout(page)
        privacy = QLabel(
            "Anonymous diagnostics only: no chat text, usernames, Twitch IDs, "
            "or drafted replies are saved in this report."
        )
        privacy.setWordWrap(True)
        layout.addWidget(privacy)

        controls = QHBoxLayout()
        self.ai_test_report_range = QComboBox()
        self.ai_test_report_range.addItems(("Current app run", "All retained"))
        self.ai_test_new_run_button = QPushButton("Start New Test")
        self.ai_test_clear_button = QPushButton("Clear Report")
        controls.addWidget(QLabel("Show"))
        controls.addWidget(self.ai_test_report_range)
        controls.addStretch()
        controls.addWidget(self.ai_test_new_run_button)
        controls.addWidget(self.ai_test_clear_button)
        layout.addLayout(controls)

        summary = QGroupBox("Results")
        summary_layout = QGridLayout(summary)
        self.ai_test_summary_labels: dict[str, QLabel] = {}
        for column, (key, title) in enumerate(
            (
                ("total", "Evaluated"),
                ("sent", "Sent"),
                ("ignored", "Ignored"),
                ("missed", "Missed"),
                ("blocked", "Blocked"),
                ("failed", "Failed"),
                ("llm", "LLM"),
                ("rivescript", "RiveScript"),
                ("average_latency_ms", "Avg latency"),
            )
        ):
            title_label = QLabel(title)
            value_label = QLabel("0")
            value_label.setStyleSheet("font-size: 18px; font-weight: 600;")
            summary_layout.addWidget(title_label, 0, column)
            summary_layout.addWidget(value_label, 1, column)
            self.ai_test_summary_labels[key] = value_label
        layout.addWidget(summary)

        self.ai_test_report_table = QTableWidget(0, 7)
        self.ai_test_report_table.setHorizontalHeaderLabels(
            (
                "Time",
                "Outcome",
                "Source",
                "Expected",
                "Latency",
                "Confidence",
                "Reason",
            )
        )
        self.ai_test_report_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        self.ai_test_report_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        report_header = self.ai_test_report_table.horizontalHeader()
        for column in range(6):
            report_header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        report_header.setSectionResizeMode(6, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.ai_test_report_table, 1)
        self.ai_tabs.addTab(page, "Test Report")

        self.ai_test_report_range.currentIndexChanged.connect(
            lambda _index: self._refresh_ai_test_report()
        )
        self.ai_test_new_run_button.clicked.connect(self._start_new_ai_test)
        self.ai_test_clear_button.clicked.connect(self._clear_ai_test_report)
        self._refresh_ai_test_report()

    def _refresh_ai_test_report(self) -> None:
        current_only = self.ai_test_report_range.currentIndex() == 0
        summary = self.test_report_store.summary(current_only)
        for key, label in self.ai_test_summary_labels.items():
            value = summary.get(key, 0)
            label.setText(
                f"{int(value) / 1000:.1f}s"
                if key == "average_latency_ms"
                else str(value)
            )
        events = self.test_report_store.selected_events(current_only)
        self.ai_test_report_table.setRowCount(0)
        for event in reversed(events[-500:]):
            row = self.ai_test_report_table.rowCount()
            self.ai_test_report_table.insertRow(row)
            recorded_at = str(event.get("recorded_at", ""))
            try:
                display_time = datetime.fromisoformat(
                    recorded_at.replace("Z", "+00:00")
                ).astimezone().strftime("%H:%M:%S")
            except ValueError:
                display_time = "--"
            latency_ms = int(event.get("latency_ms", 0))
            values = (
                display_time,
                str(event.get("outcome", "unknown")).replace("_", " ").title(),
                str(event.get("response_source", "llm")).title(),
                "Yes" if bool(event.get("response_expected")) else "No",
                f"{latency_ms / 1000:.1f}s",
                f"{float(event.get('confidence', 0.0)):.0%}",
                str(event.get("reason", "unknown")).replace("_", " "),
            )
            for column, value in enumerate(values):
                self.ai_test_report_table.setItem(
                    row, column, QTableWidgetItem(value)
                )

    def _start_new_ai_test(self) -> None:
        self.test_report_store.start_new_session()
        self.ai_test_report_range.setCurrentIndex(0)
        self._refresh_ai_test_report()

    def _clear_ai_test_report(self) -> None:
        try:
            self.test_report_store.clear()
        except OSError as error:
            Logger.warning(
                f"Could not clear anonymous AI test diagnostics: {error}",
                source="AI",
            )
        self._refresh_ai_test_report()

    def _build_training_tab(self) -> None:
        page = QWidget(self.ai_tabs)
        layout = QVBoxLayout(page)
        notice = QLabel(
            "Only messages from viewers who type !sallytrain on are captured. "
            "Samples stay local, omit usernames and Twitch IDs, and remain "
            "pending until you review their intent label."
        )
        notice.setWordWrap(True)
        self.training_status_label = QLabel()
        self.training_table = QTableWidget(0, 6)
        self.training_table.setHorizontalHeaderLabels(
            ("Message", "Model label", "Decision", "State", "Label", "Reviewed")
        )
        self.training_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        self.training_table.setSelectionMode(
            QTableWidget.SelectionMode.SingleSelection
        )
        self.training_table.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers
        )
        header = self.training_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 6):
            header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        controls = QHBoxLayout()
        self.training_label_combo = QComboBox()
        self.training_label_combo.addItems(TrainingStore.LABELS)
        self.save_training_label_button = QPushButton("Save Reviewed Label")
        self.delete_training_example_button = QPushButton("Delete Selected")
        self.clear_training_examples_button = QPushButton("Delete All")
        controls.addWidget(QLabel("Correct intent"))
        controls.addWidget(self.training_label_combo)
        controls.addWidget(self.save_training_label_button)
        controls.addWidget(self.delete_training_example_button)
        controls.addStretch()
        controls.addWidget(self.clear_training_examples_button)
        layout.addWidget(notice)
        layout.addWidget(self.training_status_label)
        layout.addWidget(self.training_table, 1)
        layout.addLayout(controls)
        self.ai_tabs.addTab(page, "Training")
        self.save_training_label_button.clicked.connect(
            self._save_training_label
        )
        self.delete_training_example_button.clicked.connect(
            self._delete_training_example
        )
        self.clear_training_examples_button.clicked.connect(
            self._clear_training_examples
        )
        self.training_table.currentCellChanged.connect(
            self._select_training_example
        )
        self._refresh_training_examples()

    def _build_personality_tab(self) -> None:
        page = QWidget(self.ai_tabs)
        layout = QVBoxLayout(page)
        introduction = QLabel(
            "Describe how Sally should sound and behave in chat. These rules "
            "are included in every live reply decision."
        )
        introduction.setWordWrap(True)
        self.ai_personality_edit = QTextEdit()
        self.ai_personality_edit.setPlaceholderText(
            "Example: Playful, confident, concise, and fond of dry humor..."
        )
        self.ai_personality_edit.setMinimumHeight(180)
        language_group = QGroupBox("Language Permissions")
        language_layout = QVBoxLayout(language_group)
        self.ai_allow_mild_profanity_check = QCheckBox(
            "Allow occasional mild profanity"
        )
        self.ai_allow_strong_profanity_check = QCheckBox(
            "Allow strong profanity / foul language"
        )
        warning = QLabel(
            "Slurs, hateful language, harassment, and targeted sexual language "
            "remain prohibited regardless of these choices."
        )
        warning.setWordWrap(True)
        language_layout.addWidget(self.ai_allow_mild_profanity_check)
        language_layout.addWidget(self.ai_allow_strong_profanity_check)
        language_layout.addWidget(warning)
        self.ai_personality_save_button = QPushButton("Save Personality")
        self.ai_personality_status_label = QLabel("")
        layout.addWidget(introduction)
        layout.addWidget(self.ai_personality_edit, 1)
        layout.addWidget(language_group)
        layout.addWidget(self.ai_personality_save_button)
        layout.addWidget(self.ai_personality_status_label)
        self.ai_tabs.addTab(page, "Personality")
        self.ai_personality_page = page
        self.ai_allow_strong_profanity_check.toggled.connect(
            self._strong_profanity_toggled
        )
        self.ai_personality_save_button.clicked.connect(
            self._save_personality
        )

    @Slot(bool)
    def _strong_profanity_toggled(self, enabled: bool) -> None:
        if enabled:
            self.ai_allow_mild_profanity_check.setChecked(True)

    @Slot()
    def _save_personality(self) -> None:
        self.save_settings()
        if self.ui.settingsStatusLabel.text() == "Settings saved.":
            self.ai_personality_status_label.setText("Personality saved.")

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
            self.ui.twitchSendEdit.setPlaceholderText(
                "Sign in with your channel account to send a message"
            )
        elif signed_in:
            scopes = set(self.twitch_auth.token.scopes) if self.twitch_auth.token else set()
            self.run_ad_button.setEnabled("channel:edit:commercial" in scopes)
            self.snooze_ad_button.setEnabled("channel:manage:ads" in scopes)
            self.ui.twitchChannelEdit.setText(detail)
            self.ui.twitchSendEdit.setPlaceholderText(
                f"Send a message as @{detail}"
            )
            self.twitch_status_bar_label.setText(f"Twitch: @{detail}")
            if self.twitch_service.state is not TwitchConnectionState.CONNECTED:
                self.connect_twitch()
            self.refresh_stream_companion()

    @Slot(object, str)
    def handle_twitch_bot_auth_changed(
        self,
        state: TwitchAuthState,
        detail: str,
    ) -> None:
        signed_in = state is TwitchAuthState.SIGNED_IN
        waiting = state is TwitchAuthState.WAITING
        missing = (
            self.twitch_bot_auth.missing_scopes(set(TWITCH_BOT_SCOPES))
            if signed_in
            else set()
        )
        if signed_in:
            status = f"@{detail}"
            if missing:
                status += " — update permissions: " + ", ".join(sorted(missing))
            broadcaster_token = self.twitch_auth.token
            bot_token = self.twitch_bot_auth.token
            if (
                broadcaster_token is not None
                and bot_token is not None
                and broadcaster_token.user_id == bot_token.user_id
            ):
                status += " — same account as broadcaster"
        else:
            status = detail
        self.twitch_bot_account_status_label.setText(status)
        self.twitch_bot_sign_in_button.setEnabled(
            (not signed_in or bool(missing)) and not waiting
        )
        self.twitch_bot_sign_in_button.setText(
            "Update Bot Permissions"
            if signed_in and missing
            else "Sign in with a Bot Account"
        )
        self.twitch_bot_sign_out_button.setEnabled(signed_in or waiting)
        if state is TwitchAuthState.ERROR:
            self.handle_twitch_error(f"Bot sign-in failed: {detail}")

        # EventSub chat subscriptions are tied to the reading identity. Reopen
        # the socket when that identity changes, while keeping channel controls
        # on the broadcaster token.
        if (
            self.twitch_auth.token is not None
            and self.twitch_service.channel
            and state
            in {
                TwitchAuthState.SIGNED_IN,
                TwitchAuthState.SIGNED_OUT,
            }
        ):
            channel = self.twitch_service.channel
            self.twitch_service.disconnect()
            self.twitch_service.connect(channel)

    @Slot()
    def disconnect_twitch(self) -> None:
        self.ui.twitchErrorLabel.clear()
        self.twitch_service.disconnect()

    @Slot()
    def send_twitch_message(self) -> None:
        self.ui.twitchErrorLabel.clear()
        if self.twitch_service.send_message(
            self.ui.twitchSendEdit.text(),
            as_bot=False,
        ):
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
        configured_bot_id = (
            self.twitch_bot_auth.token.user_id
            if self.twitch_bot_auth.token is not None
            else ""
        )
        is_bot = any(
            badge.set_id in {"bot", "verified-bot"}
            for badge in chat_message.badges
        ) or (
            bool(configured_bot_id)
            and chat_message.user_id == configured_bot_id
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
        training_command = self._handle_sally_training_command(
            chat_message, is_bot
        )
        memory_command = self._handle_sally_memory_command(chat_message, is_bot)
        if (
            self.settings.ai_viewer_memory_enabled
            and chat_message.user_id
            and not is_bot
            and not memory_command
            and not training_command
        ):
            if self.chatter_history.has_memory_consent(chat_message.user_id):
                self.chatter_history.record_memory_stream(
                    chat_message.user_id,
                    self.current_memory_stream_id,
                )
                self.chatter_history.record_daily_memory(
                    chat_message.user_id,
                    speaker="viewer",
                    viewer=chat_message.username,
                    message=chat_message.text,
                    timestamp=chat_message.received_at,
                    stream_id=self.current_memory_stream_id,
                )
            self._maybe_promote_sally_memory()
        if not memory_command and not training_command:
            if not is_bot:
                self.viewer_messages_since_sally_reply += 1
            self._buffer_message_for_memory_reasoning(chat_message, is_bot)
            self._queue_response_decision(chat_message, is_bot)
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

        context_url = "sally-chat-context://message?" + urlencode(
            {
                "user_id": chat_message.user_id,
                "user_name": chat_message.username,
                "message_id": chat_message.message_id,
            }
        )

        self.ui.twitchChatOutput.append(
            "<div class='chat-message' "
            f"data-user-id='{escape(chat_message.user_id)}' "
            f"data-user-name='{escape(chat_message.username)}' "
            f"data-message-id='{escape(chat_message.message_id)}' "
            "style='margin: 4px 2px 7px 2px;'>"
            f"<a class='chat-context-target' href='{escape(context_url)}'></a>"
            "<span class='chat-content'>"
            f"{timestamp_html}"
            f"{badges_html}"
            f"<span style='color: {username_color}; font-weight: 600;'>"
            f"{username}:</span>"
            f" <span style='color: #efeff1;'>{message}</span>"
            "</span>"
            "</div>"
        )
        self.twitch_message_count += 1
        if self.session_tracker.observe_message():
            self._refresh_session_history()
        self.twitch_chat_has_content = True
        self._update_twitch_chat_count()

        scroll_bar = self.ui.twitchChatOutput.verticalScrollBar()
        scroll_bar.setValue(scroll_bar.maximum())

    def _handle_sally_memory_command(
        self,
        chat_message: TwitchMessage,
        is_bot: bool,
    ) -> bool:
        text = " ".join(chat_message.text.casefold().strip().split())
        if is_bot or not text.startswith("!sallymemory"):
            return False
        user_id = chat_message.user_id
        if not user_id:
            return True
        command = text.removeprefix("!sallymemory").strip()
        mention = f"@{chat_message.username}"
        if (
            not self.settings.ai_viewer_memory_enabled
            and command not in {"off", "delete", "confirmdelete", "status"}
        ):
            reply = (
                f"{mention} Sally Memory is currently disabled by the streamer. "
                "No viewer memories are being collected."
            )
        elif command in {"", "help", "info"}:
            reply = (
                f"{mention} Sally Memory is optional. I can keep today's chat "
                "context until the daily reset/end of a stream. After 5 opted-in "
                "streams I may propose non-sensitive keynotes for review. Use "
                "!sallymemory on, !sallymemory off, or !sallymemory delete."
            )
        elif command == "on":
            already_enabled = self.chatter_history.has_memory_consent(user_id)
            self.chatter_history.opt_in_memory(
                user_id,
                chat_message.username,
                consented_at=chat_message.received_at,
            )
            count = self.chatter_history.record_memory_stream(
                user_id, self.current_memory_stream_id
            )
            self._save_chatter_history()
            reply = (
                f"{mention} Sally Memory is already on ({count}/5 opted-in streams)."
                if already_enabled
                else f"{mention} Sally Memory is on. Today's context will expire at "
                f"the configured reset, unless this stream is still live. Regular "
                f"keynotes unlock after 5 opted-in streams ({count}/5)."
            )
            self._refresh_memory_viewer_list()
        elif command == "off":
            if user_id in self.chatter_history.records:
                self.chatter_history.opt_out_memory(user_id)
            self._clear_viewer_runtime_memory(user_id)
            self._save_chatter_history()
            reply = (
                f"{mention} Sally Memory is off and your saved conversation and "
                "keynotes were erased. A minimal opt-out preference remains."
            )
        elif command == "delete":
            self.pending_memory_deletions[user_id] = monotonic() + 120
            reply = (
                f"{mention} this erases all Sally data about you, including consent. "
                "Type !sallymemory confirmdelete within 2 minutes to confirm."
            )
        elif command == "confirmdelete":
            if monotonic() > self.pending_memory_deletions.get(user_id, 0.0):
                reply = f"{mention} no active deletion request. Use !sallymemory delete first."
            else:
                self.pending_memory_deletions.pop(user_id, None)
                try:
                    self.activity_history.delete_user(
                        user_id, chat_message.username
                    )
                    self.release_controller.scrub_viewer_data(
                        user_id, chat_message.username
                    )
                except OSError as error:
                    Logger.warning(
                        f"Could not erase viewer activity history: {error}",
                        source="DATA",
                    )
                self.chatter_history.delete_viewer_data(user_id)
                self._clear_viewer_runtime_memory(user_id)
                self._save_chatter_history()
                self._refresh_memory_viewer_list()
                reply = f"{mention} all of your locally stored Sally data was deleted."
        elif command == "status":
            record = self.chatter_history.records.get(user_id)
            enabled = self.chatter_history.has_memory_consent(user_id)
            streams = len(record.memory_stream_ids) if record is not None else 0
            notes = len(record.memories) if enabled and record is not None else 0
            reply = (
                f"{mention} Sally Memory is "
                f"{'disabled globally' if not self.settings.ai_viewer_memory_enabled else 'on' if enabled else 'off'}; "
                f"{streams}/5 opted-in streams and {notes} saved keynote(s)."
            )
        else:
            reply = f"{mention} try !sallymemory, on, off, status, or delete."
        self.twitch_service.send_message(reply)
        return True

    def _handle_sally_training_command(
        self,
        chat_message: TwitchMessage,
        is_bot: bool,
    ) -> bool:
        text = " ".join(chat_message.text.casefold().strip().split())
        if is_bot or not text.startswith("!sallytrain"):
            return False
        user_id = chat_message.user_id
        if not user_id:
            return True
        command = text.removeprefix("!sallytrain").strip()
        mention = f"@{chat_message.username}"
        if command == "on":
            if not self.settings.ai_training_capture_enabled:
                reply = (
                    f"{mention} Sally's training capture is currently disabled."
                )
            else:
                self.training_opted_in_users.add(user_id)
                reply = (
                    f"{mention} training capture is on for this session. Your "
                    "messages and Sally's intent decisions may be saved locally "
                    "without your username. Use !sallytrain off or delete anytime."
                )
        elif command == "off":
            self.training_opted_in_users.discard(user_id)
            reply = (
                f"{mention} training capture is off. Existing samples remain; "
                "use !sallytrain delete to erase them."
            )
        elif command == "delete":
            self.training_opted_in_users.discard(user_id)
            try:
                removed = self.training_store.delete_participant(user_id)
            except OSError as error:
                Logger.warning(
                    f"Could not delete local training samples: {error}",
                    source="AI",
                )
                removed = 0
            self._refresh_training_examples()
            reply = (
                f"{mention} training capture is off and {removed} saved "
                "training sample(s) were deleted."
            )
        elif command == "status":
            enabled = (
                self.settings.ai_training_capture_enabled
                and user_id in self.training_opted_in_users
            )
            reply = (
                f"{mention} your training capture is "
                f"{'on' if enabled else 'off'} for this session."
            )
        else:
            reply = (
                f"{mention} Sally training is optional and separate from memory. "
                "Use !sallytrain on, off, status, or delete."
            )
        self.twitch_service.send_message(reply)
        return True

    def _maybe_announce_training_capture(self) -> None:
        if not (
            self.settings.ai_training_capture_enabled
            and self.settings.ai_training_notice_enabled
            and self.twitch_service.state is TwitchConnectionState.CONNECTED
            and self.current_memory_stream_id
        ):
            return
        context = self.current_memory_stream_id
        if (
            self.settings.ai_training_notice_stream_id == context
            or self.training_notice_attempt_context == context
        ):
            return
        self.training_notice_attempt_context = context
        sent, pinned = self.twitch_service.send_pinned_message(
            self.settings.ai_training_notice_message
        )
        if not sent:
            Logger.warning(
                "The once-per-stream training notice could not be sent.",
                source="TWITCH",
            )
            return
        self.settings.ai_training_notice_stream_id = context
        try:
            self.settings_store.save(self.settings)
        except OSError as error:
            Logger.warning(
                f"Could not remember the announced training stream: {error}",
                source="SETTINGS",
            )
        Logger.info(
            "Posted the once-per-stream training notice"
            + (" and pinned it." if pinned else "; pinning was unavailable."),
            source="TWITCH",
        )

    def _clear_viewer_runtime_memory(self, user_id: str) -> None:
        self.memory_message_buffers.pop(user_id, None)
        self.memory_extraction_retry_after.pop(user_id, None)
        self.response_decision_queue = deque(
            (item for item in self.response_decision_queue if item.user_id != user_id),
            maxlen=100,
        )
        self.recent_ai_chat = deque(
            (
                item for item in self.recent_ai_chat
                if str(item.get("user_id", "")) != user_id
            ),
            maxlen=100,
        )

    def _maybe_promote_sally_memory(self) -> None:
        if not (
            self.settings.ai_viewer_memory_enabled
            and self.settings.ai_memory_promo_enabled
            and self.current_memory_stream_id
            and self.twitch_service.state is TwitchConnectionState.CONNECTED
        ):
            return
        self.memory_promo_message_count += 1
        if (
            self.memory_promo_message_count
            < self.settings.ai_memory_promo_interval_messages
            or monotonic() - self.last_memory_promo_at < 3600
        ):
            return
        if self.twitch_service.send_message(
            "Sally can remember today's conversations for viewers who choose to opt in. "
            "Type !sallymemory to learn more or manage your data."
        ):
            self.memory_promo_message_count = 0
            self.last_memory_promo_at = monotonic()

    def _buffer_message_for_memory_reasoning(
        self,
        chat_message: TwitchMessage,
        is_bot: bool,
    ) -> None:
        if not (
            self.settings.local_ai_enabled
            and self.settings.ai_viewer_memory_enabled
            and self.settings.ai_memory_reasoning_enabled
        ):
            return
        user_id = chat_message.user_id
        if (
            not user_id
            or user_id == self.twitch_service.broadcaster_user_id
            or is_bot
            or user_id in self.known_bot_user_ids
        ):
            return
        record = self.chatter_history.records.get(user_id)
        if (
            record is None
            or not self.chatter_history.can_create_keynotes(user_id)
            or record.is_bot
            or record.manual_group == "Bots"
        ):
            return
        text = " ".join(chat_message.text.strip().split())[:500]
        if len(text) < 4 or text.startswith(("/", "!")):
            return
        buffer = self.memory_message_buffers.setdefault(
            user_id,
            deque(maxlen=30),
        )
        timestamp = chat_message.received_at.astimezone(
            timezone.utc
        ).isoformat()
        buffer.append(
            BufferedChatMessage(
                buffer_id=(
                    chat_message.message_id
                    or f"local-{uuid4().hex}"
                ),
                message_id=chat_message.message_id,
                user_id=user_id,
                user_name=chat_message.username,
                text=text,
                timestamp=timestamp,
            )
        )
        self._start_memory_extraction_if_ready(user_id)

    def _start_memory_extraction_if_ready(self, user_id: str) -> None:
        buffer = self.memory_message_buffers.get(user_id)
        if (
            buffer is None
            or len(buffer) < self.settings.ai_memory_message_threshold
            or user_id in self.memory_extraction_in_flight
            or monotonic()
            < self.memory_extraction_retry_after.get(user_id, 0.0)
        ):
            return
        record = self.chatter_history.records.get(user_id)
        if record is None or not self.chatter_history.can_create_keynotes(user_id):
            self.memory_message_buffers.pop(user_id, None)
            return
        messages = tuple(buffer)
        existing = tuple(
            str(memory.get("text", ""))
            for memory in record.memories
            if memory.get("status", "approved") in {"approved", "pending"}
            and not memory.get("archived", False)
        )
        worker = MemoryExtractionWorker(
            user_id,
            record.user_name,
            messages,
            existing,
            self.settings.local_ai_endpoint,
            self.settings.local_ai_model,
        )
        worker.signals.completed.connect(self._apply_memory_extraction)
        worker.signals.failed.connect(self._memory_extraction_failed)
        self.memory_extraction_in_flight.add(user_id)
        self.memory_reasoning_status_label.setText(
            f"Analyzing recent chat from {record.user_name} locally…"
        )
        self.memory_reasoning_thread_pool.start(worker)

    @Slot(object)
    def _apply_memory_extraction(
        self,
        result: MemoryExtractionResult,
    ) -> None:
        self.memory_extraction_in_flight.discard(result.user_id)
        self.memory_extraction_retry_after.pop(result.user_id, None)
        self._remove_analyzed_memory_messages(
            result.user_id,
            result.buffer_ids,
        )
        record = self.chatter_history.records.get(result.user_id)
        if (
            record is None
            or not self.chatter_history.can_create_keynotes(result.user_id)
            or not self.settings.local_ai_enabled
            or not self.settings.ai_viewer_memory_enabled
            or not self.settings.ai_memory_reasoning_enabled
        ):
            return
        added = 0
        for proposal in result.proposals:
            before = len(record.memories)
            self.chatter_history.propose_memory(
                result.user_id,
                proposal.text,
                proposal.category,
                confidence=proposal.confidence,
                evidence=proposal.evidence,
                key=proposal.key,
                source=f"local-ai:{self.settings.local_ai_model}",
            )
            if len(record.memories) > before:
                added += 1
        if result.proposals:
            self._save_chatter_history()
            self._refresh_memory_viewer_list()
            current = self.memory_viewer_list.currentItem()
            if (
                current is not None
                and str(current.data(Qt.ItemDataRole.UserRole))
                == result.user_id
            ):
                self._show_memory_viewer(current, None)
        self.memory_reasoning_status_label.setText(
            f"Analyzed {len(result.buffer_ids)} messages from "
            f"{result.user_name}; found {len(result.proposals)} candidate(s) "
            f"and added {added} pending proposal(s)."
        )
        self._start_memory_extraction_if_ready(result.user_id)

    @Slot(str, object, str)
    def _memory_extraction_failed(
        self,
        user_id: str,
        _buffer_ids: object,
        error: str,
    ) -> None:
        self.memory_extraction_in_flight.discard(user_id)
        self.memory_extraction_retry_after[user_id] = monotonic() + 300
        self.memory_reasoning_status_label.setText(
            "Local memory reasoning could not run; it will retry after more chat."
        )
        Logger.warning(
            f"Local memory extraction failed: {error}",
            source="AI",
        )

    def _remove_analyzed_memory_messages(
        self,
        user_id: str,
        buffer_ids: tuple[str, ...],
    ) -> None:
        buffer = self.memory_message_buffers.get(user_id)
        if buffer is None:
            return
        analyzed = set(buffer_ids)
        remaining = [
            message for message in buffer
            if message.buffer_id not in analyzed
        ]
        if remaining:
            self.memory_message_buffers[user_id] = deque(
                remaining,
                maxlen=30,
            )
        else:
            self.memory_message_buffers.pop(user_id, None)

    def _queue_response_decision(
        self,
        chat_message: TwitchMessage,
        is_bot: bool,
    ) -> None:
        if not (
            self.settings.local_ai_enabled
            and self.settings.ai_response_decisions_enabled
        ):
            return
        user_id = chat_message.user_id
        text = " ".join(chat_message.text.strip().split())[:500]
        received_at = chat_message.received_at.astimezone(timezone.utc)
        directed_at_sally, reply_to_sally = self._sally_address_signals(
            chat_message, text
        )
        (
            conversation_continuation,
            previous_sally_reply,
            response_expected,
        ) = self._conversation_context(user_id, text, received_at)
        third_person_reference = bool(
            conversation_continuation
            and not directed_at_sally
            and re.search(
                r"\b(?:she(?:['’]?s)?|her|hers)\b",
                text,
                flags=re.IGNORECASE,
            )
        )
        addressed_to_other = self._message_addresses_other(
            chat_message, text, user_id
        )
        if conversation_continuation and (
            third_person_reference or addressed_to_other
        ):
            self.closed_ai_conversations[user_id] = received_at
            conversation_continuation = False
            previous_sally_reply = ""
            response_expected = False
        if (
            not user_id
            or is_bot
            or user_id in self.known_bot_user_ids
        ):
            return
        if not text:
            return
        viewer_context = (
            build_viewer_context(
                self.chatter_history,
                user_id,
                text,
                limit=5,
            )
            if self.settings.ai_viewer_memory_enabled
            else {}
        )
        scripted_match = self.rivescript_engine.match(user_id, text)
        request = ResponseMessage(
            request_id=uuid4().hex,
            message_id=chat_message.message_id,
            user_id=user_id,
            user_name=chat_message.username,
            text=text,
            received_at=received_at.isoformat(),
            memory_summary=str(viewer_context.get("summary", "")),
            memories=tuple(
                str(memory.get("text", ""))
                for memory in viewer_context.get("memories", [])
                if isinstance(memory, dict)
            ),
            conversation_continuation=conversation_continuation,
            previous_sally_reply=previous_sally_reply,
            response_expected=response_expected,
            directed_at_sally=directed_at_sally,
            reply_to_sally=reply_to_sally,
            third_person_reference=third_person_reference,
            addressed_to_other=addressed_to_other,
            scripted_reply=scripted_match[1] if scripted_match else "",
            scripted_rule_id=scripted_match[0] if scripted_match else "",
        )
        if len(self.response_decision_queue) == self.response_decision_queue.maxlen:
            dropped = self.response_decision_queue.popleft()
            self._add_reply_decision(
                ResponseDecision(
                    request_id=dropped.request_id,
                    message_id=dropped.message_id,
                    user_id=dropped.user_id,
                    user_name=dropped.user_name,
                    source_text=dropped.text,
                    received_at=dropped.received_at,
                    decision="ignore",
                    reply="",
                    reason="Decision queue reached its safety limit.",
                    confidence=1.0,
                )
            )
        self.response_decision_queue.append(request)
        self.recent_ai_chat.append(
            {
                "speaker": "viewer",
                "viewer": chat_message.username,
                "message": text,
                "user_id": user_id,
                "timestamp": received_at.isoformat(),
            }
        )
        self._start_next_response_batch()

    def _sally_address_signals(
        self,
        chat_message: TwitchMessage,
        text: str,
    ) -> tuple[bool, bool]:
        """Detect explicit names, mentions, and Twitch replies to Sally."""

        bot_token = self.twitch_bot_auth.token
        bot_user_id = str(getattr(bot_token, "user_id", "") or "")
        bot_login = str(getattr(bot_token, "login", "") or "").casefold()
        aliases = {"sally"}
        if bot_login:
            aliases.add(bot_login)
        directed = any(
            self._text_directly_addresses_alias(text, alias)
            for alias in aliases
        )
        directed = directed or any(
            fragment.mention is not None
            and (
                (bot_user_id and fragment.mention.user_id == bot_user_id)
                or fragment.mention.user_login.casefold() in aliases
            )
            for fragment in chat_message.fragments
        )
        reply = chat_message.reply
        reply_to_sally = bool(
            reply is not None
            and (
                (bot_user_id and reply.parent_user_id == bot_user_id)
                or reply.parent_user_login.casefold() in aliases
            )
        )
        return directed or reply_to_sally, reply_to_sally

    @staticmethod
    def _text_directly_addresses_alias(text: str, alias: str) -> bool:
        """Recognize common vocative phrasing without treating mentions as speech."""

        escaped = re.escape(alias)
        patterns = (
            # "Sally ...", "hey Sally", and "what do you think, Sally?"
            rf"^\s*@?{escaped}(?:\b|[,:!?])",
            rf"(?:^|\s)(?:hey|hi|hello|yo|ok|okay)\s+@?{escaped}\b",
            rf"[,;]\s*@?{escaped}[.!?]*\s*$",
            # Natural questions such as "are you sassy Sally?"
            rf"\b(?:you|your|yours|yourself)\b.*\b@?{escaped}[.!?]*\s*$",
            # Direct requests such as "say hello Sally".
            rf"^\s*(?:please\s+)?(?:say|answer|explain|show|give|make|tell)\b"
            rf".*\b@?{escaped}[.!?]*\s*$",
            # Conversational endings such as "thanks Sally, bye".
            rf"\b(?:thanks?|thank\s+you)\s*,?\s+@?{escaped}\b"
            rf"(?:\s*[,;:]\s*(?:bye|goodbye|night|later))?",
        )
        return any(
            re.search(pattern, text, flags=re.IGNORECASE)
            for pattern in patterns
        )

    def _message_addresses_other(
        self,
        chat_message: TwitchMessage,
        text: str,
        user_id: str,
    ) -> bool:
        bot_token = self.twitch_bot_auth.token
        bot_user_id = str(getattr(bot_token, "user_id", "") or "")
        bot_login = str(getattr(bot_token, "login", "") or "").casefold()
        if chat_message.reply is not None:
            parent_id = chat_message.reply.parent_user_id
            parent_login = chat_message.reply.parent_user_login.casefold()
            if (
                parent_id
                and parent_id != user_id
                and parent_id != bot_user_id
                and parent_login not in {"sally", bot_login}
            ):
                return True
        for fragment in chat_message.fragments:
            mention = fragment.mention
            if mention is None:
                continue
            if (
                mention.user_id not in {user_id, bot_user_id}
                and mention.user_login.casefold() not in {"sally", bot_login}
            ):
                return True
        aliases = {"sally", bot_login, chat_message.username.casefold()}
        for match in re.finditer(
            r"\b(?:eh|hey|yo)\s*[,;:]?\s*@?([a-z0-9_]{2,25})\b",
            text,
            flags=re.IGNORECASE,
        ):
            if match.group(1).casefold() not in aliases:
                return True
        for other_id, record in self.chatter_history.records.items():
            if other_id in {user_id, bot_user_id}:
                continue
            other_name = str(getattr(record, "user_name", "") or "").strip()
            if not other_name:
                continue
            if re.search(
                rf"(?:@|\b(?:eh|hey|yo)\s+){re.escape(other_name)}\b",
                text,
                flags=re.IGNORECASE,
            ):
                return True
        return False

    def _conversation_context(
        self,
        user_id: str,
        text: str,
        received_at: datetime,
    ) -> tuple[bool, str, bool]:
        """Return recent same-viewer Sally context for a natural follow-up."""

        if not user_id:
            return False, "", False
        for item in reversed(self.recent_ai_chat):
            if (
                str(item.get("speaker", "")).casefold() != "sally"
                or str(item.get("user_id", "")) != user_id
            ):
                continue
            previous_reply = str(item.get("message", "")).strip()
            try:
                replied_at = datetime.fromisoformat(
                    str(item.get("timestamp", "")).replace("Z", "+00:00")
                )
                if replied_at.tzinfo is None:
                    replied_at = replied_at.replace(tzinfo=timezone.utc)
            except ValueError:
                return False, "", False
            age = (received_at - replied_at.astimezone(timezone.utc)).total_seconds()
            if age < 0 or age > self.settings.ai_conversation_followup_seconds:
                return False, "", False
            closed_at = self.closed_ai_conversations.get(user_id)
            if closed_at is not None and replied_at <= closed_at:
                return False, "", False
            response_expected = "?" in previous_reply or "?" in text
            return True, previous_reply, response_expected
        return False, "", False

    def _start_next_response_batch(self) -> None:
        if (
            self.response_decision_in_flight
            or not self.response_decision_queue
            or not self.settings.local_ai_enabled
            or not self.settings.ai_response_decisions_enabled
        ):
            return
        batch = tuple(
            self.response_decision_queue.popleft()
            for _ in range(min(8, len(self.response_decision_queue)))
        )
        worker = ResponseDecisionWorker(
            batch,
            tuple(self.recent_ai_chat),
            self.settings.local_ai_endpoint,
            self.settings.local_ai_model,
            self.settings.ai_personality,
            self.settings.ai_allow_mild_profanity,
            self.settings.ai_allow_strong_profanity,
        )
        worker.signals.completed.connect(self._apply_response_batch)
        worker.signals.failed.connect(self._response_batch_failed)
        self.response_decision_in_flight = True
        self.reply_decision_status_label.setText(
            f"Sally is evaluating {len(batch)} live message(s)…"
        )
        self.response_decision_thread_pool.start(worker)

    @Slot(object)
    def _apply_response_batch(self, result: ResponseBatchResult) -> None:
        self.response_decision_in_flight = False
        reply_count = 0
        sent_count = 0
        for decision in result.decisions:
            if decision.decision == "reply":
                reply_count += 1
            sent = self._maybe_auto_send_reply(decision)
            self._update_conversation_state(decision)
            self._capture_training_decision(decision)
            sent_count += int(sent)
            if sent:
                self._drop_duplicate_queued_invocations(decision)
            self._add_reply_decision(decision, sent=sent)
        self.reply_decision_status_label.setText(
            f"Evaluated {len(result.decisions)} message(s): "
            f"{reply_count} reply draft(s), {sent_count} auto-sent."
        )
        self._start_next_response_batch()

    def _capture_training_decision(self, decision: ResponseDecision) -> None:
        if not (
            self.settings.ai_training_capture_enabled
            and decision.user_id in self.training_opted_in_users
        ):
            return
        try:
            self.training_store.capture(decision.user_id, decision)
        except OSError as error:
            Logger.warning(
                f"Could not save a local training example: {error}",
                source="AI",
            )
            return
        self._refresh_training_examples()

    def _update_conversation_state(self, decision: ResponseDecision) -> None:
        if not decision.user_id:
            return
        if decision.conversation_state == "end":
            self.closed_ai_conversations[decision.user_id] = datetime.now(
                timezone.utc
            )
        elif decision.conversation_state in {"start", "continue"}:
            self.closed_ai_conversations.pop(decision.user_id, None)

    def _drop_duplicate_queued_invocations(
        self, decision: ResponseDecision
    ) -> None:
        if not (
            ResponseDecisionEngine.requires_reply(decision.source_text)
            or decision.solicited
        ):
            return
        normalized = " ".join(decision.source_text.casefold().split())
        self.response_decision_queue = deque(
            (
                message
                for message in self.response_decision_queue
                if not (
                    message.user_id == decision.user_id
                    and " ".join(message.text.casefold().split()) == normalized
                )
            ),
            maxlen=100,
        )

    @Slot(object, str)
    def _response_batch_failed(
        self,
        messages: object,
        error: str,
    ) -> None:
        self.response_decision_in_flight = False
        for message in messages if isinstance(messages, tuple) else ():
            if not isinstance(message, ResponseMessage):
                continue
            if ResponseDecisionEngine.message_requires_reply(message):
                recent_replies = [
                    str(item.get("message", ""))
                    for item in self.recent_ai_chat
                    if str(item.get("speaker", "")).casefold() == "sally"
                ]
                decision = ResponseDecisionEngine._fallback_reply(
                    message, recent_replies
                )
            else:
                decision = ResponseDecision(
                    request_id=message.request_id,
                    message_id=message.message_id,
                    user_id=message.user_id,
                    user_name=message.user_name,
                    source_text=message.text,
                    received_at=message.received_at,
                    decision="ignore",
                    reply="",
                    reason=f"Local AI unavailable: {error}"[:300],
                    confidence=0.0,
                )
            sent = self._maybe_auto_send_reply(decision)
            self.auto_send_diagnostic_reasons[decision.request_id] = (
                "local_ai_fallback_sent" if sent else "local_ai_fallback"
            )
            self._add_reply_decision(decision, sent=sent)
        self.reply_decision_status_label.setText(
            "Local AI reply evaluation failed; continuing with newer chat."
        )
        Logger.warning(
            f"Local reply decision failed: {error}",
            source="AI",
        )
        self._start_next_response_batch()

    def _decision_age_seconds(self, decision: ResponseDecision) -> float:
        try:
            received = datetime.fromisoformat(
                decision.received_at.replace("Z", "+00:00")
            )
        except ValueError:
            return float("inf")
        return max(
            (datetime.now(timezone.utc) - received).total_seconds(),
            0.0,
        )

    def _maybe_auto_send_reply(self, decision: ResponseDecision) -> bool:
        required = (
            ResponseDecisionEngine.requires_reply(decision.source_text)
            or decision.response_expected
            or decision.solicited
        )
        interjection = not required
        maximum_age = (
            max(self.settings.ai_response_max_age_seconds, 120)
            if required
            else self.settings.ai_response_max_age_seconds
        )
        minimum_gap = (
            0 if required else self.settings.ai_response_min_interval_seconds
        )
        reason = ""
        if decision.decision != "reply" or not decision.reply:
            reason = "model_ignored"
        elif not required and decision.confidence < 0.65:
            reason = "low_confidence"
        elif interjection and not self.settings.ai_interjections_enabled:
            reason = "interjections_disabled"
        elif interjection and decision.confidence < 0.88:
            reason = "interjection_low_confidence"
        elif (
            interjection
            and self.viewer_messages_since_sally_reply
            < self.settings.ai_interjection_min_messages
        ):
            reason = "interjection_message_threshold"
        elif (
            interjection
            and (
                monotonic() - self.last_interjection_at
                < self.settings.ai_interjection_min_interval_seconds
                or monotonic() - self.last_auto_reply_at
                < self.settings.ai_interjection_min_interval_seconds
            )
        ):
            reason = "interjection_cooldown"
        elif self._decision_age_seconds(decision) > maximum_age:
            reason = "stale"
        elif monotonic() - self.last_auto_reply_at < minimum_gap:
            reason = "reply_cooldown"
        elif self.twitch_service.state is not TwitchConnectionState.CONNECTED:
            reason = "twitch_disconnected"
        if reason:
            self.auto_send_diagnostic_reasons[decision.request_id] = reason
            return False
        if not self.twitch_service.send_message(decision.reply):
            self.auto_send_diagnostic_reasons[decision.request_id] = (
                "twitch_send_failed"
            )
            return False
        self.auto_send_diagnostic_reasons[decision.request_id] = "sent"
        self.last_auto_reply_at = monotonic()
        if interjection:
            self.last_interjection_at = self.last_auto_reply_at
        self._remember_sally_reply(
            decision.reply,
            user_id=decision.user_id,
            user_name=decision.user_name,
        )
        return True

    def _remember_sally_reply(
        self,
        reply: str,
        *,
        user_id: str = "",
        user_name: str = "",
    ) -> None:
        clean = " ".join(reply.strip().split())[:500]
        if not clean:
            return
        self.viewer_messages_since_sally_reply = 0
        bot_name = (
            self.twitch_bot_auth.token.login
            if self.twitch_bot_auth.token is not None
            else "Sally"
        )
        self.recent_ai_chat.append(
            {
                "speaker": "sally",
                "viewer": bot_name or "Sally",
                "message": clean,
                "user_id": user_id,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )
        if (
            self.settings.ai_viewer_memory_enabled
            and user_id
            and self.chatter_history.has_memory_consent(user_id)
        ):
            self.chatter_history.record_daily_memory(
                user_id,
                speaker="sally",
                viewer=bot_name or "Sally",
                message=clean,
                stream_id=self.current_memory_stream_id,
            )

    def _add_reply_decision(
        self,
        decision: ResponseDecision,
        *,
        sent: bool = False,
    ) -> None:
        table = self.reply_review_table
        table.insertRow(0)
        age = self._decision_age_seconds(decision)
        stale = age > self.settings.ai_response_max_age_seconds
        decision_text = (
            "SENT"
            if sent
            else "STALE"
            if stale and decision.decision == "reply"
            else f"{decision.decision.upper()} {decision.confidence:.0%}"
        )
        values = (
            f"{int(age)}s" if age != float("inf") else "--",
            decision.user_name,
            decision.source_text,
            decision_text,
            decision.reply,
            decision.reason,
        )
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column == 0:
                item.setData(Qt.ItemDataRole.UserRole, decision)
            table.setItem(0, column, item)
        if table.rowCount() > 100:
            table.removeRow(table.rowCount() - 1)
        if decision.decision == "reply" and not sent:
            table.selectRow(0)
        self._record_ai_test_result(decision, sent=sent, latency_seconds=age)

    def _record_ai_test_result(
        self,
        decision: ResponseDecision,
        *,
        sent: bool,
        latency_seconds: float,
    ) -> None:
        response_expected = bool(
            ResponseDecisionEngine.requires_reply(decision.source_text)
            or decision.response_expected
            or decision.solicited
        )
        delivery_reason = self.auto_send_diagnostic_reasons.pop(
            decision.request_id, ""
        )
        if sent:
            outcome = "sent"
            reason = delivery_reason or "sent"
        elif decision.decision == "ignore":
            reason = self._normalize_ai_decision_reason(decision.reason)
            outcome = (
                "failed"
                if reason == "local_ai_unavailable"
                else "missed"
                if response_expected
                else "ignored"
            )
        elif delivery_reason == "twitch_send_failed":
            outcome = "failed"
            reason = delivery_reason
        else:
            outcome = "blocked"
            reason = delivery_reason or "draft_not_sent"
        latency_ms = (
            0
            if latency_seconds == float("inf")
            else round(max(latency_seconds, 0.0) * 1000)
        )
        try:
            self.test_report_store.record(
                outcome=outcome,
                reason=reason,
                latency_ms=latency_ms,
                response_expected=response_expected,
                confidence=decision.confidence,
                response_source=decision.response_source,
                save=False,
            )
        except OSError as error:
            Logger.warning(
                f"Could not save anonymous AI test diagnostics: {error}",
                source="AI",
            )
            return
        self.ai_test_report_flush_timer.start()

    def _flush_ai_test_report(self) -> None:
        try:
            self.test_report_store.save()
        except OSError as error:
            Logger.warning(
                f"Could not save anonymous AI test diagnostics: {error}",
                source="AI",
            )
            return
        self._refresh_ai_test_report()

    @staticmethod
    def _normalize_ai_decision_reason(reason: str) -> str:
        normalized = reason.casefold()
        categories = (
            ("queue", "queue_full"),
            ("omitted", "model_omitted_message"),
            ("duplicate", "duplicate_reply"),
            ("unavailable", "local_ai_unavailable"),
            ("required", "model_ignored_required_message"),
            ("address", "not_addressed_to_sally"),
            ("conversation", "conversation_state"),
        )
        for keyword, category in categories:
            if keyword in normalized:
                return category
        return "model_decision"

    def _selected_reply_decision(self) -> ResponseDecision | None:
        row = self.reply_review_table.currentRow()
        item = self.reply_review_table.item(row, 0) if row >= 0 else None
        decision = (
            item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        )
        return decision if isinstance(decision, ResponseDecision) else None

    def _update_reply_review_actions(self) -> None:
        decision = self._selected_reply_decision()
        has_reply = bool(decision and decision.reply)
        self.send_reply_draft_button.setEnabled(has_reply)
        self.edit_send_reply_button.setEnabled(has_reply)
        self.dismiss_reply_button.setEnabled(decision is not None)
        self.teach_rivescript_button.setEnabled(has_reply)

    def _teach_selected_rivescript_reply(self) -> None:
        decision = self._selected_reply_decision()
        if decision is None or not decision.reply:
            return
        broadcaster_id = (
            self.twitch_auth.token.user_id
            if self.twitch_auth.token is not None
            else ""
        )
        may_prefill_viewer_text = bool(
            decision.user_id == broadcaster_id
            or decision.user_id in self.training_opted_in_users
        )
        trigger = (
            self.rivescript_store.suggest_trigger(decision.source_text)
            if may_prefill_viewer_text
            else ""
        )
        if not may_prefill_viewer_text:
            self.reply_decision_status_label.setText(
                "That viewer has not opted into training, so their message "
                "was not copied. Enter a generalized trigger manually."
            )
        values = self._prompt_rivescript_rule(
            trigger=trigger,
            reply=decision.reply,
            name="Taught from Reply Review",
        )
        if values is None:
            return
        if self._save_new_rivescript_rule(*values):
            self.ai_tabs.setCurrentWidget(self.rivescript_rules_page)

    @Slot()
    def _send_selected_reply_draft(self) -> None:
        decision = self._selected_reply_decision()
        if decision is None or not decision.reply:
            return
        if self.twitch_service.send_message(decision.reply):
            self._remember_sally_reply(
                decision.reply,
                user_id=decision.user_id,
                user_name=decision.user_name,
            )
            row = self.reply_review_table.currentRow()
            self.reply_review_table.item(row, 3).setText("SENT")
            self.reply_decision_status_label.setText(
                f"Sent Sally's reply to {decision.user_name}."
            )

    @Slot()
    def _edit_and_send_reply_draft(self) -> None:
        decision = self._selected_reply_decision()
        if decision is None or not decision.reply:
            return
        reply, accepted = QInputDialog.getMultiLineText(
            self,
            f"Reply to {decision.user_name}",
            "Twitch message:",
            decision.reply,
        )
        clean = " ".join(reply.strip().split())[:400]
        if accepted and clean and self.twitch_service.send_message(clean):
            self._remember_sally_reply(clean)
            row = self.reply_review_table.currentRow()
            self.reply_review_table.item(row, 3).setText("SENT (EDITED)")
            self.reply_review_table.item(row, 4).setText(clean)

    @Slot()
    def _dismiss_reply_decision(self) -> None:
        row = self.reply_review_table.currentRow()
        if row >= 0:
            self.reply_review_table.removeRow(row)

    def _refresh_training_examples(self) -> None:
        table = self.training_table
        table.setRowCount(0)
        for example in reversed(self.training_store.examples):
            row = table.rowCount()
            table.insertRow(row)
            values = (
                str(example.get("message", "")),
                str(example.get("model_label", "")),
                str(example.get("decision", "")),
                str(example.get("conversation_state", "")),
                str(example.get("label", "")) or "Pending",
                "Yes" if bool(example.get("reviewed")) else "No",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setData(
                        Qt.ItemDataRole.UserRole,
                        str(example.get("id", "")),
                    )
                table.setItem(row, column, item)
        reviewed = sum(
            bool(example.get("reviewed"))
            for example in self.training_store.examples
        )
        self.training_status_label.setText(
            f"{len(self.training_store.examples)} local sample(s); "
            f"{reviewed} reviewed. Pending samples expire after 30 days."
        )

    def _selected_training_example_id(self) -> str:
        row = self.training_table.currentRow()
        item = self.training_table.item(row, 0) if row >= 0 else None
        return str(item.data(Qt.ItemDataRole.UserRole)) if item else ""

    @Slot(int, int, int, int)
    def _select_training_example(
        self,
        current_row: int,
        _current_column: int,
        _previous_row: int,
        _previous_column: int,
    ) -> None:
        item = self.training_table.item(current_row, 0)
        example_id = (
            str(item.data(Qt.ItemDataRole.UserRole)) if item is not None else ""
        )
        example = next(
            (
                value
                for value in self.training_store.examples
                if value.get("id") == example_id
            ),
            None,
        )
        if example is None:
            return
        label = str(example.get("label") or example.get("model_label") or "")
        if label in TrainingStore.LABELS:
            self.training_label_combo.setCurrentText(label)

    @Slot()
    def _save_training_label(self) -> None:
        example_id = self._selected_training_example_id()
        if not example_id:
            return
        try:
            updated = self.training_store.label(
                example_id, self.training_label_combo.currentText()
            )
        except (OSError, ValueError) as error:
            self.training_status_label.setText(
                f"Could not save training label: {error}"
            )
            return
        if updated:
            self._refresh_training_examples()

    @Slot()
    def _delete_training_example(self) -> None:
        example_id = self._selected_training_example_id()
        if not example_id:
            return
        try:
            self.training_store.delete(example_id)
        except OSError as error:
            self.training_status_label.setText(
                f"Could not delete training sample: {error}"
            )
            return
        self._refresh_training_examples()

    @Slot()
    def _clear_training_examples(self) -> None:
        if not self.training_store.examples:
            return
        if QMessageBox.question(
            self,
            "Delete Training Samples",
            "Permanently delete every local Sally training sample?",
        ) is not QMessageBox.StandardButton.Yes:
            return
        try:
            self.training_store.clear()
        except OSError as error:
            self.training_status_label.setText(
                f"Could not delete training samples: {error}"
            )
            return
        self._refresh_training_examples()

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
        if twitch_event.subscription_type in {
            "stream.online",
            "stream.offline",
        }:
            if self.session_tracker.observe_event(
                twitch_event.subscription_type
            ):
                self._refresh_session_history()
            event = twitch_event.payload.get("event", {})
            if (
                twitch_event.subscription_type == "stream.online"
                and isinstance(event, dict)
            ):
                next_stream_id = str(
                    event.get("id") or event.get("started_at") or ""
                )
                if next_stream_id != self.current_memory_stream_id:
                    self.current_memory_stream_id = next_stream_id
                    self.memory_promo_message_count = 0
                    self.last_memory_promo_at = 0.0
                    self.training_notice_attempt_context = ""
                self._maybe_announce_training_capture()
            else:
                self.current_memory_stream_id = ""
                self.training_notice_attempt_context = ""
            self.refresh_stream_companion()
            return
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
            user_id=viewer_id if isinstance(event_payload, dict) else "",
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
        self.ui.twitchChatCountLabel.setText("Chat")

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

    def _build_ai_settings(self) -> None:
        group = QGroupBox("Local AI", self.ui.settingsPage)
        self.local_ai_settings_group = group
        layout = QFormLayout(group)
        self.local_ai_enabled_check = QCheckBox("Use local AI when available")
        self.local_ai_endpoint_edit = QLineEdit()
        self.local_ai_model_edit = QLineEdit()
        self.local_ai_test_button = QPushButton("Test Local AI")
        self.local_ai_status_label = QLabel("Not tested")
        self.ai_viewer_memory_check = QCheckBox(
            "Enable opt-in viewer memories"
        )
        self.ai_viewer_memory_check.setToolTip(
            "Master switch for memory invitations, collection, restoration, "
            "reasoning, and viewer memory context."
        )
        self.ai_memory_reasoning_check = QCheckBox(
            "Propose viewer memories from live chat"
        )
        self.ai_memory_threshold_spin = QSpinBox()
        self.ai_memory_threshold_spin.setRange(5, 50)
        self.ai_memory_threshold_spin.setSuffix(" messages")
        self.ai_memory_reset_time_edit = QTimeEdit()
        self.ai_memory_reset_time_edit.setDisplayFormat("HH:mm")
        self.ai_memory_promo_check = QCheckBox(
            "Occasionally mention !sallymemory in chat"
        )
        self.ai_memory_promo_interval_spin = QSpinBox()
        self.ai_memory_promo_interval_spin.setRange(25, 1000)
        self.ai_memory_promo_interval_spin.setSuffix(" messages")
        self.ai_response_decisions_check = QCheckBox(
            "Evaluate eligible live chat messages"
        )
        self.ai_response_max_age_spin = QSpinBox()
        self.ai_response_max_age_spin.setRange(5, 60)
        self.ai_response_max_age_spin.setSuffix(" seconds")
        self.ai_response_interval_spin = QSpinBox()
        self.ai_response_interval_spin.setRange(3, 60)
        self.ai_response_interval_spin.setSuffix(" seconds")
        self.ai_conversation_followup_spin = QSpinBox()
        self.ai_conversation_followup_spin.setRange(30, 600)
        self.ai_conversation_followup_spin.setSuffix(" seconds")
        self.ai_conversation_followup_spin.setToolTip(
            "How long a viewer can continue talking with Sally without "
            "repeating 'hey Sally'."
        )
        self.ai_interjections_check = QCheckBox(
            "Let Sally occasionally join relevant chat"
        )
        self.ai_interjections_check.setToolTip(
            "Sally may act as a restrained co-host when she has something "
            "specific and worthwhile to add."
        )
        self.ai_interjection_interval_spin = QSpinBox()
        self.ai_interjection_interval_spin.setRange(60, 1800)
        self.ai_interjection_interval_spin.setSuffix(" seconds")
        self.ai_interjection_min_messages_spin = QSpinBox()
        self.ai_interjection_min_messages_spin.setRange(2, 50)
        self.ai_interjection_min_messages_spin.setSuffix(" messages")
        self.ai_training_capture_check = QCheckBox(
            "Allow opt-in classifier training capture"
        )
        self.ai_training_capture_check.setToolTip(
            "Viewers must separately type !sallytrain on each session. "
            "Captured samples stay local and are pseudonymous."
        )
        self.ai_training_notice_check = QCheckBox(
            "Post and pin once when the stream goes live"
        )
        self.ai_training_notice_check.setToolTip(
            "Sally posts one opt-in disclosure per Twitch stream and the "
            "broadcaster account pins it until the stream ends."
        )
        self.ai_training_notice_edit = QLineEdit()
        self.ai_training_notice_edit.setMaxLength(500)
        layout.addRow("Enabled", self.local_ai_enabled_check)
        layout.addRow("Endpoint", self.local_ai_endpoint_edit)
        layout.addRow("Model", self.local_ai_model_edit)
        layout.addRow("Viewer memory system", self.ai_viewer_memory_check)
        layout.addRow("Memory reasoning", self.ai_memory_reasoning_check)
        layout.addRow("Analyze after", self.ai_memory_threshold_spin)
        layout.addRow("Daily reset", self.ai_memory_reset_time_edit)
        layout.addRow("Memory invitation", self.ai_memory_promo_check)
        layout.addRow("Invitation interval", self.ai_memory_promo_interval_spin)
        layout.addRow("Reply decisions", self.ai_response_decisions_check)
        layout.addRow("Reply freshness", self.ai_response_max_age_spin)
        layout.addRow("Minimum reply gap", self.ai_response_interval_spin)
        layout.addRow("Conversation window", self.ai_conversation_followup_spin)
        layout.addRow("Co-host interjections", self.ai_interjections_check)
        layout.addRow("Interjection cooldown", self.ai_interjection_interval_spin)
        layout.addRow(
            "Chat before interjection", self.ai_interjection_min_messages_spin
        )
        layout.addRow("Training capture", self.ai_training_capture_check)
        layout.addRow("Pinned training notice", self.ai_training_notice_check)
        layout.addRow("Training notice text", self.ai_training_notice_edit)
        layout.addRow("", self.local_ai_test_button)
        layout.addRow("Status", self.local_ai_status_label)
        self.ui.settingsLayout.insertWidget(3, group)
        self.local_ai_test_button.clicked.connect(self._test_local_ai)
        self.ai_viewer_memory_check.toggled.connect(
            self._update_memory_setting_controls
        )
        self.ai_training_capture_check.toggled.connect(
            self._update_training_setting_controls
        )
        self.ai_training_notice_check.toggled.connect(
            self._update_training_setting_controls
        )

    @Slot(bool)
    def _update_memory_setting_controls(self, enabled: bool) -> None:
        for control in (
            self.ai_memory_reasoning_check,
            self.ai_memory_threshold_spin,
            self.ai_memory_reset_time_edit,
            self.ai_memory_promo_check,
            self.ai_memory_promo_interval_spin,
        ):
            control.setEnabled(enabled)

    def _update_training_setting_controls(self, _enabled: bool = False) -> None:
        capture_enabled = self.ai_training_capture_check.isChecked()
        self.ai_training_notice_check.setEnabled(capture_enabled)
        self.ai_training_notice_edit.setEnabled(
            capture_enabled and self.ai_training_notice_check.isChecked()
        )

    @Slot()
    def _test_local_ai(self) -> None:
        provider = OllamaProvider(
            self.local_ai_endpoint_edit.text().strip(),
            self.local_ai_model_edit.text().strip(),
            timeout=5.0,
        )
        status = provider.status()
        if not status.available:
            self.local_ai_status_label.setText(f"Unavailable: {status.error}")
            return
        if provider.model not in status.models:
            self.local_ai_status_label.setText(
                f"Connected; model {provider.model} is not installed"
            )
            return
        self.local_ai_status_label.setText(
            f"Ready: {provider.model} ({len(status.models)} local model(s))"
        )

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
        self.local_ai_enabled_check.setChecked(settings.local_ai_enabled)
        self.local_ai_endpoint_edit.setText(settings.local_ai_endpoint)
        self.local_ai_model_edit.setText(settings.local_ai_model)
        self.ai_viewer_memory_check.setChecked(settings.ai_viewer_memory_enabled)
        self._update_memory_setting_controls(settings.ai_viewer_memory_enabled)
        self.ai_memory_reasoning_check.setChecked(
            settings.ai_memory_reasoning_enabled
        )
        self.ai_memory_threshold_spin.setValue(
            settings.ai_memory_message_threshold
        )
        self.ai_memory_reset_time_edit.setTime(
            QTime(settings.ai_memory_reset_hour, settings.ai_memory_reset_minute)
        )
        self.ai_memory_promo_check.setChecked(settings.ai_memory_promo_enabled)
        self.ai_memory_promo_interval_spin.setValue(
            settings.ai_memory_promo_interval_messages
        )
        self.ai_response_decisions_check.setChecked(
            settings.ai_response_decisions_enabled
        )
        self.ai_response_max_age_spin.setValue(
            settings.ai_response_max_age_seconds
        )
        self.ai_response_interval_spin.setValue(
            settings.ai_response_min_interval_seconds
        )
        self.ai_conversation_followup_spin.setValue(
            settings.ai_conversation_followup_seconds
        )
        self.ai_interjections_check.setChecked(settings.ai_interjections_enabled)
        self.ai_interjection_interval_spin.setValue(
            settings.ai_interjection_min_interval_seconds
        )
        self.ai_interjection_min_messages_spin.setValue(
            settings.ai_interjection_min_messages
        )
        self.ai_training_capture_check.setChecked(
            settings.ai_training_capture_enabled
        )
        self.ai_training_notice_check.setChecked(
            settings.ai_training_notice_enabled
        )
        self.ai_training_notice_edit.setText(
            settings.ai_training_notice_message
        )
        self._update_training_setting_controls()
        self.ai_personality_edit.setPlainText(settings.ai_personality)
        self.ai_allow_mild_profanity_check.setChecked(
            settings.ai_allow_mild_profanity
        )
        self.ai_allow_strong_profanity_check.setChecked(
            settings.ai_allow_strong_profanity
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
            local_ai_enabled=self.local_ai_enabled_check.isChecked(),
            local_ai_endpoint=self.local_ai_endpoint_edit.text(),
            local_ai_model=self.local_ai_model_edit.text(),
            ai_viewer_memory_enabled=self.ai_viewer_memory_check.isChecked(),
            ai_memory_reasoning_enabled=(
                self.ai_memory_reasoning_check.isChecked()
            ),
            ai_memory_message_threshold=(
                self.ai_memory_threshold_spin.value()
            ),
            ai_memory_reset_hour=self.ai_memory_reset_time_edit.time().hour(),
            ai_memory_reset_minute=self.ai_memory_reset_time_edit.time().minute(),
            ai_memory_promo_enabled=self.ai_memory_promo_check.isChecked(),
            ai_memory_promo_interval_messages=(
                self.ai_memory_promo_interval_spin.value()
            ),
            ai_response_decisions_enabled=(
                self.ai_response_decisions_check.isChecked()
            ),
            ai_auto_send_replies=True,
            ai_response_max_age_seconds=(
                self.ai_response_max_age_spin.value()
            ),
            ai_response_min_interval_seconds=(
                self.ai_response_interval_spin.value()
            ),
            ai_conversation_followup_seconds=(
                self.ai_conversation_followup_spin.value()
            ),
            ai_interjections_enabled=self.ai_interjections_check.isChecked(),
            ai_interjection_min_interval_seconds=(
                self.ai_interjection_interval_spin.value()
            ),
            ai_interjection_min_messages=(
                self.ai_interjection_min_messages_spin.value()
            ),
            ai_training_capture_enabled=(
                self.ai_training_capture_check.isChecked()
            ),
            ai_training_notice_enabled=(
                self.ai_training_notice_check.isChecked()
            ),
            ai_training_notice_message=(
                self.ai_training_notice_edit.text()
            ),
            ai_training_notice_stream_id=(
                self.settings.ai_training_notice_stream_id
            ),
            ai_personality=self.ai_personality_edit.toPlainText(),
            ai_allow_mild_profanity=(
                self.ai_allow_mild_profanity_check.isChecked()
            ),
            ai_allow_strong_profanity=(
                self.ai_allow_strong_profanity_check.isChecked()
            ),
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
        if not (
            settings.local_ai_enabled
            and settings.ai_viewer_memory_enabled
            and settings.ai_memory_reasoning_enabled
        ):
            self.memory_message_buffers.clear()
            self.recent_ai_chat = deque(
                (
                    item for item in self.recent_ai_chat
                    if item.get("memory_source") != "daily"
                ),
                maxlen=100,
            )
            self.memory_reasoning_status_label.setText(
                "Viewer memory is disabled."
            )
        elif self.memory_reasoning_status_label.text().endswith("disabled."):
            self.memory_reasoning_status_label.setText(
                "Local memory reasoning is waiting for enough chat context."
            )
        if not (
            settings.local_ai_enabled
            and settings.ai_response_decisions_enabled
        ):
            self.response_decision_queue.clear()
            self.reply_decision_status_label.setText(
                "Local AI reply decisions are disabled."
            )
        if not settings.ai_training_capture_enabled:
            self.training_opted_in_users.clear()
        self._update_memory_action_states()

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
            self.memory_name_label.setText("Select a viewer")
            self.memory_id_label.clear()
            self._update_memory_action_states()
            return
        user_id = str(current.data(Qt.ItemDataRole.UserRole))
        record = self.chatter_history.records.get(user_id)
        if record is None:
            self._update_memory_action_states()
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
        self.memory_enabled_check.blockSignals(True)
        self.memory_enabled_check.setChecked(record.memory_enabled)
        active_settings = getattr(self, "settings", AppSettings())
        self.memory_enabled_check.setEnabled(
            bool(
                active_settings.ai_viewer_memory_enabled
                and self.chatter_history.has_memory_consent(user_id)
            )
        )
        self.memory_enabled_check.setToolTip(
            "Viewer consent is required; this control can only pause or resume it."
        )
        self.memory_enabled_check.blockSignals(False)
        if not self.chatter_history.has_memory_consent(user_id):
            memory_status = "Viewer has not opted in to Sally Memory."
        elif not self.chatter_history.can_create_keynotes(user_id):
            memory_status = (
                "Daily memory is enabled. Persistent keynotes unlock after "
                f"5 opted-in streams ({len(record.memory_stream_ids)}/5)."
            )
        elif record.memory_enabled:
            memory_status = str(self.chatter_history.viewer_summary(user_id))
        else:
            memory_status = "AI memory is paused for this viewer."
        self.memory_summary_label.setText(memory_status)
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
        selected_status = self.memory_status_filter.currentText()
        if selected_status == "Pending review":
            visible_memories = [
                memory for memory in visible_memories
                if memory.get("status", "approved") == "pending"
            ]
        elif selected_status == "Approved":
            visible_memories = [
                memory for memory in visible_memories
                if memory.get("status", "approved") == "approved"
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
                status = str(memory.get("status", "approved")).upper()
                confidence = float(memory.get("confidence", 1.0))
                conflict = " | CONFLICT" if memory.get("conflicts_with") else ""
                item = QListWidgetItem(
                    f"{prefix}[{status} | {category} | {confidence:.0%}{conflict}] "
                    f"{memory.get('text', 'Memory')}"
                )
                item.setData(
                    Qt.ItemDataRole.UserRole,
                    str(memory.get("id", "")),
                )
                self.memory_ai_list.addItem(item)
        else:
            self.memory_ai_list.addItem(
                "No memories match this review filter."
            )
        self._update_memory_action_states()

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

    def _update_memory_action_states(self) -> None:
        viewer_item = self.memory_viewer_list.currentItem()
        user_id = (
            str(viewer_item.data(Qt.ItemDataRole.UserRole) or "")
            if viewer_item is not None
            else ""
        )
        record = self.chatter_history.records.get(user_id) if user_id else None
        memory = None
        context = self._selected_memory_context()
        if context is not None:
            try:
                memory = self.chatter_history.get_memory(*context)
            except (KeyError, ValueError):
                memory = None

        has_viewer = record is not None
        has_memory = memory is not None
        settings = getattr(self, "settings", AppSettings())
        can_create = bool(
            has_viewer
            and settings.ai_viewer_memory_enabled
            and self.chatter_history.can_create_keynotes(user_id)
        )
        self.save_viewer_profile_button.setEnabled(has_viewer)
        self.merge_viewer_button.setEnabled(
            has_viewer and len(self.chatter_history.records) > 1
        )
        self.export_timeline_csv_button.setEnabled(
            bool(has_viewer and (record.timeline or record.session_messages))
        )
        self.add_memory_button.setEnabled(can_create)
        for button in (
            self.edit_memory_button,
            self.pin_memory_button,
            self.delete_memory_button,
        ):
            button.setEnabled(has_memory)
        archived = bool(memory and memory.get("archived", False))
        self.archive_memory_button.setEnabled(has_memory and not archived)
        self.archive_memory_button.setText("Archived" if archived else "Archive")
        pinned = bool(memory and memory.get("pinned", False))
        self.pin_memory_button.setText("Unpin" if pinned else "Pin")
        pending = bool(memory and memory.get("status", "approved") == "pending")
        self.approve_memory_button.setEnabled(pending)
        self.reject_memory_button.setEnabled(pending)
        self.export_memory_button.setEnabled(has_viewer)
        self.erase_memories_button.setEnabled(
            bool(has_viewer and (record.memories or record.daily_memory))
        )

    @Slot(object, object)
    def _show_memory_details(self, current: object, _previous: object) -> None:
        if not isinstance(current, QListWidgetItem):
            self.memory_detail_label.setText(
                "Select a memory to inspect its evidence."
            )
            self._update_memory_action_states()
            return
        context = self._selected_memory_context()
        if context is None:
            self._update_memory_action_states()
            return
        user_id, memory_id = context
        memory = self.chatter_history.get_memory(user_id, memory_id)
        evidence = [
            str(item.get("text", ""))
            for item in memory.get("evidence", [])
            if isinstance(item, dict) and item.get("text")
        ]
        details = [
            f"Source: {memory.get('source', 'manual')}",
            f"Confidence: {float(memory.get('confidence', 1.0)):.0%}",
            "Created: " + self._format_memory_timestamp(
                str(memory.get("created_at", ""))
            ),
            "Last confirmed: " + self._format_memory_timestamp(
                str(memory.get("last_confirmed_at", ""))
            ),
        ]
        if memory.get("conflicts_with"):
            details.append("Conflict: may replace an existing memory")
        if memory.get("rejection_reason"):
            details.append(f"Rejection: {memory['rejection_reason']}")
        details.append(
            "Evidence: " + (" | ".join(evidence[:3]) if evidence else "Manual entry")
        )
        self.memory_detail_label.setText("\n".join(details))
        self._update_memory_action_states()

    @Slot(bool)
    def _set_viewer_memory_enabled(self, enabled: bool) -> None:
        viewer_item = self.memory_viewer_list.currentItem()
        if viewer_item is None:
            return
        user_id = str(viewer_item.data(Qt.ItemDataRole.UserRole))
        self.chatter_history.set_memory_enabled(user_id, enabled)
        if not enabled:
            self.memory_message_buffers.pop(user_id, None)
        self._save_chatter_history()
        self._show_memory_viewer(viewer_item, None)
        state = "enabled" if enabled else "disabled"
        self.statusBar().showMessage(f"Viewer AI memory {state}.", 5000)

    @Slot()
    def _approve_viewer_memory(self) -> None:
        context = self._selected_memory_context()
        if context is None:
            return
        user_id, memory_id = context
        self.chatter_history.review_memory(user_id, memory_id, True)
        self._save_chatter_history()
        self._show_memory_viewer(self.memory_viewer_list.currentItem(), None)

    @Slot()
    def _reject_viewer_memory(self) -> None:
        context = self._selected_memory_context()
        if context is None:
            return
        reason, accepted = QInputDialog.getText(
            self,
            "Reject AI Memory",
            "Reason (optional)",
        )
        if not accepted:
            return
        user_id, memory_id = context
        self.chatter_history.review_memory(
            user_id, memory_id, False, reason
        )
        self._save_chatter_history()
        self._show_memory_viewer(self.memory_viewer_list.currentItem(), None)

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
        categories = ("General", "Preference", "Community")
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
        try:
            self.chatter_history.add_memory(user_id, text, category)
        except PermissionError as error:
            self.statusBar().showMessage(str(error), 8000)
            return
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
        self.last_companion_result = result
        self.companion_refresh_in_flight = False
        snapshot = result.snapshot
        stream = snapshot.get("stream")
        next_stream_id = ""
        if isinstance(stream, dict):
            next_stream_id = str(
                stream.get("id") or stream.get("started_at") or ""
            )
        if next_stream_id != self.current_memory_stream_id:
            self.current_memory_stream_id = next_stream_id
            self.memory_promo_message_count = 0
            self.last_memory_promo_at = 0.0
            self.training_notice_attempt_context = ""
        self._expire_daily_memory()
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
        if isinstance(stream, dict):
            self._maybe_announce_training_capture()
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

    @Slot()
    def _expire_daily_memory(self) -> None:
        removed = self.chatter_history.expire_daily_memories(
            reset_at=time(
                self.settings.ai_memory_reset_hour,
                self.settings.ai_memory_reset_minute,
            ),
            active_stream_id=self.current_memory_stream_id,
        )
        if removed:
            self.recent_ai_chat = deque(
                (
                    item for item in self.recent_ai_chat
                    if item.get("memory_source") != "daily"
                ),
                maxlen=100,
            )
            Logger.info(
                f"Expired {removed} daily Sally memory message(s).",
                source="AI",
            )
            self._save_chatter_history()
        if self.daily_memory_expiry_pending:
            self.daily_memory_expiry_pending = False
            self._restore_daily_memory_context()

    def _restore_daily_memory_context(self) -> None:
        if not self.settings.ai_viewer_memory_enabled:
            return
        restored: list[dict[str, str]] = []
        for user_id, record in self.chatter_history.records.items():
            if not self.chatter_history.has_memory_consent(user_id):
                continue
            for item in record.daily_memory:
                restored.append(
                    {**item, "user_id": user_id, "memory_source": "daily"}
                )
        restored.sort(key=lambda item: str(item.get("timestamp", "")))
        self.recent_ai_chat.extend(restored[-100:])

    def _apply_chatter_groups(
        self,
        result: CompanionRefreshResult,
    ) -> None:
        self.chatter_list.clear()
        if not result.can_read_chatters:
            self.chatter_title_label.setText("Chatters")
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
        groups: dict[str, list[tuple[str, str]]] = {
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
            if user_id == self.twitch_service.broadcaster_user_id:
                continue
            record = self.chatter_history.records.get(user_id)
            manual_group = record.manual_group if record else ""
            is_bot = (
                manual_group == "Bots"
                or user_id in self.known_bot_user_ids
                or self.chatter_history.is_bot(user_id)
            )
            if is_bot:
                groups["Bots"].append((user_name, user_id))
            elif user_id in result.moderator_ids:
                groups["Moderators"].append((user_name, user_id))
            elif user_id in result.vip_ids:
                groups["VIPs"].append((user_name, user_id))
            elif user_id in result.subscriber_ids:
                groups["Subscribers"].append((user_name, user_id))
            elif manual_group:
                groups[manual_group].append((user_name, user_id))
            elif self.chatter_history.is_regular(user_id):
                groups["Regulars"].append((user_name, user_id))
            else:
                groups["Viewers"].append((user_name, user_id))
        for group_name, users in groups.items():
            group_item = QTreeWidgetItem([f"{group_name} ({len(users)})"])
            for user_name, user_id in sorted(
                users, key=lambda user: user[0].casefold()
            ):
                child = QTreeWidgetItem([user_name])
                child.setData(
                    0,
                    Qt.ItemDataRole.UserRole,
                    {"user_id": user_id, "user_name": user_name},
                )
                group_item.addChild(child)
            self.chatter_list.addTopLevelItem(group_item)
            group_item.setExpanded(True)
        visible_chatter_count = sum(len(users) for users in groups.values())
        self.chatter_title_label.setText(
            f"Chatters ({visible_chatter_count:,})"
        )

    @Slot(object)
    def _show_chatter_tree_context_menu(self, position) -> None:
        item = self.chatter_list.itemAt(position)
        if item is None or item.parent() is None:
            return
        details = item.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(details, dict):
            return
        self._show_chatter_context_menu(
            str(details.get("user_id", "")),
            str(details.get("user_name", "")),
            "",
        )

    @Slot(str, str, str)
    def _show_chatter_context_menu(
        self,
        user_id: str,
        user_name: str,
        message_id: str,
    ) -> None:
        if not user_id:
            return
        menu = QMenu(self)
        heading = menu.addAction(user_name or user_id)
        heading.setEnabled(False)
        move_menu = menu.addMenu("Move to local group")
        record = self.chatter_history.records.get(user_id)
        current_group = record.manual_group if record else ""
        for title, group in (
            ("Automatic", ""),
            ("Regulars", "Regulars"),
            ("Bots", "Bots"),
            ("Viewers", "Viewers"),
        ):
            action = move_menu.addAction(title)
            action.setCheckable(True)
            action.setChecked(current_group == group)
            action.triggered.connect(
                lambda _checked=False, selected=group: self._set_local_chatter_group(
                    user_id, selected
                )
            )

        menu.addSeparator()
        token = self.twitch_auth.token
        scopes = set(token.scopes) if token is not None else set()
        can_ban = (
            "moderator:manage:banned_users" in scopes
            and bool(self.twitch_service.broadcaster_user_id)
        )
        can_delete = (
            "moderator:manage:chat_messages" in scopes
            and bool(self.twitch_service.broadcaster_user_id)
            and bool(message_id)
        )
        for title, action_name, duration in (
            ("Timeout 10 minutes", "timeout", 600),
            ("Timeout 1 hour", "timeout", 3600),
            ("Ban…", "ban", None),
            ("Remove ban / timeout", "unban", None),
        ):
            action = menu.addAction(title)
            action.setEnabled(can_ban)
            action.triggered.connect(
                lambda _checked=False, name=action_name, seconds=duration: self._run_moderation_action(
                    name,
                    user_id,
                    user_name,
                    duration=seconds,
                )
            )
        delete_action = menu.addAction("Delete this message")
        delete_action.setEnabled(can_delete)
        delete_action.triggered.connect(
            lambda _checked=False: self._run_moderation_action(
                "delete_message",
                user_id,
                user_name,
                message_id=message_id,
            )
        )
        if not can_ban or (message_id and not can_delete):
            menu.addSeparator()
            permissions = menu.addAction("Enable moderation permissions…")
            permissions.triggered.connect(self.twitch_auth.sign_in)
        menu.exec(QCursor.pos())

    def _set_local_chatter_group(self, user_id: str, group: str) -> None:
        if user_id not in self.chatter_history.records:
            return
        self.chatter_history.set_manual_group(user_id, group)
        self.chatter_history.save()
        if self.last_companion_result is not None:
            self._apply_chatter_groups(self.last_companion_result)
        self._refresh_memory_viewer_list()

    def _run_moderation_action(
        self,
        action: str,
        user_id: str,
        user_name: str,
        *,
        message_id: str = "",
        duration: int | None = None,
    ) -> None:
        reason = ""
        if action == "ban":
            reason, accepted = QInputDialog.getText(
                self,
                f"Ban {user_name}",
                "Reason (optional):",
            )
            if not accepted:
                return
        labels = {
            "timeout": f"timeout {user_name}",
            "ban": f"ban {user_name}",
            "unban": f"remove the ban or timeout for {user_name}",
            "delete_message": f"delete {user_name}'s message",
        }
        answer = QMessageBox.question(
            self,
            "Confirm Twitch moderation",
            f"Are you sure you want to {labels[action]}?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if self.twitch_service.moderate_user(
            action,
            user_id,
            message_id=message_id,
            duration=duration,
            reason=reason,
        ):
            self.statusBar().showMessage(
                f"Twitch moderation completed for {user_name}.",
                5000,
            )

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
        self.ui.mainStack.setCurrentWidget(self.settings_container)
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
        self.ai_test_report_flush_timer.stop()
        try:
            self.test_report_store.save()
        except OSError as error:
            Logger.warning(
                f"Could not save anonymous AI test diagnostics: {error}",
                source="AI",
            )
        self.companion_refresh_request_id += 1
        self.companion_thread_pool.clear()
        self.companion_thread_pool.waitForDone(2_000)
        self.memory_reasoning_thread_pool.clear()
        self.memory_reasoning_thread_pool.waitForDone(2_000)
        self.memory_message_buffers.clear()
        self.response_decision_thread_pool.clear()
        self.response_decision_thread_pool.waitForDone(2_000)
        self.response_decision_queue.clear()
        self.recent_ai_chat.clear()
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
            "twitch_bot_auth_changed",
            self.twitch_bridge.handle_bot_auth_changed,
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
