"""Generate a VAPID key pair for closed-tab browser push.

Run it once per deployment and copy the three lines into the environment that
gunicorn sees (the deployment's ``.env``), then restart the service:

    venv/bin/python toms_backend/manage.py vapid_keys

The public key is safe to publish - it is handed to browsers so they can
subscribe. The private key signs each push request and must stay secret; this
command writes it to the terminal once, exactly like a password reset token.
Rotating the pair invalidates every existing subscription, so browsers simply
re-subscribe the next time an authorised person opens TOWEMS.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from django.core.management.base import BaseCommand


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class Command(BaseCommand):
    help = "Generate a VAPID public/private key pair for browser push notifications."

    def handle(self, *args, **options):
        key = ec.generate_private_key(ec.SECP256R1())
        public_raw = key.public_key().public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
        private_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        self.stdout.write(f"VAPID_PUBLIC_KEY={_b64url(public_raw)}")
        # One line, so it survives an env file without quoting games.
        self.stdout.write(
            f"VAPID_PRIVATE_KEY={base64.b64encode(private_pem).decode('ascii')}"
        )
        self.stdout.write("VAPID_SUBJECT=mailto:no-reply@mrm-system.online")
        self.stdout.write("")
        self.stdout.write(
            self.style.WARNING(
                "Store these in the deployment environment and restart the "
                "service. Keep the private key secret; rotating the pair makes "
                "every browser subscribe again."
            )
        )
