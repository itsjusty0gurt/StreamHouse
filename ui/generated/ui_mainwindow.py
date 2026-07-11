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
from PySide6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QMainWindow,
    QMenuBar, QPushButton, QSizePolicy, QSpacerItem,
    QStackedWidget, QStatusBar, QVBoxLayout, QWidget)

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
        self.navigationFrame.setMinimumSize(QSize(170, 0))
        self.navigationFrame.setMaximumSize(QSize(220, 16777215))
        self.navigationFrame.setFrameShape(QFrame.Shape.StyledPanel)
        self.navigationFrame.setFrameShadow(QFrame.Shadow.Raised)
        self.verticalLayout = QVBoxLayout(self.navigationFrame)
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.dashboardButton = QPushButton(self.navigationFrame)
        self.dashboardButton.setObjectName(u"dashboardButton")

        self.verticalLayout.addWidget(self.dashboardButton)

        self.twitchbutton = QPushButton(self.navigationFrame)
        self.twitchbutton.setObjectName(u"twitchbutton")

        self.verticalLayout.addWidget(self.twitchbutton)

        self.verticalSpacer = QSpacerItem(20, 40, QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

        self.verticalLayout.addItem(self.verticalSpacer)

        self.settingsButton = QPushButton(self.navigationFrame)
        self.settingsButton.setObjectName(u"settingsButton")

        self.verticalLayout.addWidget(self.settingsButton)


        self.horizontalLayout.addWidget(self.navigationFrame)

        self.mainStack = QStackedWidget(self.centralwidget)
        self.mainStack.setObjectName(u"mainStack")
        self.dashboardPage = QWidget()
        self.dashboardPage.setObjectName(u"dashboardPage")
        self.mainStack.addWidget(self.dashboardPage)
        self.settingsPage = QWidget()
        self.settingsPage.setObjectName(u"settingsPage")
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
        MainWindow.setWindowTitle(QCoreApplication.translate("MainWindow", u"MainWindow", None))
        self.dashboardButton.setText(QCoreApplication.translate("MainWindow", u"Dashboard", None))
        self.twitchbutton.setText(QCoreApplication.translate("MainWindow", u"Twitch", None))
        self.settingsButton.setText(QCoreApplication.translate("MainWindow", u"Settings", None))
    # retranslateUi

