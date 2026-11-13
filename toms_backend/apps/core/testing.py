"""Create the legacy (``managed = False``) tables inside the TEST database.

Django never emits DDL for unmanaged models, which is exactly what protects
production - but tests still need the tables. This helper builds them in the
throw-away test database only, and refuses to run against a configured
database whose name does not look like a test database.
"""

from __future__ import annotations

from django.db import connection


def _legacy_models():
    from apps.accounts.models import User
    from apps.legacy.models import (
        FeedbackNotification,
        HazardRegistry,
        MRMFeedback,
        MRMForm,
        MRMPersonnel,
        MissionHazards,
        Unit,
        UnitAssignment,
        UnitLogo,
        UserActivity,
    )

    return [
        Unit,
        User,
        UnitAssignment,
        UnitLogo,
        HazardRegistry,
        MRMForm,
        MissionHazards,
        MRMPersonnel,
        MRMFeedback,
        FeedbackNotification,
        UserActivity,
    ]


def create_legacy_tables() -> None:
    """Create every legacy table in the current (test) database."""
    if not connection.settings_dict["NAME"].startswith((":memory:", "file:memorydb", "test_")):
        # SQLite in-memory reports NAME ':memory:'; PostgreSQL test databases
        # are prefixed with 'test_'. Anything else is too risky to touch.
        raise RuntimeError(
            "Refusing to create legacy tables outside a test database "
            f"(NAME={connection.settings_dict['NAME']!r})."
        )

    existing = set(connection.introspection.table_names())
    with connection.schema_editor() as editor:
        for model in _legacy_models():
            if model._meta.db_table in existing:
                continue
            editor.create_model(model)
