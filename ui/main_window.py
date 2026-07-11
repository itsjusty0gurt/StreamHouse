import logging

from PySide6.QtCore import Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QButtonGroup, QMainWindow

from core.logger import Logger
from core.settings import AppSettings, SettingsStore
from ui.generated.ui_mainwindow import Ui_MainWindow
from ui.log_handler import QtLogHandler


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()

        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)

        self.navigation_group = QButtonGroup(self)
        self.navigation_group.setExclusive(True)
        for button in (
            self.ui.dashboardButton,
            self.ui.twitchButton,
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

        self.ui.dashboardButton.clicked.connect(self.show_dashboard)
        self.ui.twitchButton.clicked.connect(self.show_twitch)
        self.ui.logsButton.clicked.connect(self.show_logs)
        self.ui.settingsButton.clicked.connect(self.show_settings)
        self.ui.testInfoButton.clicked.connect(self.test_info_log)
        self.ui.testWarningButton.clicked.connect(self.test_warning_log)
        self.ui.testErrorButton.clicked.connect(self.test_error_log)
        self.ui.saveSettingsButton.clicked.connect(self.save_settings)
        self.ui.resetSettingsButton.clicked.connect(self.reset_settings)

        self._populate_settings_controls()
        self._apply_settings(self.settings)
        self._show_startup_page()
        Logger.info("UI log viewer connected.", source="UI")

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

    def _settings_from_controls(self) -> AppSettings:
        return AppSettings(
            startup_page=self.ui.startupPageCombo.currentText(),
            log_level=self.ui.logLevelCombo.currentText(),
            ui_log_limit=self.ui.uiLogLimitSpin.value(),
            show_developer_tools=(
                self.ui.showDeveloperToolsCheck.isChecked()
            ),
        )

    def _apply_settings(self, settings: AppSettings) -> None:
        Logger.set_level(getattr(logging, settings.log_level))
        self.ui.logOutput.setMaximumBlockCount(settings.ui_log_limit)
        self.ui.testInfoButton.setVisible(settings.show_developer_tools)
        self.ui.testWarningButton.setVisible(settings.show_developer_tools)
        self.ui.testErrorButton.setVisible(settings.show_developer_tools)

    def _show_startup_page(self) -> None:
        page_actions = {
            "Dashboard": self.show_dashboard,
            "Twitch": self.show_twitch,
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
    def show_logs(self) -> None:
        self.ui.mainStack.setCurrentWidget(self.ui.logsPage)
        self.ui.logsButton.setChecked(True)

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

    def closeEvent(self, event: QCloseEvent) -> None:
        Logger.remove_handler(self.log_handler)
        super().closeEvent(event)
