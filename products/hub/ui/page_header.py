from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import QBoxLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget


class PageHeader(QWidget):
    """Compact, responsive heading for a top-level Hub page or workspace tab."""

    COMPACT_WIDTH = 640
    COMPACT_HYSTERESIS = 24

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("pageHeader")
        self._compact = False

        self._layout = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self._layout.setContentsMargins(2, 2, 2, 8)
        self._layout.setSpacing(12)

        text_widget = QWidget(self)
        self.text_layout = QVBoxLayout(text_widget)
        self.text_layout.setContentsMargins(0, 0, 0, 0)
        self.text_layout.setSpacing(2)
        self.title_label = QLabel(title, text_widget)
        self.title_label.setObjectName("pageHeaderTitle")
        title_font = self.title_label.font()
        title_font.setPointSize(18)
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        self.title_label.setWordWrap(True)
        self.subtitle_label = QLabel(subtitle, text_widget)
        self.subtitle_label.setObjectName("pageHeaderSubtitle")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.setStyleSheet("color:#adadb8;")
        self.subtitle_label.setVisible(bool(subtitle.strip()))
        self.text_layout.addWidget(self.title_label)
        self.text_layout.addWidget(self.subtitle_label)
        self._layout.addWidget(text_widget, 1)

        self.action_widget = QWidget(self)
        self.action_widget.setObjectName("pageHeaderActions")
        self.action_layout = QHBoxLayout(self.action_widget)
        self.action_layout.setContentsMargins(0, 0, 0, 0)
        self.action_layout.setSpacing(8)
        self.action_layout.addStretch()
        self.action_widget.hide()
        self._layout.addWidget(
            self.action_widget,
            0,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

    def add_action(self, widget: QWidget) -> None:
        """Place an existing page-level action at the right of the heading."""
        self.action_layout.insertWidget(self.action_layout.count() - 1, widget)
        self.action_widget.show()
        self._update_compact(force=True)

    def set_subtitle(self, subtitle: str) -> None:
        self.subtitle_label.setText(subtitle)
        self.subtitle_label.setVisible(bool(subtitle.strip()))

    @property
    def compact(self) -> bool:
        return self._compact

    def set_compact(self, compact: bool) -> None:
        if compact == self._compact:
            return
        self._compact = compact
        self.setProperty("compact", compact)
        self._layout.setDirection(
            QBoxLayout.Direction.TopToBottom
            if compact
            else QBoxLayout.Direction.LeftToRight
        )
        self._layout.setAlignment(
            self.action_widget,
            Qt.AlignmentFlag.AlignLeft
            if compact
            else Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._update_compact()

    def _update_compact(self, *, force: bool = False) -> None:
        if self.action_widget.isHidden():
            self.set_compact(False)
            return
        width = self.width()
        if width <= 0 and not force:
            return
        if self._compact:
            compact = width < self.COMPACT_WIDTH + self.COMPACT_HYSTERESIS
        else:
            compact = width < self.COMPACT_WIDTH - self.COMPACT_HYSTERESIS
        self.set_compact(compact)
