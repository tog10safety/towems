"""Receipt generation and opening helpers shared by transaction screens."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QWidget

from .. import db, reports, services


def open_path(path: Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))


def offer_receipt(parent: QWidget, transaction_id: int, *, generated_by: str = "") -> Path | None:
    with db.session_scope() as session:
        transaction = services.transaction_by_id(session, transaction_id)
        if transaction is None:
            QMessageBox.warning(parent, "Receipt", "The transaction could not be loaded.")
            return None
        try:
            path = reports.write_receipt(transaction)
        except Exception as exc:
            QMessageBox.critical(parent, "Receipt", f"The receipt could not be generated:\n{exc}")
            return None
    result = QMessageBox.question(
        parent,
        "Receipt generated",
        f"Receipt saved to:\n{path}\n\nOpen it now?",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.Yes,
    )
    if result == QMessageBox.Yes:
        open_path(path)
    return path
