"""Signed session cookies for the web UI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from repo_core.config import get_settings


@dataclass
class SessionData:
    user_id: str
    org_id: str
    github_user_id: int
    login: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionData:
        return cls(
            user_id=str(data["user_id"]),
            org_id=str(data["org_id"]),
            github_user_id=int(data["github_user_id"]),
            login=str(data["login"]),
        )

    @property
    def user_uuid(self) -> UUID:
        return UUID(self.user_id)

    @property
    def org_uuid(self) -> UUID:
        return UUID(self.org_id)


def _serializer() -> URLSafeTimedSerializer:
    settings = get_settings()
    return URLSafeTimedSerializer(settings.app_secret_key, salt="repo-session-v1")


def dump_session(data: SessionData) -> str:
    return _serializer().dumps(data.to_dict())


def load_session(token: str) -> SessionData | None:
    settings = get_settings()
    try:
        raw = _serializer().loads(token, max_age=settings.session_max_age_seconds)
        return SessionData.from_dict(raw)
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
        return None
