"""HTTPS REST client for the central TOWEMS armory API.

Only the refresh token is persisted, and only in the operating system's
credential vault (Windows Credential Manager through ``keyring``).  Access
tokens stay in memory.  The central application remains the authority for
personnel, mission orders and designation state; this client uploads local
accountability transactions and pulls the scoped reference snapshot.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests

from . import config

KEYRING_SERVICE = "TOWEMS Armory Management"
REQUEST_TIMEOUT = 30


class ApiError(Exception):
    def __init__(self, message: str, *, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


class AuthenticationError(ApiError):
    pass


class NetworkError(ApiError):
    pass


@dataclass
class TokenState:
    access: str = ""
    refresh: str = ""


def validate_base_url(value: str) -> str:
    value = str(value or "").strip().rstrip("/")
    if not value:
        raise ApiError("Enter the TOWEMS API address.")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ApiError("The TOWEMS API address must be a full http(s) URL.")
    host = (parsed.hostname or "").lower()
    loopback = host in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not loopback:
        raise ApiError(
            "Use HTTPS for a remote TOWEMS server. Plain HTTP is allowed only on "
            "the local development machine."
        )
    return value


def _error_message(response: requests.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"TOWEMS returned HTTP {response.status_code}."
    if isinstance(data, dict):
        detail = data.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        if detail:
            return json.dumps(detail, ensure_ascii=True)
        errors = data.get("errors")
        if isinstance(errors, dict) and errors:
            parts = []
            for field, messages in errors.items():
                if isinstance(messages, list):
                    text = "; ".join(str(item) for item in messages)
                else:
                    text = str(messages)
                parts.append(f"{field}: {text}")
            return " ".join(parts)
        pieces = []
        for field, messages in data.items():
            if field in {"code", "gaps"}:
                continue
            if isinstance(messages, list):
                pieces.append(f"{field}: {'; '.join(map(str, messages))}")
            elif isinstance(messages, str):
                pieces.append(f"{field}: {messages}")
        if pieces:
            return " ".join(pieces)
    return f"TOWEMS returned HTTP {response.status_code}."


class TowemsClient:
    def __init__(self, base_url: str, *, timeout: int = REQUEST_TIMEOUT):
        self.base_url = validate_base_url(base_url)
        self.timeout = timeout
        self.tokens = TokenState()
        self.username = ""
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": f"TOWEMS-Armory-Desktop/{config.APP_VERSION}",
            }
        )

    # -- credential storage -------------------------------------------------

    def _token_key(self) -> str:
        return f"refresh::{self.base_url}::{self.username.lower()}"

    def _save_refresh(self, token: str) -> None:
        try:
            import keyring

            keyring.set_password(KEYRING_SERVICE, self._token_key(), token)
        except Exception:
            # A locked/unavailable credential vault must not prevent the current
            # session from working; the next run simply asks for the password.
            pass

    def _load_refresh(self) -> str:
        if self.tokens.refresh:
            return self.tokens.refresh
        try:
            import keyring

            return keyring.get_password(KEYRING_SERVICE, self._token_key()) or ""
        except Exception:
            return ""

    def clear_credentials(self) -> None:
        self.tokens = TokenState()
        try:
            import keyring

            keyring.delete_password(KEYRING_SERVICE, self._token_key())
        except Exception:
            pass

    # -- authentication -----------------------------------------------------

    def login(self, username: str, password: str) -> None:
        self.username = str(username or "").strip()
        if not self.username or not password:
            raise AuthenticationError("Enter the TOWEMS username and password.")
        response = self.session.post(
            f"{self.base_url}/auth/token/",
            json={"username": self.username, "password": password},
            timeout=self.timeout,
        )
        if response.status_code == 403:
            raise AuthenticationError(_error_message(response), status=403)
        if response.status_code == 401:
            raise AuthenticationError("Invalid TOWEMS username or password.", status=401)
        if not response.ok:
            raise ApiError(
                _error_message(response), status=response.status_code, payload=response
            )
        data = response.json()
        access = str(data.get("access") or "")
        refresh = str(data.get("refresh") or "")
        if not access or not refresh:
            raise AuthenticationError("TOWEMS did not return an API token.")
        self.tokens = TokenState(access=access, refresh=refresh)
        self._save_refresh(refresh)

    def restore_session(self, username: str) -> bool:
        self.username = str(username or "").strip()
        refresh = self._load_refresh()
        if not refresh:
            return False
        self.tokens = TokenState(refresh=refresh)
        try:
            self.refresh()
        except AuthenticationError:
            return False
        return True

    def refresh(self) -> None:
        if not self.tokens.refresh:
            raise AuthenticationError("A TOWEMS sign-in is required.")
        response = self.session.post(
            f"{self.base_url}/auth/token/refresh/",
            json={"refresh": self.tokens.refresh},
            timeout=self.timeout,
        )
        if response.status_code in {401, 403}:
            self.tokens.access = ""
            raise AuthenticationError("The saved TOWEMS session has expired.", status=response.status_code)
        if not response.ok:
            raise ApiError(
                _error_message(response), status=response.status_code, payload=response
            )
        data = response.json()
        self.tokens.access = str(data.get("access") or "")
        if data.get("refresh"):
            self.tokens.refresh = str(data["refresh"])
            self._save_refresh(self.tokens.refresh)

    # -- transport ----------------------------------------------------------

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.tokens.access}"} if self.tokens.access else {}

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
        retry_auth: bool = True,
    ) -> Any:
        if not self.tokens.access:
            self.refresh()
        url = f"{self.base_url}/{str(path).lstrip('/')}"
        try:
            response = self.session.request(
                method,
                url,
                json=json_body,
                params=params,
                headers=self._headers(),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise NetworkError(f"Cannot reach TOWEMS: {exc}") from exc
        if response.status_code == 401 and retry_auth and self.tokens.refresh:
            self.refresh()
            try:
                response = self.session.request(
                    method,
                    url,
                    json=json_body,
                    params=params,
                    headers=self._headers(),
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                raise NetworkError(f"Cannot reach TOWEMS: {exc}") from exc
        if not response.ok:
            message = _error_message(response)
            if response.status_code == 401:
                raise AuthenticationError(message, status=response.status_code)
            raise ApiError(message, status=response.status_code, payload=response)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise ApiError("TOWEMS returned an unexpected, non-JSON response.") from exc

    # -- central reference --------------------------------------------------

    def snapshot(self) -> dict:
        data = self.request("GET", "armory/sync/snapshot/")
        if not isinstance(data, dict) or data.get("schema") != 1:
            raise ApiError("TOWEMS returned an unsupported synchronization snapshot.")
        return data

    def create_firearm(self, payload: dict) -> dict:
        return self.request("POST", "armory/firearms/", json_body=payload)

    def create_ammunition(self, payload: dict) -> dict:
        return self.request("POST", "armory/ammunition/", json_body=payload)

    def assignments(self, personnel_remote_id: int, active: str = "all") -> list[dict]:
        data = self.request(
            "GET",
            "armory/assignments/",
            params={"personnel": personnel_remote_id, "active": active, "page_size": 200},
        )
        if isinstance(data, dict):
            return list(data.get("results") or [])
        return list(data or [])

    def assign(
        self,
        *,
        personnel: int,
        client_uuid: str,
        rifle_firearm_id: str = "",
        pistol_firearm_id: str = "",
        notes: str = "",
    ) -> list[dict]:
        data = self.request(
            "POST",
            "armory/assignments/assign/",
            json_body={
                "personnel": personnel,
                "client_uuid": client_uuid,
                "rifle_firearm_id": rifle_firearm_id,
                "pistol_firearm_id": pistol_firearm_id,
                "notes": notes,
            },
        )
        return list(data or [])

    def release_assignment(self, remote_id: int, reason: str = "") -> dict:
        return self.request(
            "POST",
            f"armory/assignments/{int(remote_id)}/release/",
            json_body={"reason": reason or "Released by offline armory"},
        )

    def issue(self, payload: dict) -> dict:
        return self.request("POST", "armory/issuances/", json_body=payload)

    def return_firearms(self, payload: dict) -> dict:
        return self.request("POST", "armory/returns/", json_body=payload)

    def inspect_firearm(self, remote_id: int, payload: dict) -> dict:
        return self.request(
            "POST", f"armory/firearms/{int(remote_id)}/inspect/", json_body=payload
        )

    def adjust_ammunition(self, remote_id: int, payload: dict) -> dict:
        return self.request(
            "POST", f"armory/ammunition/{int(remote_id)}/adjust/", json_body=payload
        )
