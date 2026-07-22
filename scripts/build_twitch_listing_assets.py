"""Build exact-size Twitch Extension listing images with Qt."""

from __future__ import annotations

from pathlib import Path
import sys

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtSvg import QSvgRenderer


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "twitch_extension" / "listing"
ICON = ROOT / "assets" / "sally-icon.svg"
GREEN = QColor("#00d47b")
DARK = QColor("#18181b")
PANEL = QColor("#242428")
BUTTON = QColor("#303035")
TEXT = QColor("#f4f4f5")
MUTED = QColor("#b8b8c0")


def rounded_rect(
    painter: QPainter,
    rect: QRectF,
    radius: float,
    fill: QColor,
    border: QColor | None = None,
    width: float = 1,
) -> None:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    painter.fillPath(path, fill)
    if border is not None:
        painter.setPen(QPen(border, width))
        painter.drawPath(path)


def draw_text(
    painter: QPainter,
    rect: QRectF,
    text: str,
    size: int,
    *,
    bold: bool = False,
    color: QColor = TEXT,
    alignment: Qt.AlignmentFlag = Qt.AlignLeft | Qt.AlignVCenter,
) -> None:
    font = QFont("Segoe UI", size)
    font.setBold(bold)
    painter.setFont(font)
    painter.setPen(color)
    painter.drawText(rect, alignment, text)


def draw_icon(painter: QPainter, rect: QRectF) -> None:
    QSvgRenderer(str(ICON)).render(painter, rect)


def save_logo() -> None:
    image = QImage(100, 100, QImage.Format_ARGB32)
    image.fill(DARK)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    draw_icon(painter, QRectF(0, 0, 100, 100))
    painter.end()
    image.save(str(OUTPUT / "logo-100x100.png"))


def save_discovery() -> None:
    image = QImage(300, 200, QImage.Format_ARGB32)
    image.fill(DARK)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.fillRect(QRect(0, 0, 300, 7), GREEN)
    draw_icon(painter, QRectF(20, 29, 70, 70))
    draw_text(painter, QRectF(106, 31, 178, 34), "SALLY", 24, bold=True)
    draw_text(painter, QRectF(106, 62, 178, 28), "SOUNDBOARD", 17, bold=True, color=GREEN)
    draw_text(
        painter,
        QRectF(20, 113, 260, 25),
        "Your sounds. Your routines.",
        14,
        bold=True,
    )
    draw_text(
        painter,
        QRectF(20, 141, 260, 36),
        "Triggered live by your Twitch viewers.",
        11,
        color=MUTED,
    )
    painter.end()
    image.save(str(OUTPUT / "discovery-300x200.png"))


def save_screenshot() -> None:
    image = QImage(1024, 768, QImage.Format_ARGB32)
    image.fill(QColor("#101012"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)

    draw_icon(painter, QRectF(64, 52, 88, 88))
    draw_text(painter, QRectF(176, 54, 760, 48), "Sally Soundboard", 32, bold=True)
    draw_text(
        painter,
        QRectF(176, 102, 760, 34),
        "Viewer-powered sounds, routed through Sally automation",
        16,
        color=MUTED,
    )

    panel_rect = QRectF(64, 176, 896, 510)
    rounded_rect(painter, panel_rect, 16, DARK, QColor("#3d3d43"), 2)
    draw_text(painter, QRectF(92, 196, 360, 42), "SOUNDS", 18, bold=True, color=GREEN)
    draw_text(
        painter,
        QRectF(640, 198, 288, 38),
        "Connected to Sally",
        13,
        bold=True,
        color=GREEN,
        alignment=Qt.AlignRight | Qt.AlignVCenter,
    )

    labels = ("Yippie", "Flute", "Air Horn", "Applause", "Drum Roll", "Rimshot")
    left, top, gap = 92.0, 258.0, 18.0
    button_width = (840.0 - gap * 2) / 3
    button_height = 145.0
    for index, label in enumerate(labels):
        row, column = divmod(index, 3)
        rect = QRectF(
            left + column * (button_width + gap),
            top + row * (button_height + gap),
            button_width,
            button_height,
        )
        rounded_rect(painter, rect, 12, BUTTON, QColor("#56565e"), 2)
        draw_text(
            painter,
            rect,
            label,
            19,
            bold=True,
            alignment=Qt.AlignCenter,
        )

    draw_text(
        painter,
        QRectF(64, 704, 896, 30),
        "Broadcasters configure every sound and action in Sally AI Bot.",
        13,
        color=MUTED,
        alignment=Qt.AlignCenter,
    )
    painter.end()
    image.save(str(OUTPUT / "screenshot-1024x768.png"))


def main() -> None:
    application = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    OUTPUT.mkdir(parents=True, exist_ok=True)
    save_logo()
    save_discovery()
    save_screenshot()
    application.quit()
    print(f"Built Twitch listing assets in {OUTPUT}")


if __name__ == "__main__":
    main()
