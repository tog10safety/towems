"""Synchronization engine for the offline armory.

The central TOWEMS server is authoritative for people, units, missions and
designation state.  The desktop is authoritative for the accountability events
it recorded while offline.  Uploads therefore run before the full reference
merge, so a local custody change is not overwritten by a stale snapshot.  Every
operation is idempotent through its UUID; queue rows and local transactions are
retained after a successful upload.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from . import db
from .api_client import (
    ApiError,
    AuthenticationError,
    NetworkError,
    TowemsClient,
    validate_base_url,
)
from .models import (
    Ammunition,
    Assignment,
    Firearm,
    Inspection,
    Mission,
    MissionPersonnel,
    Personnel,
    SyncHistory,
    SyncQueue,
    Transaction,
    TransactionLine,
    Unit,
    now,
)
from .services import active_assignments


@dataclass
class SyncResult:
    status: str
    message: str
    uploaded: int = 0
    downloaded: int = 0
    errors: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def ok(self) -> bool:
        return self.status == "success"


def _payload(row: SyncQueue) -> dict:
    try:
        data = json.loads(row.payload_json or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _as_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone().replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def _as_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _truthy(value: Any) -> bool:
    return bool(value) and str(value).lower() not in {"0", "false", "none"}


def _parse_names(full_name: str, last_name: str, rank: str) -> tuple[str, str, str]:
    """Split the central rank-prefixed display name into name fields."""
    text = " ".join(str(full_name or "").split())
    rank = " ".join(str(rank or "").split())
    if rank and text.lower().startswith(rank.lower() + " "):
        text = text[len(rank) + 1 :].strip()
    last = " ".join(str(last_name or "").split())
    if last and text.lower().endswith(" " + last.lower()):
        rest = text[: -(len(last) + 1)].strip()
    elif last and text.lower() == last.lower():
        rest = ""
    else:
        rest = text
    parts = rest.split()
    if not last and len(parts) >= 2:
        first, middle = parts[0], " ".join(parts[1:-1])
        last = parts[-1]
    else:
        first, middle = (parts[0] if parts else ""), " ".join(parts[1:])
    return first, middle, last


class SyncService:
    def __init__(self, client_factory: Callable[[str], TowemsClient] = TowemsClient):
        self.client_factory = client_factory

    # -- public entry points ------------------------------------------------

    def run(
        self,
        *,
        username: str = "",
        password: str = "",
        base_url: str = "",
        trigger: str = "manual",
    ) -> SyncResult:
        started = now()
        with db.session_scope() as session:
            effective_base = validate_base_url(
                base_url or db.get_setting(session, "api_base", "")
            )
            effective_user = username or db.get_setting(session, "sync_username", "")
            history = SyncHistory(started_at=started, status="running")
            session.add(history)
            db.audit(
                session,
                username=effective_user or "system",
                action="sync_started",
                entity_type="sync",
                entity_id="",
                reference=trigger,
                note=effective_base,
            )
            session.flush()
            result = SyncResult(
                status="failed", message="", started_at=started, finished_at=started
            )
            client = self.client_factory(effective_base)
            try:
                if password:
                    client.login(effective_user, password)
                    db.set_setting(session, "sync_username", client.username)
                elif not client.restore_session(effective_user):
                    raise AuthenticationError(
                        "No saved TOWEMS session. Enter the synchronization password once "
                        "to provision this computer."
                    )
                snapshot = client.snapshot()
                self._bootstrap_reference(session, snapshot)
                session.flush()

                uploaded, errors, warnings = self._upload_queue(session, client)
                result.uploaded = uploaded
                result.errors = errors
                result.warnings = warnings
                session.flush()

                final_snapshot = client.snapshot()
                result.downloaded = self._merge_snapshot(session, final_snapshot)
                result.status = "partial" if errors else "success"
                result.message = (
                    f"{uploaded} operation(s) uploaded; {result.downloaded} central "
                    f"record(s) merged."
                )
                if errors:
                    result.message += f" {len(errors)} operation(s) need attention."
                db.audit(
                    session,
                    username=client.username or effective_user or "system",
                    action="sync_completed",
                    entity_type="sync",
                    entity_id=history.id,
                    reference=trigger,
                    after={
                        "uploaded": uploaded,
                        "downloaded": result.downloaded,
                        "status": result.status,
                    },
                )
            except AuthenticationError as exc:
                result.status = "failed"
                result.message = str(exc)
                result.errors.append({"action": "authenticate", "error": str(exc)})
            except NetworkError as exc:
                result.status = "failed"
                result.message = str(exc)
                result.errors.append({"action": "network", "error": str(exc)})
            except ApiError as exc:
                result.status = "failed"
                result.message = str(exc)
                result.errors.append({"action": "sync", "error": str(exc)})
            except Exception as exc:  # A scheduler must never take down the UI.
                result.status = "failed"
                result.message = f"Synchronization failed: {exc}"
                result.errors.append({"action": "internal", "error": str(exc)})
            finally:
                result.finished_at = now()
                history.finished_at = result.finished_at
                history.status = result.status
                history.uploaded = result.uploaded
                history.downloaded = result.downloaded
                history.message = result.message[:4000]
                history.details = json.dumps(result.errors, default=str)[:20000]
            return result

    # -- bootstrap ----------------------------------------------------------

    def _bootstrap_reference(self, session: Session, snapshot: dict) -> None:
        """Map units and people before any local record is uploaded."""
        self._merge_units(session, snapshot.get("units") or [])
        self._merge_personnel(
            session, snapshot.get("personnel") or [], deactivate=False
        )
        session.flush()

    # -- queue upload -------------------------------------------------------

    def _upload_queue(
        self, session: Session, client: TowemsClient
    ) -> tuple[int, list[dict], list[str]]:
        rows = list(
            session.scalars(
                select(SyncQueue)
                .where(
                    SyncQueue.state == "pending",
                    or_(
                        SyncQueue.next_attempt_at.is_(None),
                        SyncQueue.next_attempt_at <= now(),
                    ),
                )
                .order_by(SyncQueue.id)
            )
        )
        uploaded = 0
        errors: list[dict] = []
        warnings: list[str] = []
        handlers = {
            "firearm.create": self._push_firearm,
            "ammunition.create": self._push_ammunition,
            "assignment.set": self._push_assignment_state,
            "transaction.issue": self._push_issue,
            "transaction.return": self._push_return,
            "inspection.create": self._push_inspection,
            "ammunition.adjust": self._push_adjustment,
        }
        for row in rows:
            handler = handlers.get(row.action)
            if handler is None:
                row.state = "failed"
                row.attempts += 1
                row.last_error = f"No uploader is defined for {row.action}."
                errors.append({"action": row.action, "error": row.last_error})
                continue
            try:
                handler(session, client, row)
            except AuthenticationError:
                row.attempts += 1
                row.last_error = "TOWEMS authentication expired."
                row.next_attempt_at = now() + timedelta(minutes=5)
                raise
            except NetworkError as exc:
                row.attempts += 1
                row.last_error = str(exc)
                row.next_attempt_at = now() + self._backoff(row.attempts)
                errors.append({"action": row.action, "error": str(exc)})
                raise
            except ApiError as exc:
                row.attempts += 1
                row.last_error = str(exc)
                if exc.status in {408, 425, 429} or (exc.status or 0) >= 500:
                    row.state = "pending"
                    row.next_attempt_at = now() + self._backoff(row.attempts)
                else:
                    row.state = "failed"
                    row.next_attempt_at = None
                errors.append({"action": row.action, "error": str(exc)})
                session.flush()
                continue
            except Exception as exc:
                row.attempts += 1
                row.last_error = str(exc)
                row.state = "failed"
                row.next_attempt_at = None
                errors.append({"action": row.action, "error": str(exc)})
                session.flush()
                continue
            row.state = "synced"
            row.attempts += 1
            row.last_error = ""
            row.next_attempt_at = None
            uploaded += 1
            session.flush()
        return uploaded, errors, warnings

    @staticmethod
    def _backoff(attempts: int) -> timedelta:
        minutes = min(120, max(2, 2 ** min(int(attempts), 6)))
        return timedelta(minutes=minutes)

    # -- individual uploaders ----------------------------------------------

    def _push_firearm(self, session: Session, client: TowemsClient, row: SyncQueue) -> None:
        firearm = session.get(Firearm, int(_payload(row).get("firearm_id") or 0))
        if firearm is None:
            raise ApiError("The local firearm no longer exists.")
        if firearm.remote_id:
            return
        unit = session.get(Unit, firearm.assigned_unit_id)
        if unit is None or unit.remote_id is None:
            raise ApiError(
                "The firearm's armory unit is not linked to TOWEMS. Synchronize the "
                "unit reference before registering firearms."
            )
        payload = {
            "serial_number": firearm.serial_number,
            "butt_number": firearm.butt_number,
            "firearm_type": firearm.firearm_type,
            "manufacturer": firearm.manufacturer,
            "model": firearm.model,
            "caliber": firearm.caliber,
            "date_acquired": firearm.date_acquired.isoformat()
            if firearm.date_acquired
            else None,
            "acquisition_reference": firearm.acquisition_reference,
            "condition_status": firearm.condition_status,
            "assigned_unit": unit.remote_id,
            "storage_location": firearm.storage_location,
            "remarks": firearm.remarks,
        }
        data = client.create_firearm(payload)
        if not isinstance(data, dict) or not data.get("id"):
            raise ApiError("TOWEMS did not return the registered firearm.")
        firearm.remote_id = int(data["id"])
        firearm.firearm_id = str(data.get("firearm_id") or firearm.firearm_id)
        firearm.synced_at = now()
        firearm.sync_state = "synced"
        session.flush()

    def _push_ammunition(
        self, session: Session, client: TowemsClient, row: SyncQueue
    ) -> None:
        lot = session.get(Ammunition, int(_payload(row).get("ammunition_id") or 0))
        if lot is None:
            raise ApiError("The local ammunition lot no longer exists.")
        if lot.remote_id:
            return
        unit = session.get(Unit, lot.owning_unit_id)
        if unit is None or unit.remote_id is None:
            raise ApiError(
                "The ammunition lot's owning unit is not linked to TOWEMS."
            )
        custodian = (
            session.get(Personnel, lot.responsible_custodian_id)
            if lot.responsible_custodian_id
            else None
        )
        payload = {
            "nomenclature": lot.nomenclature,
            "caliber": lot.caliber,
            "ammunition_type": lot.ammunition_type,
            "lot_number": lot.lot_number,
            "unit_of_measure": lot.unit_of_measure,
            "quantity_received": lot.quantity_received,
            "serviceability": lot.serviceability,
            "storage_location": lot.storage_location,
            "date_received": lot.date_received.isoformat() if lot.date_received else None,
            "expiration_date": lot.expiration_date.isoformat()
            if lot.expiration_date
            else None,
            "responsible_custodian": custodian.remote_id
            if custodian and custodian.remote_id
            else None,
            "owning_unit": unit.remote_id,
            "remarks": lot.remarks,
        }
        data = client.create_ammunition(payload)
        if not isinstance(data, dict) or not data.get("id"):
            raise ApiError("TOWEMS did not return the registered ammunition lot.")
        lot.remote_id = int(data["id"])
        lot.ammunition_id = str(data.get("ammunition_id") or lot.ammunition_id)
        session.flush()

    def _push_assignment_state(
        self, session: Session, client: TowemsClient, row: SyncQueue
    ) -> None:
        payload = _payload(row)
        person = session.get(Personnel, int(payload.get("personnel_id") or 0))
        if person is None:
            raise ApiError("The local personnel record no longer exists.")
        if person.remote_id is None:
            raise ApiError(
                f"{person.full_name} is a local-only record and is not linked to TOWEMS. "
                "Create the person in TOWEMS, then synchronize before uploading designations."
            )
        desired = {
            item.assignment_type: item.firearm
            for item in active_assignments(session, person.id)
        }
        for kind, weapon in desired.items():
            if weapon.remote_id is None:
                raise ApiError(
                    f"{weapon.firearm_id} is not linked to TOWEMS yet; its registration "
                    "must upload before the designation."
                )
        central = client.assignments(person.remote_id, active="1")
        central_by_type = {
            item.get("assignment_type"): item
            for item in central
            if item.get("assignment_type") and item.get("is_active")
        }
        notes = str(payload.get("notes") or "").strip()
        assigned_rows: list[dict] = []
        if desired:
            response = client.assign(
                personnel=person.remote_id,
                client_uuid=row.uuid,
                rifle_firearm_id=desired["rifle"].firearm_id if "rifle" in desired else "",
                pistol_firearm_id=desired["pistol"].firearm_id if "pistol" in desired else "",
                notes=notes,
            )
            assigned_rows = response
        for kind, current in list(central_by_type.items()):
            if kind in desired:
                continue
            if current.get("id"):
                client.release_assignment(
                    int(current["id"]),
                    reason=notes or f"Designation removed on offline armory ({kind})",
                )
        for item in assigned_rows:
            if item.get("assignment_type") and item.get("is_active"):
                central_by_type[item["assignment_type"]] = item
        for kind, weapon in desired.items():
            remote = central_by_type.get(kind)
            if remote is None:
                raise ApiError(
                    f"TOWEMS did not confirm the {kind} designation for {person.full_name}."
                )
            local = next(
                (
                    candidate
                    for candidate in active_assignments(session, person.id)
                    if candidate.assignment_type == kind
                ),
                None,
            )
            if local is not None:
                local.remote_id = int(remote["id"]) if remote.get("id") else None
                local.sync_state = "synced"
                local.synced_at = now()
        for kind, current in central_by_type.items():
            if kind in desired:
                continue
            for local_assignment in active_assignments(session, person.id):
                if (
                    local_assignment.assignment_type == kind
                    and local_assignment.remote_id is None
                ):
                    local_assignment.remote_id = (
                        int(current["id"]) if current.get("id") else None
                    )
        for candidate in session.scalars(
            select(Assignment).where(
                Assignment.personnel_id == person.id,
                Assignment.is_active.is_(False),
                Assignment.remote_id.is_(None),
            )
        ):
            matching = next(
                (
                    item
                    for item in central
                    if not item.get("is_active")
                    and item.get("assignment_type") == candidate.assignment_type
                    and (item.get("firearm") or {}).get("firearm_id")
                    == candidate.firearm.firearm_id
                ),
                None,
            )
            if matching and matching.get("id"):
                candidate.remote_id = int(matching["id"])
                candidate.sync_state = "synced"
                candidate.synced_at = now()
        session.flush()

    def _push_issue(self, session: Session, client: TowemsClient, row: SyncQueue) -> None:
        transaction = session.get(
            Transaction, int(_payload(row).get("transaction_id") or 0)
        )
        if transaction is None:
            raise ApiError("The queued issuance no longer exists locally.")
        if transaction.remote_id:
            return
        if transaction.personnel is None or transaction.personnel.remote_id is None:
            raise ApiError(
                "The receiving person is not linked to TOWEMS; this issuance remains "
                "in the local queue until the person is available in a snapshot."
            )
        mission_remote = None
        if transaction.mission_id:
            if transaction.mission is None or transaction.mission.remote_id is None:
                raise ApiError("The linked Mission Order is not linked to TOWEMS.")
            mission_remote = transaction.mission.remote_id
        firearms = []
        ammunition = []
        for line in transaction.lines:
            if line.firearm_id:
                firearms.append(
                    {
                        "firearm_id": line.firearm_id_snapshot,
                        "butt_number": line.butt_number_snapshot,
                        "inspection_status": "serviceable",
                        "inspection_remarks": line.inspection_remarks or "",
                    }
                )
            elif line.ammunition_id:
                lot = session.get(Ammunition, line.ammunition_id)
                if lot is None or lot.remote_id is None:
                    raise ApiError(
                        f"Ammunition {line.ammunition_name} is not linked to TOWEMS."
                    )
                if line.quantity > 0:
                    ammunition.append(
                        {"ammunition": lot.remote_id, "quantity": line.quantity}
                    )
        if not firearms:
            raise ApiError("The queued issuance has no firearm lines.")
        data = client.issue(
            {
                "mission": mission_remote,
                "client_uuid": transaction.uuid,
                "purpose": transaction.purpose,
                "personnel": transaction.personnel.remote_id,
                "firearms": firearms,
                "ammunition": ammunition,
                "identity_verified": True,
                "acknowledged": bool(transaction.acknowledgement_name),
                "acknowledgement_name": transaction.acknowledgement_name,
                "inspection_remarks": transaction.inspection_remarks or "",
                "supporting_documents": transaction.supporting_documents or "",
            }
        )
        if not isinstance(data, dict) or not data.get("id"):
            raise ApiError("TOWEMS did not return the saved issuance.")
        transaction.remote_id = int(data["id"])
        transaction.remote_reference = str(data.get("reference_number") or "")
        transaction.sync_state = "synced"
        transaction.synced_at = now()
        self._map_response_lines(session, transaction, data.get("lines") or [])
        for inspection in session.scalars(
            select(Inspection).where(Inspection.transaction_id == transaction.id)
        ):
            inspection.sync_state = "synced"
            inspection.synced_at = now()
        session.flush()

    def _push_return(self, session: Session, client: TowemsClient, row: SyncQueue) -> None:
        transaction = session.get(
            Transaction, int(_payload(row).get("transaction_id") or 0)
        )
        if transaction is None:
            raise ApiError("The queued return no longer exists locally.")
        if transaction.remote_id:
            return
        issuance = session.get(Transaction, transaction.return_of_id)
        if issuance is None or issuance.remote_id is None:
            raise ApiError(
                "The original issuance has not reached TOWEMS yet. The return remains "
                "queued and will upload after the issuance succeeds."
            )
        original_firearms = {
            line.id: line
            for line in issuance.lines
            if line.firearm_id
        }
        return_firearms = {}
        for line in transaction.lines:
            if line.original_line_id:
                return_firearms[line.original_line_id] = line
        firearms = []
        for original_line_id, original in original_firearms.items():
            returned = return_firearms.get(original_line_id)
            if returned is None:
                raise ApiError("A return firearm line is missing from the local record.")
            firearms.append(
                {
                    "firearm_id": original.firearm_id_snapshot,
                    "butt_number": original.butt_number_snapshot,
                    "condition_status": returned.condition_status or "serviceable",
                    "inspection_remarks": returned.inspection_remarks or "",
                }
            )
        ammunition = []
        for original in issuance.lines:
            if not original.ammunition_id:
                continue
            if original.remote_id is None:
                raise ApiError(
                    "An original ammunition line has no TOWEMS id; synchronize the "
                    "issuance before uploading the return."
                )
            returned = return_firearms.get(original.id)
            quantity = returned.quantity if returned else 0
            ammunition.append(
                {
                    "line": original.remote_id,
                    "quantity": quantity,
                    "serviceability": (returned.condition_status if returned else "")
                    or "serviceable",
                    "discrepancy_note": returned.inspection_remarks
                    if returned
                    else "",
                }
            )
        data = client.return_firearms(
            {
                "issuance": issuance.remote_id,
                "client_uuid": transaction.uuid,
                "firearms": firearms,
                "ammunition": ammunition,
                "identity_verified": True,
                "acknowledged": bool(transaction.acknowledgement_name),
                "acknowledgement_name": transaction.acknowledgement_name,
                "inspection_remarks": transaction.inspection_remarks or "",
                "discrepancy_notes": transaction.discrepancy_notes or "",
                "supporting_documents": transaction.supporting_documents or "",
            }
        )
        if not isinstance(data, dict) or not data.get("id"):
            raise ApiError("TOWEMS did not return the saved return.")
        transaction.remote_id = int(data["id"])
        transaction.remote_reference = str(data.get("reference_number") or "")
        transaction.sync_state = "synced"
        transaction.synced_at = now()
        self._map_response_lines(session, transaction, data.get("lines") or [])
        for inspection in session.scalars(
            select(Inspection).where(Inspection.transaction_id == transaction.id)
        ):
            inspection.sync_state = "synced"
            inspection.synced_at = now()
        session.flush()

    def _map_response_lines(
        self, session: Session, transaction: Transaction, response_lines: list[dict]
    ) -> None:
        local_lines = list(transaction.lines)
        for remote in response_lines:
            if not isinstance(remote, dict) or not remote.get("id"):
                continue
            snapshot = str(remote.get("firearm_id_snapshot") or "")
            local = next(
                (
                    line
                    for line in local_lines
                    if snapshot and line.firearm_id_snapshot == snapshot
                ),
                None,
            )
            if local is None:
                ammo_remote = remote.get("ammunition")
                quantity = remote.get("quantity")
                local = next(
                    (
                        line
                        for line in local_lines
                        if line.ammunition_id
                        and line.remote_id is None
                        and quantity is not None
                        and line.quantity == int(quantity)
                        and session.get(Ammunition, line.ammunition_id).remote_id
                        == ammo_remote
                    ),
                    None,
                )
            if local is not None and local.remote_id is None:
                local.remote_id = int(remote["id"])

    def _push_inspection(
        self, session: Session, client: TowemsClient, row: SyncQueue
    ) -> None:
        inspection = session.get(
            Inspection, int(_payload(row).get("inspection_id") or 0)
        )
        if inspection is None:
            raise ApiError("The queued inspection no longer exists locally.")
        if inspection.remote_id:
            return
        firearm = session.get(Firearm, inspection.firearm_id)
        if firearm is None or firearm.remote_id is None:
            raise ApiError(
                "The inspected firearm has not reached TOWEMS yet. The inspection "
                "will upload after the firearm registration."
            )
        data = client.inspect_firearm(
            firearm.remote_id,
            {
                "client_uuid": inspection.uuid,
                "condition_status": inspection.condition_status,
                "remarks": inspection.remarks or "",
                "inspected_at": inspection.inspected_at.isoformat()
                if inspection.inspected_at
                else None,
                "next_inspection_date": inspection.next_inspection_date.isoformat()
                if inspection.next_inspection_date
                else None,
            },
        )
        remote = (data or {}).get("inspection") if isinstance(data, dict) else None
        if not isinstance(remote, dict) or not remote.get("id"):
            raise ApiError("TOWEMS did not return the saved inspection.")
        inspection.remote_id = int(remote["id"])
        inspection.sync_state = "synced"
        inspection.synced_at = now()
        session.flush()

    def _push_adjustment(
        self, session: Session, client: TowemsClient, row: SyncQueue
    ) -> None:
        payload = _payload(row)
        transaction = session.get(Transaction, int(payload.get("transaction_id") or 0))
        if transaction is None:
            raise ApiError("The queued ammunition adjustment no longer exists.")
        if transaction.remote_id:
            return
        line = next((line for line in transaction.lines if line.ammunition_id), None)
        if line is None:
            raise ApiError("The adjustment has no ammunition line.")
        lot = session.get(Ammunition, line.ammunition_id)
        if lot is None or lot.remote_id is None:
            raise ApiError(
                "The ammunition lot has not reached TOWEMS yet. The adjustment will "
                "upload after the lot registration."
            )
        data = client.adjust_ammunition(
            lot.remote_id,
            {
                "client_uuid": transaction.uuid,
                "stock_balance": int(payload.get("stock_balance", lot.stock_balance)),
                "reason": str(payload.get("reason") or transaction.discrepancy_notes or "")
                or "Offline stock adjustment",
                "supporting_documents": transaction.supporting_documents or "",
            },
        )
        remote = (data or {}).get("transaction") if isinstance(data, dict) else None
        if not isinstance(remote, dict) or not remote.get("id"):
            raise ApiError("TOWEMS did not return the saved adjustment.")
        transaction.remote_id = int(remote["id"])
        transaction.remote_reference = str(remote.get("reference_number") or "")
        transaction.sync_state = "synced"
        transaction.synced_at = now()
        session.flush()

    # -- reference merge ----------------------------------------------------

    def _merge_snapshot(self, session: Session, snapshot: dict) -> int:
        downloaded = 0
        downloaded += self._merge_units(session, snapshot.get("units") or [])
        downloaded += self._merge_personnel(
            session, snapshot.get("personnel") or [], deactivate=True
        )
        downloaded += self._merge_firearms(session, snapshot.get("firearms") or [])
        downloaded += self._merge_ammunition(session, snapshot.get("ammunition") or [])
        downloaded += self._merge_missions(session, snapshot.get("missions") or [])
        downloaded += self._merge_assignments(session, snapshot.get("assignments") or [])
        session.flush()
        return downloaded

    def _merge_units(self, session: Session, rows: list[dict]) -> int:
        count = 0
        for row in rows:
            remote_id = int(row.get("id") or 0)
            if not remote_id:
                continue
            unit = session.scalar(select(Unit).where(Unit.remote_id == remote_id))
            if unit is None:
                acronym = str(row.get("acronym") or "").strip()
                name = str(row.get("name") or "").strip()
                candidates = [
                    value
                    for value in (
                        (Unit.acronym == acronym) if acronym else None,
                        (Unit.name == name) if name else None,
                    )
                    if value is not None
                ]
                unit = session.scalar(
                    select(Unit).where(Unit.remote_id.is_(None), or_(*candidates))
                ) if candidates else None
            if unit is None:
                unit = Unit(
                    remote_id=remote_id,
                    name=str(row.get("name") or "Unit").strip(),
                    acronym=str(row.get("acronym") or "").strip(),
                    parent_remote_id=row.get("parent_id"),
                )
                session.add(unit)
            else:
                unit.remote_id = remote_id
                unit.name = str(row.get("name") or unit.name).strip()
                unit.acronym = str(row.get("acronym") or unit.acronym).strip()
                unit.parent_remote_id = row.get("parent_id")
            count += 1
        session.flush()
        remote_units = [
            unit for unit in session.scalars(select(Unit)) if unit.remote_id is not None
        ]
        unlinked = [
            unit for unit in session.scalars(select(Unit)) if unit.remote_id is None
        ]
        if len(rows) == 1 and len(remote_units) == 1 and len(unlinked) == 1:
            # A single-armory deployment should not strand locally registered
            # firearms because the setup unit was named differently. Match the
            # one local armory to the one unit in the account's scoped snapshot.
            unlinked[0].remote_id = remote_units[0].remote_id
        session.flush()
        return count

    def _merge_personnel(
        self, session: Session, rows: list[dict], *, deactivate: bool
    ) -> int:
        unit_by_remote = {
            unit.remote_id: unit
            for unit in session.scalars(select(Unit))
            if unit.remote_id is not None
        }
        seen: set[int] = set()
        for row in rows:
            remote_id = int(row.get("id") or 0)
            if not remote_id:
                continue
            seen.add(remote_id)
            username = str(row.get("username") or "").strip().lower()
            serial = str(row.get("serial_number") or "").strip().upper()
            person = session.scalar(
                select(Personnel).where(Personnel.remote_id == remote_id)
            )
            if person is None and username:
                person = session.scalar(
                    select(Personnel).where(func.lower(Personnel.username) == username)
                )
            if person is None and serial:
                person = session.scalar(
                    select(Personnel).where(
                        func.upper(Personnel.serial_number) == serial
                    )
                )
            first, middle, last = _parse_names(
                str(row.get("full_name") or ""),
                str(row.get("last_name") or ""),
                str(row.get("rank") or ""),
            )
            if person is None:
                unique_username = username or f"central-{remote_id}"
                clash = session.scalar(
                    select(Personnel).where(Personnel.username == unique_username)
                )
                if clash is not None:
                    unique_username = f"{unique_username}-{remote_id}"
                person = Personnel(
                    remote_id=remote_id,
                    username=unique_username,
                    first_name=first or "Unknown",
                    middle_name=middle,
                    last_name=last or "Personnel",
                    serial_number=serial or f"CENTRAL-{remote_id}",
                    is_authorized=True,
                )
                session.add(person)
            else:
                person.remote_id = remote_id
                if username and not session.scalar(
                    select(Personnel.id).where(
                        Personnel.username == username, Personnel.id != person.id
                    )
                ):
                    person.username = username
                if serial:
                    person.serial_number = serial
                if first:
                    person.first_name = first
                if middle or not person.middle_name:
                    person.middle_name = middle
                if last:
                    person.last_name = last
            person.rank = str(row.get("rank") or person.rank or "").strip()
            person.branch_of_service = str(
                row.get("branch_of_service") or person.branch_of_service or ""
            ).strip()
            person.contact_number = str(
                row.get("contact_number") or person.contact_number or ""
            ).strip()
            unit = unit_by_remote.get(row.get("unit_id"))
            if unit is not None:
                person.unit_id = unit.id
            if _truthy(row.get("is_active")):
                person.is_active = True
        if deactivate and seen:
            for person in session.scalars(
                select(Personnel).where(
                    Personnel.remote_id.is_not(None),
                    Personnel.remote_id.not_in(seen),
                )
            ):
                person.is_active = False
        session.flush()
        return len(seen)

    def _merge_firearms(self, session: Session, rows: list[dict]) -> int:
        unit_by_remote = {
            unit.remote_id: unit
            for unit in session.scalars(select(Unit))
            if unit.remote_id is not None
        }
        person_by_remote = {
            person.remote_id: person
            for person in session.scalars(select(Personnel))
            if person.remote_id is not None
        }
        pending_firearms = self._pending_transaction_firearm_ids(session)
        pending_inspections = set(
            session.scalars(
                select(Inspection.firearm_id).where(Inspection.sync_state != "synced")
            )
        )
        for row in rows:
            remote_id = int(row.get("id") or 0)
            if not remote_id:
                continue
            serial = str(row.get("serial_number") or "").strip().upper()
            firearm = session.scalar(
                select(Firearm).where(Firearm.remote_id == remote_id)
            )
            if firearm is None and serial:
                firearm = session.scalar(
                    select(Firearm).where(
                        func.upper(Firearm.serial_number) == serial,
                        Firearm.remote_id.is_(None),
                    )
                )
            if firearm is None:
                firearm = Firearm(
                    remote_id=remote_id,
                    firearm_id=str(row.get("firearm_id") or f"PAF-{serial}"),
                    serial_number=serial,
                    butt_number=str(row.get("butt_number") or "").strip().upper(),
                    firearm_type=str(row.get("firearm_type") or "rifle"),
                )
                session.add(firearm)
            else:
                firearm.remote_id = remote_id
                if serial:
                    firearm.serial_number = serial
                if row.get("butt_number"):
                    firearm.butt_number = str(row["butt_number"]).strip().upper()
                if row.get("firearm_type") in {"rifle", "pistol"}:
                    firearm.firearm_type = row["firearm_type"]
                if row.get("firearm_id"):
                    firearm.firearm_id = str(row["firearm_id"])
            firearm.manufacturer = str(row.get("manufacturer") or "")
            firearm.model = str(row.get("model") or "")
            firearm.caliber = str(row.get("caliber") or "")
            firearm.date_acquired = _as_date(row.get("date_acquired"))
            firearm.acquisition_reference = str(
                row.get("acquisition_reference") or ""
            )
            firearm.storage_location = str(row.get("storage_location") or "Armory")
            firearm.date_last_inspected = _as_date(row.get("date_last_inspected"))
            firearm.remarks = str(row.get("remarks") or "")
            if firearm.id not in pending_firearms and firearm.id not in pending_inspections:
                if row.get("condition_status") in {
                    "serviceable",
                    "unserviceable",
                    "maintenance",
                    "condemned",
                }:
                    firearm.condition_status = row["condition_status"]
            if firearm.id not in pending_firearms:
                if row.get("custody_status") in {"stored", "issued", "retired"}:
                    firearm.custody_status = row["custody_status"]
            unit = unit_by_remote.get(row.get("assigned_unit"))
            if unit is not None:
                firearm.assigned_unit_id = unit.id
            person = person_by_remote.get(row.get("assigned_personnel"))
            if person is not None:
                firearm.assigned_personnel_id = person.id
            firearm.sync_state = "synced"
            firearm.synced_at = now()
        session.flush()
        return len(rows)

    def _merge_ammunition(self, session: Session, rows: list[dict]) -> int:
        unit_by_remote = {
            unit.remote_id: unit
            for unit in session.scalars(select(Unit))
            if unit.remote_id is not None
        }
        person_by_remote = {
            person.remote_id: person
            for person in session.scalars(select(Personnel))
            if person.remote_id is not None
        }
        pending_lots = self._pending_transaction_ammunition_ids(session)
        pending_creates = set(
            int(row.entity_uuid)
            for row in session.scalars(
                select(SyncQueue).where(
                    SyncQueue.entity_type == "ammunition",
                    SyncQueue.action == "ammunition.create",
                    SyncQueue.state.in_(("pending", "failed")),
                )
            )
            if str(row.entity_uuid).isdigit()
        )
        for row in rows:
            remote_id = int(row.get("id") or 0)
            if not remote_id:
                continue
            ammunition_id = str(row.get("ammunition_id") or "").strip()
            lot = session.scalar(
                select(Ammunition).where(Ammunition.remote_id == remote_id)
            )
            if lot is None:
                lot = session.scalar(
                    select(Ammunition).where(
                        Ammunition.ammunition_id == ammunition_id
                    )
                )
            if lot is None:
                lot = session.scalar(
                    select(Ammunition).where(
                        Ammunition.remote_id.is_(None),
                        func.upper(Ammunition.lot_number)
                        == str(row.get("lot_number") or "").strip().upper(),
                        func.upper(Ammunition.caliber)
                        == str(row.get("caliber") or "").strip().upper(),
                        func.upper(Ammunition.nomenclature)
                        == str(row.get("nomenclature") or "").strip().upper(),
                    )
                )
            if lot is None:
                lot = Ammunition(
                    remote_id=remote_id,
                    ammunition_id=ammunition_id or f"AMM-{remote_id}",
                    nomenclature=str(row.get("nomenclature") or "Ammunition"),
                    caliber=str(row.get("caliber") or ""),
                    lot_number=str(row.get("lot_number") or ""),
                )
                session.add(lot)
            else:
                lot.remote_id = remote_id
                if ammunition_id:
                    lot.ammunition_id = ammunition_id
            lot.nomenclature = str(row.get("nomenclature") or lot.nomenclature)
            lot.caliber = str(row.get("caliber") or lot.caliber)
            lot.ammunition_type = str(row.get("ammunition_type") or "")
            lot.lot_number = str(row.get("lot_number") or lot.lot_number)
            lot.unit_of_measure = str(row.get("unit_of_measure") or "rounds")
            lot.serviceability = str(row.get("serviceability") or "serviceable")
            lot.storage_location = str(row.get("storage_location") or "Armory")
            lot.date_received = _as_date(row.get("date_received"))
            lot.expiration_date = _as_date(row.get("expiration_date"))
            lot.remarks = str(row.get("remarks") or "")
            lot.quantity_received = int(row.get("quantity_received") or 0)
            if lot.id not in pending_lots and lot.id not in pending_creates:
                lot.quantity_issued = int(row.get("quantity_issued") or 0)
                lot.stock_balance = int(row.get("stock_balance") or 0)
            unit = unit_by_remote.get(row.get("owning_unit"))
            if unit is not None:
                lot.owning_unit_id = unit.id
            custodian = person_by_remote.get(row.get("responsible_custodian"))
            if custodian is not None:
                lot.responsible_custodian_id = custodian.id
            lot.sync_state = "synced"
            lot.synced_at = now()
        session.flush()
        return len(rows)

    def _merge_missions(self, session: Session, rows: list[dict]) -> int:
        unit_by_remote = {
            unit.remote_id: unit
            for unit in session.scalars(select(Unit))
            if unit.remote_id is not None
        }
        person_by_remote = {
            person.remote_id: person
            for person in session.scalars(select(Personnel))
            if person.remote_id is not None
        }
        for row in rows:
            remote_id = int(row.get("id") or 0)
            number = str(row.get("mission_number") or "").strip()
            if not remote_id or not number:
                continue
            mission = session.scalar(
                select(Mission).where(Mission.remote_id == remote_id)
            )
            if mission is None:
                mission = session.scalar(
                    select(Mission).where(Mission.mission_number == number)
                )
            if mission is None:
                mission = Mission(remote_id=remote_id, mission_number=number)
                session.add(mission)
            else:
                mission.remote_id = remote_id
                mission.mission_number = number
            mission.title = str(row.get("title") or "")
            mission.category = str(row.get("category") or "")
            mission.status = str(row.get("status") or "")
            mission.approval_status = str(row.get("approval_status") or "")
            mission.mission_order_status = str(
                row.get("mission_order_status") or ""
            )
            mission.start_at = _as_datetime(row.get("start_at"))
            mission.end_at = _as_datetime(row.get("end_at"))
            mission.order_ready = bool(row.get("order_ready"))
            mission.order_error = (
                ""
                if mission.order_ready
                else "Mission Order is not approved and signed."
            )
            unit = unit_by_remote.get(row.get("unit"))
            if unit is not None:
                mission.unit_id = unit.id
            mission.synced_at = now()
            session.flush()
            session.query(MissionPersonnel).filter(
                MissionPersonnel.mission_id == mission.id
            ).delete(synchronize_session=False)
            for roster in row.get("personnel") or []:
                person = person_by_remote.get(roster.get("personnel"))
                if person is None:
                    continue
                session.add(
                    MissionPersonnel(
                        mission_id=mission.id,
                        personnel_id=person.id,
                        role=str(roster.get("role") or ""),
                        custom_role=str(roster.get("custom_role") or ""),
                    )
                )
        session.flush()
        return len(rows)

    def _merge_assignments(self, session: Session, rows: list[dict]) -> int:
        person_by_remote = {
            person.remote_id: person
            for person in session.scalars(select(Personnel))
            if person.remote_id is not None
        }
        firearm_by_remote = {
            firearm.remote_id: firearm
            for firearm in session.scalars(select(Firearm))
            if firearm.remote_id is not None
        }
        pending_person = set(
            int(row.entity_uuid)
            for row in session.scalars(
                select(SyncQueue).where(
                    SyncQueue.entity_type == "assignment_state",
                    SyncQueue.state.in_(("pending", "failed")),
                )
            )
            if str(row.entity_uuid).isdigit()
        )
        remote_active: dict[int, set[int]] = {}
        remote_seen: set[int] = set()
        for row in rows:
            remote_id = int(row.get("id") or 0)
            if not remote_id:
                continue
            remote_seen.add(remote_id)
            person = person_by_remote.get(row.get("user"))
            firearm_data = row.get("firearm") or {}
            firearm = firearm_by_remote.get(
                firearm_data.get("id") if isinstance(firearm_data, dict) else None
            )
            if person is None or firearm is None:
                continue
            if person.id in pending_person:
                continue
            assignment = session.scalar(
                select(Assignment).where(Assignment.remote_id == remote_id)
            )
            if assignment is None:
                assignment = session.scalar(
                    select(Assignment).where(
                        Assignment.remote_id.is_(None),
                        Assignment.personnel_id == person.id,
                        Assignment.firearm_id == firearm.id,
                        Assignment.assignment_type
                        == str(row.get("assignment_type") or ""),
                    )
                )
            if assignment is None:
                assignment = Assignment(
                    remote_id=remote_id,
                    personnel_id=person.id,
                    firearm_id=firearm.id,
                    assignment_type=str(row.get("assignment_type") or "rifle"),
                    is_active=bool(row.get("is_active")),
                )
                session.add(assignment)
            assignment.remote_id = remote_id
            assignment.personnel_id = person.id
            assignment.firearm_id = firearm.id
            assignment.assignment_type = str(row.get("assignment_type") or "rifle")
            assignment.is_active = bool(row.get("is_active"))
            assignment.assigned_at = _as_datetime(row.get("assigned_at")) or now()
            assignment.assigned_by = str(row.get("assigned_by_name") or "")
            assignment.released_at = _as_datetime(row.get("released_at"))
            assignment.released_by = str(row.get("released_by_name") or "")
            assignment.notes = str(row.get("notes") or "")
            assignment.sync_state = "synced"
            assignment.synced_at = now()
            if assignment.is_active:
                remote_active.setdefault(person.id, set()).add(firearm.id)
                for other in session.scalars(
                    select(Assignment).where(
                        Assignment.personnel_id == person.id,
                        Assignment.assignment_type == assignment.assignment_type,
                        Assignment.is_active.is_(True),
                        Assignment.id != (assignment.id or -1),
                    )
                ):
                    other.is_active = False
                    other.released_at = now()
                    other.released_by = "sync"
            session.flush()
        if remote_active or remote_seen:
            for assignment in session.scalars(
                select(Assignment).where(
                    Assignment.remote_id.is_not(None),
                    Assignment.is_active.is_(True),
                )
            ):
                if assignment.personnel_id in pending_person:
                    continue
                if assignment.remote_id not in remote_seen:
                    assignment.is_active = False
                    assignment.released_at = now()
                    assignment.released_by = "sync"
        session.flush()
        active_firearms = {
            firearm_id
            for firearm_ids in remote_active.values()
            for firearm_id in firearm_ids
        }
        for firearm in session.scalars(
            select(Firearm).where(Firearm.remote_id.is_not(None))
        ):
            if firearm.id in active_firearms:
                holder = next(
                    (
                        assignment.personnel_id
                        for assignment in session.scalars(
                            select(Assignment).where(
                                Assignment.firearm_id == firearm.id,
                                Assignment.is_active.is_(True),
                            )
                        )
                    ),
                    None,
                )
                if holder:
                    firearm.assigned_personnel_id = holder
            elif firearm.assigned_personnel_id is not None:
                pending = session.scalar(
                    select(Assignment.id).where(
                        Assignment.firearm_id == firearm.id,
                        Assignment.is_active.is_(True),
                    )
                )
                if not pending:
                    firearm.assigned_personnel_id = None
        session.flush()
        return len(rows)

    # -- pending-state helpers ---------------------------------------------

    def _pending_transaction_firearm_ids(self, session: Session) -> set[int]:
        return set(
            session.scalars(
                select(TransactionLine.firearm_id)
                .join(Transaction, Transaction.id == TransactionLine.transaction_id)
                .where(
                    TransactionLine.firearm_id.is_not(None),
                    Transaction.sync_state != "synced",
                )
            )
        )

    def _pending_transaction_ammunition_ids(self, session: Session) -> set[int]:
        return set(
            session.scalars(
                select(TransactionLine.ammunition_id)
                .join(Transaction, Transaction.id == TransactionLine.transaction_id)
                .where(
                    TransactionLine.ammunition_id.is_not(None),
                    Transaction.sync_state != "synced",
                )
            )
        )


def run_headless_sync() -> SyncResult:
    """Entry point for Windows Task Scheduler / ``--sync-only``."""
    with db.session_scope() as session:
        username = db.get_setting(session, "sync_username", "")
        base_url = db.get_setting(session, "api_base", "")
        auto = db.setting_bool(session, "auto_sync", True)
    if not auto:
        return SyncResult(
            status="skipped", message="Automatic synchronization is disabled."
        )
    return SyncService().run(username=username, base_url=base_url, trigger="scheduled")


def retry_failed_operations(session: Session) -> int:
    rows = list(
        session.scalars(select(SyncQueue).where(SyncQueue.state == "failed"))
    )
    for row in rows:
        row.state = "pending"
        row.next_attempt_at = None
        row.last_error = ""
    return len(rows)
