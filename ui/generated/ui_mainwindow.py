# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'mainwindow.ui'
##
## Created by: Qt User Interface Compiler version 6.11.1
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox,
    QFontComboBox, QFormLayout, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QMainWindow, QMenuBar, QPlainTextEdit,
    QPushButton, QSizePolicy, QSpacerItem, QSpinBox,
    QStackedWidget, QStatusBar, QTabWidget, QTableWidget,
    QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget)

class Ui_MainWindow(object):
    def setupUi(self, MainWindow):
        if not MainWindow.objectName():
            MainWindow.setObjectName(u"MainWindow")
        MainWindow.resize(856, 405)
        self.centralwidget = QWidget(MainWindow)
        self.centralwidget.setObjectName(u"centralwidget")
        self.horizontalLayout_2 = QHBoxLayout(self.centralwidget)
        self.horizontalLayout_2.setObjectName(u"horizontalLayout_2")
        self.horizontalLayout = QHBoxLayout()
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.navigationFrame = QFrame(self.centralwidget)
        self.navigationFrame.setObjectName(u"navigationFrame")
        self.navigationFrame.setStyleSheet(u"QPushButton {\n"
"    border: none;\n"
"    border-radius: 0px;\n"
"    padding: 0px;\n"
"    min-height: 40px;\n"
"}\n"
"QPushButton:hover {\n"
"    background-color: palette(midlight);\n"
"}\n"
"QPushButton:checked {\n"
"    background-color: palette(highlight);\n"
"    color: palette(highlighted-text);\n"
"    font-weight: bold;\n"
"}")
        self.navigationFrame.setMinimumSize(QSize(170, 0))
        self.navigationFrame.setMaximumSize(QSize(220, 16777215))
        self.navigationFrame.setFrameShape(QFrame.Shape.StyledPanel)
        self.navigationFrame.setFrameShadow(QFrame.Shadow.Raised)
        self.verticalLayout = QVBoxLayout(self.navigationFrame)
        self.verticalLayout.setSpacing(0)
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.verticalLayout.setContentsMargins(0, 0, 0, 0)
        self.dashboardButton = QPushButton(self.navigationFrame)
        self.dashboardButton.setObjectName(u"dashboardButton")
        self.dashboardButton.setCheckable(True)

        self.verticalLayout.addWidget(self.dashboardButton)

        self.twitchButton = QPushButton(self.navigationFrame)
        self.twitchButton.setObjectName(u"twitchButton")
        self.twitchButton.setCheckable(True)

        self.verticalLayout.addWidget(self.twitchButton)

        self.logsButton = QPushButton(self.navigationFrame)
        self.logsButton.setObjectName(u"logsButton")
        self.logsButton.setCheckable(True)

        self.verticalLayout.addWidget(self.logsButton)

        self.verticalSpacer = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.verticalLayout.addItem(self.verticalSpacer)

        self.settingsButton = QPushButton(self.navigationFrame)
        self.settingsButton.setObjectName(u"settingsButton")
        self.settingsButton.setCheckable(True)

        self.verticalLayout.addWidget(self.settingsButton)


        self.horizontalLayout.addWidget(self.navigationFrame)

        self.mainStack = QStackedWidget(self.centralwidget)
        self.mainStack.setObjectName(u"mainStack")
        self.dashboardPage = QWidget()
        self.dashboardPage.setObjectName(u"dashboardPage")
        self.dashboardLayout = QVBoxLayout(self.dashboardPage)
        self.dashboardLayout.setObjectName(u"dashboardLayout")
        self.dashboardTitleLabel = QLabel(self.dashboardPage)
        self.dashboardTitleLabel.setObjectName(u"dashboardTitleLabel")
        font = QFont()
        font.setPointSize(18)
        font.setBold(True)
        self.dashboardTitleLabel.setFont(font)

        self.dashboardLayout.addWidget(self.dashboardTitleLabel)

        self.serviceStatusLayout = QFormLayout()
        self.serviceStatusLayout.setObjectName(u"serviceStatusLayout")
        self.coreNameLabel = QLabel(self.dashboardPage)
        self.coreNameLabel.setObjectName(u"coreNameLabel")

        self.serviceStatusLayout.setWidget(0, QFormLayout.ItemRole.LabelRole, self.coreNameLabel)

        self.coreStatusLabel = QLabel(self.dashboardPage)
        self.coreStatusLabel.setObjectName(u"coreStatusLabel")

        self.serviceStatusLayout.setWidget(0, QFormLayout.ItemRole.FieldRole, self.coreStatusLabel)

        self.aiNameLabel = QLabel(self.dashboardPage)
        self.aiNameLabel.setObjectName(u"aiNameLabel")

        self.serviceStatusLayout.setWidget(1, QFormLayout.ItemRole.LabelRole, self.aiNameLabel)

        self.aiStatusLabel = QLabel(self.dashboardPage)
        self.aiStatusLabel.setObjectName(u"aiStatusLabel")

        self.serviceStatusLayout.setWidget(1, QFormLayout.ItemRole.FieldRole, self.aiStatusLabel)

        self.twitchNameLabel = QLabel(self.dashboardPage)
        self.twitchNameLabel.setObjectName(u"twitchNameLabel")

        self.serviceStatusLayout.setWidget(2, QFormLayout.ItemRole.LabelRole, self.twitchNameLabel)

        self.twitchStatusLabel = QLabel(self.dashboardPage)
        self.twitchStatusLabel.setObjectName(u"twitchStatusLabel")

        self.serviceStatusLayout.setWidget(2, QFormLayout.ItemRole.FieldRole, self.twitchStatusLabel)

        self.obsNameLabel = QLabel(self.dashboardPage)
        self.obsNameLabel.setObjectName(u"obsNameLabel")

        self.serviceStatusLayout.setWidget(3, QFormLayout.ItemRole.LabelRole, self.obsNameLabel)

        self.obsStatusLabel = QLabel(self.dashboardPage)
        self.obsStatusLabel.setObjectName(u"obsStatusLabel")

        self.serviceStatusLayout.setWidget(3, QFormLayout.ItemRole.FieldRole, self.obsStatusLabel)

        self.voiceNameLabel = QLabel(self.dashboardPage)
        self.voiceNameLabel.setObjectName(u"voiceNameLabel")

        self.serviceStatusLayout.setWidget(4, QFormLayout.ItemRole.LabelRole, self.voiceNameLabel)

        self.voiceStatusLabel = QLabel(self.dashboardPage)
        self.voiceStatusLabel.setObjectName(u"voiceStatusLabel")

        self.serviceStatusLayout.setWidget(4, QFormLayout.ItemRole.FieldRole, self.voiceStatusLabel)


        self.dashboardLayout.addLayout(self.serviceStatusLayout)

        self.dashboardSpacer = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.dashboardLayout.addItem(self.dashboardSpacer)

        self.mainStack.addWidget(self.dashboardPage)
        self.logsPage = QWidget()
        self.logsPage.setObjectName(u"logsPage")
        self.logsLayout = QVBoxLayout(self.logsPage)
        self.logsLayout.setObjectName(u"logsLayout")
        self.logsTitleLabel = QLabel(self.logsPage)
        self.logsTitleLabel.setObjectName(u"logsTitleLabel")
        self.logsTitleLabel.setFont(font)

        self.logsLayout.addWidget(self.logsTitleLabel)

        self.logOutput = QPlainTextEdit(self.logsPage)
        self.logOutput.setObjectName(u"logOutput")
        self.logOutput.setReadOnly(True)
        self.logOutput.setMaximumBlockCount(2000)

        self.logsLayout.addWidget(self.logOutput)

        self.logTestButtonsLayout = QHBoxLayout()
        self.logTestButtonsLayout.setObjectName(u"logTestButtonsLayout")
        self.testInfoButton = QPushButton(self.logsPage)
        self.testInfoButton.setObjectName(u"testInfoButton")

        self.logTestButtonsLayout.addWidget(self.testInfoButton)

        self.testWarningButton = QPushButton(self.logsPage)
        self.testWarningButton.setObjectName(u"testWarningButton")

        self.logTestButtonsLayout.addWidget(self.testWarningButton)

        self.testErrorButton = QPushButton(self.logsPage)
        self.testErrorButton.setObjectName(u"testErrorButton")

        self.logTestButtonsLayout.addWidget(self.testErrorButton)


        self.logsLayout.addLayout(self.logTestButtonsLayout)

        self.mainStack.addWidget(self.logsPage)
        self.settingsPage = QWidget()
        self.settingsPage.setObjectName(u"settingsPage")
        self.settingsLayout = QVBoxLayout(self.settingsPage)
        self.settingsLayout.setObjectName(u"settingsLayout")
        self.settingsTitleLabel = QLabel(self.settingsPage)
        self.settingsTitleLabel.setObjectName(u"settingsTitleLabel")
        self.settingsTitleLabel.setFont(font)

        self.settingsLayout.addWidget(self.settingsTitleLabel)

        self.generalSettingsGroup = QGroupBox(self.settingsPage)
        self.generalSettingsGroup.setObjectName(u"generalSettingsGroup")
        self.generalSettingsLayout = QFormLayout(self.generalSettingsGroup)
        self.generalSettingsLayout.setObjectName(u"generalSettingsLayout")
        self.startupPageLabel = QLabel(self.generalSettingsGroup)
        self.startupPageLabel.setObjectName(u"startupPageLabel")

        self.generalSettingsLayout.setWidget(0, QFormLayout.ItemRole.LabelRole, self.startupPageLabel)

        self.startupPageCombo = QComboBox(self.generalSettingsGroup)
        self.startupPageCombo.setObjectName(u"startupPageCombo")

        self.generalSettingsLayout.setWidget(0, QFormLayout.ItemRole.FieldRole, self.startupPageCombo)


        self.settingsLayout.addWidget(self.generalSettingsGroup)

        self.loggingSettingsGroup = QGroupBox(self.settingsPage)
        self.loggingSettingsGroup.setObjectName(u"loggingSettingsGroup")
        self.loggingSettingsLayout = QFormLayout(self.loggingSettingsGroup)
        self.loggingSettingsLayout.setObjectName(u"loggingSettingsLayout")
        self.logLevelLabel = QLabel(self.loggingSettingsGroup)
        self.logLevelLabel.setObjectName(u"logLevelLabel")

        self.loggingSettingsLayout.setWidget(0, QFormLayout.ItemRole.LabelRole, self.logLevelLabel)

        self.logLevelCombo = QComboBox(self.loggingSettingsGroup)
        self.logLevelCombo.setObjectName(u"logLevelCombo")

        self.loggingSettingsLayout.setWidget(0, QFormLayout.ItemRole.FieldRole, self.logLevelCombo)

        self.uiLogLimitLabel = QLabel(self.loggingSettingsGroup)
        self.uiLogLimitLabel.setObjectName(u"uiLogLimitLabel")

        self.loggingSettingsLayout.setWidget(1, QFormLayout.ItemRole.LabelRole, self.uiLogLimitLabel)

        self.uiLogLimitSpin = QSpinBox(self.loggingSettingsGroup)
        self.uiLogLimitSpin.setObjectName(u"uiLogLimitSpin")
        self.uiLogLimitSpin.setMinimum(100)
        self.uiLogLimitSpin.setMaximum(10000)
        self.uiLogLimitSpin.setSingleStep(100)

        self.loggingSettingsLayout.setWidget(1, QFormLayout.ItemRole.FieldRole, self.uiLogLimitSpin)


        self.settingsLayout.addWidget(self.loggingSettingsGroup)

        self.twitchChatSettingsGroup = QGroupBox(self.settingsPage)
        self.twitchChatSettingsGroup.setObjectName(u"twitchChatSettingsGroup")
        self.twitchChatSettingsLayout = QFormLayout(self.twitchChatSettingsGroup)
        self.twitchChatSettingsLayout.setObjectName(u"twitchChatSettingsLayout")
        self.twitchChatTimestampLabel = QLabel(self.twitchChatSettingsGroup)
        self.twitchChatTimestampLabel.setObjectName(u"twitchChatTimestampLabel")

        self.twitchChatSettingsLayout.setWidget(0, QFormLayout.ItemRole.LabelRole, self.twitchChatTimestampLabel)

        self.twitchChatTimestampCheck = QCheckBox(self.twitchChatSettingsGroup)
        self.twitchChatTimestampCheck.setObjectName(u"twitchChatTimestampCheck")

        self.twitchChatSettingsLayout.setWidget(0, QFormLayout.ItemRole.FieldRole, self.twitchChatTimestampCheck)

        self.twitchChatFontLabel = QLabel(self.twitchChatSettingsGroup)
        self.twitchChatFontLabel.setObjectName(u"twitchChatFontLabel")

        self.twitchChatSettingsLayout.setWidget(1, QFormLayout.ItemRole.LabelRole, self.twitchChatFontLabel)

        self.twitchChatFontCombo = QFontComboBox(self.twitchChatSettingsGroup)
        self.twitchChatFontCombo.setObjectName(u"twitchChatFontCombo")

        self.twitchChatSettingsLayout.setWidget(1, QFormLayout.ItemRole.FieldRole, self.twitchChatFontCombo)

        self.twitchChatFontSizeLabel = QLabel(self.twitchChatSettingsGroup)
        self.twitchChatFontSizeLabel.setObjectName(u"twitchChatFontSizeLabel")

        self.twitchChatSettingsLayout.setWidget(2, QFormLayout.ItemRole.LabelRole, self.twitchChatFontSizeLabel)

        self.twitchChatFontSizeSpin = QSpinBox(self.twitchChatSettingsGroup)
        self.twitchChatFontSizeSpin.setObjectName(u"twitchChatFontSizeSpin")
        self.twitchChatFontSizeSpin.setMinimum(8)
        self.twitchChatFontSizeSpin.setMaximum(24)

        self.twitchChatSettingsLayout.setWidget(2, QFormLayout.ItemRole.FieldRole, self.twitchChatFontSizeSpin)


        self.settingsLayout.addWidget(self.twitchChatSettingsGroup)

        self.developerSettingsGroup = QGroupBox(self.settingsPage)
        self.developerSettingsGroup.setObjectName(u"developerSettingsGroup")
        self.developerSettingsLayout = QVBoxLayout(self.developerSettingsGroup)
        self.developerSettingsLayout.setObjectName(u"developerSettingsLayout")
        self.showDeveloperToolsCheck = QCheckBox(self.developerSettingsGroup)
        self.showDeveloperToolsCheck.setObjectName(u"showDeveloperToolsCheck")

        self.developerSettingsLayout.addWidget(self.showDeveloperToolsCheck)

        self.toggleDeveloperToolsButton = QPushButton(self.developerSettingsGroup)
        self.toggleDeveloperToolsButton.setObjectName(u"toggleDeveloperToolsButton")

        self.developerSettingsLayout.addWidget(self.toggleDeveloperToolsButton)


        self.settingsLayout.addWidget(self.developerSettingsGroup)

        self.settingsStatusLabel = QLabel(self.settingsPage)
        self.settingsStatusLabel.setObjectName(u"settingsStatusLabel")

        self.settingsLayout.addWidget(self.settingsStatusLabel)

        self.settingsSpacer = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.settingsLayout.addItem(self.settingsSpacer)

        self.settingsButtonsLayout = QHBoxLayout()
        self.settingsButtonsLayout.setObjectName(u"settingsButtonsLayout")
        self.settingsButtonSpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.settingsButtonsLayout.addItem(self.settingsButtonSpacer)

        self.resetSettingsButton = QPushButton(self.settingsPage)
        self.resetSettingsButton.setObjectName(u"resetSettingsButton")

        self.settingsButtonsLayout.addWidget(self.resetSettingsButton)

        self.saveSettingsButton = QPushButton(self.settingsPage)
        self.saveSettingsButton.setObjectName(u"saveSettingsButton")

        self.settingsButtonsLayout.addWidget(self.saveSettingsButton)


        self.settingsLayout.addLayout(self.settingsButtonsLayout)

        self.mainStack.addWidget(self.settingsPage)
        self.twitchPage = QWidget()
        self.twitchPage.setObjectName(u"twitchPage")
        self.twitchPageLayout = QVBoxLayout(self.twitchPage)
        self.twitchPageLayout.setObjectName(u"twitchPageLayout")
        self.twitchTitleLabel = QLabel(self.twitchPage)
        self.twitchTitleLabel.setObjectName(u"twitchTitleLabel")
        self.twitchTitleLabel.setFont(font)

        self.twitchPageLayout.addWidget(self.twitchTitleLabel)

        self.twitchConnectionGroup = QGroupBox(self.twitchPage)
        self.twitchConnectionGroup.setObjectName(u"twitchConnectionGroup")
        self.twitchConnectionLayout = QGridLayout(self.twitchConnectionGroup)
        self.twitchConnectionLayout.setObjectName(u"twitchConnectionLayout")
        self.twitchAccountLabel = QLabel(self.twitchConnectionGroup)
        self.twitchAccountLabel.setObjectName(u"twitchAccountLabel")

        self.twitchConnectionLayout.addWidget(self.twitchAccountLabel, 0, 0, 1, 1)

        self.twitchAccountStatusLabel = QLabel(self.twitchConnectionGroup)
        self.twitchAccountStatusLabel.setObjectName(u"twitchAccountStatusLabel")
        self.twitchAccountStatusLabel.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.twitchConnectionLayout.addWidget(self.twitchAccountStatusLabel, 0, 1, 1, 1)

        self.twitchAuthButtonsLayout = QHBoxLayout()
        self.twitchAuthButtonsLayout.setObjectName(u"twitchAuthButtonsLayout")
        self.twitchSignInButton = QPushButton(self.twitchConnectionGroup)
        self.twitchSignInButton.setObjectName(u"twitchSignInButton")

        self.twitchAuthButtonsLayout.addWidget(self.twitchSignInButton)

        self.twitchSignOutButton = QPushButton(self.twitchConnectionGroup)
        self.twitchSignOutButton.setObjectName(u"twitchSignOutButton")
        self.twitchSignOutButton.setEnabled(False)

        self.twitchAuthButtonsLayout.addWidget(self.twitchSignOutButton)


        self.twitchConnectionLayout.addLayout(self.twitchAuthButtonsLayout, 0, 2, 1, 1)

        self.twitchChannelLabel = QLabel(self.twitchConnectionGroup)
        self.twitchChannelLabel.setObjectName(u"twitchChannelLabel")

        self.twitchConnectionLayout.addWidget(self.twitchChannelLabel, 1, 0, 1, 1)

        self.twitchChannelEdit = QLineEdit(self.twitchConnectionGroup)
        self.twitchChannelEdit.setObjectName(u"twitchChannelEdit")

        self.twitchConnectionLayout.addWidget(self.twitchChannelEdit, 1, 1, 1, 2)

        self.twitchConnectionStatusNameLabel = QLabel(self.twitchConnectionGroup)
        self.twitchConnectionStatusNameLabel.setObjectName(u"twitchConnectionStatusNameLabel")

        self.twitchConnectionLayout.addWidget(self.twitchConnectionStatusNameLabel, 2, 0, 1, 1)

        self.twitchConnectionStatusLabel = QLabel(self.twitchConnectionGroup)
        self.twitchConnectionStatusLabel.setObjectName(u"twitchConnectionStatusLabel")

        self.twitchConnectionLayout.addWidget(self.twitchConnectionStatusLabel, 2, 1, 1, 1)

        self.twitchConnectionButtonsLayout = QHBoxLayout()
        self.twitchConnectionButtonsLayout.setObjectName(u"twitchConnectionButtonsLayout")
        self.twitchConnectButton = QPushButton(self.twitchConnectionGroup)
        self.twitchConnectButton.setObjectName(u"twitchConnectButton")

        self.twitchConnectionButtonsLayout.addWidget(self.twitchConnectButton)

        self.twitchDisconnectButton = QPushButton(self.twitchConnectionGroup)
        self.twitchDisconnectButton.setObjectName(u"twitchDisconnectButton")
        self.twitchDisconnectButton.setEnabled(False)

        self.twitchConnectionButtonsLayout.addWidget(self.twitchDisconnectButton)


        self.twitchConnectionLayout.addLayout(self.twitchConnectionButtonsLayout, 2, 2, 1, 1)

        self.twitchListenerNameLabel = QLabel(self.twitchConnectionGroup)
        self.twitchListenerNameLabel.setObjectName(u"twitchListenerNameLabel")

        self.twitchConnectionLayout.addWidget(self.twitchListenerNameLabel, 3, 0, 1, 1)

        self.twitchListenerUrlLabel = QLabel(self.twitchConnectionGroup)
        self.twitchListenerUrlLabel.setObjectName(u"twitchListenerUrlLabel")
        self.twitchListenerUrlLabel.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self.twitchConnectionLayout.addWidget(self.twitchListenerUrlLabel, 3, 1, 1, 2)


        self.twitchPageLayout.addWidget(self.twitchConnectionGroup)

        self.twitchErrorLabel = QLabel(self.twitchPage)
        self.twitchErrorLabel.setObjectName(u"twitchErrorLabel")
        self.twitchErrorLabel.setStyleSheet(u"color: #d9534f;")
        self.twitchErrorLabel.setWordWrap(True)

        self.twitchPageLayout.addWidget(self.twitchErrorLabel)

        self.twitchDetailTabs = QTabWidget(self.twitchPage)
        self.twitchDetailTabs.setObjectName(u"twitchDetailTabs")
        self.twitchChatTab = QWidget()
        self.twitchChatTab.setObjectName(u"twitchChatTab")
        self.twitchChatTabLayout = QVBoxLayout(self.twitchChatTab)
        self.twitchChatTabLayout.setObjectName(u"twitchChatTabLayout")
        self.twitchChatHeaderLayout = QHBoxLayout()
        self.twitchChatHeaderLayout.setObjectName(u"twitchChatHeaderLayout")
        self.twitchChatCountLabel = QLabel(self.twitchChatTab)
        self.twitchChatCountLabel.setObjectName(u"twitchChatCountLabel")
        font1 = QFont()
        font1.setBold(True)
        self.twitchChatCountLabel.setFont(font1)

        self.twitchChatHeaderLayout.addWidget(self.twitchChatCountLabel)

        self.twitchChatHeaderSpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.twitchChatHeaderLayout.addItem(self.twitchChatHeaderSpacer)

        self.clearTwitchChatButton = QPushButton(self.twitchChatTab)
        self.clearTwitchChatButton.setObjectName(u"clearTwitchChatButton")

        self.twitchChatHeaderLayout.addWidget(self.clearTwitchChatButton)


        self.twitchChatTabLayout.addLayout(self.twitchChatHeaderLayout)

        self.twitchChatOutput = QTextBrowser(self.twitchChatTab)
        self.twitchChatOutput.setObjectName(u"twitchChatOutput")
        self.twitchChatOutput.setStyleSheet(u"QTextBrowser {\n"
"    background-color: #18181b;\n"
"    color: #efeff1;\n"
"    border: 1px solid #303034;\n"
"    border-radius: 7px;\n"
"    padding: 8px;\n"
"    selection-background-color: #9147ff;\n"
"}")
        self.twitchChatOutput.setOpenExternalLinks(False)

        self.twitchChatTabLayout.addWidget(self.twitchChatOutput)

        self.twitchSendLayout = QHBoxLayout()
        self.twitchSendLayout.setObjectName(u"twitchSendLayout")
        self.twitchSendEdit = QLineEdit(self.twitchChatTab)
        self.twitchSendEdit.setObjectName(u"twitchSendEdit")
        self.twitchSendEdit.setEnabled(False)
        self.twitchSendEdit.setMaxLength(500)

        self.twitchSendLayout.addWidget(self.twitchSendEdit)

        self.twitchSendButton = QPushButton(self.twitchChatTab)
        self.twitchSendButton.setObjectName(u"twitchSendButton")
        self.twitchSendButton.setEnabled(False)

        self.twitchSendLayout.addWidget(self.twitchSendButton)


        self.twitchChatTabLayout.addLayout(self.twitchSendLayout)

        self.twitchSimulationGroup = QGroupBox(self.twitchChatTab)
        self.twitchSimulationGroup.setObjectName(u"twitchSimulationGroup")
        self.twitchSimulationLayout = QGridLayout(self.twitchSimulationGroup)
        self.twitchSimulationLayout.setObjectName(u"twitchSimulationLayout")
        self.simulationUsernameLabel = QLabel(self.twitchSimulationGroup)
        self.simulationUsernameLabel.setObjectName(u"simulationUsernameLabel")

        self.twitchSimulationLayout.addWidget(self.simulationUsernameLabel, 0, 0, 1, 1)

        self.simulationUsernameEdit = QLineEdit(self.twitchSimulationGroup)
        self.simulationUsernameEdit.setObjectName(u"simulationUsernameEdit")

        self.twitchSimulationLayout.addWidget(self.simulationUsernameEdit, 0, 1, 1, 1)

        self.simulationMessageLabel = QLabel(self.twitchSimulationGroup)
        self.simulationMessageLabel.setObjectName(u"simulationMessageLabel")

        self.twitchSimulationLayout.addWidget(self.simulationMessageLabel, 1, 0, 1, 1)

        self.simulationMessageEdit = QLineEdit(self.twitchSimulationGroup)
        self.simulationMessageEdit.setObjectName(u"simulationMessageEdit")

        self.twitchSimulationLayout.addWidget(self.simulationMessageEdit, 1, 1, 1, 1)

        self.simulateTwitchMessageButton = QPushButton(self.twitchSimulationGroup)
        self.simulateTwitchMessageButton.setObjectName(u"simulateTwitchMessageButton")
        self.simulateTwitchMessageButton.setEnabled(False)

        self.twitchSimulationLayout.addWidget(self.simulateTwitchMessageButton, 2, 1, 1, 1)


        self.twitchChatTabLayout.addWidget(self.twitchSimulationGroup)

        self.twitchDetailTabs.addTab(self.twitchChatTab, "")
        self.twitchEventsTab = QWidget()
        self.twitchEventsTab.setObjectName(u"twitchEventsTab")
        self.twitchEventsTabLayout = QVBoxLayout(self.twitchEventsTab)
        self.twitchEventsTabLayout.setObjectName(u"twitchEventsTabLayout")
        self.twitchEventSimulatorGroup = QGroupBox(self.twitchEventsTab)
        self.twitchEventSimulatorGroup.setObjectName(u"twitchEventSimulatorGroup")
        self.twitchEventSimulatorLayout = QGridLayout(self.twitchEventSimulatorGroup)
        self.twitchEventSimulatorLayout.setObjectName(u"twitchEventSimulatorLayout")
        self.twitchEventTypeLabel = QLabel(self.twitchEventSimulatorGroup)
        self.twitchEventTypeLabel.setObjectName(u"twitchEventTypeLabel")

        self.twitchEventSimulatorLayout.addWidget(self.twitchEventTypeLabel, 0, 0, 1, 1)

        self.twitchEventTypeCombo = QComboBox(self.twitchEventSimulatorGroup)
        self.twitchEventTypeCombo.setObjectName(u"twitchEventTypeCombo")

        self.twitchEventSimulatorLayout.addWidget(self.twitchEventTypeCombo, 0, 1, 1, 1)

        self.twitchEventVersionLabel = QLabel(self.twitchEventSimulatorGroup)
        self.twitchEventVersionLabel.setObjectName(u"twitchEventVersionLabel")

        self.twitchEventSimulatorLayout.addWidget(self.twitchEventVersionLabel, 0, 2, 1, 1)

        self.twitchEventVersionEdit = QLineEdit(self.twitchEventSimulatorGroup)
        self.twitchEventVersionEdit.setObjectName(u"twitchEventVersionEdit")
        self.twitchEventVersionEdit.setMaximumWidth(80)

        self.twitchEventSimulatorLayout.addWidget(self.twitchEventVersionEdit, 0, 3, 1, 1)

        self.twitchEventPayloadEdit = QPlainTextEdit(self.twitchEventSimulatorGroup)
        self.twitchEventPayloadEdit.setObjectName(u"twitchEventPayloadEdit")
        self.twitchEventPayloadEdit.setMaximumHeight(140)

        self.twitchEventSimulatorLayout.addWidget(self.twitchEventPayloadEdit, 1, 0, 1, 4)

        self.resetTwitchEventPayloadButton = QPushButton(self.twitchEventSimulatorGroup)
        self.resetTwitchEventPayloadButton.setObjectName(u"resetTwitchEventPayloadButton")

        self.twitchEventSimulatorLayout.addWidget(self.resetTwitchEventPayloadButton, 2, 2, 1, 1)

        self.sendTwitchEventButton = QPushButton(self.twitchEventSimulatorGroup)
        self.sendTwitchEventButton.setObjectName(u"sendTwitchEventButton")
        self.sendTwitchEventButton.setEnabled(False)

        self.twitchEventSimulatorLayout.addWidget(self.sendTwitchEventButton, 2, 3, 1, 1)


        self.twitchEventsTabLayout.addWidget(self.twitchEventSimulatorGroup)

        self.twitchEventFiltersLayout = QHBoxLayout()
        self.twitchEventFiltersLayout.setObjectName(u"twitchEventFiltersLayout")
        self.twitchEventSearchEdit = QLineEdit(self.twitchEventsTab)
        self.twitchEventSearchEdit.setObjectName(u"twitchEventSearchEdit")

        self.twitchEventFiltersLayout.addWidget(self.twitchEventSearchEdit)

        self.twitchEventResultCombo = QComboBox(self.twitchEventsTab)
        self.twitchEventResultCombo.setObjectName(u"twitchEventResultCombo")

        self.twitchEventFiltersLayout.addWidget(self.twitchEventResultCombo)

        self.pauseTwitchEventsCheck = QCheckBox(self.twitchEventsTab)
        self.pauseTwitchEventsCheck.setObjectName(u"pauseTwitchEventsCheck")

        self.twitchEventFiltersLayout.addWidget(self.pauseTwitchEventsCheck)

        self.clearTwitchEventsButton = QPushButton(self.twitchEventsTab)
        self.clearTwitchEventsButton.setObjectName(u"clearTwitchEventsButton")

        self.twitchEventFiltersLayout.addWidget(self.clearTwitchEventsButton)


        self.twitchEventsTabLayout.addLayout(self.twitchEventFiltersLayout)

        self.twitchEventTable = QTableWidget(self.twitchEventsTab)
        self.twitchEventTable.setObjectName(u"twitchEventTable")
        self.twitchEventTable.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.twitchEventTable.setAlternatingRowColors(True)
        self.twitchEventTable.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.twitchEventTable.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        self.twitchEventsTabLayout.addWidget(self.twitchEventTable)

        self.twitchEventDetails = QPlainTextEdit(self.twitchEventsTab)
        self.twitchEventDetails.setObjectName(u"twitchEventDetails")
        self.twitchEventDetails.setReadOnly(True)

        self.twitchEventsTabLayout.addWidget(self.twitchEventDetails)

        self.twitchEventDetailsButtonsLayout = QHBoxLayout()
        self.twitchEventDetailsButtonsLayout.setObjectName(u"twitchEventDetailsButtonsLayout")
        self.twitchEventDetailsSpacer = QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)

        self.twitchEventDetailsButtonsLayout.addItem(self.twitchEventDetailsSpacer)

        self.copyTwitchEventButton = QPushButton(self.twitchEventsTab)
        self.copyTwitchEventButton.setObjectName(u"copyTwitchEventButton")
        self.copyTwitchEventButton.setEnabled(False)

        self.twitchEventDetailsButtonsLayout.addWidget(self.copyTwitchEventButton)


        self.twitchEventsTabLayout.addLayout(self.twitchEventDetailsButtonsLayout)

        self.twitchDetailTabs.addTab(self.twitchEventsTab, "")

        self.twitchPageLayout.addWidget(self.twitchDetailTabs)

        self.mainStack.addWidget(self.twitchPage)

        self.horizontalLayout.addWidget(self.mainStack)


        self.horizontalLayout_2.addLayout(self.horizontalLayout)

        MainWindow.setCentralWidget(self.centralwidget)
        self.menubar = QMenuBar(MainWindow)
        self.menubar.setObjectName(u"menubar")
        self.menubar.setGeometry(QRect(0, 0, 856, 33))
        MainWindow.setMenuBar(self.menubar)
        self.statusbar = QStatusBar(MainWindow)
        self.statusbar.setObjectName(u"statusbar")
        MainWindow.setStatusBar(self.statusbar)

        self.retranslateUi(MainWindow)

        self.twitchDetailTabs.setCurrentIndex(0)


        QMetaObject.connectSlotsByName(MainWindow)
    # setupUi

    def retranslateUi(self, MainWindow):
        MainWindow.setWindowTitle(QCoreApplication.translate("MainWindow", u"Sally AI", None))
        self.dashboardButton.setText(QCoreApplication.translate("MainWindow", u"Dashboard", None))
        self.twitchButton.setText(QCoreApplication.translate("MainWindow", u"Your Channel", None))
        self.logsButton.setText(QCoreApplication.translate("MainWindow", u"Logs", None))
        self.settingsButton.setText(QCoreApplication.translate("MainWindow", u"Settings", None))
        self.dashboardTitleLabel.setText(QCoreApplication.translate("MainWindow", u"Sally Status", None))
        self.coreNameLabel.setText(QCoreApplication.translate("MainWindow", u"Sally Core", None))
        self.coreStatusLabel.setText(QCoreApplication.translate("MainWindow", u"Ready", None))
        self.aiNameLabel.setText(QCoreApplication.translate("MainWindow", u"AI Provider", None))
        self.aiStatusLabel.setText(QCoreApplication.translate("MainWindow", u"Not configured", None))
        self.twitchNameLabel.setText(QCoreApplication.translate("MainWindow", u"Twitch", None))
        self.twitchStatusLabel.setText(QCoreApplication.translate("MainWindow", u"Disconnected", None))
        self.obsNameLabel.setText(QCoreApplication.translate("MainWindow", u"OBS", None))
        self.obsStatusLabel.setText(QCoreApplication.translate("MainWindow", u"Disconnected", None))
        self.voiceNameLabel.setText(QCoreApplication.translate("MainWindow", u"Voice", None))
        self.voiceStatusLabel.setText(QCoreApplication.translate("MainWindow", u"Disabled", None))
        self.logsTitleLabel.setText(QCoreApplication.translate("MainWindow", u"Application Logs", None))
        self.logOutput.setPlaceholderText(QCoreApplication.translate("MainWindow", u"Live log messages will appear here.", None))
        self.testInfoButton.setText(QCoreApplication.translate("MainWindow", u"Test Info", None))
        self.testWarningButton.setText(QCoreApplication.translate("MainWindow", u"Test Warning", None))
        self.testErrorButton.setText(QCoreApplication.translate("MainWindow", u"Test Error", None))
        self.settingsTitleLabel.setText(QCoreApplication.translate("MainWindow", u"Settings", None))
        self.generalSettingsGroup.setTitle(QCoreApplication.translate("MainWindow", u"General", None))
        self.startupPageLabel.setText(QCoreApplication.translate("MainWindow", u"Startup page", None))
        self.loggingSettingsGroup.setTitle(QCoreApplication.translate("MainWindow", u"Logging", None))
        self.logLevelLabel.setText(QCoreApplication.translate("MainWindow", u"Minimum log level", None))
        self.uiLogLimitLabel.setText(QCoreApplication.translate("MainWindow", u"UI log entry limit", None))
        self.twitchChatSettingsGroup.setTitle(QCoreApplication.translate("MainWindow", u"Twitch Chat", None))
        self.twitchChatTimestampLabel.setText(QCoreApplication.translate("MainWindow", u"Timestamps", None))
        self.twitchChatTimestampCheck.setText(QCoreApplication.translate("MainWindow", u"Show message timestamps", None))
        self.twitchChatFontLabel.setText(QCoreApplication.translate("MainWindow", u"Font", None))
        self.twitchChatFontSizeLabel.setText(QCoreApplication.translate("MainWindow", u"Font size", None))
        self.twitchChatFontSizeSpin.setSuffix(QCoreApplication.translate("MainWindow", u" pt", None))
        self.developerSettingsGroup.setTitle(QCoreApplication.translate("MainWindow", u"Developer", None))
        self.showDeveloperToolsCheck.setText(QCoreApplication.translate("MainWindow", u"Enable developer tools", None))
        self.toggleDeveloperToolsButton.setText(QCoreApplication.translate("MainWindow", u"Open Developer Tools", None))
        self.settingsStatusLabel.setText("")
        self.resetSettingsButton.setText(QCoreApplication.translate("MainWindow", u"Reset Defaults", None))
        self.saveSettingsButton.setText(QCoreApplication.translate("MainWindow", u"Save Settings", None))
        self.twitchTitleLabel.setText(QCoreApplication.translate("MainWindow", u"Your Channel", None))
        self.twitchConnectionGroup.setTitle(QCoreApplication.translate("MainWindow", u"Connection", None))
        self.twitchAccountLabel.setText(QCoreApplication.translate("MainWindow", u"Twitch account", None))
        self.twitchAccountStatusLabel.setText(QCoreApplication.translate("MainWindow", u"Not signed in", None))
        self.twitchSignInButton.setText(QCoreApplication.translate("MainWindow", u"Sign in with Twitch", None))
        self.twitchSignOutButton.setText(QCoreApplication.translate("MainWindow", u"Sign out", None))
        self.twitchChannelLabel.setText(QCoreApplication.translate("MainWindow", u"Your channel", None))
        self.twitchChannelEdit.setPlaceholderText(QCoreApplication.translate("MainWindow", u"Streamer channel name", None))
        self.twitchConnectionStatusNameLabel.setText(QCoreApplication.translate("MainWindow", u"Status", None))
        self.twitchConnectionStatusLabel.setText(QCoreApplication.translate("MainWindow", u"Disconnected", None))
        self.twitchConnectButton.setText(QCoreApplication.translate("MainWindow", u"Connect", None))
        self.twitchDisconnectButton.setText(QCoreApplication.translate("MainWindow", u"Disconnect", None))
        self.twitchListenerNameLabel.setText(QCoreApplication.translate("MainWindow", u"Local listener", None))
        self.twitchListenerUrlLabel.setText(QCoreApplication.translate("MainWindow", u"Stopped", None))
        self.twitchErrorLabel.setText("")
        self.twitchChatCountLabel.setText(QCoreApplication.translate("MainWindow", u"Chat - 0 messages", None))
        self.clearTwitchChatButton.setText(QCoreApplication.translate("MainWindow", u"Clear Chat", None))
        self.twitchSendEdit.setPlaceholderText(QCoreApplication.translate("MainWindow", u"Send a message as the signed-in Twitch account", None))
        self.twitchSendButton.setText(QCoreApplication.translate("MainWindow", u"Send", None))
        self.twitchSimulationGroup.setTitle(QCoreApplication.translate("MainWindow", u"Developer Simulation", None))
        self.simulationUsernameLabel.setText(QCoreApplication.translate("MainWindow", u"Username", None))
        self.simulationUsernameEdit.setText(QCoreApplication.translate("MainWindow", u"test_viewer", None))
        self.simulationMessageLabel.setText(QCoreApplication.translate("MainWindow", u"Message", None))
        self.simulationMessageEdit.setPlaceholderText(QCoreApplication.translate("MainWindow", u"Hello Sally!", None))
        self.simulateTwitchMessageButton.setText(QCoreApplication.translate("MainWindow", u"Simulate Message", None))
        self.twitchDetailTabs.setTabText(self.twitchDetailTabs.indexOf(self.twitchChatTab), QCoreApplication.translate("MainWindow", u"Chat", None))
        self.twitchEventSimulatorGroup.setTitle(QCoreApplication.translate("MainWindow", u"Developer Event Simulator", None))
        self.twitchEventTypeLabel.setText(QCoreApplication.translate("MainWindow", u"Event type", None))
        self.twitchEventVersionLabel.setText(QCoreApplication.translate("MainWindow", u"Version", None))
        self.twitchEventVersionEdit.setText(QCoreApplication.translate("MainWindow", u"1", None))
        self.twitchEventPayloadEdit.setPlaceholderText(QCoreApplication.translate("MainWindow", u"Editable Twitch EventSub JSON payload", None))
        self.resetTwitchEventPayloadButton.setText(QCoreApplication.translate("MainWindow", u"Reset Payload", None))
        self.sendTwitchEventButton.setText(QCoreApplication.translate("MainWindow", u"Send Signed Event", None))
        self.twitchEventSearchEdit.setPlaceholderText(QCoreApplication.translate("MainWindow", u"Search events...", None))
        self.pauseTwitchEventsCheck.setText(QCoreApplication.translate("MainWindow", u"Pause follow", None))
        self.clearTwitchEventsButton.setText(QCoreApplication.translate("MainWindow", u"Clear Events", None))
        self.twitchEventDetails.setPlaceholderText(QCoreApplication.translate("MainWindow", u"Select an event to inspect its sanitized headers and JSON payload.", None))
        self.copyTwitchEventButton.setText(QCoreApplication.translate("MainWindow", u"Copy Details", None))
        self.twitchDetailTabs.setTabText(self.twitchDetailTabs.indexOf(self.twitchEventsTab), QCoreApplication.translate("MainWindow", u"Events", None))
    # retranslateUi

