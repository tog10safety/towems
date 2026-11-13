"""Authentication backend compatible with the legacy password hashes.

The existing ``users.password_hash`` values were produced by Werkzeug
(scrypt by default in Werkzeug 3.x; pbkdf2/scrypt for older rows). Django's
hashers cannot read those formats, and forcing 484 users to reset their
passwords would take the system offline.

This backend therefore verifies with Werkzeug and keeps the legacy digest
untouched. Setting ``TOMS_REHASH_ON_LOGIN=True`` would upgrade a digest to
Django's PBKDF2 on successful login - it writes to the legacy table, so it is
disabled by default and requires explicit approval.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth.backends import BaseBackend
from werkzeug.security import check_password_hash

logger = logging.getLogger(__name__)


def verify_password(stored: str | None, raw_password: str | None) -> bool:
    """Verify a password against a stored digest, tolerating either format."""
    if not stored or raw_password is None:
        return False
    stored = stored.strip()
    try:
        if check_password_hash(stored, raw_password):
            return True
    except (ValueError, TypeError):
        # Malformed Werkzeug digest - fall through to the Django check.
        pass
    try:
        from django.contrib.auth.hashers import check_password as django_check

        return django_check(raw_password, stored)
    except Exception:  # pragma: no cover - defensive
        return False


class WerkzeugPasswordBackend(BaseBackend):
    """Authenticate against the legacy ``users`` table."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        from apps.accounts.models import User

        username = username or kwargs.get(User.USERNAME_FIELD)
        if not username or password is None:
            return None

        try:
            user = User.objects.get(username__iexact=username)
        except User.DoesNotExist:
            # Run a dummy hash comparison to keep timing roughly constant.
            verify_password("scrypt:32768:8:1$dummy$deadbeef", password)
            return None
        except User.MultipleObjectsReturned:  # pragma: no cover - data defect
            logger.error("Duplicate username detected for %s", username)
            return None

        if not user.is_active:
            return None
        if not verify_password(user.password_hash, password):
            return None

        if getattr(settings, "TOMS_REHASH_ON_LOGIN", False):
            self._maybe_rehash(user, password)

        return user

    def _maybe_rehash(self, user, password: str) -> None:
        """Upgrade the legacy digest. Disabled unless explicitly enabled."""
        from django.contrib.auth.hashers import identify_hasher, make_password

        try:
            identify_hasher(user.password_hash)
            return  # already a Django hash
        except Exception:
            pass
        try:
            user.password_hash = make_password(password)
            user.save(update_fields=["password_hash"])
            logger.info("Upgraded password hash for user id=%s", user.pk)
        except Exception:  # pragma: no cover
            logger.exception("Password rehash failed for user id=%s", user.pk)

    def get_user(self, user_id):
        from apps.accounts.models import User

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
        # A deactivated account must lose access immediately, including any
        # session that was already open when the administrator acted.
        if not user.is_active:
            return None
        return user
