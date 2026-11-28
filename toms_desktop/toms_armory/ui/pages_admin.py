"""Reports, synchronization management, local users and system settings."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QDate, QTime, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QTabWidget,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from .. import config, db, reports, services
from ..models import LocalUser, SyncHistory, SyncQueue
from ..security import ROLE_LABELS
from ..sync import retry_failed_operations
from . import theme
from .forms import ChangePasswordDialog, UserDialog
from .pages_core import BasePage
from .receipts import open_path
from .sync_ui import SyncConfigDialog, WEEKDAYS
from .widgets import (
    DataTable,
    PageHeader,
    Panel,
    StatCard,
    badge,
    button,
    confirm,
    error,
    info,
    label,
)

REPORT_TYPES = [
    ("firearms", "Firearm inventory"),
    ("ammunition", "Ammunition inventory"),
    ("assignments", "Personnel designations"),
    ("transactions", "Transaction ledger"),
    ("audit", "Audit trail"),
]

AUDIT_HEADERS = ["Date", "User", "Action", "Entity", "Reference", "Note"]


class ReportsPage(BasePage):
    def __init__(self, actor: LocalUser, parent=None):
        super().__init__(actor, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        header = PageHeader(
            "Reports",
            "Printable PDF and Excel extracts from the local armory ledger.",
        )
        pdf = button("GENERATE PDF", primary=True)
        pdf.clicked.connect(self._pdf)
        excel = button("EXPORT EXCEL")
        excel.clicked.connect(self._excel)
        header.add_action(pdf)
        header.add_action(excel)
        layout.addWidget(header)
        filters = QHBoxLayout()
        self.report_type = QComboBox()
        for key, text in REPORT_TYPES:
            self.report_type.addItem(text, key)
        self.report_type.currentIndexChanged.connect(self.refresh)
        self.from_date = QDateEdit()
        self.from_date.setCalendarPopup(True)
        self.from_date.setDisplayFormat("yyyy-MM-dd")
        self.from_date.setDate(QDate.currentDate().addMonths(-1))
        self.from_date.dateChanged.connect(self.refresh)
        self.to_date = QDateEdit()
        self.to_date.setCalendarPopup(True)
        self.to_date.setDisplayFormat("yyyy-MM-dd")
        self.to_date.setDate(QDate.currentDate())
        self.to_date.dateChanged.connect(self.refresh)
        filters.addWidget(QLabel("Report"))
        filters.addWidget(self.report_type, 1)
        filters.addWidget(QLabel("From"))
        filters.addWidget(self.from_date)
        filters.addWidget(QLabel("To"))
        filters.addWidget(self.to_date)
        layout.addLayout(filters)
        self.preview = DataTable(["Report preview"])
        layout.addWidget(self.preview, 1)
        self.summary = label("", "Muted")
        layout.addWidget(self.summary)
        self._current = None
        self.refresh()

    def _build(self):
        report_type = self.report_type.currentData()
        start = self.from_date.date().toPython()
        end = self.to_date.date().toPython()
        if end < start:
            end = start
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        title = dict(REPORT_TYPES).get(report_type, "Armory report")
        subtitle = f"Period {start.isoformat()} to {end.isoformat()}"
        with db.session_scope() as session:
            if report_type == "firearms":
                rows = reports.firearm_export_rows(services.firearm_rows(session))
                headers = reports.FIREARM_REPORT_HEADERS
                subtitle = "All registered firearms and live custody"
            elif report_type == "ammunition":
                rows = reports.ammunition_export_rows(services.ammunition_rows(session))
                headers = reports.AMMUNITION_REPORT_HEADERS
                subtitle = "All ammunition lots and stock balances"
            elif report_type == "assignments":
                source = [
                    item
                    for item in services.assignment_history(session)
                    if start <= item.assigned_at.date() <= end
                ]
                rows = reports.assignment_export_rows(source)
                headers = reports.ASSIGNMENT_REPORT_HEADERS
                subtitle = "Designation and reassignment history"
            elif report_type == "transactions":
                source = [
                    item
                    for item in services.transaction_rows(session)
                    if start <= item.transaction_at.date() <= end
                ]
                rows = reports.transaction_export_rows(source)
                headers = reports.TRANSACTION_REPORT_HEADERS
                subtitle = "Issuance, return and stock-adjustment ledger"
            else:
                source = [
                    item
                    for item in services.audit_rows(session, limit=5000)
                    if start <= item.created_at.date() <= end
                ]
                rows = [
                    [
                        item.created_at.strftime("%Y-%m-%d %H:%M"),
                        item.username,
                        item.action,
                        item.entity_type,
                        item.reference,
                        item.note,
                    ]
                    for item in source
                ]
                headers = AUDIT_HEADERS
                subtitle = "Local accountability and security events"
        return {
            "type": report_type,
            "title": title,
            "subtitle": subtitle,
            "headers": headers,
            "rows": rows,
            "stamp": stamp,
            "filename": f"{report_type}-{stamp}",
        }

    def refresh(self):
        spec = self._build()
        self._current = spec
        preview_rows = spec["rows"][:2000]
        self.preview.setColumnCount(len(spec["headers"]))
        self.preview.setHorizontalHeaderLabels(spec["headers"])
        self.preview.set_data(preview_rows)
        self.summary.setText(
            f"{len(spec['rows'])} row(s) · {spec['subtitle']}"
            + (" · preview limited to 2,000 rows" if len(spec["rows"]) > 2000 else "")
        )

    def _pdf(self):
        spec = self._current or self._build()
        default = config.exports_dir() / f"{spec['filename']}.pdf"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save PDF report", str(default), "PDF files (*.pdf)"
        )
        if not path:
            return
        try:
            reports.write_report(
                title=spec["title"],
                subtitle=spec["subtitle"],
                headers=spec["headers"],
                rows=spec["rows"],
                path=path,
                generated_by=self.actor.display_name,
            )
        except Exception as exc:
            error(self, "Report failed", str(exc))
            return
        if confirm(self, "PDF generated", f"Saved to:\n{path}\n\nOpen now?"):
            open_path(Path(path))

    def _excel(self):
        spec = self._current or self._build()
        default = config.exports_dir() / f"{spec['filename']}.xlsx"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Excel report", str(default), "Excel workbook (*.xlsx)"
        )
        if not path:
            return
        try:
            reports.write_excel(
                path,
                {spec["title"][:31]: (spec["headers"], spec["rows"])},
            )
        except Exception as exc:
            error(self, "Export failed", str(exc))
            return
        if confirm(self, "Workbook generated", f"Saved to:\n{path}\n\nOpen now?"):
            open_path(Path(path))


class SyncPage(BasePage):
    sync_requested = Signal(bool)

    def __init__(self, actor: LocalUser, parent=None):
        super().__init__(actor, parent)
        self.running = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        header = PageHeader(
            "Synchronization management",
            "Weekly at the configured time, or manually with SYNC NOW.",
        )
        self.state_badge = badge("IDLE", "ok")
        header.add_action(self.state_badge)
        sync_now = button("SYNC NOW", primary=True)
        sync_now.clicked.connect(lambda: self.sync_requested.emit(True))
        retry = button("RETRY FAILED")
        retry.clicked.connect(self._retry)
        configure = button("CONFIGURE")
        configure.clicked.connect(self._configure)
        for widget in (sync_now, retry, configure):
            header.add_action(widget)
        layout.addWidget(header)
        stats = QHBoxLayout()
        self.pending_card = StatCard("Pending uploads")
        self.failed_card = StatCard("Failed uploads")
        self.last_card = StatCard("Last attempt")
        self.next_card = StatCard("Next scheduled")
        for card in (
            self.pending_card,
            self.failed_card,
            self.last_card,
            self.next_card,
        ):
            stats.addWidget(card)
        layout.addLayout(stats)
        self.config_label = label("", "Muted", wrap=True)
        layout.addWidget(self.config_label)
        tabs = QTabWidget()
        queue_page = QWidget()
        queue_layout = QVBoxLayout(queue_page)
        queue_layout.setContentsMargins(8, 8, 8, 8)
        self.queue = DataTable(
            ["Created", "Action", "Entity", "State", "Attempts", "Last error", "Next attempt"]
        )
        self.queue.doubleClicked.connect(self._queue_error)
        queue_layout.addWidget(self.queue)
        tabs.addTab(queue_page, "Upload queue")
        history_page = QWidget()
        history_layout = QVBoxLayout(history_page)
        history_layout.setContentsMargins(8, 8, 8, 8)
        self.history = DataTable(
            ["Started", "Finished", "Status", "Uploaded", "Downloaded", "Message"]
        )
        history_layout.addWidget(self.history)
        tabs.addTab(history_page, "History")
        layout.addWidget(tabs, 1)
        self.note = label("", "Muted", wrap=True)
        layout.addWidget(self.note)
        self.refresh()

    def refresh(self):
        with db.session_scope() as session:
            pending = db.pending_sync_count(session)
            failed = db.failed_sync_count(session)
            queue = services.queue_rows(session)
            history = list(
                session.query(SyncHistory).order_by(SyncHistory.started_at.desc()).limit(100)
            )
            base = db.get_setting(session, "api_base", config.DEFAULT_API_BASE)
            username = db.get_setting(session, "sync_username", "")
            last_attempt = db.get_setting(session, "last_sync_attempt", "")
            last_success = db.get_setting(session, "last_sync_at", "")
            due = db.next_sync_due(session)
            auto = db.setting_bool(session, "auto_sync", True)
            weekday = int(db.get_setting(session, "sync_weekday", "6"))
            hour = int(db.get_setting(session, "sync_hour", "23"))
            minute = int(db.get_setting(session, "sync_minute", "0"))
            queue_rows = []
            for row in queue:
                queue_rows.append(
                    [
                        row.created_at.strftime("%Y-%m-%d %H:%M"),
                        row.action,
                        row.entity_type,
                        row.state.title(),
                        row.attempts,
                        row.last_error,
                        row.next_attempt_at.strftime("%Y-%m-%d %H:%M")
                        if row.next_attempt_at
                        else "—",
                    ]
                )
            ids = [row.id for row in queue]
            history_rows = [
                [
                    item.started_at.strftime("%Y-%m-%d %H:%M"),
                    item.finished_at.strftime("%Y-%m-%d %H:%M") if item.finished_at else "—",
                    item.status.title(),
                    item.uploaded,
                    item.downloaded,
                    item.message,
                ]
                for item in history
            ]
        self.pending_card.set_value(str(pending), "Waiting for the next cycle")
        self.failed_card.set_value(str(failed), "Review and retry")
        self.last_card.set_value(
            last_attempt[:16] if last_attempt else "Never",
            f"Last success: {last_success[:16] if last_success else '—'}",
        )
        self.next_card.set_value(due.strftime("%a %d %b %H:%M"), "Weekly schedule")
        self.config_label.setText(
            f"{base} · account {username or 'not configured'} · "
            f"automatic sync {'enabled' if auto else 'disabled'} · "
            f"{WEEKDAYS[weekday]} {hour:02d}:{minute:02d}"
        )
        self.queue.set_data(queue_rows, ids=ids, status_column=3)
        self.history.set_data(history_rows, status_column=2)
        self.state_badge.setText("RUNNING" if self.running else "IDLE")
        self.state_badge.setObjectName("BadgeWarn" if self.running else "BadgeOk")
        self.state_badge.style().unpolish(self.state_badge)
        self.state_badge.style().polish(self.state_badge)

    def set_running(self, running: bool):
        self.running = running
        self.state_badge.setText("RUNNING" if running else "IDLE")
        self.state_badge.setObjectName("BadgeWarn" if running else "BadgeOk")
        self.state_badge.style().unpolish(self.state_badge)
        self.state_badge.style().polish(self.state_badge)
        if running:
            self.note.setText("Synchronizing with TOWEMS. Local use may continue.")
        self.refresh()

    def update_result(self, result):
        self.note.setText(result.message)
        self.refresh()

    def _retry(self):
        with db.session_scope() as session:
            count = retry_failed_operations(session)
        info(self, "Retry queued", f"{count} failed operation(s) returned to the pending queue.")
        self.refresh()

    def _configure(self):
        if SyncConfigDialog(self).exec() == QDialog.Accepted:
            self.refresh()
            self.changed.emit()

    def _queue_error(self):
        row = self.queue.currentRow()
        if row < 0:
            return
        message = self.queue.selected_row_value(5) or "No error detail."
        QMessageBox.information(self, "Queue error", message)


class UsersPage(BasePage):
    def __init__(self, actor: LocalUser, parent=None):
        super().__init__(actor, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        header = PageHeader(
            "User management",
            "Local accounts and role-based access to armory functions.",
        )
        add = button("ADD USER", primary=True)
        add.clicked.connect(self._add)
        edit = button("EDIT")
        edit.clicked.connect(self._edit)
        password = button("RESET PASSWORD")
        password.clicked.connect(self._edit)
        refresh = button("REFRESH")
        refresh.clicked.connect(self.refresh)
        for widget in (add, edit, password, refresh):
            header.add_action(widget)
        layout.addWidget(header)
        self.table = DataTable(
            ["Username", "Display name", "Rank", "Role", "Active", "Last sign-in", "Created"]
        )
        self.table.doubleClicked.connect(self._edit)
        layout.addWidget(self.table, 1)
        self.summary = label("", "Muted")
        layout.addWidget(self.summary)
        self.refresh()

    def refresh(self):
        with db.session_scope() as session:
            users = services.list_users(session)
            rows = [
                [
                    user.username,
                    user.display_name,
                    user.rank,
                    ROLE_LABELS.get(user.role, user.role),
                    "Active" if user.is_active else "Inactive",
                    user.last_login_at.strftime("%Y-%m-%d %H:%M")
                    if user.last_login_at
                    else "—",
                    user.created_at.strftime("%Y-%m-%d"),
                ]
                for user in users
            ]
            ids = [user.id for user in users]
        self.table.set_data(rows, ids=ids, status_column=4)
        self.summary.setText(
            f"{len(rows)} local account(s). Password hashes use PBKDF2-HMAC-SHA256."
        )

    def _add(self):
        dialog = UserDialog(self, actor=self.actor)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()
            self.changed.emit()

    def _edit(self):
        user_id = self.table.selected_id()
        if user_id is None:
            info(self, "Users", "Select a local account first.")
            return
        with db.session_scope() as session:
            user = session.get(LocalUser, user_id)
        dialog = UserDialog(self, actor=self.actor, user=user)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()
            self.changed.emit()


class SettingsPage(BasePage):
    def __init__(self, actor: LocalUser, parent=None):
        super().__init__(actor, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        header = PageHeader(
            "System settings",
            "Organization, security, scheduled synchronization, backup and audit.",
        )
        save = button("SAVE SETTINGS", primary=True)
        save.clicked.connect(self._save)
        header.add_action(save)
        layout.addWidget(header)
        tabs = QTabWidget()
        general = QWidget()
        general_layout = QFormLayout(general)
        general_layout.setContentsMargins(18, 18, 18, 18)
        general_layout.setSpacing(10)
        self.org = QLineEdit()
        self.session_minutes = QSpinBox()
        self.session_minutes.setRange(1, 240)
        self.auto_sync = QCheckBox("Enable automatic weekly synchronization")
        self.api_base = QLabel()
        self.sync_user = QLabel()
        self.schedule = QLabel()
        self.next_due = QLabel()
        configure = button("OPEN SYNC CONFIGURATION")
        configure.clicked.connect(self._configure_sync)
        general_layout.addRow("Organisation", self.org)
        general_layout.addRow("Session timeout (minutes)", self.session_minutes)
        general_layout.addRow("", self.auto_sync)
        general_layout.addRow("TOWEMS API", self.api_base)
        general_layout.addRow("Sync account", self.sync_user)
        general_layout.addRow("Schedule", self.schedule)
        general_layout.addRow("Next due", self.next_due)
        general_layout.addRow("", configure)
        tabs.addTab(general, "General")
        security = QWidget()
        security_layout = QVBoxLayout(security)
        security_layout.setContentsMargins(18, 18, 18, 18)
        security_layout.setSpacing(10)
        password = button("CHANGE MY PASSWORD")
        password.clicked.connect(self._change_password)
        backup = button("BACKUP DATABASE NOW")
        backup.clicked.connect(self._backup)
        restore = button("RESTORE DATABASE", danger=True)
        restore.clicked.connect(self._restore)
        folder = button("OPEN DATA FOLDER")
        folder.clicked.connect(lambda: open_path(config.data_dir()))
        receipts = button("OPEN RECEIPTS FOLDER")
        receipts.clicked.connect(lambda: open_path(config.receipts_dir()))
        security_layout.addWidget(password)
        security_layout.addWidget(backup)
        security_layout.addWidget(restore)
        security_layout.addWidget(folder)
        security_layout.addWidget(receipts)
        security_layout.addWidget(
            label(
                "A backup is a consistent SQLite copy and can be restored on this "
                "computer. Store backups on controlled media.",
                "Muted",
                wrap=True,
            )
        )
        security_layout.addStretch(1)
        tabs.addTab(security, "Security & backup")
        audit_page = QWidget()
        audit_layout = QVBoxLayout(audit_page)
        audit_layout.setContentsMargins(8, 8, 8, 8)
        self.audit_table = DataTable(
            ["Date", "User", "Action", "Entity", "Reference", "Note"]
        )
        audit_layout.addWidget(self.audit_table)
        tabs.addTab(audit_page, "Audit trail")
        about = QWidget()
        about_layout = QVBoxLayout(about)
        about_layout.setContentsMargins(18, 18, 18, 18)
        about_layout.setSpacing(8)
        self.about = label(
            f"{config.APP_NAME} {config.APP_VERSION}\n"
            "Offline armory ledger with weekly TOWEMS synchronization.\n\n"
            f"Data directory: {config.data_dir()}\n"
            f"Database: {config.db_path()}\n"
            "Python, PySide6, SQLite and SQLAlchemy.\n\n"
            "Firearm ID rule: PAF- + serial number. Butt numbers are unique within "
            "each firearm type.",
            "Muted",
            wrap=True,
        )
        about_layout.addWidget(self.about)
        about_layout.addStretch(1)
        tabs.addTab(about, "About")
        layout.addWidget(tabs, 1)
        self.refresh()

    def refresh(self):
        with db.session_scope() as session:
            self.org.setText(db.get_setting(session, "org_name", "TOWEMS Armory"))
            self.session_minutes.setValue(
                int(db.get_setting(session, "session_timeout_minutes", "15"))
            )
            self.auto_sync.setChecked(db.setting_bool(session, "auto_sync", True))
            self.api_base.setText(db.get_setting(session, "api_base", config.DEFAULT_API_BASE))
            self.sync_user.setText(
                db.get_setting(session, "sync_username", "") or "Not configured"
            )
            weekday = int(db.get_setting(session, "sync_weekday", "6"))
            hour = int(db.get_setting(session, "sync_hour", "23"))
            minute = int(db.get_setting(session, "sync_minute", "0"))
            self.schedule.setText(f"{WEEKDAYS[weekday]} at {hour:02d}:{minute:02d}")
            self.next_due.setText(db.next_sync_due(session).strftime("%Y-%m-%d %H:%M"))
            audit = services.audit_rows(session, limit=500)
            audit_rows = [
                [
                    item.created_at.strftime("%Y-%m-%d %H:%M"),
                    item.username,
                    item.action,
                    item.entity_type,
                    item.reference,
                    item.note,
                ]
                for item in audit
            ]
        self.audit_table.set_data(audit_rows)

    def _save(self):
        with db.session_scope() as session:
            db.set_setting(session, "org_name", self.org.text())
            db.set_setting(session, "session_timeout_minutes", self.session_minutes.value())
            db.set_setting(session, "auto_sync", "1" if self.auto_sync.isChecked() else "0")
        info(self, "Settings", "Settings saved.")
        self.refresh()
        self.changed.emit()

    def _configure_sync(self):
        if SyncConfigDialog(self).exec() == QDialog.Accepted:
            self.refresh()
            self.changed.emit()

    def _change_password(self):
        dialog = ChangePasswordDialog(self, user=self.actor)
        if dialog.exec() == QDialog.Accepted:
            info(self, "Password", "Your local password has been changed.")

    def _backup(self):
        default = config.backups_dir() / f"armory-backup-{datetime.now():%Y%m%d-%H%M%S}.db"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save database backup", str(default), "SQLite database (*.db)"
        )
        if not path:
            return
        try:
            db.backup_database(path)
        except Exception as exc:
            error(self, "Backup failed", str(exc))
            return
        info(self, "Backup complete", f"Database backup saved to:\n{path}")

    def _restore(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Restore database backup", str(config.backups_dir()), "SQLite database (*.db)"
        )
        if not path:
            return
        if not confirm(
            self,
            "Restore database",
            "Restoring replaces the live local armory database with the selected backup.\n\n"
            "Current data since that backup will be lost. Continue?",
        ):
            return
        try:
            db.restore_database(path)
        except Exception as exc:
            error(self, "Restore failed", str(exc))
            return
        QMessageBox.warning(
            self,
            "Restart required",
            "The database has been restored. Close and reopen the application before "
            "continuing, so every screen uses the restored data.",
        )
        self.refresh()
