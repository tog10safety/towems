"""Reusable desktop widgets."""

from __future__ import annotations

from typing import Iterable, Sequence

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import theme


def label(text: str = "", name: str = "", *, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def button(text: str, *, primary: bool = False, danger: bool = False, ghost: bool = False) -> QPushButton:
    widget = QPushButton(text)
    if primary:
        widget.setObjectName("Primary")
    elif danger:
        widget.setObjectName("Danger")
    elif ghost:
        widget.setObjectName("Ghost")
    widget.setCursor(Qt.PointingHandCursor)
    return widget


def badge(text: str, kind: str = "") -> QLabel:
    name = {"ok": "BadgeOk", "warn": "BadgeWarn", "danger": "BadgeDanger"}.get(kind, "Badge")
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setAlignment(Qt.AlignCenter)
    widget.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
    return widget


class PageHeader(QWidget):
    def __init__(self, title: str, subtitle: str = "", parent=None):
        super().__init__(parent)
        self.row = QHBoxLayout(self)
        self.row.setContentsMargins(0, 0, 0, 0)
        self.row.setSpacing(10)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(label(title, "PageTitle"))
        if subtitle:
            text.addWidget(label(subtitle, "PageSubtitle", wrap=True))
        self.row.addLayout(text, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        self.row.addLayout(self.actions)

    def add_action(self, widget: QWidget) -> None:
        self.actions.addWidget(widget)


class StatCard(QFrame):
    clicked = Signal()

    def __init__(self, caption: str, value: str = "—", hint: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("StatCard")
        self.setCursor(Qt.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 13, 16, 12)
        layout.setSpacing(5)
        self.caption = label(caption.upper(), "StatLabel")
        self.value = label(value, "StatValue")
        self.hint = label(hint, "CardHint", wrap=True)
        layout.addWidget(self.caption)
        layout.addWidget(self.value)
        layout.addWidget(self.hint)
        layout.addStretch(1)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def set_value(self, value: str, hint: str = "") -> None:
        self.value.setText(str(value))
        if hint:
            self.hint.setText(hint)


class Panel(QFrame):
    def __init__(self, title: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("Panel")
        self.layout_box = QVBoxLayout(self)
        self.layout_box.setContentsMargins(14, 12, 14, 12)
        self.layout_box.setSpacing(9)
        if title:
            self.layout_box.addWidget(label(title, "SectionTitle"))
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        self.layout_box.addLayout(self.body, 1)

    def add(self, widget: QWidget) -> None:
        self.body.addWidget(widget)

    def add_layout(self, layout) -> None:
        self.body.addLayout(layout)


class DataTable(QTableWidget):
    def __init__(self, headers: Sequence[str], parent=None):
        super().__init__(0, len(headers), parent)
        self._headers = list(headers)
        self.setHorizontalHeaderLabels(self._headers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setSortingEnabled(True)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(34)
        self.horizontalHeader().setHighlightSections(False)
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.horizontalHeader().setStretchLastSection(True)
        self.setFocusPolicy(Qt.StrongFocus)

    def set_data(
        self,
        rows: Iterable[Sequence],
        *,
        ids: Sequence | None = None,
        id_column: int = 0,
        status_column: int | None = None,
    ) -> None:
        rows = list(rows)
        ids = list(ids) if ids is not None else None
        sorting = self.isSortingEnabled()
        self.setSortingEnabled(False)
        self.setRowCount(len(rows))
        colours = {
            "open": QColor(theme.AMBER),
            "issued": QColor(theme.AMBER),
            "pending": QColor(theme.AMBER),
            "failed": QColor(theme.RED),
            "closed": QColor(theme.GREEN),
            "stored": QColor(theme.GREEN),
            "serviceable": QColor(theme.GREEN),
            "synced": QColor(theme.GREEN),
            "success": QColor(theme.GREEN),
            "partial": QColor(theme.AMBER),
            "active": QColor(theme.GREEN),
            "inactive": QColor(theme.MUTED),
            "retired": QColor(theme.MUTED),
            "unserviceable": QColor(theme.RED),
            "maintenance": QColor(theme.AMBER),
            "condemned": QColor(theme.RED),
            "draft": QColor(theme.MUTED),
            "running": QColor(theme.AMBER),
        }
        for row_index, row in enumerate(rows):
            for column, value in enumerate(row):
                item = QTableWidgetItem(str(value if value is not None else ""))
                if column == 0:
                    if ids is not None and row_index < len(ids):
                        item.setData(Qt.UserRole, ids[row_index])
                    elif len(row) > id_column:
                        item.setData(Qt.UserRole, row[id_column])
                item.setToolTip(str(value if value is not None else ""))
                if status_column is not None and column == status_column:
                    color = colours.get(str(value).lower())
                    if color:
                        item.setForeground(color)
                        font = QFont()
                        font.setBold(True)
                        item.setFont(font)
                self.setItem(row_index, column, item)
        self.setSortingEnabled(sorting)
        self.resizeColumnsToContents()
        header = self.horizontalHeader()
        for index in range(self.columnCount()):
            width = self.columnWidth(index)
            if width > 330:
                self.setColumnWidth(index, 330)
            elif width < 78:
                self.setColumnWidth(index, 78)

    def selected_id(self):
        row = self.currentRow()
        if row < 0:
            return None
        item = self.item(row, 0)
        if item is None:
            return None
        value = item.data(Qt.UserRole)
        if value is None:
            value = item.text()
        try:
            return int(value)
        except (TypeError, ValueError):
            return value

    def selected_row_value(self, column: int):
        row = self.currentRow()
        if row < 0:
            return None
        item = self.item(row, column)
        return item.text() if item else None

    def clear_rows(self) -> None:
        self.setRowCount(0)


class SearchBox(QLineEdit):
    def __init__(self, placeholder: str = "Search…", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.setMinimumWidth(230)


class DebouncedSearch(SearchBox):
    changed = Signal(str)

    def __init__(self, placeholder: str = "Search…", parent=None):
        super().__init__(placeholder, parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(260)
        self._timer.timeout.connect(self._emit)
        self.textChanged.connect(lambda _text: self._timer.start())

    def _emit(self):
        self.changed.emit(self.text().strip())


def info(parent, title: str, message: str) -> None:
    QMessageBox.information(parent, title, message)


def warn(parent, title: str, message: str) -> None:
    QMessageBox.warning(parent, title, message)


def error(parent, title: str, message: str) -> None:
    QMessageBox.critical(parent, title, message)


def confirm(parent, title: str, message: str) -> bool:
    result = QMessageBox.question(
        parent,
        title,
        message,
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    )
    return result == QMessageBox.Yes


def separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setStyleSheet(f"color: {theme.BORDER_SOFT}; background: {theme.BORDER_SOFT};")
    line.setFixedHeight(1)
    return line
