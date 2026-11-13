"""Test factories for legacy records (used by the isolated test database only)."""

from __future__ import annotations

import itertools
import uuid

from werkzeug.security import generate_password_hash

from apps.accounts.models import User
from apps.legacy.models import Unit

_counter = itertools.count(1)
PASSWORD = "Passw0rd!23"


def unique_serial() -> str:
    return f"SN-{uuid.uuid4().hex[:10].upper()}"


def make_user(*, unit=None, authority="Creator", password=PASSWORD, **overrides) -> User:
    index = next(_counter)
    data = {
        "username": f"user{index}",
        "password_hash": generate_password_hash(password),
        "authority": authority,
        "category": "Officer",
        "rank": "Capt",
        "first_name": f"First{index}",
        "last_name": f"Last{index}",
        "serial_number": unique_serial(),
        "branch_of_service": "PAF",
        "unit": unit,
        "unit_legacy": unit.name if unit is not None else "",
        "is_active": True,
        "force_password_change": False,
    }
    data.update(overrides)
    return User.objects.create(**data)


def make_unit(*, name=None, acronym=None, parent=None, **overrides) -> Unit:
    index = next(_counter)
    data = {
        "name": name or f"Unit {index}",
        "acronym": acronym or f"U{index}",
        "category": "Squadron",
        "location": "Base",
        "parent": parent,
        "is_active": True,
    }
    data.update(overrides)
    return Unit.objects.create(**data)
