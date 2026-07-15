from __future__ import annotations

import re
from html import unescape

from PySide6.QtCore import QUrl, QUrlQuery, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView


class _ChatPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if url.scheme() == "sally-chat-context":
            return False
        if navigation_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, navigation_type, is_main_frame)


class TwitchChatView(QWebEngineView):
    """Chromium-backed chat view supporting animated artwork and links."""

    chatter_context_requested = Signal(str, str, str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        page = _ChatPage(self)
        self.setPage(page)
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

    def contextMenuEvent(self, event) -> None:
        request = self.lastContextMenuRequest()
        target = request.linkUrl() if request is not None else QUrl()
        if target.scheme() == "sally-chat-context":
            query = QUrlQuery(target)
            user_id = query.queryItemValue("user_id")
            if user_id:
                self.chatter_context_requested.emit(
                    user_id,
                    query.queryItemValue("user_name"),
                    query.queryItemValue("message_id"),
                )
            request.setAccepted(True)
        event.accept()

    def _render(self) -> None:
        size = max(self._font.pointSize(), 8)
        page = f"""<!doctype html><html><head><style>
body {{ background:#18181b; color:#efeff1; font-family:{self._font.family()};
font-size:{size}pt; margin:8px; overflow-wrap:anywhere; }}
a {{ color:#5cafff; }} img {{ vertical-align:middle; }}
.chat-message {{ position:relative; padding:4px 5px; border-radius:3px;
cursor:context-menu; }}
.chat-message:hover {{ background:#26262c; }}
.chat-context-target {{ position:absolute; inset:0; z-index:1; }}
.chat-content {{ position:relative; z-index:2; pointer-events:none; }}
.chat-content a {{ pointer-events:auto; }}
</style></head><body>{self._body}</body></html>"""
        super().setHtml(page, QUrl("about:blank"))
