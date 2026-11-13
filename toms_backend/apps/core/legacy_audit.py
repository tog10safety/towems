"""Write to the legacy ``user_activities`` audit table.

The historical audit trail is preserved and continued: TOWEMS records its own
entries in ``core_audit_log`` **and** mirrors significant actions into the
existing table so that the old audit screens and reports keep working.

Only the enum labels present in the live database are used (``unit_management``
is absent from the PostgreSQL enum - drift D1 - so it is never written).
"""

from __future__ import annotations

import datetime as dt
import logging

from apps.core import timeutils

logger = logging.getLogger(__name__)

VALID_MODULES = {"auth", "hazards", "mrm", "admin", "users", "unit_safety"}


def record_activity(
    *,
    user,
    activity_type: str,
    module: str,
    description: str,
    request=None,
    resource_id=None,
    resource_type=None,
    data=None,
):
    """Insert one legacy audit row. Never raises to the caller."""
    from apps.legacy.models import UserActivity

    if user is None or not getattr(user, "id", None):
        return None
    if module not in VALID_MODULES:
        logger.warning("Refusing to write unknown legacy module %r", module)
        return None

    ip = None
    agent = None
    if request is not None:
        ip = (request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
              or request.META.get("REMOTE_ADDR"))
        agent = request.META.get("HTTP_USER_AGENT", "")[:400]

    try:
        return UserActivity.objects.create(
            user_id=user.id,
            activity_type=activity_type,
            module=module,
            description=description,
            ip_address=ip,
            user_agent=agent,
            resource_id=resource_id,
            resource_type=resource_type,
            activity_data=data or {},
            timestamp=timeutils.now(),
        )
    except Exception:  # pragma: no cover - auditing must not break requests
        logger.exception("Failed to write legacy activity row")
        return None
