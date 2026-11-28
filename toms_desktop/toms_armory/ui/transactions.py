"""Issuance, deposit/return and transaction detail dialogs."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .. import db, services
from ..models import Ammunition, Firearm, LocalUser, Personnel, Transaction
from .receipts import offer_receipt
from .widgets import button, label


def _check_item(checked: bool = True) -> QTableWidgetItem:
    item = QTableWidgetItem()
    item.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
    item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
    return item


def _readonly(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(str(text or ""))
    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
    return item


def _table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setAlternatingRowColors(True)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.horizontalHeader().setStretchLastSection(True)
    table.setSelectionMode(QTableWidget.NoSelection)
    return table


class IssueDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        actor: LocalUser,
        personnel_id: int | None = None,
        mission_id: int | None = None,
    ):
        super().__init__(parent)
        self.actor_id = actor.id
        self.transaction_id: int | None = None
        self.people: dict[int, dict] = {}
        self.firearm_widgets: list[dict] = []
        self.ammo_widgets: list[dict] = []
        self.setWindowTitle("Issue firearms and ammunition")
        self.setModal(True)
        self.resize(980, 820)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(10)
        root.addWidget(label("FIREARM AND AMMUNITION ISSUANCE", "PageTitle"))
        root.addWidget(
            label(
                "Mission Order is optional. Regular issuance requires a purpose and "
                "the receiving person's acknowledgement. Every designated firearm must "
                "be physically verified and accounted for.",
                "PageSubtitle",
                wrap=True,
            )
        )
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        top = QFormLayout()
        top.setSpacing(8)
        self.mode = QComboBox()
        self.mode.addItem("Regular / non-mission issuance", "regular")
        self.mode.addItem("Mission-based issuance", "mission")
        self.mission = QComboBox()
        self.mission.setEnabled(False)
        self.person = QComboBox()
        self.purpose = QLineEdit()
        self.purpose.setPlaceholderText("Reason / authority for a regular issuance")
        top.addRow("Issuance type *", self.mode)
        top.addRow("Mission Order", self.mission)
        top.addRow("Receiving person *", self.person)
        top.addRow("Purpose / reason", self.purpose)
        layout.addLayout(top)
        self.summary = label("", "Muted", wrap=True)
        layout.addWidget(self.summary)
        tabs = QTabWidget()
        firearm_page = QWidget()
        firearm_layout = QVBoxLayout(firearm_page)
        firearm_layout.setContentsMargins(8, 8, 8, 8)
        self.firearm_table = _table(
            ["Type", "Firearm ID", "Butt No. (verify)", "Serial No.", "Passed inspection", "Inspection remarks"]
        )
        self.firearm_table.setMinimumHeight(125)
        firearm_layout.addWidget(self.firearm_table)
        tabs.addTab(firearm_page, "1 · Designated firearms")
        ammo_page = QWidget()
        ammo_layout = QVBoxLayout(ammo_page)
        ammo_layout.setContentsMargins(8, 8, 8, 8)
        self.ammo_table = _table(
            ["Issue", "Nomenclature", "Caliber / lot", "Available", "Quantity"]
        )
        self.ammo_table.setMinimumHeight(160)
        ammo_layout.addWidget(self.ammo_table)
        ammo_layout.addWidget(
            label(
                "Only serviceable, unexpired lots are shown. Leave a lot unchecked if "
                "no ammunition is being drawn.",
                "Muted",
                wrap=True,
            )
        )
        tabs.addTab(ammo_page, "2 · Ammunition")
        accountability = QWidget()
        accountability_layout = QFormLayout(accountability)
        accountability_layout.setContentsMargins(10, 10, 10, 10)
        accountability_layout.setSpacing(9)
        self.identity = QCheckBox("I verified the receiving person's identity")
        self.acknowledged = QCheckBox("The receiving person acknowledged the issue")
        self.ack_name = QLineEdit()
        self.ack_name.setPlaceholderText("Full name of the receiving person")
        self.inspection_remarks = QPlainTextEdit()
        self.inspection_remarks.setFixedHeight(60)
        self.documents = QLineEdit()
        self.documents.setPlaceholderText("Mission Order / authority / supporting reference")
        accountability_layout.addRow("", self.identity)
        accountability_layout.addRow("", self.acknowledged)
        accountability_layout.addRow("Acknowledged by *", self.ack_name)
        accountability_layout.addRow("Inspection remarks", self.inspection_remarks)
        accountability_layout.addRow("Supporting documents", self.documents)
        tabs.addTab(accountability, "3 · Accountability")
        layout.addWidget(tabs, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = button("CANCEL")
        cancel.clicked.connect(self.reject)
        self.release = button("RELEASE AND GENERATE RECEIPT", primary=True)
        self.release.clicked.connect(self._save)
        actions.addWidget(cancel)
        actions.addWidget(self.release)
        root.addLayout(actions)

        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.mission.currentIndexChanged.connect(self._refresh_people)
        self.person.currentIndexChanged.connect(self._person_changed)
        self._load_missions(mission_id)
        self._mode_changed()
        if personnel_id:
            index = self.person.findData(personnel_id)
            if index >= 0:
                self.person.setCurrentIndex(index)

    def _load_missions(self, selected_id: int | None = None):
        self.mission.clear()
        self.mission.addItem("Select an approved Mission Order…", None)
        with db.session_scope() as session:
            missions = services.mission_rows(session, only_ready=True)
            for mission in missions:
                self.mission.addItem(
                    f"{mission.mission_number} · {mission.title}",
                    mission.id,
                )
                if mission.id == selected_id:
                    self.mission.setCurrentIndex(self.mission.count() - 1)

    def _mode_changed(self):
        mission_mode = self.mode.currentData() == "mission"
        self.mission.setEnabled(mission_mode)
        if not mission_mode:
            self.mission.setCurrentIndex(0)
        self._refresh_people()

    def _refresh_people(self):
        mission_mode = self.mode.currentData() == "mission"
        mission_id = self.mission.currentData() if mission_mode else None
        if mission_mode and mission_id is None:
            self.people = {}
            self.person.clear()
            self.summary.setText("Choose an approved Mission Order to load its roster.")
            self.release.setEnabled(False)
            self._clear_firearms()
            self._load_ammunition()
            return
        with db.session_scope() as session:
            rows = services.eligibility_rows(session, mission_id)
        self.people = {row["personnel"].id: row for row in rows}
        self.person.blockSignals(True)
        self.person.clear()
        ready_count = 0
        for person_id, row in self.people.items():
            person = row["personnel"]
            self.person.addItem(services.personnel_label(person), person_id)
            if row["ready"]:
                ready_count += 1
        self.person.blockSignals(False)
        if not self.people:
            self.summary.setText(
                "No personnel are available for this selection. Check the Mission Order "
                "roster, designations and authorisation state."
            )
            self.release.setEnabled(False)
            self._clear_firearms()
        else:
            self.summary.setText(
                f"{len(self.people)} personnel loaded · {ready_count} ready for issue."
            )
            self.person.setCurrentIndex(0)
            self._person_changed()
        self._load_ammunition()

    def _person_changed(self):
        person_id = self.person.currentData()
        row = self.people.get(person_id)
        self._clear_firearms()
        if row is None:
            self.summary.setText("Choose a receiving person.")
            self.release.setEnabled(False)
            return
        person = row["personnel"]
        blockers = row["blockers"]
        if blockers:
            self.summary.setText(
                f"{person.display_name} is not ready: " + " ".join(blockers)
            )
            self.release.setEnabled(False)
        else:
            self.summary.setText(
                f"{person.display_name} · {person.unit.label() if person.unit else 'No unit'} · "
                f"rifle {row['rifle'].firearm_id} · pistol {row['pistol'].firearm_id}"
            )
            self.release.setEnabled(True)
        self.ack_name.setText(person.full_name)
        for kind in ("rifle", "pistol"):
            weapon = row.get(kind)
            if weapon is None:
                continue
            row_index = self.firearm_table.rowCount()
            self.firearm_table.insertRow(row_index)
            self.firearm_table.setItem(row_index, 0, _readonly(kind.title()))
            self.firearm_table.setItem(row_index, 1, _readonly(weapon.firearm_id))
            butt = QLineEdit(weapon.butt_number)
            butt.setPlaceholderText("Verify butt number")
            self.firearm_table.setCellWidget(row_index, 2, butt)
            self.firearm_table.setItem(row_index, 3, _readonly(weapon.serial_number))
            passed = QCheckBox()
            passed.setChecked(True)
            self.firearm_table.setCellWidget(row_index, 4, passed)
            remarks = QLineEdit()
            remarks.setPlaceholderText("Optional inspection remark")
            self.firearm_table.setCellWidget(row_index, 5, remarks)
            self.firearm_widgets.append(
                {
                    "kind": kind,
                    "weapon": weapon,
                    "butt": butt,
                    "passed": passed,
                    "remarks": remarks,
                }
            )
        self.firearm_table.resizeRowsToContents()

    def _clear_firearms(self):
        self.firearm_table.setRowCount(0)
        self.firearm_widgets = []

    def _load_ammunition(self):
        self.ammo_table.setRowCount(0)
        self.ammo_widgets = []
        with db.session_scope() as session:
            lots = services.ammunition_rows(session, serviceability="serviceable")
        for lot in lots:
            if lot.stock_balance <= 0:
                continue
            row_index = self.ammo_table.rowCount()
            self.ammo_table.insertRow(row_index)
            self.ammo_table.setItem(row_index, 0, _check_item(False))
            self.ammo_table.setItem(
                row_index, 1, _readonly(f"{lot.nomenclature} ({lot.ammunition_id})")
            )
            self.ammo_table.setItem(
                row_index, 2, _readonly(f"{lot.caliber} / lot {lot.lot_number}")
            )
            self.ammo_table.setItem(
                row_index, 3, _readonly(f"{lot.stock_balance} {lot.unit_of_measure}")
            )
            quantity = QSpinBox()
            quantity.setRange(0, lot.stock_balance)
            quantity.setValue(0)
            self.ammo_table.setCellWidget(row_index, 4, quantity)
            self.ammo_widgets.append(
                {
                    "check": self.ammo_table.item(row_index, 0),
                    "quantity": quantity,
                    "lot": lot,
                }
            )
        self.ammo_table.resizeRowsToContents()

    def _save(self):
        if self.mode.currentData() == "mission" and self.mission.currentData() is None:
            QMessageBox.warning(self, "Issue", "Choose an approved Mission Order.")
            return
        person_id = self.person.currentData()
        row = self.people.get(person_id)
        if row is None:
            QMessageBox.warning(self, "Issue", "Choose the receiving person.")
            return
        if row["blockers"]:
            QMessageBox.warning(
                self,
                "Issue blocked",
                "This person is not ready for issue:\n\n" + "\n".join(row["blockers"]),
            )
            return
        if self.mode.currentData() == "regular" and not self.purpose.text().strip():
            QMessageBox.warning(
                self, "Issue", "Give the purpose or reason for a regular issuance."
            )
            return
        if not self.identity.isChecked():
            QMessageBox.warning(self, "Issue", "Verify the person's identity first.")
            return
        if not self.acknowledged.isChecked():
            QMessageBox.warning(self, "Issue", "The receiving person must acknowledge receipt.")
            return
        if not self.ack_name.text().strip():
            QMessageBox.warning(self, "Issue", "Enter the acknowledgement name.")
            return
        firearm_lines = []
        for widget in self.firearm_widgets:
            if not widget["passed"].isChecked():
                QMessageBox.warning(
                    self,
                    "Inspection",
                    f"{widget['weapon'].firearm_id} did not pass the physical inspection.",
                )
                return
            firearm_lines.append(
                {
                    "firearm_id": widget["weapon"].firearm_id,
                    "butt_number": widget["butt"].text(),
                    "inspection_status": "serviceable",
                    "inspection_remarks": widget["remarks"].text(),
                }
            )
        ammunition_lines = []
        for widget in self.ammo_widgets:
            if widget["check"].checkState() != Qt.Checked:
                continue
            quantity = widget["quantity"].value()
            if quantity <= 0:
                QMessageBox.warning(
                    self,
                    "Ammunition",
                    f"Enter the quantity for {widget['lot'].nomenclature} or uncheck it.",
                )
                return
            ammunition_lines.append(
                {"ammunition_id": widget["lot"].id, "quantity": quantity}
            )
        try:
            with db.session_scope() as session:
                actor = session.get(LocalUser, self.actor_id)
                transaction = services.issue_firearms(
                    session,
                    actor=actor,
                    personnel_id=person_id,
                    mission_id=self.mission.currentData()
                    if self.mode.currentData() == "mission"
                    else None,
                    purpose=self.purpose.text(),
                    firearm_lines=firearm_lines,
                    ammunition_lines=ammunition_lines,
                    identity_verified=self.identity.isChecked(),
                    acknowledged=self.acknowledged.isChecked(),
                    acknowledgement_name=self.ack_name.text(),
                    inspection_remarks=self.inspection_remarks.toPlainText(),
                    supporting_documents=self.documents.text(),
                )
                self.transaction_id = transaction.id
        except Exception as exc:
            QMessageBox.critical(self, "Issue failed", str(exc))
            return
        offer_receipt(self, self.transaction_id, generated_by=self.ack_name.text())
        self.accept()


class ReturnDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        actor: LocalUser,
        issuance_id: int | None = None,
    ):
        super().__init__(parent)
        self.actor_id = actor.id
        self.transaction_id: int | None = None
        self.current_issue: Transaction | None = None
        self.firearm_widgets: list[dict] = []
        self.ammo_widgets: list[dict] = []
        self.setWindowTitle("Deposit / return firearms and ammunition")
        self.setModal(True)
        self.resize(980, 820)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(10)
        root.addWidget(label("FIREARM AND AMMUNITION DEPOSIT / RETURN", "PageTitle"))
        root.addWidget(
            label(
                "Return all firearms from the original issue, verify identity and "
                "reconcile every ammunition line. Differences are recorded as discrepancies.",
                "PageSubtitle",
                wrap=True,
            )
        )
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)
        top = QFormLayout()
        self.issue = QComboBox()
        top.addRow("Original issuance *", self.issue)
        layout.addLayout(top)
        self.summary = label("", "Muted", wrap=True)
        layout.addWidget(self.summary)
        tabs = QTabWidget()
        firearm_page = QWidget()
        firearm_layout = QVBoxLayout(firearm_page)
        firearm_layout.setContentsMargins(8, 8, 8, 8)
        self.firearm_table = _table(
            ["Firearm ID", "Butt No.", "Type", "Serial No.", "Returned condition", "Remarks"]
        )
        self.firearm_table.setMinimumHeight(125)
        firearm_layout.addWidget(self.firearm_table)
        tabs.addTab(firearm_page, "1 · Firearms")
        ammo_page = QWidget()
        ammo_layout = QVBoxLayout(ammo_page)
        ammo_layout.setContentsMargins(8, 8, 8, 8)
        self.ammo_table = _table(
            ["Ammunition", "Lot", "Issued", "Returned", "Serviceability", "Discrepancy note"]
        )
        self.ammo_table.setMinimumHeight(160)
        ammo_layout.addWidget(self.ammo_table)
        ammo_layout.addWidget(
            label(
                "Returned quantity less than issued is recorded as a consumption / "
                "discrepancy in the return voucher.",
                "Muted",
                wrap=True,
            )
        )
        tabs.addTab(ammo_page, "2 · Ammunition reconciliation")
        accountability = QWidget()
        accountability_layout = QFormLayout(accountability)
        accountability_layout.setContentsMargins(10, 10, 10, 10)
        accountability_layout.setSpacing(9)
        self.identity = QCheckBox("I verified the returning person's identity")
        self.acknowledged = QCheckBox("The person acknowledged the deposit")
        self.ack_name = QLineEdit()
        self.ack_name.setPlaceholderText("Full name of the returning person")
        self.inspection = QPlainTextEdit()
        self.inspection.setFixedHeight(55)
        self.discrepancies = QPlainTextEdit()
        self.discrepancies.setFixedHeight(55)
        self.documents = QLineEdit()
        accountability_layout.addRow("", self.identity)
        accountability_layout.addRow("", self.acknowledged)
        accountability_layout.addRow("Acknowledged by *", self.ack_name)
        accountability_layout.addRow("Inspection remarks", self.inspection)
        accountability_layout.addRow("Additional discrepancies", self.discrepancies)
        accountability_layout.addRow("Supporting documents", self.documents)
        tabs.addTab(accountability, "3 · Accountability")
        layout.addWidget(tabs, 1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = button("CANCEL")
        cancel.clicked.connect(self.reject)
        self.deposit = button("DEPOSIT AND GENERATE RECEIPT", primary=True)
        self.deposit.clicked.connect(self._save)
        actions.addWidget(cancel)
        actions.addWidget(self.deposit)
        root.addLayout(actions)
        self.issue.currentIndexChanged.connect(self._load_issue)
        self._load_open_issues(issuance_id)

    def _load_open_issues(self, selected_id: int | None = None):
        self.issue.blockSignals(True)
        self.issue.clear()
        with db.session_scope() as session:
            rows = services.open_issues(session)
            for row in rows:
                self.issue.addItem(
                    f"{row.reference_number} · {row.personnel_name} · "
                    f"{row.mission_order_reference or row.purpose or 'Regular issue'}",
                    row.id,
                )
                if row.id == selected_id:
                    self.issue.setCurrentIndex(self.issue.count() - 1)
        self.issue.blockSignals(False)
        if self.issue.count() == 0:
            self.summary.setText("There are no open issuances to receive.")
            self.deposit.setEnabled(False)
            return
        self._load_issue()

    def _load_issue(self):
        issue_id = self.issue.currentData()
        self.firearm_table.setRowCount(0)
        self.ammo_table.setRowCount(0)
        self.firearm_widgets = []
        self.ammo_widgets = []
        self.current_issue = None
        if issue_id is None:
            return
        with db.session_scope() as session:
            issuance = services.transaction_by_id(session, issue_id)
            if issuance is None:
                return
            self.current_issue = issuance
            self.summary.setText(
                f"{issuance.personnel_name} · rank {issuance.rank or '—'} · "
                f"{issuance.unit_name or 'No unit'} · issued "
                f"{issuance.transaction_at:%Y-%m-%d %H:%M} · "
                f"{issuance.mission_order_reference or 'Standalone issue'} · "
                f"purpose: {issuance.purpose or '—'}"
            )
            self.ack_name.setText(issuance.personnel_name)
            for line in issuance.lines:
                if line.firearm_id:
                    row = self.firearm_table.rowCount()
                    self.firearm_table.insertRow(row)
                    self.firearm_table.setItem(row, 0, _readonly(line.firearm_id_snapshot))
                    self.firearm_table.setItem(row, 1, _readonly(line.butt_number_snapshot))
                    self.firearm_table.setItem(row, 2, _readonly(line.firearm_type.title()))
                    self.firearm_table.setItem(row, 3, _readonly(line.serial_number_snapshot))
                    condition = QComboBox()
                    for key, text in services.CONDITIONS.items():
                        condition.addItem(text, key)
                    self.firearm_table.setCellWidget(row, 4, condition)
                    remarks = QLineEdit()
                    self.firearm_table.setCellWidget(row, 5, remarks)
                    self.firearm_widgets.append(
                        {
                            "line": line,
                            "condition": condition,
                            "remarks": remarks,
                        }
                    )
                elif line.ammunition_id:
                    row = self.ammo_table.rowCount()
                    self.ammo_table.insertRow(row)
                    self.ammo_table.setItem(
                        row, 0, _readonly(f"{line.ammunition_name} ({line.ammunition_caliber})")
                    )
                    self.ammo_table.setItem(row, 1, _readonly(line.lot_number))
                    self.ammo_table.setItem(row, 2, _readonly(str(line.quantity)))
                    quantity = QSpinBox()
                    quantity.setRange(0, line.quantity)
                    quantity.setValue(line.quantity)
                    self.ammo_table.setCellWidget(row, 3, quantity)
                    serviceability = QComboBox()
                    for key, text in services.AMMO_CONDITIONS.items():
                        serviceability.addItem(text, key)
                    serviceability.setCurrentIndex(
                        max(0, serviceability.findData(line.condition_status))
                    )
                    self.ammo_table.setCellWidget(row, 4, serviceability)
                    note = QLineEdit()
                    note.setPlaceholderText("Explain missing / damaged rounds")
                    self.ammo_table.setCellWidget(row, 5, note)
                    self.ammo_widgets.append(
                        {
                            "line": line,
                            "quantity": quantity,
                            "serviceability": serviceability,
                            "note": note,
                        }
                    )
        self.firearm_table.resizeRowsToContents()
        self.ammo_table.resizeRowsToContents()

    def _save(self):
        if self.current_issue is None:
            QMessageBox.warning(self, "Return", "Choose the original issuance.")
            return
        if not self.identity.isChecked():
            QMessageBox.warning(self, "Return", "Verify the returning person's identity.")
            return
        if not self.acknowledged.isChecked():
            QMessageBox.warning(self, "Return", "The person must acknowledge the deposit.")
            return
        if not self.ack_name.text().strip():
            QMessageBox.warning(self, "Return", "Enter the acknowledgement name.")
            return
        firearm_lines = []
        for widget in self.firearm_widgets:
            line = widget["line"]
            firearm_lines.append(
                {
                    "firearm_id": line.firearm_id_snapshot,
                    "butt_number": line.butt_number_snapshot,
                    "condition_status": widget["condition"].currentData(),
                    "inspection_remarks": widget["remarks"].text(),
                }
            )
        ammunition_lines = []
        for widget in self.ammo_widgets:
            ammunition_lines.append(
                {
                    "line_id": widget["line"].id,
                    "quantity": widget["quantity"].value(),
                    "serviceability": widget["serviceability"].currentData(),
                    "discrepancy_note": widget["note"].text(),
                }
            )
        try:
            with db.session_scope() as session:
                actor = session.get(LocalUser, self.actor_id)
                transaction = services.return_firearms(
                    session,
                    actor=actor,
                    issuance_id=self.current_issue.id,
                    firearm_lines=firearm_lines,
                    ammunition_lines=ammunition_lines,
                    identity_verified=self.identity.isChecked(),
                    acknowledged=self.acknowledged.isChecked(),
                    acknowledgement_name=self.ack_name.text(),
                    inspection_remarks=self.inspection.toPlainText(),
                    discrepancy_notes=self.discrepancies.toPlainText(),
                    supporting_documents=self.documents.text(),
                )
                self.transaction_id = transaction.id
        except Exception as exc:
            QMessageBox.critical(self, "Return failed", str(exc))
            return
        offer_receipt(self, self.transaction_id, generated_by=self.ack_name.text())
        self.accept()


class TransactionDetailDialog(QDialog):
    def __init__(self, parent=None, *, transaction_id: int):
        super().__init__(parent)
        self.transaction_id = transaction_id
        self.setWindowTitle("Transaction detail")
        self.setModal(True)
        self.resize(960, 700)
        root = QVBoxLayout(self)
        root.setContentsMargins(22, 18, 22, 18)
        root.setSpacing(10)
        with db.session_scope() as session:
            transaction = services.transaction_by_id(session, transaction_id)
            if transaction is None:
                root.addWidget(label("The transaction no longer exists.", "PageTitle"))
                close = button("CLOSE", primary=True)
                close.clicked.connect(self.accept)
                root.addWidget(close)
                return
            title = label(
                f"{transaction.transaction_type.title()} · {transaction.reference_number}",
                "PageTitle",
            )
            subtitle = label(
                f"{transaction.status.title()} · {transaction.transaction_at:%Y-%m-%d %H:%M} · "
                f"{transaction.personnel_name} · {transaction.unit_name}",
                "PageSubtitle",
                wrap=True,
            )
            root.addWidget(title)
            root.addWidget(subtitle)
            info = QFormLayout()
            info.setSpacing(6)
            rows = [
                ("Mission Order", transaction.mission_order_reference or "Standalone / none"),
                ("Purpose / reason", transaction.purpose or "—"),
                ("Releasing armorer", transaction.releasing_armorer),
                ("Receiving person", transaction.receiving_personnel),
                ("Acknowledged by", transaction.acknowledgement_name),
                ("Supporting documents", transaction.supporting_documents),
                ("Discrepancies", transaction.discrepancy_notes),
                ("TOWEMS reference", transaction.remote_reference or "Not yet synchronized"),
            ]
            for key, value in rows:
                info.addRow(key, label(str(value or "—"), wrap=True))
            root.addLayout(info)
            tabs = QTabWidget()
            firearms = QTableWidget(0, 5)
            firearms.setHorizontalHeaderLabels(
                ["Firearm ID", "Butt No.", "Type", "Serial No.", "Condition"]
            )
            for line in transaction.lines:
                if not line.firearm_id:
                    continue
                row = firearms.rowCount()
                firearms.insertRow(row)
                for column, value in enumerate(
                    [
                        line.firearm_id_snapshot,
                        line.butt_number_snapshot,
                        line.firearm_type.title(),
                        line.serial_number_snapshot,
                        line.condition_status.title(),
                    ]
                ):
                    firearms.setItem(row, column, QTableWidgetItem(str(value or "")))
            firearms.verticalHeader().setVisible(False)
            firearms.horizontalHeader().setStretchLastSection(True)
            tabs.addTab(firearms, "Firearms")
            ammo = QTableWidget(0, 6)
            ammo.setHorizontalHeaderLabels(
                ["Nomenclature", "Caliber", "Lot", "Quantity", "UoM", "Condition"]
            )
            for line in transaction.lines:
                if not line.ammunition_id:
                    continue
                row = ammo.rowCount()
                ammo.insertRow(row)
                for column, value in enumerate(
                    [
                        line.ammunition_name,
                        line.ammunition_caliber,
                        line.lot_number,
                        line.quantity,
                        line.unit_of_measure,
                        line.condition_status.title(),
                    ]
                ):
                    ammo.setItem(row, column, QTableWidgetItem(str(value or "")))
            ammo.verticalHeader().setVisible(False)
            ammo.horizontalHeader().setStretchLastSection(True)
            tabs.addTab(ammo, "Ammunition")
            root.addWidget(tabs, 1)
        actions = QHBoxLayout()
        receipt = button("OPEN / PRINT RECEIPT")
        receipt.clicked.connect(self._receipt)
        close = button("CLOSE", primary=True)
        close.clicked.connect(self.accept)
        actions.addWidget(receipt)
        actions.addStretch(1)
        actions.addWidget(close)
        root.addLayout(actions)

    def _receipt(self):
        offer_receipt(self, self.transaction_id)
