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
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFormLayout,
    QFrame, QGroupBox, QHBoxLayout, QLabel,
    QMainWindow, QMenuBar, QPlainTextEdit, QPushButton,
    QSizePolicy, QSpacerItem, QSpinBox, QStackedWidget,
    QStatusBar, QVBoxLayout, QWidget)

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
"    border-radius: 5px;\n"
"    padding: 8px 12px;\n"
"    text-align: left;\n"
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
        self.verticalLayout.setObjectName(u"verticalLayout")
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

        self.developerSettingsGroup = QGroupBox(self.settingsPage)
        self.developerSettingsGroup.setObjectName(u"developerSettingsGroup")
        self.developerSettingsLayout = QVBoxLayout(self.developerSettingsGroup)
        self.developerSettingsLayout.setObjectName(u"developerSettingsLayout")
        self.showDeveloperToolsCheck = QCheckBox(self.developerSettingsGroup)
        self.showDeveloperToolsCheck.setObjectName(u"showDeveloperToolsCheck")

        self.developerSettingsLayout.addWidget(self.showDeveloperToolsCheck)


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

        QMetaObject.connectSlotsByName(MainWindow)
    # setupUi

    def retranslateUi(self, MainWindow):
        MainWindow.setWindowTitle(QCoreApplication.translate("MainWindow", u"Sally AI", None))
        self.dashboardButton.setText(QCoreApplication.translate("MainWindow", u"Dashboard", None))
        self.twitchButton.setText(QCoreApplication.translate("MainWindow", u"Twitch", None))
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
        self.developerSettingsGroup.setTitle(QCoreApplication.translate("MainWindow", u"Developer", None))
        self.showDeveloperToolsCheck.setText(QCoreApplication.translate("MainWindow", u"Show log test controls", None))
        self.settingsStatusLabel.setText("")
        self.resetSettingsButton.setText(QCoreApplication.translate("MainWindow", u"Reset Defaults", None))
        self.saveSettingsButton.setText(QCoreApplication.translate("MainWindow", u"Save Settings", None))
    # retranslateUi

