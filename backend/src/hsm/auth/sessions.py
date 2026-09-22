"""Opaque, hashed browser sessions with status and expiry enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import secrets

from hsm.db import connect


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class AuthenticatedUser:
    id: int
    email: str
    role: str


class SessionService:
    def __init__(self, database_path: Path, *, idle_seconds: int, absolute_seconds: int) -> None:
        self._database_path = database_path
        self._idle_seconds = idle_seconds
        self._absolute_seconds = absolute_seconds

    def create(self, user_id: int, *, now: datetime | None = None) -> str:
        issued_at = now or _utc_now()
        token = secrets.token_urlsafe(32)
        connection = connect(self._database_path)
        try:
            with connection:
                connection.execute(
                    """INSERT INTO sessions(token_hash, user_id, created_at, last_seen_at, expires_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (token_hash(token), user_id, _timestamp(issued_at), _timestamp(issued_at),
                     _timestamp(issued_at + timedelta(seconds=self._absolute_seconds))),
                )
        finally:
            connection.close()
        return token

    def resolve(self, token: str | None, *, now: datetime | None = None) -> AuthenticatedUser | None:
        if not token or len(token) > 512:
            return None
        observed = now or _utc_now()
        connection = connect(self._database_path)
        try:
            row = connection.execute(
                """SELECT s.user_id, s.last_seen_at, s.expires_at, u.email, u.role
                   FROM sessions AS s JOIN users AS u ON u.id = s.user_id
                   WHERE s.token_hash = ? AND u.status = 'active'""",
                (token_hash(token),),
            ).fetchone()
            if row is None:
                return None
            last_seen = datetime.fromisoformat(row[1])
            expires_at = datetime.fromisoformat(row[2])
            if observed - last_seen > timedelta(seconds=self._idle_seconds) or observed >= expires_at:
                with connection:
                    connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))
                return None
            with connection:
                connection.execute(
                    "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?",
                    (_timestamp(observed), token_hash(token)),
                )
            return AuthenticatedUser(row[0], row[3], row[4])
        finally:
            connection.close()

    def revoke(self, token: str | None) -> None:
        if not token:
            return
        connection = connect(self._database_path)
        try:
            with connection:
                connection.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))
        finally:
            connection.close()
