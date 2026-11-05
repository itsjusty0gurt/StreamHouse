import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QSplitter, QWidget

from products.hub.ui.twitch_chat_workspace import TwitchChatWorkspaceLayout


class RecordingSplitter(QSplitter):
    def __init__(self, orientation: Qt.Orientation) -> None:
        super().__init__(orientation)
        self.collapsible_indexes: list[int] = []

    def setCollapsible(self, index: int, collapse: bool) -> None:
        self.collapsible_indexes.append(index)
        super().setCollapsible(index, collapse)


def test_responsive_layout_does_not_address_removed_splitter_children() -> None:
    app = QApplication.instance() or QApplication([])
    chat_splitter = RecordingSplitter(Qt.Orientation.Horizontal)
    side_splitter = RecordingSplitter(Qt.Orientation.Vertical)
    chat_panel = QWidget()

    layout = TwitchChatWorkspaceLayout(
        chat_splitter,
        side_splitter,
        chat_panel,
    )
    layout.apply_responsive_layout()
    app.processEvents()

    assert chat_splitter.collapsible_indexes == []
    assert side_splitter.collapsible_indexes == []


def test_responsive_layout_configures_only_children_still_in_splitters() -> None:
    QApplication.instance() or QApplication([])
    chat_splitter = RecordingSplitter(Qt.Orientation.Horizontal)
    side_splitter = RecordingSplitter(Qt.Orientation.Vertical)
    chat_splitter.addWidget(QWidget())
    chat_splitter.addWidget(QWidget())
    side_splitter.addWidget(QWidget())

    layout = TwitchChatWorkspaceLayout(
        chat_splitter,
        side_splitter,
        QWidget(),
    )

    assert chat_splitter.collapsible_indexes == [0, 1]
    assert side_splitter.collapsible_indexes == [0]
