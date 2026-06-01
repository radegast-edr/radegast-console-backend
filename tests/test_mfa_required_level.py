"""
Tests for MFA required-level enforcement:
- Login only offers methods that satisfy the required level
- Disabling OTP or deleting the last hardware token is blocked when it would
  leave the user below their required MFA level
"""
import pytest
import pyotp
from unittest.mock import MagicMock, patch
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.models.user import User, UserRole
from app.services.auth import hash_password


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _create_user(db_engine, email: str, password: str, role: UserRole = UserRole.user) -> User:
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        user = User(
            email=email,
            password=hash_password(password),
            role=role,
            verified=True,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _register_mock_hardware_token(client: AsyncClient, name: str = "Test Key") -> int:
    """Register a hardware token with a mocked WebAuthn verification. Returns token DB id."""
    resp = await client.post("/auth/mfa/hardware-token/setup")
    assert resp.status_code == 200
    reg_token = resp.json()["registration_token"]

    mock_verification = MagicMock()
    mock_verification.credential_id = b"mock_credential_id_" + name.encode()
    mock_verification.credential_public_key = b"mock_public_key"
    mock_verification.sign_count = 1

    with patch("webauthn.verify_registration_response", return_value=mock_verification):
        resp = await client.post(
            "/auth/mfa/hardware-token/verify",
            json={
                "registration_token": reg_token,
                "credential_response": {"id": "mock_id"},
                "name": name,
            },
        )
        assert resp.status_code == 200

    resp = await client.get("/auth/mfa/settings")
    assert resp.status_code == 200
    tokens = resp.json()["hardware_tokens"]
    return next(t["id"] for t in tokens if t["name"] == name)


async def _enable_otp(client: AsyncClient) -> str:
    """Set up and enable OTP. Returns the TOTP secret."""
    resp = await client.post("/auth/mfa/otp/setup")
    assert resp.status_code == 200
    secret = resp.json()["secret"]
    totp = pyotp.TOTP(secret)
    resp = await client.post("/auth/mfa/otp/verify", json={"code": totp.now()})
    assert resp.status_code == 200
    return secret


# ===========================================================================
# Login method filtering
# ===========================================================================

class TestLoginMethodFiltering:
    """When required_level is hardware_token, OTP must not appear in methods."""

    @pytest.mark.asyncio
    async def test_admin_with_hardware_token_required_only_sees_hardware_token_in_login(
        self, client: AsyncClient, db_engine
    ):
        original = settings.mfa_required_level_admin
        settings.mfa_required_level_admin = "hardware_token"
        try:
            email, password = "filter_hw@example.com", "Password123!"
            await _create_user(db_engine, email, password, UserRole.admin)

            # Log in to get a session (no MFA configured yet → bypass)
            resp = await client.post("/auth/login", json={"email": email, "password": password})
            assert resp.status_code == 200

            # Register both OTP and a hardware token
            await _enable_otp(client)
            await _register_mock_hardware_token(client, "Admin Key")

            # Log out then log back in
            await client.post("/auth/logout")
            resp = await client.post("/auth/login", json={"email": email, "password": password})
            assert resp.status_code == 200
            data = resp.json()

            assert data["status"] == "mfa_required"
            assert "hardware_token" in data["methods"]
            # OTP must NOT be offered because hardware_token is required
            assert "otp" not in data["methods"]
        finally:
            settings.mfa_required_level_admin = original

    @pytest.mark.asyncio
    async def test_user_with_otp_required_sees_otp_and_hardware_token_in_login(
        self, client: AsyncClient, db_engine
    ):
        original = settings.mfa_required_level_user
        settings.mfa_required_level_user = "otp"
        try:
            email, password = "filter_otp@example.com", "Password123!"
            await _create_user(db_engine, email, password, UserRole.user)

            resp = await client.post("/auth/login", json={"email": email, "password": password})
            assert resp.status_code == 200

            # Register both OTP and a hardware token
            await _enable_otp(client)
            await _register_mock_hardware_token(client, "User Key")

            await client.post("/auth/logout")
            resp = await client.post("/auth/login", json={"email": email, "password": password})
            assert resp.status_code == 200
            data = resp.json()

            assert data["status"] == "mfa_required"
            # When only "otp" is required, both methods should be available
            assert "otp" in data["methods"]
            assert "hardware_token" in data["methods"]
        finally:
            settings.mfa_required_level_user = original

    @pytest.mark.asyncio
    async def test_admin_with_only_otp_and_hardware_token_required_sees_only_hardware_token(
        self, client: AsyncClient, db_engine
    ):
        """Admin has OTP but no hardware token, and required level is hardware_token.
        OTP should still not appear (it can't satisfy the requirement on its own)."""
        original = settings.mfa_required_level_admin
        settings.mfa_required_level_admin = "hardware_token"
        try:
            email, password = "filter_otp_only@example.com", "Password123!"
            await _create_user(db_engine, email, password, UserRole.admin)

            resp = await client.post("/auth/login", json={"email": email, "password": password})
            assert resp.status_code == 200

            # Only OTP, no hardware token
            await _enable_otp(client)

            await client.post("/auth/logout")
            resp = await client.post("/auth/login", json={"email": email, "password": password})
            assert resp.status_code == 200
            data = resp.json()

            assert data["status"] == "mfa_required"
            assert "otp" not in data["methods"]
            assert "hardware_token" not in data["methods"]
            # methods should be empty — user hasn't set up the required factor yet
            assert data["methods"] == []
        finally:
            settings.mfa_required_level_admin = original


# ===========================================================================
# OTP disable protection
# ===========================================================================

class TestOtpDisableProtection:
    """Cannot disable OTP when it is the only factor satisfying the required level."""

    @pytest.mark.asyncio
    async def test_cannot_disable_otp_when_required_and_no_hardware_token(
        self, client: AsyncClient, db_engine
    ):
        original = settings.mfa_required_level_user
        settings.mfa_required_level_user = "otp"
        try:
            email, password = "nodisable_otp@example.com", "Password123!"
            await _create_user(db_engine, email, password, UserRole.user)

            resp = await client.post("/auth/login", json={"email": email, "password": password})
            assert resp.status_code == 200

            await _enable_otp(client)

            # No hardware token — disabling OTP should be blocked
            resp = await client.post("/auth/mfa/otp/disable")
            assert resp.status_code == 400
            assert "requires" in resp.json()["detail"].lower()
        finally:
            settings.mfa_required_level_user = original

    @pytest.mark.asyncio
    async def test_can_disable_otp_when_hardware_token_satisfies_requirement(
        self, client: AsyncClient, db_engine
    ):
        original = settings.mfa_required_level_user
        settings.mfa_required_level_user = "otp"
        try:
            email, password = "disable_otp_ok@example.com", "Password123!"
            await _create_user(db_engine, email, password, UserRole.user)

            resp = await client.post("/auth/login", json={"email": email, "password": password})
            assert resp.status_code == 200

            await _enable_otp(client)
            await _register_mock_hardware_token(client, "Backup Key")

            # Hardware token satisfies "otp" requirement — disabling OTP is OK
            resp = await client.post("/auth/mfa/otp/disable")
            assert resp.status_code == 200
        finally:
            settings.mfa_required_level_user = original

    @pytest.mark.asyncio
    async def test_can_disable_otp_when_hardware_token_required_and_token_present(
        self, client: AsyncClient, db_engine
    ):
        """hardware_token requirement: OTP is irrelevant, so disabling it is always fine
        as long as the user still has a hardware token."""
        original = settings.mfa_required_level_admin
        settings.mfa_required_level_admin = "hardware_token"
        try:
            email, password = "disable_otp_hw@example.com", "Password123!"
            await _create_user(db_engine, email, password, UserRole.admin)

            resp = await client.post("/auth/login", json={"email": email, "password": password})
            assert resp.status_code == 200

            await _enable_otp(client)
            await _register_mock_hardware_token(client, "Required Key")

            # Hardware token satisfies the "hardware_token" requirement — disabling OTP is fine
            resp = await client.post("/auth/mfa/otp/disable")
            assert resp.status_code == 200
        finally:
            settings.mfa_required_level_admin = original

    @pytest.mark.asyncio
    async def test_cannot_disable_otp_when_hardware_token_required_and_no_token(
        self, client: AsyncClient, db_engine
    ):
        """hardware_token required, user only has OTP → cannot disable OTP."""
        original = settings.mfa_required_level_admin
        settings.mfa_required_level_admin = "hardware_token"
        try:
            email, password = "nodisable_otp_hw@example.com", "Password123!"
            await _create_user(db_engine, email, password, UserRole.admin)

            resp = await client.post("/auth/login", json={"email": email, "password": password})
            assert resp.status_code == 200

            await _enable_otp(client)

            resp = await client.post("/auth/mfa/otp/disable")
            assert resp.status_code == 400
        finally:
            settings.mfa_required_level_admin = original

    @pytest.mark.asyncio
    async def test_can_disable_otp_when_no_mfa_required(
        self, client: AsyncClient, db_engine
    ):
        original = settings.mfa_required_level_user
        settings.mfa_required_level_user = "none"
        try:
            email, password = "disable_otp_free@example.com", "Password123!"
            await _create_user(db_engine, email, password, UserRole.user)

            resp = await client.post("/auth/login", json={"email": email, "password": password})
            assert resp.status_code == 200

            await _enable_otp(client)

            resp = await client.post("/auth/mfa/otp/disable")
            assert resp.status_code == 200
        finally:
            settings.mfa_required_level_user = original


# ===========================================================================
# Hardware token delete protection
# ===========================================================================

class TestHardwareTokenDeleteProtection:
    """Cannot delete the last hardware token when hardware_token level is required."""

    @pytest.mark.asyncio
    async def test_cannot_delete_last_hardware_token_when_required(
        self, client: AsyncClient, db_engine
    ):
        original = settings.mfa_required_level_admin
        settings.mfa_required_level_admin = "hardware_token"
        try:
            email, password = "nodelete_hw@example.com", "Password123!"
            await _create_user(db_engine, email, password, UserRole.admin)

            resp = await client.post("/auth/login", json={"email": email, "password": password})
            assert resp.status_code == 200

            token_id = await _register_mock_hardware_token(client, "Only Key")

            resp = await client.delete(f"/auth/mfa/hardware-token/{token_id}")
            assert resp.status_code == 400
            assert "last" in resp.json()["detail"].lower()
        finally:
            settings.mfa_required_level_admin = original

    @pytest.mark.asyncio
    async def test_can_delete_non_last_hardware_token_when_required(
        self, client: AsyncClient, db_engine
    ):
        original = settings.mfa_required_level_admin
        settings.mfa_required_level_admin = "hardware_token"
        try:
            email, password = "delete_hw_ok@example.com", "Password123!"
            await _create_user(db_engine, email, password, UserRole.admin)

            resp = await client.post("/auth/login", json={"email": email, "password": password})
            assert resp.status_code == 200

            token_id_1 = await _register_mock_hardware_token(client, "Key One")
            await _register_mock_hardware_token(client, "Key Two")

            # Deleting the first token is fine — one still remains
            resp = await client.delete(f"/auth/mfa/hardware-token/{token_id_1}")
            assert resp.status_code == 200
        finally:
            settings.mfa_required_level_admin = original

    @pytest.mark.asyncio
    async def test_can_delete_last_hardware_token_when_not_required(
        self, client: AsyncClient, db_engine
    ):
        original = settings.mfa_required_level_user
        settings.mfa_required_level_user = "none"
        try:
            email, password = "delete_hw_free@example.com", "Password123!"
            await _create_user(db_engine, email, password, UserRole.user)

            resp = await client.post("/auth/login", json={"email": email, "password": password})
            assert resp.status_code == 200

            token_id = await _register_mock_hardware_token(client, "Free Key")

            resp = await client.delete(f"/auth/mfa/hardware-token/{token_id}")
            assert resp.status_code == 200
        finally:
            settings.mfa_required_level_user = original

    @pytest.mark.asyncio
    async def test_cannot_delete_last_hardware_token_when_otp_required_and_otp_disabled(
        self, client: AsyncClient, db_engine
    ):
        """otp required, user has hardware token but no OTP → deleting last token leaves
        them with no satisfying factor."""
        original = settings.mfa_required_level_user
        settings.mfa_required_level_user = "otp"
        try:
            email, password = "nodelete_hw_otp@example.com", "Password123!"
            await _create_user(db_engine, email, password, UserRole.user)

            resp = await client.post("/auth/login", json={"email": email, "password": password})
            assert resp.status_code == 200

            token_id = await _register_mock_hardware_token(client, "Solo Key")
            # No OTP enabled

            resp = await client.delete(f"/auth/mfa/hardware-token/{token_id}")
            assert resp.status_code == 400
            assert "otp" in resp.json()["detail"].lower()
        finally:
            settings.mfa_required_level_user = original

    @pytest.mark.asyncio
    async def test_can_delete_last_hardware_token_when_otp_required_and_otp_enabled(
        self, client: AsyncClient, db_engine
    ):
        """otp required, user has both hardware token AND OTP → deleting hardware token
        is fine because OTP still satisfies the requirement."""
        original = settings.mfa_required_level_user
        settings.mfa_required_level_user = "otp"
        try:
            email, password = "delete_hw_otp_ok@example.com", "Password123!"
            await _create_user(db_engine, email, password, UserRole.user)

            resp = await client.post("/auth/login", json={"email": email, "password": password})
            assert resp.status_code == 200

            await _enable_otp(client)
            token_id = await _register_mock_hardware_token(client, "Redundant Key")

            # OTP is enabled — deleting the hardware token is allowed
            resp = await client.delete(f"/auth/mfa/hardware-token/{token_id}")
            assert resp.status_code == 200
        finally:
            settings.mfa_required_level_user = original
