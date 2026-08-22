from __future__ import annotations

import json
import re
from datetime import datetime
from html import escape, unescape
from urllib.parse import urlencode

from PySide6.QtCore import QUrl, QUrlQuery, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView

from products.hub.twitch.chat_entries import (
    TwitchChatEntry,
    TwitchChatEntryType,
    TwitchChatHistory,
)
from products.hub.twitch.models import TwitchChatNotice, TwitchMessage
from products.hub.ui.twitch_assets import twitch_emote_url


class _ChatPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, navigation_type, is_main_frame):
        if url.scheme() == "streamhouse-chat-context":
            return False
        if navigation_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            QDesktopServices.openUrl(url)
            return False
        return super().acceptNavigationRequest(url, navigation_type, is_main_frame)


class TwitchChatView(QWebEngineView):
    """A bounded chat timeline backed by structured Twitch entries."""

    chatter_context_requested = Signal(str, str, str)
    entry_context_requested = Signal(object)

    def __init__(self, parent=None, *, history_limit: int = 1000) -> None:
        super().__init__(parent)
        page = _ChatPage(self)
        page.setBackgroundColor(QColor("#18181b"))
        self.setPage(page)
        self.history = TwitchChatHistory(history_limit)
        self._html_by_entry: list[tuple[str, str]] = []
        self._options: dict[str, dict[str, object]] = {}
        self._static_html = ""
        self._loaded = False
        self._font = QFont("Segoe UI", 10)
        self.loadFinished.connect(self._page_loaded)
        self._render()

    def append_message(
        self,
        message: TwitchMessage,
        *,
        badge_urls: tuple[str, ...] = (),
        username_color: str = "#bf94ff",
        show_timestamp: bool = True,
        emote_size: int = 20,
    ) -> TwitchChatEntry:
        entry = TwitchChatEntry.from_message(message)
        self._options[entry.entry_id] = {
            "badge_urls": badge_urls,
            "username_color": username_color,
            "show_timestamp": show_timestamp,
            "emote_size": max(16, int(emote_size)),
        }
        self._add_entry(entry)
        return entry

    def append_notice(self, notice: TwitchChatNotice) -> TwitchChatEntry:
        entry = TwitchChatEntry.from_notice(notice)
        self._add_entry(entry)
        return entry

    def append_system(self, text: str, received_at: datetime) -> TwitchChatEntry:
        entry = TwitchChatEntry.system(text, received_at)
        self._add_entry(entry)
        return entry

    def append(self, html: str) -> None:
        """Compatibility path for the initial empty-state message."""
        self._static_html = ""
        self._html_by_entry.append(("", html))
        self._trim()
        if self._loaded:
            self._append_html(html)

    def clear(self) -> None:
        self.history.clear()
        self._options.clear()
        self._html_by_entry.clear()
        self._static_html = ""
        if self._loaded:
            self.page().runJavaScript(
                "const root=document.getElementById('chat-root');"
                "if(root){root.replaceChildren();}"
            )
        else:
            self._render()

    def setHtml(self, html: str, base_url: QUrl = QUrl()) -> None:
        self.clear()
        self._static_html = html
        self._render()

    def toHtml(self) -> str:
        return self._body_html()

    def toPlainText(self) -> str:
        text = re.sub(r"<br\s*/?>", "\n", self._body_html(), flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
        return unescape(text).strip()

    def document(self):
        return self

    def setMaximumBlockCount(self, count: int) -> None:
        self.history.set_limit(count)
        self._trim()
        self._render()

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
        if target.scheme() == "streamhouse-chat-context":
            query = QUrlQuery(target)
            entry = self.history.get(query.queryItemValue("entry_id"))
            if entry is not None:
                self.entry_context_requested.emit(entry)
                if entry.user_id:
                    self.chatter_context_requested.emit(
                        entry.user_id, entry.username, entry.message_id
                    )
            else:
                user_id = query.queryItemValue("user_id")
                if user_id:
                    self.chatter_context_requested.emit(
                        user_id,
                        query.queryItemValue("user_name"),
                        query.queryItemValue("message_id"),
                    )
            request.setAccepted(True)
        event.accept()

    def mark_deleted(self, message_id: str) -> bool:
        entry = self.history.mark_deleted(message_id)
        if entry is None:
            return False
        self._html_by_entry = [
            (entry_id, self._entry_html(entry) if entry_id == entry.entry_id else html)
            for entry_id, html in self._html_by_entry
        ]
        self._render()
        return True

    def _add_entry(self, entry: TwitchChatEntry) -> None:
        self._static_html = ""
        removed = self.history.add(entry)
        for old in removed:
            self._options.pop(old.entry_id, None)
        html = self._entry_html(entry)
        self._html_by_entry.append((entry.entry_id, html))
        self._trim()
        if self._loaded:
            self._append_html(html)

    def _trim(self) -> None:
        overflow = max(0, len(self._html_by_entry) - self.history.limit)
        if overflow:
            del self._html_by_entry[:overflow]

    def _body_html(self) -> str:
        return self._static_html or "".join(html for _, html in self._html_by_entry)

    def _entry_html(self, entry: TwitchChatEntry) -> str:
        timestamp = entry.received_at.astimezone().strftime("%H:%M:%S")
        if entry.message is None:
            css_class = {
                TwitchChatEntryType.MODERATION: "moderation-event",
                TwitchChatEntryType.SYSTEM: "system-event",
            }.get(entry.kind, "twitch-event")
            return (
                f"<div class='special-entry {css_class}' "
                f"data-entry-id='{escape(entry.entry_id)}'>"
                f"<span class='chat-time'>{timestamp}</span> "
                f"{escape(entry.text)}</div>"
            )

        message = entry.message
        options = self._options.get(entry.entry_id, {})
        timestamp_html = (
            f"<span class='chat-time'>{timestamp}</span> "
            if options.get("show_timestamp", True)
            else ""
        )
        badge_html = "".join(
            f"<img class='chat-badge' src='{escape(str(url))}' "
            "width='18' height='18' alt='badge' /> "
            for url in options.get("badge_urls", ())
        )
        if entry.deleted:
            body = "<span class='deleted-message'>[message deleted]</span>"
        else:
            parts: list[str] = []
            size = int(options.get("emote_size", 20))
            for fragment in message.fragments:
                if fragment.emote is None:
                    parts.append(self._linkify(fragment.text))
                else:
                    animated = "animated" in fragment.emote.formats
                    url = twitch_emote_url(fragment.emote.id, animated)
                    parts.append(
                        f"<img src='{escape(url)}' width='{size}' height='{size}' "
                        f"alt='{escape(fragment.text)}' />"
                    )
            body = "".join(parts) or self._linkify(message.text)
        context_url = "streamhouse-chat-context://message?" + urlencode(
            {"entry_id": entry.entry_id}
        )
        color = escape(str(options.get("username_color", "#bf94ff")))
        return (
            "<div class='chat-message' "
            f"data-entry-id='{escape(entry.entry_id)}' "
            f"data-user-id='{escape(message.user_id)}' "
            f"data-user-name='{escape(message.username)}' "
            f"data-message-id='{escape(message.message_id)}'>"
            f"<a class='chat-context-target' href='{escape(context_url)}'></a>"
            "<span class='chat-content'>"
            f"{timestamp_html}{badge_html}"
            f"<span class='chat-user' style='color:{color};'>"
            f"{escape(message.username)}:</span> "
            f"<span class='chat-text'>{body}</span>"
            "</span></div>"
        )

    @staticmethod
    def _linkify(text: str) -> str:
        escaped = escape(text).replace("\n", "<br>")
        return re.sub(r"(https?://[^\s<]+)", r"<a href='\1'>\1</a>", escaped)

    def _render(self) -> None:
        size = max(self._font.pointSize(), 8)
        self._loaded = False
        body = self._body_html()
        page = f"""<!doctype html><html><head><style>
html,body {{ background:#18181b; }}
body {{ color:#efeff1; font-family:{self._font.family()}; font-size:{size}pt;
margin:0; overflow-wrap:anywhere; }}
#chat-root {{ padding:6px 8px; }} a {{ color:#5cafff; }} img {{ vertical-align:middle; }}
.chat-message {{ position:relative; padding:2px 4px; line-height:1.42; cursor:context-menu; }}
.chat-message:hover {{ background:#242429; }}
.chat-message:hover::after {{ content:'\\2026'; position:absolute; right:5px; top:0;
color:#adadb8; font-weight:700; }}
.chat-context-target {{ position:absolute; inset:0; z-index:1; }}
.chat-content {{ position:relative; z-index:2; pointer-events:none; }}
.chat-content a {{ pointer-events:auto; }}
.chat-time {{ color:#adadb8; }} .chat-user {{ font-weight:600; }}
.deleted-message {{ color:#adadb8; font-style:italic; }}
.special-entry {{ margin:7px 3px; padding:6px 8px; border-left:3px solid #9147ff;
background:#211d28; color:#dedee3; }}
.moderation-event {{ border-left-color:#e91916; background:#2a1d20; }}
.system-event {{ border-left-color:#f0b429; background:#29251b; }}
</style></head><body><div id='chat-root'>{body}</div></body></html>"""
        super().setHtml(page, QUrl("about:blank"))

    def _page_loaded(self, success: bool) -> None:
        if success:
            self._loaded = True
            self._scroll_to_bottom()

    def _append_html(self, html: str) -> None:
        script = f"""
(() => {{
  const root = document.getElementById('chat-root'); if (!root) return;
  const doc = document.documentElement;
  const nearBottom = window.scrollY + window.innerHeight >= doc.scrollHeight - 48;
  root.insertAdjacentHTML('beforeend', {json.dumps(html)});
  while (root.children.length > {self.history.limit}) root.firstElementChild.remove();
  const scroll = () => {{ if (nearBottom) window.scrollTo(0, doc.scrollHeight); }};
  root.querySelectorAll('img:not([data-streamhouse-scroll])').forEach((image) => {{
    image.dataset.streamhouseScroll='1'; image.addEventListener('load', scroll, {{once:true}});
  }});
  requestAnimationFrame(scroll);
}})();
"""
        self.page().runJavaScript(script)

    def _scroll_to_bottom(self) -> None:
        if self._loaded:
            self.page().runJavaScript(
                "window.scrollTo(0, document.documentElement.scrollHeight)"
            )
