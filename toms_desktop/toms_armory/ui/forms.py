"""Modal forms for registers, designations and local users."""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import db, services
from ..models import Ammunition, Firearm, LocalUser, Personnel
from ..security import ROLE_LABELS
from .widgets import button, label


def _date_edit(value: date | None = None) -> QDateEdit:
    widget = QDateEdit()
    widget.setCalendarPopup(True)
    widget.setDisplayFormat("yyyy-MM-dd")
    widget.setDate(
        QDate(value.year, value.month, value.day) if value else QDate.currentDate()
    )
    return widget


def _unit_combo(units, selected_id: int | None = None) -> QComboBox:
    combo = QComboBox()
    combo.addItem("Select armory unit…", None)
    for unit in units:
        combo.addItem(unit.label(), unit.id)
        if unit.id == selected_id:
            combo.setCurrentIndex(combo.count() - 1)
    return combo


def _person_combo(people, selected_id: int | None = None, *, allow_none: bool = False) -> QComboBox:
    combo = QComboBox()
    if allow_none:
        combo.addItem("No custodian", None)
    for person in people:
        combo.addItem(services.personnel_label(person), person.id)
        if person.id == selected_id:
            combo.setCurrentIndex(combo.count() - 1)
    return combo


def _shell(dialog: QDialog, title: str, subtitle: str) -> QVBoxLayout:
    root = QVBoxLayout(dialog)
    root.setContentsMargins(24, 22, 24, 20)
    root.setSpacing(12)
    head = label(title, "PageTitle")
    sub = label(subtitle, "PageSubtitle", wrap=True)
    root.addWidget(head)
    root.addWidget(sub)
    return root


class _FormDialog(QDialog):
    def __init__(self, parent=None, *, title: str, subtitle: str, width: int = 560):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(width, 520)
        self.root = _shell(self, title, subtitle)
        self.form = QFormLayout()
        self.form.setSpacing(9)
        self.form.setLabelAlignment(Qt.AlignLeft)
        self.root.addLayout(self.form)
        self.footer = QHBoxLayout()
        self.footer.addStretch(1)
        self.root.addLayout(self.footer)
        self.cancel = button("CANCEL")
        self.cancel.clicked.connect(self.reject)
        self.save = button("SAVE", primary=True)
        self.save.clicked.connect(self._save)
        self.footer.addWidget(self.cancel)
        self.footer.addWidget(self.save)

    def _save(self):
        raise NotImplementedError

    def fail(self, message: str):
        QMessageBox.warning(self, "Check the form", message)


class FirearmDialog(_FormDialog):
    def __init__(
        self, parent=None, *, firearm: Firearm | None = None, actor: LocalUser
    ):
        editing = firearm is not None
        super().__init__(
            parent,
            title="Edit firearm" if editing else "Register firearm",
            subtitle=(
                "Registry corrections are limited while a firearm is synchronised. "
                "The Firearm ID is derived automatically from the serial number."
            ),
            width=600,
        )
        self.firearm_id = firearm.id if firearm else None
        self.actor_id = actor.id
        with db.session_scope() as session:
            units = services.units(session)
            if editing:
                weapon = session.get(Firearm, firearm.id)
                unit_id = weapon.assigned_unit_id
                values = {
                    "serial": weapon.serial_number,
                    "butt": weapon.butt_number,
                    "type": weapon.firearm_type,
                    "manufacturer": weapon.manufacturer,
                    "model": weapon.model,
                    "caliber": weapon.caliber,
                    "storage": weapon.storage_location,
                    "acquisition": weapon.acquisition_reference,
                    "remarks": weapon.remarks,
                    "date": weapon.date_acquired,
                }
            else:
                unit_id = units[0].id if units else None
                values = {
                    "serial": "",
                    "butt": "",
                    "type": "rifle",
                    "manufacturer": "",
                    "model": "",
                    "caliber": "",
                    "storage": "Armory",
                    "acquisition": "",
                    "remarks": "",
                    "date": date.today(),
                }
        self.serial = QLineEdit(values["serial"])
        self.serial.setPlaceholderText("Serial number; PAF- is added automatically")
        self.butt = QLineEdit(values["butt"])
        self.butt.setPlaceholderText("Unique within the firearm type")
        self.weapon_type = QComboBox()
        self.weapon_type.addItem("Rifle", "rifle")
        self.weapon_type.addItem("Pistol", "pistol")
        self.weapon_type.setCurrentIndex(0 if values["type"] == "rifle" else 1)
        self.manufacturer = QLineEdit(values["manufacturer"])
        self.model = QLineEdit(values["model"])
        self.caliber = QLineEdit(values["caliber"])
        self.unit = _unit_combo(units, unit_id)
        self.storage = QLineEdit(values["storage"])
        self.acquired = _date_edit(values["date"])
        self.acquisition = QLineEdit(values["acquisition"])
        self.remarks = QPlainTextEdit(values["remarks"])
        self.remarks.setFixedHeight(70)
        self.form.addRow("Serial number *", self.serial)
        self.form.addRow("Butt number *", self.butt)
        self.form.addRow("Firearm type *", self.weapon_type)
        self.form.addRow("Manufacturer", self.manufacturer)
        self.form.addRow("Model", self.model)
        self.form.addRow("Caliber", self.caliber)
        self.form.addRow("Armory unit *", self.unit)
        self.form.addRow("Storage location", self.storage)
        self.form.addRow("Date acquired", self.acquired)
        self.form.addRow("Acquisition reference", self.acquisition)
        self.form.addRow("Remarks", self.remarks)
        if editing:
            self.serial.setReadOnly(True)
            self.butt.setReadOnly(True)
            self.weapon_type.setEnabled(False)
            self.unit.setEnabled(False)
            self.acquired.setEnabled(False)
            self.acquisition.setReadOnly(True)

    def _save(self):
        try:
            with db.session_scope() as session:
                actor = session.get(LocalUser, self.actor_id)
                if self.firearm_id:
                    firearm = session.get(Firearm, self.firearm_id)
                    services.update_firearm_local(
                        session,
                        actor=actor,
                        firearm_id=self.firearm_id,
                        manufacturer=self.manufacturer.text(),
                        model=self.model.text(),
                        caliber=self.caliber.text(),
                        storage_location=self.storage.text(),
                        remarks=self.remarks.toPlainText(),
                    )
                    self.firearm_id = firearm.id
                else:
                    firearm = services.register_firearm(
                        session,
                        actor=actor,
                        serial_number=self.serial.text(),
                        butt_number=self.butt.text(),
                        firearm_type=self.weapon_type.currentData(),
                        manufacturer=self.manufacturer.text(),
                        model=self.model.text(),
                        caliber=self.caliber.text(),
                        unit_id=self.unit.currentData(),
                        storage_location=self.storage.text(),
                        date_acquired=self.acquired.date().toPython(),
                        acquisition_reference=self.acquisition.text(),
                        remarks=self.remarks.toPlainText(),
                    )
                    self.firearm_id = firearm.id
        except Exception as exc:
            self.fail(str(exc))
            return
        self.accept()


class InspectionDialog(_FormDialog):
    def __init__(self, parent=None, *, firearm: Firearm, actor: LocalUser):
        super().__init__(
            parent,
            title="Record firearm inspection",
            subtitle=(
                f"{firearm.firearm_id} · butt {firearm.butt_number} · "
                f"{firearm.firearm_type.title()} · serial {firearm.serial_number}"
            ),
            width=560,
        )
        self.actor_id = actor.id
        self.firearm_id = firearm.id
        self.condition = QComboBox()
        for key, text in services.CONDITIONS.items():
            self.condition.addItem(text, key)
        self.remarks = QPlainTextEdit()
        self.remarks.setFixedHeight(80)
        self.next_date = _date_edit(date.today() + timedelta(days=services.INSPECTION_DUE_DAYS))
        self.form.addRow("Result *", self.condition)
        self.form.addRow("Next inspection", self.next_date)
        self.form.addRow("Remarks", self.remarks)

    def _save(self):
        try:
            with db.session_scope() as session:
                actor = session.get(LocalUser, self.actor_id)
                services.inspect_firearm(
                    session,
                    actor=actor,
                    firearm_id=self.firearm_id,
                    condition_status=self.condition.currentData(),
                    remarks=self.remarks.toPlainText(),
                    next_inspection_date=self.next_date.date().toPython(),
                )
        except Exception as exc:
            self.fail(str(exc))
            return
        self.accept()


class AmmunitionDialog(_FormDialog):
    def __init__(self, parent=None, *, actor: LocalUser):
        super().__init__(
            parent,
            title="Register ammunition lot",
            subtitle=(
                "Register the lot once. Stock movements are recorded as adjustments "
                "or issue/return transactions, never by rewriting the received quantity."
            ),
            width=610,
        )
        self.actor_id = actor.id
        with db.session_scope() as session:
            units = services.units(session)
            people = services.personnel_rows(session, active_only=True)
            unit_id = units[0].id if units else None
        self.nomenclature = QLineEdit()
        self.nomenclature.setPlaceholderText("e.g. 5.56 mm ball ammunition")
        self.caliber = QLineEdit()
        self.ammo_type = QLineEdit()
        self.lot = QLineEdit()
        self.uom = QLineEdit("rounds")
        self.quantity = QSpinBox()
        self.quantity.setRange(0, 2_000_000_000)
        self.unit = _unit_combo(units, unit_id)
        self.serviceability = QComboBox()
        for key, text in services.AMMO_CONDITIONS.items():
            self.serviceability.addItem(text, key)
        self.storage = QLineEdit("Armory")
        self.received = _date_edit(date.today())
        self.expiry = _date_edit(date.today() + timedelta(days=365 * 5))
        self.custodian = _person_combo(people, None, allow_none=True)
        self.remarks = QPlainTextEdit()
        self.remarks.setFixedHeight(65)
        self.form.addRow("Nomenclature *", self.nomenclature)
        self.form.addRow("Caliber *", self.caliber)
        self.form.addRow("Ammunition type", self.ammo_type)
        self.form.addRow("Lot number *", self.lot)
        self.form.addRow("Unit of measure", self.uom)
        self.form.addRow("Quantity received *", self.quantity)
        self.form.addRow("Owning unit *", self.unit)
        self.form.addRow("Serviceability", self.serviceability)
        self.form.addRow("Storage location", self.storage)
        self.form.addRow("Date received", self.received)
        self.form.addRow("Expiry date", self.expiry)
        self.form.addRow("Responsible custodian", self.custodian)
        self.form.addRow("Remarks", self.remarks)
        self.lot_id = None

    def _save(self):
        try:
            with db.session_scope() as session:
                actor = session.get(LocalUser, self.actor_id)
                lot = services.register_ammunition(
                    session,
                    actor=actor,
                    nomenclature=self.nomenclature.text(),
                    caliber=self.caliber.text(),
                    ammunition_type=self.ammo_type.text(),
                    lot_number=self.lot.text(),
                    unit_of_measure=self.uom.text(),
                    quantity_received=self.quantity.value(),
                    unit_id=self.unit.currentData(),
                    serviceability=self.serviceability.currentData(),
                    storage_location=self.storage.text(),
                    date_received=self.received.date().toPython(),
                    expiration_date=self.expiry.date().toPython(),
                    custodian_id=self.custodian.currentData(),
                    remarks=self.remarks.toPlainText(),
                )
                self.lot_id = lot.id
        except Exception as exc:
            self.fail(str(exc))
            return
        self.accept()


class AdjustmentDialog(_FormDialog):
    def __init__(self, parent=None, *, ammunition: Ammunition, actor: LocalUser):
        super().__init__(
            parent,
            title="Adjust ammunition stock",
            subtitle=(
                f"{ammunition.ammunition_id} · {ammunition.nomenclature} · lot "
                f"{ammunition.lot_number} · current balance {ammunition.stock_balance} "
                f"{ammunition.unit_of_measure}"
            ),
            width=560,
        )
        self.actor_id = actor.id
        self.lot_id = ammunition.id
        self.balance = QSpinBox()
        self.balance.setRange(0, 2_000_000_000)
        self.balance.setValue(ammunition.stock_balance)
        self.reason = QPlainTextEdit()
        self.reason.setFixedHeight(90)
        self.reason.setPlaceholderText("Mandatory reason for the stock correction")
        self.documents = QLineEdit()
        self.form.addRow("New stock balance *", self.balance)
        self.form.addRow("Reason *", self.reason)
        self.form.addRow("Supporting documents", self.documents)

    def _save(self):
        try:
            with db.session_scope() as session:
                actor = session.get(LocalUser, self.actor_id)
                services.adjust_ammunition(
                    session,
                    actor=actor,
                    ammunition_id=self.lot_id,
                    new_balance=self.balance.value(),
                    reason=self.reason.toPlainText(),
                    supporting_documents=self.documents.text(),
                )
        except Exception as exc:
            self.fail(str(exc))
            return
        self.accept()


class PersonnelDialog(_FormDialog):
    def __init__(self, parent=None, *, person: Personnel | None = None, actor: LocalUser):
        editing = person is not None
        remote = bool(person.remote_id) if editing else False
        super().__init__(
            parent,
            title="Edit personnel record" if editing else "Add personnel record",
            subtitle=(
                "Downloaded TOWEMS record: identity, rank, unit and contact fields "
                "are maintained centrally and shown read-only. Armoury authorisation "
                "and active state can still be changed locally."
                if remote
                else (
                    "Personnel are normally downloaded from TOWEMS. Local records "
                    "remain local until the person exists centrally."
                )
            ),
            width=610,
        )
        self.actor_id = actor.id
        self.person_id = person.id if person else None
        with db.session_scope() as session:
            units = services.units(session)
            if editing:
                current = session.get(Personnel, person.id)
                values = {
                    "username": current.username,
                    "rank": current.rank,
                    "first": current.first_name,
                    "middle": current.middle_name,
                    "last": current.last_name,
                    "serial": current.serial_number,
                    "branch": current.branch_of_service,
                    "unit": current.unit_id,
                    "contact": current.contact_number,
                    "authorized": current.is_authorized,
                    "active": current.is_active,
                }
            else:
                values = {
                    "username": "",
                    "rank": "",
                    "first": "",
                    "middle": "",
                    "last": "",
                    "serial": "",
                    "branch": "",
                    "unit": units[0].id if units else None,
                    "contact": "",
                    "authorized": True,
                    "active": True,
                }
        self.username = QLineEdit(values["username"])
        self.rank = QLineEdit(values["rank"])
        self.first = QLineEdit(values["first"])
        self.middle = QLineEdit(values["middle"])
        self.last = QLineEdit(values["last"])
        self.serial = QLineEdit(values["serial"])
        self.branch = QLineEdit(values["branch"])
        self.unit = _unit_combo(units, values["unit"])
        self.contact = QLineEdit(values["contact"])
        self.authorized = QCheckBox("Authorised to receive firearms")
        self.authorized.setChecked(bool(values["authorized"]))
        self.active = QCheckBox("Active personnel record")
        self.active.setChecked(bool(values["active"]))
        self.form.addRow("Username *", self.username)
        self.form.addRow("Rank", self.rank)
        self.form.addRow("First name *", self.first)
        self.form.addRow("Middle name", self.middle)
        self.form.addRow("Last name *", self.last)
        self.form.addRow("Service number *", self.serial)
        self.form.addRow("Branch", self.branch)
        self.form.addRow("Unit", self.unit)
        self.form.addRow("Contact", self.contact)
        self.form.addRow("", self.authorized)
        self.form.addRow("", self.active)
        if editing:
            self.username.setReadOnly(True)
            self.serial.setReadOnly(True)
        if remote:
            for field in (
                self.rank,
                self.first,
                self.middle,
                self.last,
                self.branch,
                self.unit,
                self.contact,
            ):
                field.setEnabled(False)

    def _save(self):
        try:
            with db.session_scope() as session:
                actor = session.get(LocalUser, self.actor_id)
                if self.person_id:
                    services.update_personnel(
                        session,
                        actor=actor,
                        personnel_id=self.person_id,
                        rank=self.rank.text(),
                        first_name=self.first.text(),
                        middle_name=self.middle.text(),
                        last_name=self.last.text(),
                        branch=self.branch.text(),
                        unit_id=self.unit.currentData(),
                        contact=self.contact.text(),
                        is_authorized=self.authorized.isChecked(),
                        is_active=self.active.isChecked(),
                    )
                else:
                    person = services.create_personnel(
                        session,
                        actor=actor,
                        username=self.username.text(),
                        rank=self.rank.text(),
                        first_name=self.first.text(),
                        middle_name=self.middle.text(),
                        last_name=self.last.text(),
                        serial_number=self.serial.text(),
                        branch=self.branch.text(),
                        unit_id=self.unit.currentData(),
                        contact=self.contact.text(),
                        is_authorized=self.authorized.isChecked(),
                    )
                    self.person_id = person.id
        except Exception as exc:
            self.fail(str(exc))
            return
        self.accept()


class AssignmentDialog(_FormDialog):
    def __init__(self, parent=None, *, actor: LocalUser, personnel_id: int | None = None):
        super().__init__(
            parent,
            title="Designate firearms",
            subtitle=(
                "Every authorised person holds one designated rifle and one designated "
                "pistol. Replacing a designation closes the previous row in history."
            ),
            width=650,
        )
        self.actor_id = actor.id
        with db.session_scope() as session:
            people = services.personnel_rows(session, active_only=True)
            people = [person for person in people if person.is_authorized]
        self.person = _person_combo(people, personnel_id)
        self.person.currentIndexChanged.connect(self._load_designations)
        self.rifle = QComboBox()
        self.pistol = QComboBox()
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(75)
        self.summary = label("", "Muted", wrap=True)
        self.history = QLabel()
        self.history.setWordWrap(True)
        self.form.addRow("Personnel *", self.person)
        self.form.addRow("Designated rifle *", self.rifle)
        self.form.addRow("Designated pistol *", self.pistol)
        self.form.addRow("Notes", self.notes)
        self.form.addRow("", self.summary)
        self.form.addRow("History", self.history)
        self._load_designations()

    def _load_designations(self):
        person_id = self.person.currentData()
        self.rifle.clear()
        self.pistol.clear()
        self.history.clear()
        if not person_id:
            self.summary.setText("Choose an authorised person.")
            return
        with db.session_scope() as session:
            person = session.get(Personnel, person_id)
            current = {
                item.assignment_type: item.firearm
                for item in services.active_assignments(session, person_id)
            }
            weapons = services.firearm_rows(session, include_retired=False)
            for kind, combo in (("rifle", self.rifle), ("pistol", self.pistol)):
                combo.addItem("Select…", None)
                for weapon in weapons:
                    if weapon.firearm_type != kind:
                        continue
                    if weapon.custody_status == "issued":
                        continue
                    if weapon.condition_status != "serviceable":
                        continue
                    if weapon.assigned_unit_id != person.unit_id:
                        continue
                    if weapon.assigned_personnel_id not in (None, person.id):
                        continue
                    combo.addItem(
                        f"{weapon.firearm_id} · butt {weapon.butt_number} · "
                        f"serial {weapon.serial_number}",
                        weapon.id,
                    )
                    if current.get(kind) and current[kind].id == weapon.id:
                        combo.setCurrentIndex(combo.count() - 1)
            history_rows = services.assignment_history(session, person_id)[:6]
            if history_rows:
                text = "<br>".join(
                    f"{item.assigned_at:%Y-%m-%d} · {item.assignment_type.title()} · "
                    f"{item.firearm.firearm_id} · {'active' if item.is_active else 'closed'}"
                    for item in history_rows
                )
                self.history.setText(text)
            self.summary.setText(
                f"{person.display_name} · {person.unit.label() if person.unit else 'No unit'}"
            )

    def _save(self):
        if not self.person.currentData():
            self.fail("Choose the person.")
            return
        try:
            with db.session_scope() as session:
                actor = session.get(LocalUser, self.actor_id)
                services.assign_designated_firearms(
                    session,
                    actor=actor,
                    personnel_id=self.person.currentData(),
                    rifle_firearm_id=self.rifle.currentData(),
                    pistol_firearm_id=self.pistol.currentData(),
                    notes=self.notes.toPlainText(),
                )
        except Exception as exc:
            self.fail(str(exc))
            return
        self.accept()


class UserDialog(_FormDialog):
    def __init__(self, parent=None, *, actor: LocalUser, user: LocalUser | None = None):
        editing = user is not None
        super().__init__(
            parent,
            title="Edit local user" if editing else "Create local user",
            subtitle=(
                "Local accounts only. TOWEMS credentials are separate and are used "
                "solely for synchronization."
            ),
            width=560,
        )
        self.actor_id = actor.id
        self.user_id = user.id if user else None
        self.username = QLineEdit(user.username if user else "")
        self.display = QLineEdit(user.display_name if user else "")
        self.rank = QLineEdit(user.rank if user else "")
        self.role = QComboBox()
        for key, text in ROLE_LABELS.items():
            self.role.addItem(text, key)
            if user and user.role == key:
                self.role.setCurrentIndex(self.role.count() - 1)
        self.active = QCheckBox("Account is active")
        self.active.setChecked(user.is_active if user else True)
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.setPlaceholderText(
            "Leave blank to keep the current password" if editing else "At least 10 characters"
        )
        self.form.addRow("Username *", self.username)
        self.form.addRow("Display name *", self.display)
        self.form.addRow("Rank", self.rank)
        self.form.addRow("Role *", self.role)
        self.form.addRow("", self.active)
        self.form.addRow("Password" + (" (new)" if editing else " *"), self.password)
        if editing:
            self.username.setReadOnly(True)

    def _save(self):
        try:
            with db.session_scope() as session:
                actor = session.get(LocalUser, self.actor_id)
                if self.user_id:
                    services.update_user(
                        session,
                        actor=actor,
                        user_id=self.user_id,
                        display_name=self.display.text(),
                        role=self.role.currentData(),
                        rank=self.rank.text(),
                        is_active=self.active.isChecked(),
                        new_password=self.password.text(),
                    )
                else:
                    user = services.create_user(
                        session,
                        actor=actor,
                        username=self.username.text(),
                        display_name=self.display.text(),
                        password=self.password.text(),
                        role=self.role.currentData(),
                        rank=self.rank.text(),
                        is_active=self.active.isChecked(),
                    )
                    self.user_id = user.id
        except Exception as exc:
            self.fail(str(exc))
            return
        self.accept()


class ChangePasswordDialog(_FormDialog):
    def __init__(self, parent=None, *, user: LocalUser):
        super().__init__(
            parent,
            title="Change local password",
            subtitle="Use at least 10 characters with letters and numbers.",
            width=480,
        )
        self.user_id = user.id
        self.current = QLineEdit()
        self.current.setEchoMode(QLineEdit.Password)
        self.new = QLineEdit()
        self.new.setEchoMode(QLineEdit.Password)
        self.confirm = QLineEdit()
        self.confirm.setEchoMode(QLineEdit.Password)
        self.form.addRow("Current password", self.current)
        self.form.addRow("New password", self.new)
        self.form.addRow("Confirm new password", self.confirm)

    def _save(self):
        if self.new.text() != self.confirm.text():
            self.fail("The new password confirmation does not match.")
            return
        try:
            with db.session_scope() as session:
                user = session.get(LocalUser, self.user_id)
                services.change_password(
                    session,
                    user=user,
                    current_password=self.current.text(),
                    new_password=self.new.text(),
                )
        except Exception as exc:
            self.fail(str(exc))
            return
        self.accept()
