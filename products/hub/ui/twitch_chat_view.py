from __future__ import annotations

import json
import re
from html import unescape

from PySide6.QtCore import QUrl, QUrlQuery, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView


class _ChatPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if url.scheme() in {"streamhouse-chat-context", "sally-chat-context"}:
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
        page.setBackgroundColor(QColor("#18181b"))
        self.setPage(page)
        self._body = ""
        self._rendered_body = ""
        self._loaded = False
        self._font = QFont("Segoe UI", 10)
        self.loadFinished.connect(self._page_loaded)
        self._render()

    def append(self, html: str) -> None:
        self._body += html
        if self._loaded:
            self._append_html(html)

    def clear(self) -> None:
        self._body = ""
        self._rendered_body = ""
        if self._loaded:
            self.page().runJavaScript(
                "const root=document.getElementById('chat-root');"
                "if(root){root.replaceChildren();}"
            )
        else:
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
        self._scroll_to_bottom()

    def contextMenuEvent(self, event) -> None:
        request = self.lastContextMenuRequest()
        target = request.linkUrl() if request is not None else QUrl()
        if target.scheme() in {"streamhouse-chat-context", "sally-chat-context"}:
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
        self._loaded = False
        self._rendered_body = self._body
        page = f"""<!doctype html><html><head><style>
html {{ background:#18181b; }}
body {{ background:#18181b; color:#efeff1; font-family:{self._font.family()};
font-size:{size}pt; margin:0; overflow-wrap:anywhere; }}
#chat-root {{ padding:8px; }}
a {{ color:#5cafff; }} img {{ vertical-align:middle; }}
.chat-message {{ position:relative; padding:4px 5px; border-radius:3px;
cursor:context-menu; }}
.chat-message:hover {{ background:#26262c; }}
.chat-context-target {{ position:absolute; inset:0; z-index:1; }}
.chat-content {{ position:relative; z-index:2; pointer-events:none; }}
.chat-content a {{ pointer-events:auto; }}
</style></head><body><div id='chat-root'>{self._body}</div></body></html>"""
        super().setHtml(page, QUrl("about:blank"))

    def _page_loaded(self, success: bool) -> None:
        if not success:
            return
        self._loaded = True
        if self._body != self._rendered_body:
            if self._body.startswith(self._rendered_body):
                self._append_html(self._body[len(self._rendered_body) :])
            else:
                self._render()
                return
        self._scroll_to_bottom()

    def _append_html(self, html: str) -> None:
        script = f"""
(() => {{
  const root = document.getElementById('chat-root');
  if (!root) return;
  root.insertAdjacentHTML('beforeend', {json.dumps(html)});
  const scroll = () => window.scrollTo(0, document.documentElement.scrollHeight);
  root.querySelectorAll('img:not([data-streamhouse-scroll])').forEach((image) => {{
    image.dataset.streamhouseScroll = '1';
    image.addEventListener('load', scroll, {{once: true}});
  }});
  requestAnimationFrame(scroll);
  setTimeout(scroll, 50);
}})();
"""
        self.page().runJavaScript(script)
        self._rendered_body = self._body

    def _scroll_to_bottom(self) -> None:
        if self._loaded:
            self.page().runJavaScript(
                "window.scrollTo(0, document.documentElement.scrollHeight)"
            )
