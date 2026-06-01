import secrets

import bcrypt
from itsdangerous import URLSafeTimedSerializer

from app.config import settings


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()


_serializer = URLSafeTimedSerializer(settings.secret_key)


def create_signed_token(data: dict, salt: str = "default") -> str:
    return _serializer.dumps(data, salt=salt)


def verify_signed_token(token: str, salt: str = "default", max_age: int = 86400) -> dict | None:
    try:
        return _serializer.loads(token, salt=salt, max_age=max_age)
    except Exception:
        return None


def load_signed_token_without_age(token: str, salt: str = "default") -> dict | None:
    try:
        return _serializer.loads(token, salt=salt)
    except Exception:
        return None

