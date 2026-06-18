from unittest.mock import MagicMock, patch

import pyotp
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.hardware_token import HardwareToken
from app.models.user import User, UserRole
from app.services.auth import hash_password


@pytest.mark.asyncio
async def test_mfa_otp_setup_and_verify(client: AsyncClient, registered_user):
    # Log in
    resp = await client.post("/auth/login", json=registered_user)
    assert resp.status_code == 200

    # Initiate OTP setup
    resp = await client.post("/user/mfa/otp/setup")
    assert resp.status_code == 200
    data = resp.json()
    assert "secret" in data
    assert "provisioning_uri" in data
    secret = data["secret"]

    # Verify invalid code
    resp = await client.post("/user/mfa/otp/verify", json={"code": "000000"})
    assert resp.status_code == 400

    # Verify valid code
    totp = pyotp.TOTP(secret)
    valid_code = totp.now()
    resp = await client.post("/user/mfa/otp/verify", json={"code": valid_code})
    assert resp.status_code == 200
    assert resp.json()["message"] == "OTP enabled successfully"


@pytest.mark.asyncio
async def test_mfa_enforcement_by_role(client: AsyncClient, db_engine):
    # Enable MFA enforcement for admins
    original_admin_level = settings.mfa_required_level_admin
    settings.mfa_required_level_admin = "otp"

    try:
        # Create an admin user
        admin_email = "admin_mfa@example.com"
        admin_pass = "Password123!"
        hashed = hash_password(admin_pass)

        from sqlalchemy.ext.asyncio import async_sessionmaker
        session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            admin_user = User(
                email=admin_email,
                password=hashed,
                role=UserRole.admin,
                verified=True,
            )
            session.add(admin_user)
            await session.commit()

        # Log in as admin
        resp = await client.post("/auth/login", json={"email": admin_email, "password": admin_pass})
        assert resp.status_code == 200

        # Try to access a protected endpoint (should succeed with 200 because they have no MFA set up yet)
        resp = await client.get("/teams/")
        assert resp.status_code == 200

        # Check `/user/me` returns mfa_setup_missing=True
        resp = await client.get("/user/me")
        assert resp.status_code == 200
        assert resp.json()["mfa_setup_missing"] is True

        # Set up OTP to satisfy the MFA requirement
        resp = await client.post("/user/mfa/otp/setup")
        assert resp.status_code == 200
        secret = resp.json()["secret"]

        # Verify OTP
        totp = pyotp.TOTP(secret)
        resp = await client.post("/user/mfa/otp/verify", json={"code": totp.now()})
        assert resp.status_code == 200

        # Now they have MFA set up. Let's verify that /user/me returns mfa_setup_missing=False
        resp = await client.get("/user/me")
        assert resp.status_code == 200
        assert resp.json()["mfa_setup_missing"] is False

    finally:
        settings.mfa_required_level_admin = original_admin_level


@pytest.mark.asyncio
async def test_mfa_redirected_login_otp(client: AsyncClient, db_engine):
    email = "redirect_login@example.com"
    password = "Password123!"
    hashed = hash_password(password)
    secret = pyotp.random_base32()

    # Create user with OTP already enabled
    from sqlalchemy.ext.asyncio import async_sessionmaker
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        user = User(
            email=email,
            password=hashed,
            verified=True,
            otp_secret=secret,
            otp_enabled=True,
        )
        session.add(user)
        await session.commit()

    # Login - should return status: mfa_required
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "mfa_required"
    assert "mfa_token" in data
    assert "otp" in data["methods"]
    mfa_token = data["mfa_token"]

    # Verify invalid OTP code
    resp = await client.post(
        "/auth/mfa/verify",
        json={"mfa_token": mfa_token, "method": "otp", "otp_code": "000000"}
    )
    assert resp.status_code == 400

    # Verify valid OTP code
    totp = pyotp.TOTP(secret)
    resp = await client.post(
        "/auth/mfa/verify",
        json={"mfa_token": mfa_token, "method": "otp", "otp_code": totp.now()}
    )
    assert resp.status_code == 200
    assert resp.json()["message"] == "Login successful"


@pytest.mark.asyncio
async def test_hardware_token_setup_and_login_flow(client: AsyncClient, db_engine, registered_user):
    # Log in
    resp = await client.post("/auth/login", json=registered_user)
    assert resp.status_code == 200

    # 1. Setup options request
    resp = await client.post("/user/mfa/hardware-token/setup")
    assert resp.status_code == 200
    setup_data = resp.json()
    assert "options" in setup_data
    assert "registration_token" in setup_data
    registration_token = setup_data["registration_token"]

    # Mock WebAuthn verification response
    mock_verification = MagicMock()
    mock_verification.credential_id = b"mock_credential_id"
    mock_verification.credential_public_key = b"mock_public_key"
    mock_verification.sign_count = 5

    # 2. Verify / register Hardware token
    with patch("webauthn.verify_registration_response", return_value=mock_verification):
        resp = await client.post(
            "/user/mfa/hardware-token/verify",
            json={
                "registration_token": registration_token,
                "credential_response": {"id": "mock_response_id"},
                "name": "My Hardware token",
            }
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Hardware token registered successfully"

    # Verify Hardware token was saved in database
    from sqlalchemy.ext.asyncio import async_sessionmaker
    session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        result = await session.execute(select(HardwareToken))
        tokens = result.scalars().all()
        assert len(tokens) == 1
        assert tokens[0].name == "My Hardware token"

    # Log out
    await client.post("/auth/logout")

    # 3. Log in again - should ask for MFA (since user has Hardware token registered)
    resp = await client.post("/auth/login", json=registered_user)
    assert resp.status_code == 200
    login_data = resp.json()
    assert login_data["status"] == "mfa_required"
    assert "hardware_token" in login_data["methods"]
    mfa_token = login_data["mfa_token"]

    # 4. Request WebAuthn Assertion Options
    resp = await client.post(
        "/auth/mfa/hardware-token/assertion-options",
        json={"mfa_token": mfa_token}
    )
    assert resp.status_code == 200
    assert_data = resp.json()
    assert "options" in assert_data
    assert "assertion_token" in assert_data
    assertion_token = assert_data["assertion_token"]

    # 5. Complete login by verifying WebAuthn Authentication response
    mock_authentication = MagicMock()
    mock_authentication.new_sign_count = 6

    with patch("webauthn.verify_authentication_response", return_value=mock_authentication):
        resp = await client.post(
            "/auth/mfa/verify",
            json={
                "mfa_token": mfa_token,
                "method": "hardware_token",
                "assertion_token": assertion_token,
                "webauthn_response": {"id": "bW9ja19jcmVkZW50aWFsX2lk"},  # base64url representation of b"mock_credential_id"
            }
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Login successful"


@pytest.mark.asyncio
async def test_mfa_settings_management(client: AsyncClient, db_engine, registered_user):
    # Log in
    resp = await client.post("/auth/login", json=registered_user)
    assert resp.status_code == 200

    # Get settings
    resp = await client.get("/user/mfa/settings")
    assert resp.status_code == 200
    settings_data = resp.json()
    assert settings_data["otp_enabled"] is False
    assert len(settings_data["hardware_tokens"]) == 0

    # Set up OTP
    resp = await client.post("/user/mfa/otp/setup")
    assert resp.status_code == 200
    secret = resp.json()["secret"]
    totp = pyotp.TOTP(secret)
    await client.post("/user/mfa/otp/verify", json={"code": totp.now()})

    # Register a mock Hardware token
    resp = await client.post("/user/mfa/hardware-token/setup")
    reg_token = resp.json()["registration_token"]
    mock_verification = MagicMock()
    mock_verification.credential_id = b"device_id_123"
    mock_verification.credential_public_key = b"pub_key_123"
    mock_verification.sign_count = 1

    with patch("webauthn.verify_registration_response", return_value=mock_verification):
        resp = await client.post(
            "/user/mfa/hardware-token/verify",
            json={
                "registration_token": reg_token,
                "credential_response": {"id": "device_id_123_b64"},
                "name": "Settings Key",
            }
        )
        assert resp.status_code == 200

    # Verify settings now reflect both
    resp = await client.get("/user/mfa/settings")
    assert resp.status_code == 200
    settings_data = resp.json()
    assert settings_data["otp_enabled"] is True
    assert len(settings_data["hardware_tokens"]) == 1
    token_id = settings_data["hardware_tokens"][0]["id"]

    # Enforce user required level to OTP
    original_admin_level = settings.mfa_required_level_admin
    settings.mfa_required_level_admin = "otp"

    try:
        original_user_level = settings.mfa_required_level_user
        settings.mfa_required_level_user = "otp"
        try:
            # Attempt to disable OTP (should succeed because user has Hardware token registered, satisfying OTP)
            resp = await client.post("/user/mfa/otp/disable")
            assert resp.status_code == 200

            # OTP is now disabled. Hardware token is still registered.
            # Attempt to delete Hardware token (should fail because user's role requires "otp", and OTP is disabled and this is the last token)
            resp = await client.delete(f"/user/mfa/hardware-token/{token_id}")
            assert resp.status_code == 400
            assert "requires at least OTP MFA" in resp.json()["detail"]

            # Enable OTP again so we can delete the Hardware token
            resp = await client.post("/user/mfa/otp/setup")
            secret = resp.json()["secret"]
            totp = pyotp.TOTP(secret)
            await client.post("/user/mfa/otp/verify", json={"code": totp.now()})

            # Now delete Hardware token should succeed (since OTP is enabled and satisfies "otp" requirement)
            resp = await client.delete(f"/user/mfa/hardware-token/{token_id}")
            assert resp.status_code == 200

            # Deleting is complete. Now attempt to disable OTP (should fail because no Hardware tokens left, and role requires "otp")
            resp = await client.post("/user/mfa/otp/disable")
            assert resp.status_code == 400
            assert "requires at least 'otp' MFA" in resp.json()["detail"]

        finally:
            settings.mfa_required_level_user = original_user_level
    finally:
        settings.mfa_required_level_admin = original_admin_level


@pytest.mark.asyncio
async def test_mfa_grace_period_and_admin_reset_password(client: AsyncClient, db_engine):
    # Enable MFA enforcement for admins
    original_admin_level = settings.mfa_required_level_admin
    settings.mfa_required_level_admin = "otp"

    try:
        admin_email = "admin_reset@example.com"
        admin_pass = "Password123!"
        hashed = hash_password(admin_pass)

        # Create admin user
        from sqlalchemy.ext.asyncio import async_sessionmaker
        session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            admin_user = User(
                email=admin_email,
                password=hashed,
                role=UserRole.admin,
                verified=True,
            )
            session.add(admin_user)
            await session.commit()

        # 1. Log in. Since no MFA setup exists, they bypass authentication challenge
        resp = await client.post("/auth/login", json={"email": admin_email, "password": admin_pass})
        assert resp.status_code == 200
        assert resp.json()["message"] == "Login successful"

        # 2. Check /user/me returns mfa_setup_missing=True
        resp = await client.get("/user/me")
        assert resp.status_code == 200
        assert resp.json()["mfa_setup_missing"] is True

        # 3. Setup OTP
        resp = await client.post("/user/mfa/otp/setup")
        assert resp.status_code == 200
        secret = resp.json()["secret"]

        # Verify OTP to enable it
        totp = pyotp.TOTP(secret)
        resp = await client.post("/user/mfa/otp/verify", json={"code": totp.now()})
        assert resp.status_code == 200

        # Now mfa_setup_missing should be False
        resp = await client.get("/user/me")
        assert resp.status_code == 200
        assert resp.json()["mfa_setup_missing"] is False

        # Log out
        await client.post("/auth/logout")

        # 4. Try logging in again. Now they should be challenged
        resp = await client.post("/auth/login", json={"email": admin_email, "password": admin_pass})
        assert resp.status_code == 200
        assert resp.json()["status"] == "mfa_required"
        mfa_token = resp.json()["mfa_token"]

        # If they try to access protected endpoint using a fresh session without MFA verify, they get 403
        # Wait, the client session cookie is not set yet because login returned mfa_required.
        # But we can verify with the admin reset password feature next.

        # Let's perform admin reset password
        # Log back in as admin user (for authorization to hit the admin endpoint, or let's use another admin, but we can just use the same admin session since they had bypass before).
        # Actually, let's log in as the admin user (complete MFA first)
        resp = await client.post("/auth/mfa/verify", json={"mfa_token": mfa_token, "method": "otp", "otp_code": totp.now()})
        assert resp.status_code == 200

        # Verify they are logged in with otp mfa level
        resp = await client.get("/user/me")
        assert resp.json()["mfa_setup_missing"] is False

        # Get target user ID
        user_id = resp.json()["id"]

        # Call admin reset-password
        with patch("app.services.email.send_password_reset_email") as mock_email:
            resp = await client.post(f"/admin/users/{user_id}/reset-password")
            assert resp.status_code == 200
            data = resp.json()
            assert "new_password" not in data
            mock_email.assert_called_once()
            # Extract new password sent via email to verify it
            sent_password = mock_email.call_args[0][1]
            assert len(sent_password) == 12

        # Check that OTP is now disabled
        async with session_factory() as session:
            result = await session.execute(select(User).where(User.id == user_id))
            updated_user = result.scalar_one()
            assert updated_user.otp_enabled is False
            assert updated_user.otp_secret is None

    finally:
        settings.mfa_required_level_admin = original_admin_level
