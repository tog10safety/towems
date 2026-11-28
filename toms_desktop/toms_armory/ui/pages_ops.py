"""Issuance eligibility, deposit/return and transaction history screens."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

from .. import db, services
from ..models import LocalUser
from .forms import AmmunitionDialog
from .pages_core import BasePage
from .transactions import IssueDialog, ReturnDialog, TransactionDetailDialog
from .widgets import DataTable, DebouncedSearch, PageHeader, button, label


class IssuePage(BasePage):
    def __init__(self, actor: LocalUser, parent=None):
        super().__init__(actor, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        header = PageHeader(
            "Firearm and ammunition issuance",
            "Mission-based or regular release. Mission Order is optional for regular issuance.",
        )
        new = button("NEW ISSUANCE", primary=True)
        new.clicked.connect(self._new)
        refresh = button("REFRESH")
        refresh.clicked.connect(self.refresh)
        header.add_action(new)
        header.add_action(refresh)
        if not self.permission("issue"):
            new.setEnabled(False)
        layout.addWidget(header)
        filters = QHBoxLayout()
        self.mission = QComboBox()
        self.mission.addItem("All eligible personnel (regular issuance)", None)
        self._load_missions()
        self.mission.currentIndexChanged.connect(self.refresh)
        self.search = DebouncedSearch("Search eligible person, rifle or pistol…")
        self.search.changed.connect(self.refresh)
        filters.addWidget(self.mission, 1)
        filters.addWidget(self.search, 1)
        layout.addLayout(filters)
        self.table = DataTable(
            [
                "Personnel",
                "Rank",
                "Service No.",
                "Unit",
                "Designated rifle",
                "Designated pistol",
                "Readiness",
                "Blockers",
                "Open issue",
            ]
        )
        self.table.doubleClicked.connect(self._new_selected)
        layout.addWidget(self.table, 1)
        self.summary = label("", "Muted")
        layout.addWidget(self.summary)
        self.refresh()

    def _load_missions(self):
        with db.session_scope() as session:
            missions = services.mission_rows(session, only_ready=True)
            for mission in missions:
                self.mission.addItem(
                    f"{mission.mission_number} · {mission.title}", mission.id
                )

    def refresh(self):
        mission_id = self.mission.currentData()
        with db.session_scope() as session:
            rows = services.eligibility_rows(session, mission_id)
            open_issues = {
                transaction.personnel_id: transaction
                for transaction in services.open_issues(session)
            }
        needle = self.search.text().lower()
        display = []
        ids = []
        ready = 0
        for row in rows:
            person = row["personnel"]
            haystack = " ".join(
                [
                    person.display_name,
                    person.serial_number,
                    row["rifle"].firearm_id if row["rifle"] else "",
                    row["pistol"].firearm_id if row["pistol"] else "",
                ]
            ).lower()
            if needle and needle not in haystack:
                continue
            issue = open_issues.get(person.id)
            display.append(
                [
                    person.display_name,
                    person.rank,
                    person.serial_number,
                    person.unit.acronym if person.unit else "",
                    f"{row['rifle'].firearm_id} / {row['rifle'].butt_number}"
                    if row["rifle"]
                    else "—",
                    f"{row['pistol'].firearm_id} / {row['pistol'].butt_number}"
                    if row["pistol"]
                    else "—",
                    "Ready" if row["ready"] and issue is None else "Blocked",
                    " ".join(row["blockers"])
                    + (f" Open issue {issue.reference_number}." if issue else ""),
                    issue.reference_number if issue else "—",
                ]
            )
            ids.append(person.id)
            if row["ready"] and issue is None:
                ready += 1
        self.table.set_data(display, ids=ids, status_column=6)
        self.summary.setText(
            f"{len(display)} personnel shown · {ready} ready to issue."
        )

    def _new(self):
        dialog = IssueDialog(
            self,
            actor=self.actor,
            mission_id=self.mission.currentData(),
        )
        if dialog.exec() == QDialog.Accepted:
            self.refresh()
            self.changed.emit()

    def _new_selected(self):
        person_id = self.table.selected_id()
        dialog = IssueDialog(
            self,
            actor=self.actor,
            personnel_id=person_id,
            mission_id=self.mission.currentData(),
        )
        if dialog.exec() == QDialog.Accepted:
            self.refresh()
            self.changed.emit()


class ReturnPage(BasePage):
    def __init__(self, actor: LocalUser, parent=None):
        super().__init__(actor, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        header = PageHeader(
            "Firearm and ammunition deposit / return",
            "Receive all firearms from an open issue and reconcile ammunition.",
        )
        receive = button("RECEIVE RETURN", primary=True)
        receive.clicked.connect(self._receive)
        refresh = button("REFRESH")
        refresh.clicked.connect(self.refresh)
        header.add_action(receive)
        header.add_action(refresh)
        if not self.permission("return"):
            receive.setEnabled(False)
        layout.addWidget(header)
        self.table = DataTable(
            [
                "Reference",
                "Issued",
                "Personnel",
                "Rank",
                "Unit",
                "Mission / purpose",
                "Firearms",
                "Ammunition",
                "Status",
            ]
        )
        self.table.doubleClicked.connect(self._receive)
        layout.addWidget(self.table, 1)
        self.summary = label("", "Muted")
        layout.addWidget(self.summary)
        self.refresh()

    def refresh(self):
        with db.session_scope() as session:
            issues = services.open_issues(session)
            display = []
            ids = []
            for issue in issues:
                firearms = len([line for line in issue.lines if line.firearm_id])
                rounds = sum(line.quantity for line in issue.lines if line.ammunition_id)
                display.append(
                    [
                        issue.reference_number,
                        issue.transaction_at.strftime("%Y-%m-%d %H:%M"),
                        issue.personnel_name,
                        issue.rank,
                        issue.unit_name,
                        issue.mission_order_reference or issue.purpose or "Regular issue",
                        str(firearms),
                        f"{rounds} rounds" if rounds else "—",
                        issue.status.title(),
                    ]
                )
                ids.append(issue.id)
        self.table.set_data(display, ids=ids, status_column=8)
        self.summary.setText(
            f"{len(display)} open issuance(s). Every firearm on an issue must be "
            "returned together."
        )

    def _receive(self):
        issuance_id = self.table.selected_id()
        if issuance_id is None:
            if self.table.rowCount() == 0:
                QMessageBox.information(self, "Returns", "There are no open issuances.")
                return
            QMessageBox.information(self, "Returns", "Select an open issuance first.")
            return
        dialog = ReturnDialog(self, actor=self.actor, issuance_id=issuance_id)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()
            self.changed.emit()


class TransactionsPage(BasePage):
    def __init__(self, actor: LocalUser, parent=None):
        super().__init__(actor, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        header = PageHeader(
            "Transaction history",
            "Issuance, return and adjustment records are retained locally after sync.",
        )
        refresh = button("REFRESH")
        refresh.clicked.connect(self.refresh)
        header.add_action(refresh)
        layout.addWidget(header)
        filters = QHBoxLayout()
        self.search = DebouncedSearch("Search reference, person, firearm, butt, serial, lot…")
        self.search.changed.connect(self.refresh)
        self.type = QComboBox()
        self.type.addItem("All transaction types", "")
        self.type.addItem("Issuance", "issuance")
        self.type.addItem("Return", "return")
        self.type.addItem("Adjustment", "adjustment")
        self.type.currentIndexChanged.connect(self.refresh)
        self.state = QComboBox()
        self.state.addItem("All statuses", "")
        self.state.addItem("Open", "open")
        self.state.addItem("Closed", "closed")
        self.state.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.type)
        filters.addWidget(self.state)
        layout.addLayout(filters)
        self.table = DataTable(
            [
                "Date",
                "Reference",
                "Type",
                "Status",
                "Personnel",
                "Rank",
                "Mission Order",
                "Purpose",
                "Firearms (ID / butt / type / serial)",
                "Ammunition",
                "Sync",
                "TOWEMS ref.",
            ]
        )
        self.table.doubleClicked.connect(self._detail)
        layout.addWidget(self.table, 1)
        self.summary = label("", "Muted")
        layout.addWidget(self.summary)
        self.refresh()

    def refresh(self):
        with db.session_scope() as session:
            transactions = services.transaction_rows(
                session,
                search=self.search.text(),
                transaction_type=self.type.currentData(),
                status=self.state.currentData(),
            )
            display = []
            ids = []
            for transaction in transactions:
                firearms = [
                    f"{line.firearm_id_snapshot} / {line.butt_number_snapshot} / "
                    f"{line.firearm_type.title()} / {line.serial_number_snapshot}"
                    for line in transaction.lines
                    if line.firearm_id
                ]
                rounds = sum(
                    line.quantity for line in transaction.lines if line.ammunition_id
                )
                display.append(
                    [
                        transaction.transaction_at.strftime("%Y-%m-%d %H:%M"),
                        transaction.reference_number,
                        transaction.transaction_type.title(),
                        transaction.status.title(),
                        transaction.personnel_name,
                        transaction.rank,
                        transaction.mission_order_reference or "—",
                        transaction.purpose or "—",
                        "; ".join(firearms) if firearms else "—",
                        f"{rounds} rounds" if rounds else "—",
                        transaction.sync_state.title(),
                        transaction.remote_reference or "—",
                    ]
                )
                ids.append(transaction.id)
        self.table.set_data(display, ids=ids, status_column=3)
        self.summary.setText(f"{len(display)} transaction(s) in the local ledger.")

    def _detail(self):
        transaction_id = self.table.selected_id()
        if transaction_id is None:
            return
        TransactionDetailDialog(self, transaction_id=transaction_id).exec()
