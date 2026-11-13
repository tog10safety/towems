"""Audit trail for TOWEMS writes.

Two trails coexist deliberately:

* ``user_activities`` - the legacy audit table, still written by the Flask app.
  Django only ever adds rows to it from well-defined actions.
* ``core_audit_log`` - the TOWEMS trail, capturing before/after snapshots with a
  request correlation id so an approver can reconstruct exactly what changed.
"""

from __future__ import annotations

import datetime as dt
import decimal
import json
from typing import Any

from django.db import models


def _json_safe(value: Any) -> Any:
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return str(value)
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if isinstance(value, models.Model):
        return value.pk
    return value


def snapshot(instance, fields: list[str] | None = None) -> dict:
    """Serialise selected model fields into a JSON-safe dict."""
    if instance is None:
        return {}
    names = fields or [
        f.name for f in instance._meta.fields
    ]
    data = {}
    for name in names:
        try:
            data[name] = _json_safe(getattr(instance, name))
        except Exception:  # pragma: no cover - defensive
            continue
    return data


def log_action(
    *,
    actor,
    action: str,
    entity_type: str,
    entity_id: Any = None,
    before: dict | None = None,
    after: dict | None = None,
    note: str = "",
    request=None,
):
    """Write one TOWEMS audit record. Never raises to the caller."""
    from apps.ops.models import AuditLog

    ip = user_agent = None
    correlation_id = ""
    if request is not None:
        ip = request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() or request.META.get(
            "REMOTE_ADDR"
        )
        user_agent = request.META.get("HTTP_USER_AGENT", "")[:400]
        correlation_id = getattr(request, "correlation_id", "") or ""

    try:
        return AuditLog.objects.create(
            actor_id=getattr(actor, "id", None) if actor and getattr(actor, "is_authenticated", False) else None,
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id) if entity_id is not None else "",
            before=_json_safe(before) if before else {},
            after=_json_safe(after) if after else {},
            note=note or "",
            # These columns are NOT NULL. Writing None would raise inside the
            # caller's transaction, and because this helper swallows its own
            # errors the surrounding atomic block would roll back silently.
            ip_address=ip or "",
            user_agent=user_agent or "",
            correlation_id=correlation_id or "",
        )
    except Exception:  # pragma: no cover - auditing must not break the request
        import logging

        logging.getLogger(__name__).exception("Failed to write audit log entry")
        return None


class AuditedModelViewSetMixin:
    """Log create/update/destroy with before/after snapshots."""

    audit_entity = "record"
    audit_fields: list[str] | None = None

    def _entity_name(self) -> str:
        return self.audit_entity or self.get_queryset().model.__name__

    def perform_create(self, serializer):
        instance = serializer.save()
        log_action(
            actor=self.request.user,
            action="create",
            entity_type=self._entity_name(),
            entity_id=instance.pk,
            after=snapshot(instance, self.audit_fields),
            request=self.request,
        )
        return instance

    def perform_update(self, serializer):
        before = snapshot(serializer.instance, self.audit_fields)
        instance = serializer.save()
        log_action(
            actor=self.request.user,
            action="update",
            entity_type=self._entity_name(),
            entity_id=instance.pk,
            before=before,
            after=snapshot(instance, self.audit_fields),
            request=self.request,
        )
        return instance

    def perform_destroy(self, instance):
        before = snapshot(instance, self.audit_fields)
        entity_id = instance.pk
        instance.delete()
        log_action(
            actor=self.request.user,
            action="delete",
            entity_type=self._entity_name(),
            entity_id=entity_id,
            before=before,
            request=self.request,
        )
