"""First-run setup, sign-in and session lock dialogs."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from .. import config, db, services
from ..models import LocalUser
from ..security import ROLE_ADMIN
from . import theme
from .widgets import button, label


class _AuthFrame(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.setFixedWidth(430)
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(30, 28, 30, 26)
        self.outer.setSpacing(14)
        logo_path = config.resource_path("logo.png")
        if logo_path.exists():
            pixmap = QPixmap(str(logo_path))
            if not pixmap.isNull():
                image = QLabel()
                image.setPixmap(
                    pixmap.scaledToHeight(64, Qt.SmoothTransformation)
                )
                image.setAlignment(Qt.AlignCenter)
                self.outer.addWidget(image)
        heading = label("TOWEMS ARMORY", "AppTitle")
        heading.setAlignment(Qt.AlignCenter)
        subtitle = label("OFFLINE ARMORY MANAGEMENT SYSTEM", "AppSubtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        self.outer.addWidget(heading)
        self.outer.addWidget(subtitle)


class LoginDialog(QDialog):
    def __init__(self, parent=None, *, username: str = "", locked: bool = False):
        super().__init__(parent)
        self.user: LocalUser | None = None
        self.setWindowTitle("Sign in — TOWEMS Armory")
        self.setModal(True)
        self.setMinimumWidth(520)
        root = QVBoxLayout(self)
        root.setContentsMargins(40, 36, 40, 34)
        root.addStretch(1)
        frame = _AuthFrame(self)
        root.addWidget(frame, 0, Qt.AlignHCenter)
        root.addStretch(1)
        if locked:
            notice = label(
                "The local session timed out. Sign in again to continue.",
                "BadgeWarn",
                wrap=True,
            )
            notice.setFixedWidth(370)
            frame.outer.addWidget(notice)
        form = QFormLayout()
        form.setSpacing(11)
        form.setLabelAlignment(Qt.AlignLeft)
        self.username = QLineEdit(username)
        self.username.setPlaceholderText("Local armory username")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("Password")
        self.password.returnPressed.connect(self._submit)
        show = QCheckBox("Show password")
        show.toggled.connect(
            lambda checked: self.password.setEchoMode(
                QLineEdit.Normal if checked else QLineEdit.Password
            )
        )
        form.addRow("Username", self.username)
        form.addRow("Password", self.password)
        frame.outer.addLayout(form)
        frame.outer.addWidget(show)
        submit = button("SIGN IN", primary=True)
        submit.setMinimumHeight(38)
        submit.clicked.connect(self._submit)
        frame.outer.addWidget(submit)
        footer = label(
            "Accounts, roles and passwords are managed locally. "
            "Failed attempts are written to the audit trail.",
            "Muted",
            wrap=True,
        )
        footer.setAlignment(Qt.AlignCenter)
        frame.outer.addWidget(footer)
        self.password.setFocus()

    def _submit(self) -> None:
        username = self.username.text().strip()
        password = self.password.text()
        if not username or not password:
            QMessageBox.warning(self, "Sign in", "Enter your username and password.")
            return
        try:
            with db.session_scope() as session:
                user = services.authenticate(session, username, password)
                if user is None:
                    db.audit(
                        session,
                        username=username,
                        action="login_failed",
                        entity_type="local_user",
                        note="Invalid local credentials",
                    )
        except Exception as exc:
            QMessageBox.critical(self, "Sign in failed", str(exc))
            return
        if user is None:
            self.password.clear()
            QMessageBox.warning(self, "Sign in failed", "Invalid username or password.")
            return
        self.user = user
        self.accept()


class SetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.user: LocalUser | None = None
        self.setWindowTitle("First-run setup — TOWEMS Armory")
        self.setModal(True)
        self.setMinimumWidth(640)
        root = QVBoxLayout(self)
        root.setContentsMargins(34, 28, 34, 28)
        root.setSpacing(14)
        frame = QFrame()
        frame.setObjectName("Card")
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(26, 24, 26, 24)
        outer.setSpacing(13)
        title = label("INITIALISE THE LOCAL ARMORY", "AppTitle")
        subtitle = label(
            "Create the first local administrator. This account controls local users, "
            "settings, backup and TOWEMS synchronization.",
            "PageSubtitle",
            wrap=True,
        )
        outer.addWidget(title)
        outer.addWidget(subtitle)
        form = QFormLayout()
        form.setSpacing(10)
        self.display_name = QLineEdit()
        self.display_name.setPlaceholderText("e.g. Maj J. Smith")
        self.username = QLineEdit()
        self.username.setPlaceholderText("e.g. armory.admin")
        self.rank = QLineEdit()
        self.rank.setPlaceholderText("Rank / appointment")
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText("At least 10 characters, letters and numbers")
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.Password)
        self.confirm.setPlaceholderText("Repeat password")
        self.unit_name = QLineEdit("Main Armory")
        self.unit_acronym = QLineEdit("MAIN")
        form.addRow("Display name", self.display_name)
        form.addRow("Username", self.username)
        form.addRow("Rank", self.rank)
        form.addRow("Password", self.password)
        form.addRow("Confirm", self.confirm)
        form.addRow("Armory unit", self.unit_name)
        form.addRow("Unit acronym", self.unit_acronym)
        outer.addLayout(form)
        note = label(
            "The unit can be aligned to a TOWEMS unit during the first synchronization. "
            "Do not lose the administrator password: it cannot be recovered without "
            "a database backup.",
            "Muted",
            wrap=True,
        )
        outer.addWidget(note)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = button("CANCEL")
        cancel.clicked.connect(self.reject)
        create = button("CREATE LOCAL ARMORY", primary=True)
        create.clicked.connect(self._create)
        actions.addWidget(cancel)
        actions.addWidget(create)
        outer.addLayout(actions)
        root.addWidget(frame)
        self.display_name.setFocus()

    def _create(self) -> None:
        if self.password.text() != self.confirm.text():
            QMessageBox.warning(self, "Setup", "The password confirmation does not match.")
            return
        if not self.unit_name.text().strip():
            QMessageBox.warning(self, "Setup", "Enter the armory unit name.")
            return
        try:
            with db.session_scope() as session:
                user = services.create_initial_admin(
                    session,
                    username=self.username.text(),
                    display_name=self.display_name.text(),
                    password=self.password.text(),
                    rank=self.rank.text(),
                    unit_name=self.unit_name.text(),
                    unit_acronym=self.unit_acronym.text(),
                )
        except Exception as exc:
            QMessageBox.critical(self, "Setup", str(exc))
            return
        self.user = user
        self.accept()


def ensure_authenticated(parent=None, *, username: str = "", locked: bool = False):
    """Return an authenticated local user, showing setup or login as needed."""
    try:
        if services.first_run() and not username:
            setup = SetupDialog(parent)
            if setup.exec() != QDialog.Accepted:
                return None
            return setup.user
    except Exception as exc:
        QMessageBox.critical(parent, "Startup", str(exc))
        return None
    dialog = LoginDialog(parent, username=username, locked=locked)
    if dialog.exec() != QDialog.Accepted:
        return None
    return dialog.user
