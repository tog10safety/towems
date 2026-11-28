"""Main shell, navigation, session security and the weekly scheduler."""

from __future__ import annotations

from datetime import datetime, timedelta

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import config, db, services
from ..models import LocalUser, SyncQueue
from ..security import ROLE_LABELS, has_permission
from ..sync import SyncResult
from . import theme
from .login import LoginDialog
from .pages_admin import ReportsPage, SettingsPage, SyncPage, UsersPage
from .pages_core import (
    AmmunitionPage,
    AssignmentsPage,
    DashboardPage,
    FirearmsPage,
    PersonnelPage,
)
from .pages_ops import IssuePage, ReturnPage, TransactionsPage
from .sync_ui import SyncCredentialDialog, SyncWorker
from .widgets import badge, button, label


NAV_ITEMS = [
    ("dashboard", "Dashboard", "◫", "view", "OPERATIONS"),
    ("firearms", "Firearm inventory", "⌖", "view", None),
    ("ammunition", "Ammunition inventory", "▦", "view", None),
    ("personnel", "Personnel management", "♟", "view", None),
    ("assignments", "Personnel assignment", "⇄", "view", None),
    ("issue", "Issuance", "↗", "issue", "ACCOUNTABILITY"),
    ("return", "Deposit / return", "↙", "return", None),
    ("transactions", "Transaction history", "≡", "view", None),
    ("reports", "Reports", "▤", "reports", None),
    ("sync", "Synchronization", "⟳", "sync", "ADMINISTRATION"),
    ("users", "User management", "♜", "manage_users", None),
    ("settings", "System settings", "⚙", "settings", None),
]

PAGE_CLASSES = {
    "dashboard": DashboardPage,
    "firearms": FirearmsPage,
    "ammunition": AmmunitionPage,
    "personnel": PersonnelPage,
    "assignments": AssignmentsPage,
    "issue": IssuePage,
    "return": ReturnPage,
    "transactions": TransactionsPage,
    "reports": ReportsPage,
    "sync": SyncPage,
    "users": UsersPage,
    "settings": SettingsPage,
}


class MainWindow(QMainWindow):
    def __init__(self, actor: LocalUser):
        super().__init__()
        self.actor = actor
        self.pages: dict[str, QWidget] = {}
        self.nav_buttons: dict[str, QPushButton] = {}
        self.current_key = ""
        self.last_activity = datetime.now()
        self.sync_worker: SyncWorker | None = None
        self.manual_sync = False
        self.setWindowTitle(f"{config.APP_NAME} · {config.APP_VERSION}")
        logo = config.resource_path("logo.png")
        if logo.exists():
            self.setWindowIcon(QIcon(str(logo)))
        self.setMinimumSize(1180, 720)
        self.resize(1440, 900)
        self._build_ui()
        self._install_security_timers()
        QApplication.instance().installEventFilter(self)
        self._show_page("dashboard")
        self._refresh_status()

    # -- shell --------------------------------------------------------------

    def _build_ui(self):
        root = QWidget()
        root.setObjectName("AppRoot")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._sidebar())
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self._topbar())
        self.stack = QStackedWidget()
        body_layout.addWidget(self.stack, 1)
        outer.addWidget(body, 1)

    def _sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(248)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        brand = QWidget()
        brand_layout = QVBoxLayout(brand)
        brand_layout.setContentsMargins(20, 20, 18, 14)
        brand_layout.setSpacing(3)
        title = label("TOWEMS", "AppTitle")
        subtitle = label("ARMORY DESKTOP · OFFLINE", "AppSubtitle")
        brand_layout.addWidget(title)
        brand_layout.addWidget(subtitle)
        layout.addWidget(brand)
        rule = QFrame()
        rule.setFixedHeight(1)
        rule.setStyleSheet(f"background: {theme.BORDER_SOFT};")
        layout.addWidget(rule)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        nav_widget = QWidget()
        nav_layout = QVBoxLayout(nav_widget)
        nav_layout.setContentsMargins(0, 8, 0, 8)
        nav_layout.setSpacing(0)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        section = None
        for key, text, glyph, permission, new_section in NAV_ITEMS:
            if permission != "view" and not has_permission(self.actor.role, permission):
                continue
            if new_section and new_section != section:
                section = new_section
                caption = label(new_section, "Muted")
                caption.setContentsMargins(20, 12, 8, 4)
                nav_layout.addWidget(caption)
            nav = QPushButton(f"  {glyph}    {text}")
            nav.setObjectName("Nav")
            nav.setCheckable(True)
            nav.setCursor(Qt.PointingHandCursor)
            nav.clicked.connect(lambda _checked=False, target=key: self._show_page(target))
            self.nav_group.addButton(nav)
            self.nav_buttons[key] = nav
            nav_layout.addWidget(nav)
        nav_layout.addStretch(1)
        scroll.setWidget(nav_widget)
        layout.addWidget(scroll, 1)
        user_box = QFrame()
        user_box.setStyleSheet(
            f"background: {theme.SURFACE}; border-top: 1px solid {theme.BORDER_SOFT};"
        )
        user_layout = QVBoxLayout(user_box)
        user_layout.setContentsMargins(18, 12, 18, 14)
        user_layout.setSpacing(4)
        self.user_name = label(self.actor.display_name, "SectionTitle")
        self.user_role = label(
            ROLE_LABELS.get(self.actor.role, self.actor.role), "Muted"
        )
        lock = button("LOCK SESSION", ghost=True)
        lock.clicked.connect(self._lock)
        user_layout.addWidget(self.user_name)
        user_layout.addWidget(self.user_role)
        user_layout.addWidget(lock)
        layout.addWidget(user_box)
        return sidebar

    def _topbar(self) -> QWidget:
        top = QFrame()
        top.setObjectName("TopBar")
        top.setFixedHeight(64)
        layout = QHBoxLayout(top)
        layout.setContentsMargins(24, 8, 22, 8)
        layout.setSpacing(12)
        self.breadcrumb = label("ARMORY / DASHBOARD", "Muted")
        layout.addWidget(self.breadcrumb)
        layout.addStretch(1)
        self.clock = label("", "Muted")
        layout.addWidget(self.clock)
        self.sync_badge = badge("LOCAL LEDGER", "ok")
        layout.addWidget(self.sync_badge)
        self.top_user = label(self.actor.display_name, "Badge")
        layout.addWidget(self.top_user)
        return top

    # -- navigation ---------------------------------------------------------

    def _page(self, key: str):
        if key in self.pages:
            return self.pages[key]
        cls = PAGE_CLASSES.get(key)
        if cls is None:
            return None
        page = cls(self.actor)
        page.changed.connect(self._data_changed)
        page.navigate.connect(self._show_page)
        if key == "sync":
            page.sync_requested.connect(self.start_sync)
        self.pages[key] = page
        self.stack.addWidget(page)
        return page

    def _show_page(self, key: str):
        page = self._page(key)
        if page is None:
            return
        self.current_key = key
        self.stack.setCurrentWidget(page)
        if hasattr(page, "on_show"):
            page.on_show()
        button_ = self.nav_buttons.get(key)
        if button_ is not None:
            button_.setChecked(True)
        title = next((item[1] for item in NAV_ITEMS if item[0] == key), key)
        self.breadcrumb.setText(f"ARMORY / {title.upper()}")

    def _data_changed(self):
        self._refresh_status()
        if self.current_key == "dashboard" and "dashboard" in self.pages:
            self.pages["dashboard"].refresh()

    def _refresh_status(self):
        try:
            with db.session_scope() as session:
                pending = db.pending_sync_count(session)
                failed = db.failed_sync_count(session)
        except Exception:
            pending = 0
            failed = 0
        if failed:
            self.sync_badge.setText(f"{failed} SYNC FAILED")
            self.sync_badge.setObjectName("BadgeDanger")
        elif pending:
            self.sync_badge.setText(f"{pending} PENDING SYNC")
            self.sync_badge.setObjectName("BadgeWarn")
        else:
            self.sync_badge.setText("LOCAL LEDGER · CLEAR")
            self.sync_badge.setObjectName("BadgeOk")
        self.sync_badge.style().unpolish(self.sync_badge)
        self.sync_badge.style().polish(self.sync_badge)
        self.clock.setText(datetime.now().strftime("%a %d %b · %H:%M"))

    # -- session security ---------------------------------------------------

    def _install_security_timers(self):
        self.clock_timer = QTimer(self)
        self.clock_timer.setInterval(15_000)
        self.clock_timer.timeout.connect(self._tick)
        self.clock_timer.start()
        self.schedule_timer = QTimer(self)
        self.schedule_timer.setInterval(60_000)
        self.schedule_timer.timeout.connect(self._check_schedule)
        self.schedule_timer.start()
        QTimer.singleShot(4_000, self._check_schedule)

    def eventFilter(self, watched, event):  # noqa: N802
        if event.type() in {
            QEvent.MouseButtonPress,
            QEvent.KeyPress,
            QEvent.Wheel,
            QEvent.TouchBegin,
        }:
            self.last_activity = datetime.now()
        return super().eventFilter(watched, event)

    def _tick(self):
        self._refresh_status()
        timeout_minutes = 15
        try:
            with db.session_scope() as session:
                timeout_minutes = int(
                    db.get_setting(session, "session_timeout_minutes", "15")
                )
        except Exception:
            pass
        if QApplication.activeModalWidget() is not None:
            return
        elapsed = datetime.now() - self.last_activity
        if elapsed >= timedelta(minutes=max(1, timeout_minutes)):
            self._lock()

    def _lock(self):
        dialog = LoginDialog(self, username=self.actor.username, locked=True)
        if dialog.exec() != QDialog.Accepted or dialog.user is None:
            self.close()
            return
        if dialog.user.id != self.actor.id:
            QMessageBox.warning(
                self,
                "Session locked",
                "This session is locked to the account that was signed in. "
                "Close the application to switch accounts.",
            )
            self._lock()
            return
        self.actor = dialog.user
        self.last_activity = datetime.now()
        self.top_user.setText(self.actor.display_name)

    # -- synchronization ----------------------------------------------------

    def start_sync(self, manual: bool = True):
        if self.sync_worker is not None and self.sync_worker.isRunning():
            QMessageBox.information(self, "Synchronization", "A synchronization is already running.")
            return
        with db.session_scope() as session:
            base = db.get_setting(session, "api_base", config.DEFAULT_API_BASE)
            username = db.get_setting(session, "sync_username", "")
        password = ""
        if manual:
            dialog = SyncCredentialDialog(self)
            if dialog.exec() != QDialog.Accepted:
                return
            base = dialog.base.text().strip().rstrip("/")
            username = dialog.username.text().strip()
            password = dialog.password.text()
        else:
            if not username:
                return
        self.manual_sync = manual
        self.sync_worker = SyncWorker(
            base_url=base,
            username=username,
            password=password,
            trigger="manual" if manual else "scheduled",
            parent=self,
        )
        self.sync_worker.result_ready.connect(self._sync_finished)
        self.sync_badge.setText("SYNCING…")
        self.sync_badge.setObjectName("BadgeWarn")
        self.sync_badge.style().unpolish(self.sync_badge)
        self.sync_badge.style().polish(self.sync_badge)
        with db.session_scope() as session:
            db.set_setting(session, "last_sync_attempt", datetime.now().isoformat(timespec="seconds"))
        if "sync" in self.pages:
            self.pages["sync"].set_running(True)
        self.sync_worker.start()

    def _sync_finished(self, result: SyncResult):
        with db.session_scope() as session:
            db.set_setting(
                session, "last_sync_attempt", datetime.now().isoformat(timespec="seconds")
            )
            if result.status in {"success", "partial"}:
                db.set_setting(
                    session, "last_sync_at", datetime.now().isoformat(timespec="seconds")
                )
        if "sync" in self.pages:
            self.pages["sync"].set_running(False)
            self.pages["sync"].update_result(result)
        for key in ("dashboard", "firearms", "ammunition", "personnel", "assignments", "transactions", "return"):
            if key in self.pages and key == self.current_key:
                self.pages[key].refresh()
        self._refresh_status()
        if self.manual_sync:
            title = {
                "success": "Synchronization complete",
                "partial": "Synchronization partially complete",
                "failed": "Synchronization failed",
                "skipped": "Synchronization skipped",
            }.get(result.status, "Synchronization")
            box = QMessageBox(self)
            box.setWindowTitle(title)
            box.setIcon(
                QMessageBox.Information
                if result.status == "success"
                else QMessageBox.Warning
            )
            box.setText(result.message or title)
            if result.errors:
                detail = "\n".join(
                    f"• {item.get('action', '')}: {item.get('error', '')}"
                    for item in result.errors[:8]
                )
                box.setDetailedText(detail)
            box.exec()
        if self.sync_worker is not None:
            self.sync_worker.deleteLater()
            self.sync_worker = None

    def _check_schedule(self):
        if self.sync_worker is not None and self.sync_worker.isRunning():
            return
        try:
            with db.session_scope() as session:
                if not db.setting_bool(session, "auto_sync", True):
                    return
                username = db.get_setting(session, "sync_username", "")
                if not username:
                    return
                weekday = int(db.get_setting(session, "sync_weekday", "6"))
                hour = int(db.get_setting(session, "sync_hour", "23"))
                minute = int(db.get_setting(session, "sync_minute", "0"))
                last_text = db.get_setting(session, "last_sync_attempt", "")
                retry_due = session.query(SyncQueue).filter(
                    SyncQueue.state == "pending",
                    SyncQueue.attempts > 0,
                    SyncQueue.next_attempt_at.is_not(None),
                    SyncQueue.next_attempt_at <= datetime.now(),
                ).first()
            now = datetime.now()
            days = (now.weekday() - weekday) % 7
            due = now.replace(hour=hour, minute=minute, second=0, microsecond=0) - timedelta(days=days)
            if due > now:
                due -= timedelta(days=7)
            last = None
            if last_text:
                try:
                    last = datetime.fromisoformat(last_text)
                except ValueError:
                    last = None
            scheduled = last is None or last < due
            if scheduled or retry_due is not None:
                self.manual_sync = False
                self.start_sync(False)
        except Exception:
            return

    # -- close --------------------------------------------------------------

    def closeEvent(self, event):  # noqa: N802
        if self.sync_worker is not None and self.sync_worker.isRunning():
            QMessageBox.warning(
                self,
                "Synchronization running",
                "Wait for the current synchronization to finish before closing.",
            )
            event.ignore()
            return
        self.clock_timer.stop()
        self.schedule_timer.stop()
        super().closeEvent(event)
