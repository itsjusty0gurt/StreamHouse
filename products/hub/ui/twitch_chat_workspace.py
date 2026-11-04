from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSplitter, QWidget


class TwitchChatWorkspaceLayout:
    """Own the responsive splitter contract for the Twitch Chat workspace."""

    def __init__(
        self,
        chat_splitter: QSplitter,
        side_splitter: QSplitter,
        chat_panel: QWidget,
    ) -> None:
        self.chat_splitter = chat_splitter
        self.side_splitter = side_splitter
        self.chat_panel = chat_panel
        self._configure()

    def _configure(self) -> None:
        self.chat_splitter.setOrientation(Qt.Orientation.Horizontal)
        self.chat_splitter.setChildrenCollapsible(False)
        self._configure_children(self.chat_splitter, (2, 1), (680, 320))
        self.chat_panel.setMinimumWidth(300)

        self.side_splitter.setOrientation(Qt.Orientation.Vertical)
        self.side_splitter.setChildrenCollapsible(False)
        self._configure_children(self.side_splitter, (2, 3), (240, 360))
        self.side_splitter.setMinimumWidth(180)

    @staticmethod
    def _configure_children(
        splitter: QSplitter,
        stretch: tuple[int, ...],
        sizes: tuple[int, ...],
    ) -> None:
        count = splitter.count()
        for index in range(min(count, len(stretch))):
            splitter.setCollapsible(index, False)
            splitter.setStretchFactor(index, stretch[index])
        if count:
            splitter.setSizes(list(sizes[:count]))

    def apply_responsive_layout(self) -> None:
        """Keep the workspace side-by-side after mode or saved-state changes."""
        restored_vertical_split = (
            self.chat_splitter.orientation() == Qt.Orientation.Vertical
        )
        self.chat_splitter.setOrientation(Qt.Orientation.Horizontal)
        self.side_splitter.setOrientation(Qt.Orientation.Vertical)
        self._configure_children(self.chat_splitter, (2, 1), (680, 320))
        self._configure_children(self.side_splitter, (2, 3), (240, 360))
        if restored_vertical_split and self.chat_splitter.count():
            # Pre-alpha portrait builds persisted a vertical Chat/content split.
            # That geometry is not meaningful for the side-column layout.
            self.chat_splitter.setSizes([680, 320])
