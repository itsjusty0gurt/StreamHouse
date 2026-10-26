from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtWidgets import QHeaderView, QStyle, QTableView


def configure_semantic_columns(
    table: QTableView,
    *,
    stretch: Iterable[int],
    compact: Iterable[int],
    header_compact: Iterable[int] = (),
    minimum_section_size: int = 56,
) -> None:
    """Allocate table width by column meaning instead of equally by index."""
    header = table.horizontalHeader()
    header.setStretchLastSection(False)
    header.setMinimumSectionSize(minimum_section_size)
    for column in compact:
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
    for column in header_compact:
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        label = str(table.model().headerData(column, header.orientation()) or "")
        margin = table.style().pixelMetric(
            QStyle.PixelMetric.PM_HeaderMargin,
            None,
            header,
        )
        mark = (
            table.style().pixelMetric(
                QStyle.PixelMetric.PM_HeaderMarkSize,
                None,
                header,
            )
            if table.isSortingEnabled()
            else 0
        )
        header.resizeSection(
            column,
            max(
                minimum_section_size,
                header.fontMetrics().horizontalAdvance(label) + (margin * 2) + mark,
            ),
        )
    for column in stretch:
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)


def make_columns_interactive(table: QTableView) -> None:
    """Preserve the current semantic widths, then allow manual resizing."""

    header = table.horizontalHeader()
    widths = tuple(
        header.sectionSize(column)
        for column in range(table.model().columnCount())
    )
    for column, width in enumerate(widths):
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Interactive)
        header.resizeSection(column, width)
