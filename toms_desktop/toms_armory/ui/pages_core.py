"""Dashboard, registers, personnel and designation screens."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import db, services
from ..models import Ammunition, Assignment, Firearm, LocalUser, Personnel
from ..security import has_permission
from . import theme
from .forms import (
    AdjustmentDialog,
    AmmunitionDialog,
    AssignmentDialog,
    FirearmDialog,
    InspectionDialog,
    PersonnelDialog,
)
from .transactions import TransactionDetailDialog
from .widgets import (
    DataTable,
    DebouncedSearch,
    PageHeader,
    Panel,
    StatCard,
    badge,
    button,
    confirm,
    error,
    label,
)


class BasePage(QWidget):
    changed = Signal()
    navigate = Signal(str)

    def __init__(self, actor: LocalUser, parent=None):
        super().__init__(parent)
        self.actor = actor

    def refresh(self) -> None:
        pass

    def on_show(self) -> None:
        self.refresh()

    def permission(self, name: str) -> bool:
        return has_permission(self.actor.role, name)


class DashboardPage(BasePage):
    def __init__(self, actor: LocalUser, parent=None):
        super().__init__(actor, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(14)
        header = PageHeader(
            "Armory dashboard",
            "Local custody, accountability and synchronization at a glance.",
        )
        self.sync_badge = badge("LOCAL LEDGER", "ok")
        header.add_action(self.sync_badge)
        layout.addWidget(header)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        definitions = [
            ("firearms", "Firearms on register", "firearms"),
            ("stored", "Stored in armory", "firearms"),
            ("issued", "Currently issued", "transactions"),
            ("open", "Open issuances", "return"),
            ("ammo", "Ammunition rounds", "ammunition"),
            ("due", "Inspection due", "firearms"),
            ("unserviceable", "Unserviceable", "firearms"),
            ("personnel", "Personnel records", "personnel"),
            ("pending", "Pending sync", "sync"),
            ("failed", "Failed sync", "sync"),
        ]
        self.cards: dict[str, StatCard] = {}
        for index, (key, title, destination) in enumerate(definitions):
            card = StatCard(title)
            card.clicked.connect(lambda target=destination: self.navigate.emit(target))
            self.cards[key] = card
            grid.addWidget(card, index // 5, index % 5)
        layout.addLayout(grid)
        lower = QHBoxLayout()
        lower.setSpacing(12)
        recent_panel = Panel("Recent transactions")
        self.recent = DataTable(
            ["Date", "Reference", "Type", "Personnel", "Status", "Firearms / ammunition"]
        )
        self.recent.setMinimumHeight(250)
        self.recent.doubleClicked.connect(self._open_transaction)
        recent_panel.add(self.recent)
        lower.addWidget(recent_panel, 2)
        side = Panel("Accountability actions")
        q1 = button("ISSUE FIREARMS")
        q1.clicked.connect(lambda: self.navigate.emit("issue"))
        q2 = button("RECEIVE RETURN")
        q2.clicked.connect(lambda: self.navigate.emit("return"))
        q3 = button("SYNC NOW", primary=True)
        q3.clicked.connect(lambda: self.navigate.emit("sync"))
        side.add(q1)
        side.add(q2)
        side.add(q3)
        side.body.addSpacing(10)
        self.open_label = label("", "Muted", wrap=True)
        self.pending_label = label("", "Muted", wrap=True)
        side.add(self.open_label)
        side.add(self.pending_label)
        side.body.addStretch(1)
        lower.addWidget(side, 1)
        layout.addLayout(lower, 1)

    def refresh(self):
        with db.session_scope() as session:
            data = services.dashboard(session)
            personnel_count = session.query(Personnel).filter(Personnel.is_active.is_(True)).count()
        self.cards["firearms"].set_value(str(data["total"]), "Registered rifles and pistols")
        self.cards["stored"].set_value(str(data["stored"]), "Physically in the armory")
        self.cards["issued"].set_value(str(data["issued"]), "Custody outside the armory")
        self.cards["open"].set_value(str(data["open_issues"]), "Awaiting deposit / return")
        self.cards["ammo"].set_value(
            f"{data['ammo_stock']:,}", f"{data['ammo_lots']} registered lots"
        )
        self.cards["due"].set_value(str(data["inspection_due"]), "Not inspected in 30 days")
        self.cards["unserviceable"].set_value(str(data["unserviceable"]), "Maintenance or worse")
        self.cards["personnel"].set_value(str(personnel_count), "Active local personnel")
        self.cards["pending"].set_value(str(data["pending_sync"]), "Queued for TOWEMS")
        self.cards["failed"].set_value(str(data["failed_sync"]), "Need review")
        rows = []
        ids = []
        for transaction in data["recent"]:
            items = len([line for line in transaction.lines if line.firearm_id])
            ammo = sum(line.quantity for line in transaction.lines if line.ammunition_id)
            rows.append(
                [
                    transaction.transaction_at.strftime("%m-%d %H:%M"),
                    transaction.reference_number,
                    transaction.transaction_type.title(),
                    transaction.personnel_name,
                    transaction.status.title(),
                    f"{items} firearm(s)" + (f" · {ammo} rounds" if ammo else ""),
                ]
            )
            ids.append(transaction.id)
        self.recent.set_data(rows, ids=ids, status_column=4)
        self.open_label.setText(
            f"{data['open_issues']} open issuance(s) require a deposit or return."
        )
        self.pending_label.setText(
            f"{data['pending_sync']} pending and {data['failed_sync']} failed upload(s) "
            "are waiting in the synchronization queue."
        )
        self.sync_badge.setText(
            "QUEUE CLEAR" if not data["pending_sync"] and not data["failed_sync"] else "SYNC PENDING"
        )
        self.sync_badge.setObjectName(
            "BadgeOk" if not data["pending_sync"] and not data["failed_sync"] else "BadgeWarn"
        )
        self.sync_badge.style().unpolish(self.sync_badge)
        self.sync_badge.style().polish(self.sync_badge)

    def _open_transaction(self):
        transaction_id = self.recent.selected_id()
        if transaction_id:
            TransactionDetailDialog(self, transaction_id=transaction_id).exec()


class FirearmsPage(BasePage):
    def __init__(self, actor: LocalUser, parent=None):
        super().__init__(actor, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        header = PageHeader(
            "Firearm inventory",
            "PAF- identifiers, butt numbers, serials and live custody state.",
        )
        add = button("REGISTER FIREARM", primary=True)
        add.clicked.connect(self._add)
        edit = button("EDIT")
        edit.clicked.connect(self._edit)
        inspect = button("RECORD INSPECTION")
        inspect.clicked.connect(self._inspect)
        refresh = button("REFRESH")
        refresh.clicked.connect(self.refresh)
        for widget in (add, edit, inspect, refresh):
            header.add_action(widget)
            if not self.permission("manage_inventory") and widget is not refresh:
                widget.setEnabled(False)
        layout.addWidget(header)
        filters = QHBoxLayout()
        self.search = DebouncedSearch("Search Firearm ID, butt, serial, model…")
        self.search.changed.connect(self.refresh)
        self.type = QComboBox()
        self.type.addItem("All firearm types", "")
        self.type.addItem("Rifles", "rifle")
        self.type.addItem("Pistols", "pistol")
        self.type.currentIndexChanged.connect(self.refresh)
        self.custody = QComboBox()
        self.custody.addItem("All custody states", "")
        self.custody.addItem("Stored", "stored")
        self.custody.addItem("Issued", "issued")
        self.custody.addItem("Retired", "retired")
        self.custody.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.type)
        filters.addWidget(self.custody)
        layout.addLayout(filters)
        self.table = DataTable(
            [
                "Firearm ID",
                "Butt No.",
                "Type",
                "Serial No.",
                "Manufacturer / model",
                "Caliber",
                "Condition",
                "Custody",
                "Designated person",
                "Unit",
                "Last inspected",
            ]
        )
        self.table.doubleClicked.connect(self._edit)
        layout.addWidget(self.table, 1)
        self.count = label("", "Muted")
        layout.addWidget(self.count)
        self.refresh()

    def refresh(self):
        with db.session_scope() as session:
            rows = services.firearm_rows(
                session,
                search=self.search.text(),
                firearm_type=self.type.currentData(),
                custody=self.custody.currentData(),
            )
            display = []
            ids = []
            for item in rows:
                display.append(
                    [
                        item.firearm_id,
                        item.butt_number,
                        item.firearm_type.title(),
                        item.serial_number,
                        f"{item.manufacturer} {item.model}".strip(),
                        item.caliber,
                        item.condition_status.title(),
                        item.custody_status.title(),
                        item.assigned_personnel.display_name if item.assigned_personnel else "",
                        item.assigned_unit.acronym if item.assigned_unit else "",
                        item.date_last_inspected.isoformat() if item.date_last_inspected else "—",
                    ]
                )
                ids.append(item.id)
        self.table.set_data(display, ids=ids, status_column=7)
        self.count.setText(f"{len(display)} firearm(s) in the current view.")

    def _selected(self):
        firearm_id = self.table.selected_id()
        if firearm_id is None:
            QMessageBox.information(self, "Firearms", "Select a firearm first.")
            return None
        return firearm_id

    def _add(self):
        if not self.permission("manage_inventory"):
            return
        dialog = FirearmDialog(self, actor=self.actor)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()
            self.changed.emit()

    def _edit(self):
        if not self.permission("manage_inventory"):
            return
        firearm_id = self._selected()
        if firearm_id is None:
            return
        with db.session_scope() as session:
            firearm = session.get(Firearm, firearm_id)
            remote = firearm.remote_id is not None
            custody = firearm.custody_status
        if remote:
            QMessageBox.information(
                self,
                "Synchronised firearm",
                "This firearm is linked to TOWEMS. Registry corrections must be made in "
                "the central web application. Local inspectors can still record condition.",
            )
            return
        if custody == "issued":
            QMessageBox.warning(self, "Firearms", "A firearm cannot be edited while issued.")
            return
        dialog = FirearmDialog(self, firearm=firearm, actor=self.actor)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()
            self.changed.emit()

    def _inspect(self):
        if not self.permission("manage_inventory"):
            return
        firearm_id = self._selected()
        if firearm_id is None:
            return
        with db.session_scope() as session:
            firearm = session.get(Firearm, firearm_id)
            if firearm.custody_status == "issued":
                QMessageBox.warning(
                    self,
                    "Inspection",
                    "An issued firearm is inspected as part of its return.",
                )
                return
            snapshot = firearm
        dialog = InspectionDialog(self, firearm=snapshot, actor=self.actor)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()
            self.changed.emit()


class AmmunitionPage(BasePage):
    def __init__(self, actor: LocalUser, parent=None):
        super().__init__(actor, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        header = PageHeader(
            "Ammunition inventory",
            "Lots, received and issued quantities, live balances and serviceability.",
        )
        add = button("REGISTER LOT", primary=True)
        add.clicked.connect(self._add)
        adjust = button("ADJUST STOCK")
        adjust.clicked.connect(self._adjust)
        refresh = button("REFRESH")
        refresh.clicked.connect(self.refresh)
        for widget in (add, adjust, refresh):
            header.add_action(widget)
            if widget is not refresh and not self.permission("manage_inventory"):
                widget.setEnabled(False)
        layout.addWidget(header)
        filters = QHBoxLayout()
        self.search = DebouncedSearch("Search ammunition ID, nomenclature, lot…")
        self.search.changed.connect(self.refresh)
        self.serviceability = QComboBox()
        self.serviceability.addItem("All serviceability states", "")
        for key, text in services.AMMO_CONDITIONS.items():
            self.serviceability.addItem(text, key)
        self.serviceability.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.serviceability)
        layout.addLayout(filters)
        self.table = DataTable(
            [
                "Ammunition ID",
                "Nomenclature",
                "Caliber",
                "Type",
                "Lot No.",
                "Received",
                "Issued",
                "Balance",
                "UoM",
                "Serviceability",
                "Owning unit",
                "Storage",
            ]
        )
        self.table.doubleClicked.connect(self._adjust)
        layout.addWidget(self.table, 1)
        self.count = label("", "Muted")
        layout.addWidget(self.count)
        self.refresh()

    def refresh(self):
        with db.session_scope() as session:
            rows = services.ammunition_rows(
                session,
                search=self.search.text(),
                serviceability=self.serviceability.currentData(),
            )
            display = []
            ids = []
            for item in rows:
                display.append(
                    [
                        item.ammunition_id,
                        item.nomenclature,
                        item.caliber,
                        item.ammunition_type,
                        item.lot_number,
                        item.quantity_received,
                        item.quantity_issued,
                        item.stock_balance,
                        item.unit_of_measure,
                        item.serviceability.title(),
                        item.owning_unit.acronym if item.owning_unit else "",
                        item.storage_location,
                    ]
                )
                ids.append(item.id)
        self.table.set_data(display, ids=ids, status_column=9)
        self.count.setText(f"{len(display)} ammunition lot(s).")

    def _selected(self):
        lot_id = self.table.selected_id()
        if lot_id is None:
            QMessageBox.information(self, "Ammunition", "Select an ammunition lot first.")
        return lot_id

    def _add(self):
        if not self.permission("manage_inventory"):
            return
        dialog = AmmunitionDialog(self, actor=self.actor)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()
            self.changed.emit()

    def _adjust(self):
        if not self.permission("manage_inventory"):
            return
        lot_id = self._selected()
        if lot_id is None:
            return
        with db.session_scope() as session:
            lot = session.get(Ammunition, lot_id)
        dialog = AdjustmentDialog(self, ammunition=lot, actor=self.actor)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()
            self.changed.emit()


class PersonnelPage(BasePage):
    def __init__(self, actor: LocalUser, parent=None):
        super().__init__(actor, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        header = PageHeader(
            "Personnel management",
            "TOWEMS people, armory authorisation and designated rifle / pistol.",
        )
        add = button("ADD PERSON", primary=True)
        add.clicked.connect(self._add)
        edit = button("EDIT")
        edit.clicked.connect(self._edit)
        designate = button("DESIGNATE FIREARMS")
        designate.clicked.connect(self._designate)
        history = button("HISTORY")
        history.clicked.connect(self._history)
        refresh = button("REFRESH")
        refresh.clicked.connect(self.refresh)
        for widget in (add, edit, designate, history, refresh):
            header.add_action(widget)
            if widget is not refresh and not self.permission("manage_personnel"):
                widget.setEnabled(False)
        history.setEnabled(True)
        layout.addWidget(header)
        self.search = DebouncedSearch("Search name, service number, username, rank…")
        self.search.changed.connect(self.refresh)
        layout.addWidget(self.search)
        self.table = DataTable(
            [
                "Personnel",
                "Service No.",
                "Username",
                "Unit",
                "Branch",
                "Contact",
                "Authorised",
                "Active",
                "Rifle",
                "Pistol",
            ]
        )
        self.table.doubleClicked.connect(self._edit)
        layout.addWidget(self.table, 1)
        self.count = label("", "Muted")
        layout.addWidget(self.count)
        self.refresh()

    def _designations(self, session):
        rows = session.query(Assignment).filter(Assignment.is_active.is_(True)).all()
        output: dict[int, dict[str, Assignment]] = {}
        for row in rows:
            output.setdefault(row.personnel_id, {})[row.assignment_type] = row
        return output

    def refresh(self):
        with db.session_scope() as session:
            people = services.personnel_rows(session, search=self.search.text())
            designations = self._designations(session)
            display = []
            ids = []
            for person in people:
                active = designations.get(person.id, {})
                rifle = active.get("rifle")
                pistol = active.get("pistol")
                display.append(
                    [
                        person.display_name,
                        person.serial_number,
                        person.username,
                        person.unit.acronym if person.unit else "",
                        person.branch_of_service,
                        person.contact_number,
                        "Yes" if person.is_authorized else "No",
                        "Active" if person.is_active else "Inactive",
                        f"{rifle.firearm.firearm_id} / {rifle.firearm.butt_number}" if rifle else "—",
                        f"{pistol.firearm.firearm_id} / {pistol.firearm.butt_number}" if pistol else "—",
                    ]
                )
                ids.append(person.id)
        self.table.set_data(display, ids=ids, status_column=7)
        self.count.setText(f"{len(display)} personnel record(s).")

    def _selected(self):
        person_id = self.table.selected_id()
        if person_id is None:
            QMessageBox.information(self, "Personnel", "Select a person first.")
        return person_id

    def _add(self):
        if not self.permission("manage_personnel"):
            return
        dialog = PersonnelDialog(self, actor=self.actor)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()
            self.changed.emit()

    def _edit(self):
        if not self.permission("manage_personnel"):
            return
        person_id = self._selected()
        if person_id is None:
            return
        with db.session_scope() as session:
            person = session.get(Personnel, person_id)
        dialog = PersonnelDialog(self, person=person, actor=self.actor)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()
            self.changed.emit()

    def _designate(self):
        if not self.permission("assign"):
            return
        person_id = self._selected()
        if person_id is None:
            return
        dialog = AssignmentDialog(self, actor=self.actor, personnel_id=person_id)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()
            self.changed.emit()

    def _history(self):
        person_id = self._selected()
        if person_id is None:
            return
        AssignmentHistoryDialog(self, person_id=person_id).exec()


class AssignmentHistoryDialog(QDialog):
    def __init__(self, parent=None, *, person_id: int):
        super().__init__(parent)
        self.setWindowTitle("Assignment history")
        self.resize(920, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        with db.session_scope() as session:
            person = session.get(Personnel, person_id)
            rows = services.assignment_history(session, person_id)
            title = person.display_name if person else "Personnel"
            display = []
            for item in rows:
                display.append(
                    [
                        item.assigned_at.strftime("%Y-%m-%d %H:%M"),
                        item.assignment_type.title(),
                        item.firearm.firearm_id,
                        item.firearm.butt_number,
                        item.firearm.firearm_type.title(),
                        item.firearm.serial_number,
                        "Active" if item.is_active else "Closed",
                        item.assigned_by,
                        item.released_at.strftime("%Y-%m-%d %H:%M") if item.released_at else "",
                        item.notes,
                    ]
                )
        layout.addWidget(label(f"Assignment history · {title}", "PageTitle"))
        table = DataTable(
            [
                "Assigned",
                "Type",
                "Firearm ID",
                "Butt No.",
                "Firearm type",
                "Serial No.",
                "State",
                "Assigned by",
                "Released",
                "Notes",
            ]
        )
        table.set_data(display, status_column=6)
        layout.addWidget(table, 1)
        close = button("CLOSE", primary=True)
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)


class AssignmentsPage(BasePage):
    def __init__(self, actor: LocalUser, parent=None):
        super().__init__(actor, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(12)
        header = PageHeader(
            "Personnel firearm assignment",
            "One designated rifle and one designated pistol per authorised person.",
        )
        new = button("NEW DESIGNATION", primary=True)
        new.clicked.connect(self._new)
        release = button("RELEASE SELECTED", danger=True)
        release.clicked.connect(self._release)
        refresh = button("REFRESH")
        refresh.clicked.connect(self.refresh)
        for widget in (new, release, refresh):
            header.add_action(widget)
            if widget is not refresh and not self.permission("assign"):
                widget.setEnabled(False)
        layout.addWidget(header)
        filters = QHBoxLayout()
        self.search = DebouncedSearch("Search person, service number, firearm…")
        self.search.changed.connect(self.refresh)
        self.state = QComboBox()
        self.state.addItem("Active designations", "active")
        self.state.addItem("All history", "all")
        self.state.addItem("Closed only", "closed")
        self.state.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.search, 1)
        filters.addWidget(self.state)
        layout.addLayout(filters)
        self.table = DataTable(
            [
                "Assigned",
                "Personnel",
                "Service No.",
                "Type",
                "Firearm ID",
                "Butt No.",
                "Firearm type",
                "Serial No.",
                "State",
                "Assigned by",
                "Released",
                "Notes",
            ]
        )
        self.table.doubleClicked.connect(self._new)
        layout.addWidget(self.table, 1)
        self.count = label("", "Muted")
        layout.addWidget(self.count)
        self.refresh()

    def refresh(self):
        with db.session_scope() as session:
            query = services.assignment_history(session)
            mode = self.state.currentData()
            needle = self.search.text().lower()
            rows = []
            for item in query:
                if mode == "active" and not item.is_active:
                    continue
                if mode == "closed" and item.is_active:
                    continue
                haystack = " ".join(
                    [
                        item.personnel.display_name,
                        item.personnel.serial_number,
                        item.firearm.firearm_id,
                        item.firearm.butt_number,
                        item.firearm.serial_number,
                    ]
                ).lower()
                if needle and needle not in haystack:
                    continue
                rows.append(item)
        display = []
        ids = []
        for item in rows:
            display.append(
                [
                    item.assigned_at.strftime("%Y-%m-%d"),
                    item.personnel.display_name,
                    item.personnel.serial_number,
                    item.assignment_type.title(),
                    item.firearm.firearm_id,
                    item.firearm.butt_number,
                    item.firearm.firearm_type.title(),
                    item.firearm.serial_number,
                    "Active" if item.is_active else "Closed",
                    item.assigned_by,
                    item.released_at.strftime("%Y-%m-%d") if item.released_at else "",
                    item.notes,
                ]
            )
            ids.append(item.id)
        self.table.set_data(display, ids=ids, status_column=8)
        self.count.setText(f"{len(display)} assignment row(s).")

    def _new(self):
        if not self.permission("assign"):
            return
        dialog = AssignmentDialog(self, actor=self.actor)
        if dialog.exec() == QDialog.Accepted:
            self.refresh()
            self.changed.emit()

    def _release(self):
        if not self.permission("assign"):
            return
        assignment_id = self.table.selected_id()
        if assignment_id is None:
            QMessageBox.information(self, "Assignments", "Select an active designation.")
            return
        with db.session_scope() as session:
            assignment = session.get(Assignment, assignment_id)
            if assignment is None or not assignment.is_active:
                QMessageBox.warning(self, "Assignments", "That designation is already closed.")
                return
            person = assignment.personnel
            firearm = assignment.firearm
        if not confirm(
            self,
            "Release designation",
            f"Release {firearm.firearm_id} from {person.display_name}?\n\n"
            "The person will no longer hold a complete rifle and pistol designation.",
        ):
            return
        try:
            with db.session_scope() as session:
                actor = session.get(LocalUser, self.actor.id)
                services.release_assignment(
                    session,
                    actor=actor,
                    assignment_id=assignment_id,
                    reason="Designation released by armorer",
                )
        except Exception as exc:
            error(self, "Release failed", str(exc))
            return
        self.refresh()
        self.changed.emit()
