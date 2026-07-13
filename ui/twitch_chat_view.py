from __future__ import annotations

import re
from html import unescape

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView


class _ChatPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if navigation_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, navigation_type, is_main_frame)


class TwitchChatView(QWebEngineView):
    """Chromium-backed chat view supporting animated artwork and links."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setPage(_ChatPage(self))
        self._body = ""
        self._font = QFont("Segoe UI", 10)

    def append(self, html: str) -> None:
        self._body += html
        self._render()

    def clear(self) -> None:
        self._body = ""
        self._render()

    def setHtml(self, html: str, base_url: QUrl = QUrl()) -> None:
        self._body = html
        self._render()

    def toHtml(self) -> str:
        return self._body

    def toPlainText(self) -> str:
        text = re.sub(r"<br\s*/?>", "\n", self._body, flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
        return unescape(text).strip()

    def document(self):
        return self

    def setMaximumBlockCount(self, count: int) -> None:
        pass

    def setDefaultFont(self, font: QFont) -> None:
        self.setFont(font)

    def defaultFont(self) -> QFont:
        return self._font

    def setFont(self, font: QFont) -> None:
        self._font = QFont(font)
        super().setFont(font)
        self._render()

    def verticalScrollBar(self):
        return self

    def maximum(self) -> int:
        return 0

    def setValue(self, value: int) -> None:
        self.page().runJavaScript("window.scrollTo(0, document.body.scrollHeight)")

    def _render(self) -> None:
        size = max(self._font.pointSize(), 8)
        page = f"""<!doctype html><html><head><style>
body {{ background:#18181b; color:#efeff1; font-family:{self._font.family()};
font-size:{size}pt; margin:8px; overflow-wrap:anywhere; }}
a {{ color:#5cafff; }} img {{ vertical-align:middle; }}
</style></head><body>{self._body}</body></html>"""
        super().setHtml(page, QUrl("https://localhost/"))
