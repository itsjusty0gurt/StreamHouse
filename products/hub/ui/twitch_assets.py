from __future__ import annotations

from PySide6.QtCore import QObject, QUrl
from PySide6.QtGui import QImage, QTextDocument
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest


class TwitchAssetManager(QObject):
    """Download Twitch chat artwork once and cache it in a text document."""

    def __init__(self, document: QTextDocument, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.document = document
        self.network = QNetworkAccessManager(self)
        self.network.finished.connect(self._finished)
        self._pending: set[str] = set()
        self._loaded: set[str] = set()

    def request(self, url: str) -> None:
        if not url or url in self._pending or url in self._loaded:
            return
        self._pending.add(url)
        self.network.get(QNetworkRequest(QUrl(url)))

    def _finished(self, reply: QNetworkReply) -> None:
        url = reply.url().toString()
        self._pending.discard(url)
        if reply.error() == QNetworkReply.NetworkError.NoError:
            image = QImage()
            if image.loadFromData(reply.readAll()):
                self.document.addResource(
                    QTextDocument.ResourceType.ImageResource,
                    QUrl(url),
                    image,
                )
                self.document.markContentsDirty(0, self.document.characterCount())
                self._loaded.add(url)
        reply.deleteLater()


def twitch_emote_url(emote_id: str, animated: bool = False) -> str:
    image_format = "animated" if animated else "static"
    return (
        "https://static-cdn.jtvnw.net/emoticons/v2/"
        f"{emote_id}/{image_format}/dark/1.0"
    )
