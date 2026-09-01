from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics, QResizeEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget


TASK_CATEGORY_ACCENTS = {
    "Core": "#bf94ff",
    "Core / Control": "#e39a4b",
    "Core / Files": "#55c7a5",
    "Core / Logic": "#e66a8d",
    "Core / Scripts": "#65a8e8",
    "Core / Variables": "#d9b957",
    "Twitch": "#a970ff",
    "OBS": "#4aa8ff",
    "Counters": "#f0a34a",
    "Audio": "#e879b2",
    "Soundboard": "#e879b2",
}
DEFAULT_TASK_ACCENT = "#8f8f9d"


def task_category_accent(category: str) -> str:
    """Return the one authoritative accent color for a task category."""

    return TASK_CATEGORY_ACCENTS.get(category, DEFAULT_TASK_ACCENT)


def task_category_display(category: str) -> str:
    root = category.partition(" / ")[0]
    return "Counter" if root == "Counters" else root


class ElidingLabel(QLabel):
    """One-line label that keeps its full text available to assistive UI."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setText(text)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)

    @property
    def full_text(self) -> str:
        return self._full_text

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API name
        self._full_text = str(text)
        self.setToolTip(self._full_text)
        self._apply_elision()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802 - Qt API name
        super().resizeEvent(event)
        self._apply_elision()

    def _apply_elision(self) -> None:
        width = max(self.contentsRect().width(), 0)
        elided = QFontMetrics(self.font()).elidedText(
            self._full_text,
            Qt.TextElideMode.ElideRight,
            width,
        )
        QLabel.setText(self, elided)


@dataclass(frozen=True, slots=True)
class TaskCardContent:
    category: str
    task_name: str
    summary: str
    instance_name: str = ""
    status: str = ""
    issues: tuple[str, ...] = ()


class TaskCardWidget(QFrame):
    """Dense Activity Feed-inspired presentation for one action task."""

    def __init__(
        self,
        content: TaskCardContent,
        *,
        nested: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.content = content
        self.setObjectName("automationTaskCard")
        self.setProperty("selected", False)
        self.setProperty("nested", nested)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        accent = task_category_accent(content.category)
        self.setStyleSheet(
            "QFrame#automationTaskCard {"
            "background-color:#242427; border:1px solid #3c3c42; border-radius:6px;"
            "}"
            "QFrame#automationTaskCard[selected=\"true\"] {"
            "background-color:#2b2930; border-color:#bf94ff;"
            "}"
            "QLabel { border:none; background:transparent; }"
        )

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.accent_bar = QFrame(self)
        self.accent_bar.setObjectName("automationTaskAccent")
        self.accent_bar.setFixedWidth(4)
        self.accent_bar.setStyleSheet(
            f"background-color:{accent}; border:none;"
            "border-top-left-radius:5px; border-bottom-left-radius:5px;"
        )
        outer.addWidget(self.accent_bar)

        row = QHBoxLayout()
        row.setContentsMargins(9 if not nested else 7, 5, 9, 5)
        row.setSpacing(8)
        category = task_category_display(content.category)
        self.name_label = QLabel(f"{category} — {content.task_name}", self)
        self.name_label.setObjectName("automationTaskName")
        self.name_label.setStyleSheet("font-weight:650; color:#efeff1;")
        self.name_label.setMinimumWidth(150)
        self.name_label.setSizePolicy(
            self.name_label.sizePolicy().horizontalPolicy(),
            self.name_label.sizePolicy().verticalPolicy(),
        )
        row.addWidget(self.name_label)

        self.summary_label = ElidingLabel(content.summary, self)
        self.summary_label.setObjectName("automationTaskSummary")
        self.summary_label.setStyleSheet("color:#adadb8;")
        self.summary_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.summary_label.setMinimumWidth(20)
        row.addWidget(self.summary_label, 1)

        self.status_label = QLabel(content.status, self)
        self.status_label.setObjectName("automationTaskStatus")
        self.status_label.setStyleSheet(
            "color:#d9b957; font-size:10px; font-weight:600;"
        )
        self.status_label.setVisible(bool(content.status))
        row.addWidget(self.status_label)
        outer.addLayout(row, 1)

        details = [
            value
            for value in (
                content.instance_name,
                content.summary,
                *content.issues,
            )
            if value
        ]
        self.setToolTip("\n".join(details) or "Ready")
        accessible = f"{category}, {content.task_name}"
        if content.summary:
            accessible += f", {content.summary}"
        if content.status:
            accessible += f", {content.status}"
        self.setAccessibleName(accessible)
        self.setMinimumHeight(34 if nested else 38)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)


class IfTaskCardWidget(QFrame):
    """Structural card that owns read-only previews of its branch routines."""

    def __init__(
        self,
        content: TaskCardContent,
        then_cards: tuple[QWidget, ...],
        else_cards: tuple[QWidget, ...],
        *,
        then_name: str = "",
        else_name: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.content = content
        self.then_cards = then_cards
        self.else_cards = else_cards
        self.setObjectName("automationIfCard")
        self.setProperty("selected", False)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        accent = task_category_accent("Core / Logic")
        self.setStyleSheet(
            "QFrame#automationIfCard {"
            "background-color:#222225; border:1px solid #4a4149; border-radius:7px;"
            "}"
            "QFrame#automationIfCard[selected=\"true\"] {"
            "background-color:#2b2930; border-color:#bf94ff;"
            "}"
            "QLabel { border:none; background:transparent; }"
        )
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.accent_bar = QFrame(self)
        self.accent_bar.setFixedWidth(4)
        self.accent_bar.setStyleSheet(
            f"background-color:{accent}; border:none;"
            "border-top-left-radius:6px; border-bottom-left-radius:6px;"
        )
        outer.addWidget(self.accent_bar)

        body = QVBoxLayout()
        body.setContentsMargins(10, 7, 9, 9)
        body.setSpacing(6)
        header = QHBoxLayout()
        header.setSpacing(8)
        self.name_label = QLabel("Core — IF", self)
        self.name_label.setStyleSheet("font-weight:750; color:#efeff1;")
        header.addWidget(self.name_label)
        self.summary_label = ElidingLabel(content.summary, self)
        self.summary_label.setStyleSheet("color:#d6d6dc; font-weight:600;")
        header.addWidget(self.summary_label, 1)
        self.status_label = QLabel(content.status, self)
        self.status_label.setStyleSheet("color:#d9b957; font-size:10px;")
        self.status_label.setVisible(bool(content.status))
        header.addWidget(self.status_label)
        body.addLayout(header)

        self._add_branch(body, "THEN", then_name, then_cards)
        if else_cards or else_name:
            divider = QFrame(self)
            divider.setFixedHeight(1)
            divider.setStyleSheet("background:#3c3c42; border:none;")
            body.addWidget(divider)
            self._add_branch(body, "ELSE", else_name, else_cards)
        outer.addLayout(body, 1)

        self.setToolTip(
            "\n".join(
                value
                for value in (content.instance_name, content.summary, *content.issues)
                if value
            )
            or "Ready"
        )
        self.setAccessibleName(f"Core Logic, If {content.summary}")

    def _add_branch(
        self,
        layout: QVBoxLayout,
        label: str,
        routine_name: str,
        cards: tuple[QWidget, ...],
    ) -> None:
        branch_label = QLabel(
            f"{label}  {routine_name}" if routine_name else label,
            self,
        )
        branch_label.setStyleSheet(
            "color:#adadb8; font-size:10px; font-weight:700; letter-spacing:0.4px;"
        )
        layout.addWidget(branch_label)
        if not cards:
            empty = QLabel(
                "No tasks in branch routine"
                if routine_name
                else "No branch routine configured",
                self,
            )
            empty.setStyleSheet("color:#777780; font-style:italic;")
            layout.addWidget(empty)
            return
        for card in cards:
            wrapper = QWidget(self)
            wrapper_layout = QHBoxLayout(wrapper)
            wrapper_layout.setContentsMargins(8, 0, 0, 0)
            wrapper_layout.addWidget(card)
            layout.addWidget(wrapper)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)
