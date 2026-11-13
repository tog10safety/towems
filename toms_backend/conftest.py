"""Shared test fixtures.

The test database is an isolated in-memory SQLite database. Legacy tables are
created inside it by ``create_legacy_tables`` (safe-guarded to test databases
only), because those models are unmanaged. No real database is touched.
"""

from __future__ import annotations

import uuid

import pytest
from django.test.utils import setup_databases, teardown_databases
from rest_framework.test import APIClient

from apps.core.factories import PASSWORD, make_unit, make_user  # noqa: F401
from apps.legacy.models import MRMForm


@pytest.fixture(scope="session")
def django_db_setup(django_db_blocker):
    with django_db_blocker.unblock():
        # ``serialized_aliases=[]`` disables Django's initial-state snapshot:
        # the legacy (managed = False) tables are created *after* migrate() by
        # the fixture below, so a snapshot taken here would query tables that do
        # not exist yet. No test uses ``serialized_rollback``.
        config = setup_databases(
            verbosity=0, interactive=False, serialized_aliases=[]
        )
        from apps.core.testing import create_legacy_tables

        create_legacy_tables()
    yield
    with django_db_blocker.unblock():
        teardown_databases(config, verbosity=0)


@pytest.fixture
def unit(db):
    return make_unit(name="Alpha Squadron", acronym="ALPHA")


@pytest.fixture
def other_unit(db):
    return make_unit(name="Bravo Squadron", acronym="BRAVO")


@pytest.fixture
def creator(db, unit):
    return make_user(unit=unit, authority="Creator")


@pytest.fixture
def evaluator(db, unit):
    return make_user(unit=unit, authority="Evaluator")


@pytest.fixture
def evaluator_other_unit(db, other_unit):
    return make_user(unit=other_unit, authority="Evaluator")


@pytest.fixture
def admin(db, unit):
    return make_user(unit=unit, authority="Administrator")


@pytest.fixture
def seat_admin(db, admin):
    """The Administrator account holding the single Super Administrator seat.

    Permanent deletion (MRM, mission, AOR, office) is reserved to this account,
    so tests that exercise it ask for ``seat_admin``; ``admin`` stays an
    ordinary administrator, which is what the refusal tests need.
    """
    from apps.accounts import ownership

    ownership.hand_over(
        admin, actor=None, reason="Test appointment of the seat", require_owner=False
    )
    return admin


@pytest.fixture
def safety(db, unit):
    return make_user(unit=unit, authority="Unit Safety Officer")


@pytest.fixture
def ops_officer(db, unit):
    """A Creator account holding the Operations Officer post at the unit."""
    from apps.command.models import UnitAppointment

    user = make_user(unit=unit, authority="Creator")
    UnitAppointment.objects.create(unit=unit, user=user, role="operations_officer")
    return user


@pytest.fixture
def operations_center(db, unit):
    """The unit's Operations Center office (planning authority)."""
    from apps.command.models import OfficeSection

    return OfficeSection.objects.create(
        unit=unit,
        name="Operations Center",
        code="OPS-C",
        kind="office",
        function=OfficeSection.FUNCTION_OPERATIONS_CENTER,
    )


@pytest.fixture
def planner(db, unit, operations_center):
    """A member of the unit's Operations Center: the only mission planner."""
    from apps.command.models import SectionAssignment

    user = make_user(unit=unit, authority="Creator")
    SectionAssignment.objects.create(
        user=user, section=operations_center, assignment_type="primary"
    )
    return user


@pytest.fixture
def logistics(db, unit):
    """A Creator account holding the Logistics Officer post.

    Inventory and the vehicle register are this post's to maintain; the Safety
    Officer keeps read access only.
    """
    from apps.command.models import UnitAppointment

    user = make_user(unit=unit, authority="Creator")
    UnitAppointment.objects.create(unit=unit, user=user, role="logistics_officer")
    return user


@pytest.fixture
def commander(db, unit):
    """A Creator account holding the Commander post (Decision Authority)."""
    from apps.command.models import UnitAppointment

    user = make_user(unit=unit, authority="Creator")
    UnitAppointment.objects.create(unit=unit, user=user, role="commander")
    return user


@pytest.fixture
def mrm_form(db, creator):
    return MRMForm.objects.create(
        mrm_number=f"MRM-{uuid.uuid4().hex[:6]}",
        activity_mission="Test mission",
        created_by=creator,
        safety_objectives="Safe operations",
        status="draft",
        review_status="pending",
        original_unit_name=creator.unit.name if creator.unit_id else None,
        original_unit_id=creator.unit_id,
    )


@pytest.fixture
def api() -> APIClient:
    return APIClient()


@pytest.fixture
def as_user():
    """An API client authenticated as ``user`` (session auth is tested separately).

    Each call returns its own client, so switching user inside one test never
    silently re-authenticates a client another step already holds.
    """

    def _as(user) -> APIClient:
        client = APIClient()
        client.force_authenticate(user=user)
        return client

    return _as


@pytest.fixture
def login(api):
    """Perform a real session login including CSRF."""

    def _login(user, password=PASSWORD):
        from django.conf import settings

        response = api.get("/api/auth/csrf/")
        assert response.status_code == 200
        token = api.cookies[settings.CSRF_COOKIE_NAME].value
        return api.post(
            "/api/auth/login/",
            {"username": user.username, "password": password},
            format="json",
            HTTP_X_CSRFTOKEN=token,
        )

    return _login
