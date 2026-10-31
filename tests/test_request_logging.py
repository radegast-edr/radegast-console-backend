import pytest

from app.config import settings
from app.main import app
from app.middleware.request_logging import get_request_session_label
from app.middleware.session import create_session_cookie


class DummyRequest:
    def __init__(self, method: str, path: str, client_host: str, client_port: int, cookies: dict[str, str]):
        self.method = method
        self.url = type("U", (), {"path": path})
        self.client = type("C", (), {"host": client_host, "port": client_port})
        self.scope = {"http_version": "1.1"}
        self.cookies = cookies


def test_get_request_session_label_for_user_and_device():
    user_cookie = create_session_cookie("user", 42)
    device_cookie = create_session_cookie("device", 99)

    user_request = DummyRequest("GET", "/api/v1/health", "127.0.0.1", 1234, {settings.session_cookie_name: user_cookie})
    device_request = DummyRequest("POST", "/logs/", "127.0.0.1", 1234, {settings.session_cookie_name: device_cookie})

    assert get_request_session_label(user_request) == "[U42]"
    assert get_request_session_label(device_request) == "[D99]"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope,session_id,label",
    [
        ("user", 101, "[U101]"),
        ("device", 202, "[D202]"),
    ],
)
async def test_request_logging_includes_session_label(client, scope, session_id, label):
    # Initialize request_log_messages on app state
    app.state.request_log_messages = []

    cookie = create_session_cookie(scope, session_id)
    client.cookies.set(settings.session_cookie_name, cookie)

    try:
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200

        messages = app.state.request_log_messages
        assert any(label in msg for msg in messages), f"Label {label} not found in request log messages: {messages}"
    finally:
        if hasattr(app.state, "request_log_messages"):
            delattr(app.state, "request_log_messages")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "headers,expected",
    [
        ({"CF-Connecting-IP": "203.0.113.7"}, "203.0.113.7"),
        ({"X-Forwarded-For": "198.51.100.4, 10.89.0.2"}, "198.51.100.4"),
        ({}, None),
    ],
)
async def test_request_logging_uses_proxy_headers(client, headers, expected):
    app.state.request_log_messages = []

    try:
        resp = await client.get("/api/v1/health", headers=headers)
        assert resp.status_code == 200

        message = app.state.request_log_messages[-1]
        if expected:
            assert message.startswith(f"{expected}:")
        else:
            assert not message.startswith(("203.0.113.7", "198.51.100.4"))
    finally:
        if hasattr(app.state, "request_log_messages"):
            delattr(app.state, "request_log_messages")
