from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtWidgets import QHeaderView, QTableView


def configure_semantic_columns(
    table: QTableView,
    *,
    stretch: Iterable[int],
    compact: Iterable[int],
    minimum_section_size: int = 56,
) -> None:
    """Allocate table width by column meaning instead of equally by index."""
    header = table.horizontalHeader()
    header.setStretchLastSection(False)
    header.setMinimumSectionSize(minimum_section_size)
    for column in compact:
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
    for column in stretch:
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
