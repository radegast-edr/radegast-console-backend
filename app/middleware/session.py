import time
from datetime import datetime

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

from app.config import settings

_serializer = URLSafeTimedSerializer(settings.secret_key)


class SessionData:
    def __init__(self, scope: str, id: int, issued_at: float, mfa_level: str = "none"):
        self.scope = scope
        self.id = id
        self.issued_at = datetime.utcfromtimestamp(issued_at)
        self.mfa_level = mfa_level


def create_session_cookie(scope: str, id: int, mfa_level: str = "none") -> str:
    data = {
        "scope": scope,
        "id": id,
        "issued_at": time.time(),  # UTC Unix timestamp, not affected by local timezone
        "mfa_level": mfa_level,
    }
    return _serializer.dumps(data, salt="session")


def parse_session_cookie(cookie_value: str) -> SessionData | None:
    try:
        data = _serializer.loads(
            cookie_value, salt="session", max_age=settings.session_max_age
        )
        return SessionData(
            scope=data["scope"],
            id=data["id"],
            issued_at=data["issued_at"],
            mfa_level=data.get("mfa_level", "none"),
        )
    except (BadSignature, SignatureExpired, KeyError):
        return None
