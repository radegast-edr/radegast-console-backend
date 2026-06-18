"""Tests for WebAuthn hardware-token RP-ID and origin handling across domain configurations.

Scenarios covered:
 - Unit tests for _normalize_origin, _configured_webauthn_origins, _resolve_webauthn_rp_id
 - RADEGAST_WEBAUTHN_RP_ID overrides all other resolution
 - Request Origin header drives RP ID when it is in the allowed-origin list
 - Unknown / unlisted origins fall back to the base_url hostname
 - RP ID is stored in registration and assertion signed tokens
 - verify_registration_response and verify_authentication_response are called with the
   RP ID that came from the signed token (not re-derived from the request)
 - Full split-domain registration-and-login flow (API on api.radegast.app,
   web on console.radegast.app, RP ID pinned to parent radegast.app)
"""
import base64
from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient

from app.config import settings
from app.routers.auth import (
    _configured_webauthn_origins,
    _normalize_origin,
    _resolve_webauthn_rp_id,
)
from app.services.auth import verify_signed_token

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _mock_request(origin: str) -> MagicMock:
    req = MagicMock()
    req.headers.get.return_value = origin
    return req


# ---------------------------------------------------------------------------
# Unit: _normalize_origin
# ---------------------------------------------------------------------------

class TestNormalizeOrigin:
    def test_https_plain(self):
        assert _normalize_origin("https://console.radegast.app") == "https://console.radegast.app"

    def test_http_with_port(self):
        assert _normalize_origin("http://localhost:5173") == "http://localhost:5173"

    def test_trailing_slash_stripped(self):
        assert _normalize_origin("https://example.com/") == "https://example.com"

    def test_empty_string_returns_none(self):
        assert _normalize_origin("") is None

    def test_invalid_scheme_returns_none(self):
        assert _normalize_origin("ftp://example.com") is None

    def test_bare_hostname_returns_none(self):
        assert _normalize_origin("example.com") is None

    def test_whitespace_stripped(self):
        assert _normalize_origin("  https://example.com  ") == "https://example.com"


# ---------------------------------------------------------------------------
# Unit: _configured_webauthn_origins
# ---------------------------------------------------------------------------

class TestConfiguredWebauthnOrigins:
    def test_includes_base_url(self):
        orig = settings.base_url
        settings.base_url = "https://api.radegast.app"
        try:
            assert "https://api.radegast.app" in _configured_webauthn_origins()
        finally:
            settings.base_url = orig

    def test_includes_cors_origins(self):
        orig_base = settings.base_url
        orig_cors = settings.cors_origins
        settings.base_url = "https://api.radegast.app"
        settings.cors_origins = "https://console.radegast.app,https://other.radegast.app"
        try:
            origins = _configured_webauthn_origins()
            assert "https://console.radegast.app" in origins
            assert "https://other.radegast.app" in origins
        finally:
            settings.base_url = orig_base
            settings.cors_origins = orig_cors

    def test_includes_webauthn_origins_setting(self):
        orig = settings.webauthn_origins
        settings.webauthn_origins = "https://extra.radegast.app"
        try:
            assert "https://extra.radegast.app" in _configured_webauthn_origins()
        finally:
            settings.webauthn_origins = orig

    def test_no_duplicates(self):
        orig_base = settings.base_url
        orig_cors = settings.cors_origins
        settings.base_url = "https://api.radegast.app"
        settings.cors_origins = "https://api.radegast.app,https://api.radegast.app"
        try:
            origins = _configured_webauthn_origins()
            assert origins.count("https://api.radegast.app") == 1
        finally:
            settings.base_url = orig_base
            settings.cors_origins = orig_cors

    def test_invalid_entries_skipped(self):
        orig = settings.cors_origins
        settings.cors_origins = "not-a-url,ftp://bad.com,https://good.radegast.app"
        try:
            origins = _configured_webauthn_origins()
            assert "not-a-url" not in origins
            assert "ftp://bad.com" not in origins
            assert "https://good.radegast.app" in origins
        finally:
            settings.cors_origins = orig


# ---------------------------------------------------------------------------
# Unit: _resolve_webauthn_rp_id
# ---------------------------------------------------------------------------

class TestResolveWebauthnRpId:
    def test_explicit_config_wins_over_request_origin(self):
        orig_rp = settings.webauthn_rp_id
        orig_base = settings.base_url
        orig_cors = settings.cors_origins
        settings.webauthn_rp_id = "radegast.app"
        settings.base_url = "https://api.radegast.app"
        settings.cors_origins = "https://console.radegast.app"
        try:
            assert _resolve_webauthn_rp_id(None) == "radegast.app"
            assert _resolve_webauthn_rp_id(_mock_request("https://console.radegast.app")) == "radegast.app"
        finally:
            settings.webauthn_rp_id = orig_rp
            settings.base_url = orig_base
            settings.cors_origins = orig_cors

    def test_request_origin_used_when_in_allowed_origins(self):
        orig_rp = settings.webauthn_rp_id
        orig_base = settings.base_url
        orig_cors = settings.cors_origins
        settings.webauthn_rp_id = None
        settings.base_url = "https://api.radegast.app"
        settings.cors_origins = "https://console.radegast.app"
        try:
            result = _resolve_webauthn_rp_id(_mock_request("https://console.radegast.app"))
            assert result == "console.radegast.app"
        finally:
            settings.webauthn_rp_id = orig_rp
            settings.base_url = orig_base
            settings.cors_origins = orig_cors

    def test_unknown_origin_falls_back_to_base_url(self):
        orig_rp = settings.webauthn_rp_id
        orig_base = settings.base_url
        orig_cors = settings.cors_origins
        settings.webauthn_rp_id = None
        settings.base_url = "https://api.radegast.app"
        settings.cors_origins = "https://console.radegast.app"
        try:
            result = _resolve_webauthn_rp_id(_mock_request("https://attacker.evil.com"))
            assert result == "api.radegast.app"
        finally:
            settings.webauthn_rp_id = orig_rp
            settings.base_url = orig_base
            settings.cors_origins = orig_cors

    def test_no_request_falls_back_to_base_url(self):
        orig_rp = settings.webauthn_rp_id
        orig_base = settings.base_url
        settings.webauthn_rp_id = None
        settings.base_url = "https://api.radegast.app"
        try:
            assert _resolve_webauthn_rp_id(None) == "api.radegast.app"
        finally:
            settings.webauthn_rp_id = orig_rp
            settings.base_url = orig_base


# ---------------------------------------------------------------------------
# Integration: registration options carry the configured RP ID
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_registration_options_embed_configured_rp_id(
    client: AsyncClient, registered_user
):
    """Setup response includes configured RP ID in options JSON, and the signed
    registration_token also embeds it so the verify endpoint uses it later."""
    orig_rp = settings.webauthn_rp_id
    orig_cors = settings.cors_origins
    settings.webauthn_rp_id = "radegast.app"
    settings.cors_origins = "https://console.radegast.app"
    try:
        await client.post("/auth/login", json=registered_user)
        resp = await client.post(
            "/user/mfa/hardware-token/setup",
            headers={"Origin": "https://console.radegast.app"},
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["options"]["rp"]["id"] == "radegast.app"

        token_payload = verify_signed_token(
            data["registration_token"], salt="hardware-token-register", max_age=300
        )
        assert token_payload is not None
        assert token_payload["rp_id"] == "radegast.app"
    finally:
        settings.webauthn_rp_id = orig_rp
        settings.cors_origins = orig_cors


# ---------------------------------------------------------------------------
# Integration: verify_registration_response receives correct args
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_registration_verify_passes_correct_rp_id_and_origins(
    client: AsyncClient, registered_user
):
    """verify_registration_response is called with the RP ID from the signed token
    and the allowed origins from settings, not hardcoded test values."""
    orig_rp = settings.webauthn_rp_id
    orig_cors = settings.cors_origins
    settings.webauthn_rp_id = "radegast.app"
    settings.cors_origins = "https://console.radegast.app"
    try:
        await client.post("/auth/login", json=registered_user)
        resp = await client.post(
            "/user/mfa/hardware-token/setup",
            headers={"Origin": "https://console.radegast.app"},
        )
        registration_token = resp.json()["registration_token"]

        mock_reg = MagicMock()
        mock_reg.credential_id = b"cred_verify_test"
        mock_reg.credential_public_key = b"pubkey_verify_test"
        mock_reg.sign_count = 1

        with patch("webauthn.verify_registration_response", return_value=mock_reg) as mock_vrr:
            await client.post(
                "/user/mfa/hardware-token/verify",
                headers={"Origin": "https://console.radegast.app"},
                json={
                    "registration_token": registration_token,
                    "credential_response": {"id": _b64url(b"cred_verify_test")},
                },
            )
            _, kwargs = mock_vrr.call_args
            assert kwargs["expected_rp_id"] == "radegast.app"
            assert "https://console.radegast.app" in kwargs["expected_origin"]
    finally:
        settings.webauthn_rp_id = orig_rp
        settings.cors_origins = orig_cors


# ---------------------------------------------------------------------------
# Integration: assertion options carry the configured RP ID
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_assertion_options_embed_configured_rp_id(
    client: AsyncClient, registered_user
):
    """Assertion options JSON and signed assertion_token both carry the configured RP ID."""
    orig_rp = settings.webauthn_rp_id
    orig_cors = settings.cors_origins
    settings.webauthn_rp_id = "radegast.app"
    settings.cors_origins = "https://console.radegast.app"
    try:
        await client.post("/auth/login", json=registered_user)
        resp = await client.post(
            "/user/mfa/hardware-token/setup",
            headers={"Origin": "https://console.radegast.app"},
        )
        registration_token = resp.json()["registration_token"]

        mock_reg = MagicMock()
        mock_reg.credential_id = b"cred_assert_opt_test"
        mock_reg.credential_public_key = b"pubkey_assert_opt_test"
        mock_reg.sign_count = 0

        with patch("webauthn.verify_registration_response", return_value=mock_reg):
            await client.post(
                "/user/mfa/hardware-token/verify",
                headers={"Origin": "https://console.radegast.app"},
                json={
                    "registration_token": registration_token,
                    "credential_response": {"id": _b64url(b"cred_assert_opt_test")},
                },
            )

        await client.post("/auth/logout")
        resp = await client.post("/auth/login", json=registered_user)
        mfa_token = resp.json()["mfa_token"]

        resp = await client.post(
            "/auth/mfa/hardware-token/assertion-options",
            headers={"Origin": "https://console.radegast.app"},
            json={"mfa_token": mfa_token},
        )
        assert resp.status_code == 200
        assert_data = resp.json()

        assert assert_data["options"]["rpId"] == "radegast.app"

        token_payload = verify_signed_token(
            assert_data["assertion_token"], salt="hardware-token-login", max_age=300
        )
        assert token_payload is not None
        assert token_payload["rp_id"] == "radegast.app"
    finally:
        settings.webauthn_rp_id = orig_rp
        settings.cors_origins = orig_cors


# ---------------------------------------------------------------------------
# Integration: verify_authentication_response receives correct args
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_login_verify_passes_rp_id_from_assertion_token(
    client: AsyncClient, registered_user
):
    """verify_authentication_response is called with the RP ID stored in the
    assertion token, and the allowed origins list from settings."""
    orig_rp = settings.webauthn_rp_id
    orig_cors = settings.cors_origins
    settings.webauthn_rp_id = "radegast.app"
    settings.cors_origins = "https://console.radegast.app"
    try:
        await client.post("/auth/login", json=registered_user)
        resp = await client.post(
            "/user/mfa/hardware-token/setup",
            headers={"Origin": "https://console.radegast.app"},
        )
        registration_token = resp.json()["registration_token"]

        mock_reg = MagicMock()
        mock_reg.credential_id = b"cred_login_verify"
        mock_reg.credential_public_key = b"pubkey_login_verify"
        mock_reg.sign_count = 0

        with patch("webauthn.verify_registration_response", return_value=mock_reg):
            await client.post(
                "/user/mfa/hardware-token/verify",
                headers={"Origin": "https://console.radegast.app"},
                json={
                    "registration_token": registration_token,
                    "credential_response": {"id": _b64url(b"cred_login_verify")},
                },
            )

        await client.post("/auth/logout")
        resp = await client.post("/auth/login", json=registered_user)
        mfa_token = resp.json()["mfa_token"]

        resp = await client.post(
            "/auth/mfa/hardware-token/assertion-options",
            headers={"Origin": "https://console.radegast.app"},
            json={"mfa_token": mfa_token},
        )
        assertion_token = resp.json()["assertion_token"]

        mock_auth = MagicMock()
        mock_auth.new_sign_count = 1

        with patch("webauthn.verify_authentication_response", return_value=mock_auth) as mock_var:
            resp = await client.post(
                "/auth/mfa/verify",
                headers={"Origin": "https://console.radegast.app"},
                json={
                    "mfa_token": mfa_token,
                    "method": "hardware_token",
                    "assertion_token": assertion_token,
                    "webauthn_response": {"id": _b64url(b"cred_login_verify")},
                },
            )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Login successful"

        _, kwargs = mock_var.call_args
        assert kwargs["expected_rp_id"] == "radegast.app"
        assert "https://console.radegast.app" in kwargs["expected_origin"]
    finally:
        settings.webauthn_rp_id = orig_rp
        settings.cors_origins = orig_cors


# ---------------------------------------------------------------------------
# Integration: full split-domain end-to-end flow
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_split_domain_flow(client: AsyncClient, registered_user):
    """Complete registration-and-login flow: API on api.radegast.app,
    web UI on console.radegast.app, RP ID pinned to parent radegast.app."""
    orig_rp = settings.webauthn_rp_id
    orig_base = settings.base_url
    orig_cors = settings.cors_origins
    settings.webauthn_rp_id = "radegast.app"
    settings.base_url = "https://api.radegast.app"
    settings.cors_origins = "https://console.radegast.app"
    try:
        # ── registration (browser on console.radegast.app) ─────────────────
        await client.post("/auth/login", json=registered_user)

        resp = await client.post(
            "/user/mfa/hardware-token/setup",
            headers={"Origin": "https://console.radegast.app"},
        )
        assert resp.status_code == 200
        assert resp.json()["options"]["rp"]["id"] == "radegast.app"
        registration_token = resp.json()["registration_token"]

        mock_reg = MagicMock()
        mock_reg.credential_id = b"split_cred"
        mock_reg.credential_public_key = b"split_pubkey"
        mock_reg.sign_count = 0

        with patch("webauthn.verify_registration_response", return_value=mock_reg) as mock_vrr:
            resp = await client.post(
                "/user/mfa/hardware-token/verify",
                headers={"Origin": "https://console.radegast.app"},
                json={
                    "registration_token": registration_token,
                    "credential_response": {"id": _b64url(b"split_cred")},
                    "name": "YubiKey",
                },
            )
        assert resp.status_code == 200
        _, reg_kwargs = mock_vrr.call_args
        assert reg_kwargs["expected_rp_id"] == "radegast.app"
        assert "https://console.radegast.app" in reg_kwargs["expected_origin"]

        # ── login (browser on console.radegast.app) ────────────────────────
        await client.post("/auth/logout")
        resp = await client.post("/auth/login", json=registered_user)
        assert resp.json()["status"] == "mfa_required"
        mfa_token = resp.json()["mfa_token"]

        resp = await client.post(
            "/auth/mfa/hardware-token/assertion-options",
            headers={"Origin": "https://console.radegast.app"},
            json={"mfa_token": mfa_token},
        )
        assert resp.status_code == 200
        assert resp.json()["options"]["rpId"] == "radegast.app"
        assertion_token = resp.json()["assertion_token"]

        mock_auth = MagicMock()
        mock_auth.new_sign_count = 1

        with patch("webauthn.verify_authentication_response", return_value=mock_auth) as mock_var:
            resp = await client.post(
                "/auth/mfa/verify",
                headers={"Origin": "https://console.radegast.app"},
                json={
                    "mfa_token": mfa_token,
                    "method": "hardware_token",
                    "assertion_token": assertion_token,
                    "webauthn_response": {"id": _b64url(b"split_cred")},
                },
            )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Login successful"

        _, auth_kwargs = mock_var.call_args
        assert auth_kwargs["expected_rp_id"] == "radegast.app"
        assert "https://console.radegast.app" in auth_kwargs["expected_origin"]
    finally:
        settings.webauthn_rp_id = orig_rp
        settings.base_url = orig_base
        settings.cors_origins = orig_cors
