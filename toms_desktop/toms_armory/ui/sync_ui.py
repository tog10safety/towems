"""Synchronization worker and configuration dialogs."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QVBoxLayout,
)

from .. import config, db
from ..api_client import validate_base_url
from ..sync import SyncService
from .widgets import button, label

WEEKDAYS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


class SyncWorker(QThread):
    result_ready = Signal(object)

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str = "",
        trigger: str = "manual",
        parent=None,
    ):
        super().__init__(parent)
        self.base_url = base_url
        self.username = username
        self.password = password
        self.trigger = trigger

    def run(self):
        result = SyncService().run(
            username=self.username,
            password=self.password,
            base_url=self.base_url,
            trigger=self.trigger,
        )
        self.result_ready.emit(result)


class SyncConfigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Synchronization configuration")
        self.setModal(True)
        self.resize(580, 440)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(12)
        root.addWidget(label("TOWEMS SYNCHRONIZATION", "PageTitle"))
        root.addWidget(
            label(
                "Use the HTTPS address of the TOWEMS API, ending in /api. Plain HTTP "
                "is accepted only for localhost development. The synchronization "
                "account must be an armory manager in TOWEMS.",
                "PageSubtitle",
                wrap=True,
            )
        )
        form = QFormLayout()
        form.setSpacing(9)
        with db.session_scope() as session:
            base = db.get_setting(session, "api_base", config.DEFAULT_API_BASE)
            username = db.get_setting(session, "sync_username", "")
            auto = db.setting_bool(session, "auto_sync", True)
            weekday = int(db.get_setting(session, "sync_weekday", "6"))
            hour = int(db.get_setting(session, "sync_hour", "23"))
            minute = int(db.get_setting(session, "sync_minute", "0"))
        self.base = QLineEdit(base)
        self.base.setPlaceholderText("https://towems.example.org/api")
        self.username = QLineEdit(username)
        self.username.setPlaceholderText("TOWEMS API account")
        self.auto = QCheckBox("Run the weekly synchronization automatically")
        self.auto.setChecked(auto)
        self.weekday = QComboBox()
        for index, name in enumerate(WEEKDAYS):
            self.weekday.addItem(name, index)
        self.weekday.setCurrentIndex(max(0, min(6, weekday)))
        self.hour = QSpinBox()
        self.hour.setRange(0, 23)
        self.hour.setValue(hour)
        self.minute = QSpinBox()
        self.minute.setRange(0, 59)
        self.minute.setValue(minute)
        schedule = QHBoxLayout()
        schedule.addWidget(self.weekday, 1)
        schedule.addWidget(self.hour)
        schedule.addWidget(label(":", "Muted"))
        schedule.addWidget(self.minute)
        form.addRow("API address", self.base)
        form.addRow("API username", self.username)
        form.addRow("", self.auto)
        form.addRow("Weekly schedule", schedule)
        root.addLayout(form)
        note = label(
            "The password is never stored. After the first successful sign-in, a "
            "refresh token is kept in Windows Credential Manager. The scheduler cannot "
            "synchronize until that token has been provisioned by a manual sync.",
            "Muted",
            wrap=True,
        )
        root.addWidget(note)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = button("CANCEL")
        cancel.clicked.connect(self.reject)
        save = button("SAVE CONFIGURATION", primary=True)
        save.clicked.connect(self._save)
        actions.addWidget(cancel)
        actions.addWidget(save)
        root.addLayout(actions)

    def _save(self):
        try:
            base = validate_base_url(self.base.text())
        except Exception as exc:
            QMessageBox.warning(self, "Configuration", str(exc))
            return
        with db.session_scope() as session:
            db.set_setting(session, "api_base", base)
            db.set_setting(session, "sync_username", self.username.text().strip())
            db.set_setting(session, "auto_sync", "1" if self.auto.isChecked() else "0")
            db.set_setting(session, "sync_weekday", self.weekday.currentData())
            db.set_setting(session, "sync_hour", self.hour.value())
            db.set_setting(session, "sync_minute", self.minute.value())
        self.accept()


class SyncCredentialDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Authorise synchronization")
        self.setModal(True)
        self.resize(540, 340)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(12)
        root.addWidget(label("AUTHORISE TOWEMS SYNC", "PageTitle"))
        root.addWidget(
            label(
                "The armory remains fully operational offline. This sign-in only "
                "establishes the secure upload/download session.",
                "PageSubtitle",
                wrap=True,
            )
        )
        form = QFormLayout()
        form.setSpacing(9)
        with db.session_scope() as session:
            base = db.get_setting(session, "api_base", config.DEFAULT_API_BASE)
            username = db.get_setting(session, "sync_username", "")
        self.base = QLineEdit(base)
        self.username = QLineEdit(username)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("Leave blank to use the saved session")
        form.addRow("API address", self.base)
        form.addRow("TOWEMS username", self.username)
        form.addRow("Password", self.password)
        root.addLayout(form)
        root.addWidget(
            label(
                "A saved refresh token expires after seven days of inactivity. "
                "Enter the password again to renew it.",
                "Muted",
                wrap=True,
            )
        )
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = button("CANCEL")
        cancel.clicked.connect(self.reject)
        go = button("SYNC NOW", primary=True)
        go.clicked.connect(self._accept)
        actions.addWidget(cancel)
        actions.addWidget(go)
        root.addLayout(actions)
        self.password.setFocus()

    def _accept(self):
        try:
            base = validate_base_url(self.base.text())
        except Exception as exc:
            QMessageBox.warning(self, "Synchronization", str(exc))
            return
        if not self.username.text().strip():
            QMessageBox.warning(self, "Synchronization", "Enter the TOWEMS API username.")
            return
        with db.session_scope() as session:
            db.set_setting(session, "api_base", base)
            db.set_setting(session, "sync_username", self.username.text().strip())
        self.accept()
