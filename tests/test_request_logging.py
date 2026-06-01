import logging

import pytest

from app.config import settings
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

    user_request = DummyRequest("GET", "/health", "127.0.0.1", 1234, {settings.session_cookie_name: user_cookie})
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
async def test_request_logging_includes_session_label(client, caplog, scope, session_id, label):
    caplog.set_level(logging.INFO, logger="radegast.access")
    cookie = create_session_cookie(scope, session_id)
    client.cookies.set(settings.session_cookie_name, cookie)

    resp = await client.get("/health")
    assert resp.status_code == 200

    assert any(label in record.message for record in caplog.records)
