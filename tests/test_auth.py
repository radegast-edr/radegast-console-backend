import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestRegistration:
    async def test_register_success(self, client: AsyncClient):
        resp = await client.post(
            "/auth/register",
            json={"email": "new@example.com", "password": "Password123!"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["email"] == "new@example.com"
        assert data["verified"] is False

    async def test_register_duplicate_email(self, client: AsyncClient):
        await client.post(
            "/auth/register",
            json={"email": "dup@example.com", "password": "Password123!"},
        )
        resp = await client.post(
            "/auth/register",
            json={"email": "dup@example.com", "password": "Password123!"},
        )
        assert resp.status_code == 400


@pytest.mark.asyncio
class TestVerification:
    async def test_verify_valid_token(self, client: AsyncClient):
        await client.post(
            "/auth/register",
            json={"email": "verify@example.com", "password": "Password123!"},
        )
        from app.services.auth import create_signed_token

        token = create_signed_token({"email": "verify@example.com"}, salt="email-verify")
        resp = await client.get(f"/auth/verify?token={token}")
        assert resp.status_code == 200

    async def test_verify_invalid_token(self, client: AsyncClient):
        resp = await client.get("/auth/verify?token=invalid-token")
        assert resp.status_code == 400

    async def test_verify_unknown_email(self, client: AsyncClient):
        from app.services.auth import create_signed_token

        token = create_signed_token({"email": "ghost@example.com"}, salt="email-verify")
        resp = await client.get(f"/auth/verify?token={token}")
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestLogin:
    async def test_login_success(self, client: AsyncClient, registered_user):
        resp = await client.post("/auth/login", json=registered_user)
        assert resp.status_code == 200
        assert "radegast_session" in resp.cookies

    async def test_login_wrong_password(self, client: AsyncClient, registered_user):
        resp = await client.post(
            "/auth/login",
            json={"email": registered_user["email"], "password": "wrong"},
        )
        assert resp.status_code == 401

    async def test_login_unverified(self, client: AsyncClient):
        await client.post(
            "/auth/register",
            json={"email": "unverified@example.com", "password": "Password123!"},
        )
        resp = await client.post(
            "/auth/login",
            json={"email": "unverified@example.com", "password": "Password123!"},
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
class TestLogout:
    async def test_logout(self, auth_client: AsyncClient):
        resp = await auth_client.post("/auth/logout")
        assert resp.status_code == 200

    async def test_session_cleared_after_logout(self, auth_client: AsyncClient):
        await auth_client.post("/auth/logout")
        resp = await auth_client.get("/auth/me")
        assert resp.status_code == 401


@pytest.mark.asyncio
class TestMe:
    async def test_me_authenticated(self, auth_client: AsyncClient):
        resp = await auth_client.get("/auth/me")
        assert resp.status_code == 200
        assert resp.json()["email"] == "test@example.com"

    async def test_me_unauthenticated(self, client: AsyncClient):
        resp = await client.get("/auth/me")
        assert resp.status_code == 401


@pytest.mark.asyncio
class TestKeySetup:
    async def test_setup_keys(self, auth_client: AsyncClient):
        from app.services.crypto import generate_age_keypair, age_encrypt
        main_pub, main_priv = generate_age_keypair()
        rec_pub, _ = generate_age_keypair()
        encrypted_priv = age_encrypt(main_priv, rec_pub)

        resp = await auth_client.post(
            "/auth/keys/setup",
            json={"public_key": main_pub, "encrypted_private_key": encrypted_priv},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Keys set up successfully"

    async def test_setup_keys_unauthenticated(self, client: AsyncClient):
        from app.services.crypto import generate_age_keypair, age_encrypt
        main_pub, main_priv = generate_age_keypair()
        rec_pub, _ = generate_age_keypair()
        encrypted_priv = age_encrypt(main_priv, rec_pub)

        resp = await client.post(
            "/auth/keys/setup",
            json={"public_key": main_pub, "encrypted_private_key": encrypted_priv},
        )
        assert resp.status_code == 401

    async def test_setup_keys_missing_fields(self, auth_client: AsyncClient):
        resp = await auth_client.post("/auth/keys/setup", json={})
        assert resp.status_code == 422

    async def test_setup_keys_duplicate_fails(self, auth_client: AsyncClient):
        from app.services.crypto import generate_age_keypair, age_encrypt
        main_pub, main_priv = generate_age_keypair()
        rec_pub, _ = generate_age_keypair()
        encrypted_priv = age_encrypt(main_priv, rec_pub)
        payload = {"public_key": main_pub, "encrypted_private_key": encrypted_priv}

        await auth_client.post("/auth/keys/setup", json=payload)
        resp = await auth_client.post("/auth/keys/setup", json=payload)
        assert resp.status_code == 400

    async def test_me_has_keys_false(self, auth_client: AsyncClient):
        resp = await auth_client.get("/auth/me")
        assert resp.status_code == 200
        assert resp.json()["has_keys"] is False

    async def test_me_has_keys_true(self, auth_client: AsyncClient):
        from app.services.crypto import generate_age_keypair, age_encrypt
        main_pub, main_priv = generate_age_keypair()
        rec_pub, _ = generate_age_keypair()
        encrypted_priv = age_encrypt(main_priv, rec_pub)
        await auth_client.post(
            "/auth/keys/setup",
            json={"public_key": main_pub, "encrypted_private_key": encrypted_priv},
        )
        resp = await auth_client.get("/auth/me")
        assert resp.json()["has_keys"] is True


@pytest.mark.asyncio
class TestKeyRecover:
    async def test_recover_keys_no_keys(self, auth_client: AsyncClient):
        resp = await auth_client.get("/auth/keys/recover")
        assert resp.status_code == 404

    async def test_recover_keys_success(self, auth_client: AsyncClient):
        from app.services.crypto import generate_age_keypair, age_encrypt, age_decrypt
        main_pub, main_priv = generate_age_keypair()
        rec_pub, rec_priv = generate_age_keypair()
        encrypted_priv = age_encrypt(main_priv, rec_pub)

        await auth_client.post(
            "/auth/keys/setup",
            json={"public_key": main_pub, "encrypted_private_key": encrypted_priv},
        )

        resp = await auth_client.get("/auth/keys/recover")
        assert resp.status_code == 200
        data = resp.json()
        assert data["public_key"] == main_pub
        # Client-side: decrypt with recovery private key
        recovered = age_decrypt(data["encrypted_private_key"], rec_priv)
        assert recovered == main_priv


@pytest.mark.asyncio
class TestKeyTransfer:
    async def _setup_keys(self, client):
        from app.services.crypto import generate_age_keypair, age_encrypt
        main_pub, main_priv = generate_age_keypair()
        rec_pub, _ = generate_age_keypair()
        await client.post(
            "/auth/keys/setup",
            json={"public_key": main_pub, "encrypted_private_key": age_encrypt(main_priv, rec_pub)},
        )
        return main_pub, main_priv

    async def test_initiate_transfer(self, auth_client: AsyncClient):
        from app.services.crypto import generate_age_keypair
        eph_pub, _ = generate_age_keypair()
        resp = await auth_client.post(
            "/auth/keys/transfer/initiate",
            json={"receiver_age_public_key": eph_pub},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "transfer_id" in data
        assert len(data["transfer_id"]) == 36  # UUID

    async def test_initiate_transfer_unauthenticated(self, client: AsyncClient):
        from app.services.crypto import generate_age_keypair
        eph_pub, _ = generate_age_keypair()
        resp = await client.post(
            "/auth/keys/transfer/initiate",
            json={"receiver_age_public_key": eph_pub},
        )
        assert resp.status_code == 401

    async def test_get_transfer_pending(self, auth_client: AsyncClient):
        from app.services.crypto import generate_age_keypair
        eph_pub, _ = generate_age_keypair()
        init_resp = await auth_client.post(
            "/auth/keys/transfer/initiate",
            json={"receiver_age_public_key": eph_pub},
        )
        transfer_id = init_resp.json()["transfer_id"]

        resp = await auth_client.get(f"/auth/keys/transfer/{transfer_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "pending"
        assert data["receiver_age_public_key"] == eph_pub
        assert data["encrypted_private_key"] is None

    async def test_get_transfer_not_found(self, auth_client: AsyncClient):
        resp = await auth_client.get("/auth/keys/transfer/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    async def test_complete_transfer(self, auth_client: AsyncClient):
        from app.services.crypto import generate_age_keypair, age_encrypt, age_decrypt
        # Receiver generates ephemeral keypair
        eph_pub, eph_priv = generate_age_keypair()
        init_resp = await auth_client.post(
            "/auth/keys/transfer/initiate",
            json={"receiver_age_public_key": eph_pub},
        )
        transfer_id = init_resp.json()["transfer_id"]

        # Sender AGE-encrypts main private key for receiver's ephemeral key
        main_pub, main_priv = generate_age_keypair()
        encrypted_payload = age_encrypt(main_priv, eph_pub)

        resp = await auth_client.post(
            f"/auth/keys/transfer/{transfer_id}/complete",
            json={"encrypted_private_key": encrypted_payload},
        )
        assert resp.status_code == 200

        # Receiver polls and gets the completed transfer
        status_resp = await auth_client.get(f"/auth/keys/transfer/{transfer_id}")
        assert status_resp.status_code == 200
        status = status_resp.json()
        assert status["status"] == "completed"

        # Receiver decrypts with ephemeral private key
        recovered = age_decrypt(status["encrypted_private_key"], eph_priv)
        assert recovered == main_priv

    async def test_complete_transfer_already_done(self, auth_client: AsyncClient):
        from app.services.crypto import generate_age_keypair, age_encrypt
        eph_pub, _ = generate_age_keypair()
        init_resp = await auth_client.post(
            "/auth/keys/transfer/initiate",
            json={"receiver_age_public_key": eph_pub},
        )
        transfer_id = init_resp.json()["transfer_id"]

        _, main_priv = generate_age_keypair()
        payload = {"encrypted_private_key": age_encrypt(main_priv, eph_pub)}

        await auth_client.post(f"/auth/keys/transfer/{transfer_id}/complete", json=payload)
        resp = await auth_client.post(f"/auth/keys/transfer/{transfer_id}/complete", json=payload)
        assert resp.status_code == 400


@pytest.mark.asyncio
class TestKeySecondary:
    async def _setup_main_key(self, client):
        from app.services.crypto import generate_age_keypair, age_encrypt
        main_pub, main_priv = generate_age_keypair()
        rec_pub, _ = generate_age_keypair()
        await client.post(
            "/auth/keys/setup",
            json={"public_key": main_pub, "encrypted_private_key": age_encrypt(main_priv, rec_pub)},
        )
        return main_pub

    async def test_setup_secondary_key(self, auth_client: AsyncClient):
        from app.services.crypto import generate_age_keypair, age_encrypt
        await self._setup_main_key(auth_client)

        sec_pub, sec_priv = generate_age_keypair()
        rec_pub, _ = generate_age_keypair()
        resp = await auth_client.post(
            "/auth/keys/secondary",
            json={"public_key": sec_pub, "encrypted_private_key": age_encrypt(sec_priv, rec_pub)},
        )
        assert resp.status_code == 200
        assert resp.json()["message"] == "Secondary key added successfully"

    async def test_setup_secondary_without_main_fails(self, auth_client: AsyncClient):
        from app.services.crypto import generate_age_keypair, age_encrypt
        sec_pub, sec_priv = generate_age_keypair()
        rec_pub, _ = generate_age_keypair()
        resp = await auth_client.post(
            "/auth/keys/secondary",
            json={"public_key": sec_pub, "encrypted_private_key": age_encrypt(sec_priv, rec_pub)},
        )
        assert resp.status_code == 400

    async def test_setup_secondary_duplicate_fails(self, auth_client: AsyncClient):
        from app.services.crypto import generate_age_keypair, age_encrypt
        await self._setup_main_key(auth_client)

        sec_pub, sec_priv = generate_age_keypair()
        rec_pub, _ = generate_age_keypair()
        payload = {"public_key": sec_pub, "encrypted_private_key": age_encrypt(sec_priv, rec_pub)}
        await auth_client.post("/auth/keys/secondary", json=payload)
        resp = await auth_client.post("/auth/keys/secondary", json=payload)
        assert resp.status_code == 400

    async def test_secondary_key_unauthenticated(self, client: AsyncClient):
        from app.services.crypto import generate_age_keypair, age_encrypt
        sec_pub, sec_priv = generate_age_keypair()
        rec_pub, _ = generate_age_keypair()
        resp = await client.post(
            "/auth/keys/secondary",
            json={"public_key": sec_pub, "encrypted_private_key": age_encrypt(sec_priv, rec_pub)},
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
class TestDeleteKeys:
    async def _setup_main_key(self, client):
        from app.services.crypto import generate_age_keypair, age_encrypt
        main_pub, main_priv = generate_age_keypair()
        rec_pub, _ = generate_age_keypair()
        await client.post(
            "/auth/keys/setup",
            json={"public_key": main_pub, "encrypted_private_key": age_encrypt(main_priv, rec_pub)},
        )

    async def test_delete_keys(self, auth_client: AsyncClient):
        await self._setup_main_key(auth_client)
        resp = await auth_client.get("/auth/me")
        assert resp.json()["has_keys"] is True

        resp = await auth_client.delete("/auth/keys")
        assert resp.status_code == 200

        resp = await auth_client.get("/auth/me")
        assert resp.json()["has_keys"] is False

    async def test_delete_keys_empty(self, auth_client: AsyncClient):
        # Deleting when no keys should succeed (no-op)
        resp = await auth_client.delete("/auth/keys")
        assert resp.status_code == 200

    async def test_delete_then_reregister(self, auth_client: AsyncClient):
        from app.services.crypto import generate_age_keypair, age_encrypt
        await self._setup_main_key(auth_client)
        await auth_client.delete("/auth/keys")

        main_pub, main_priv = generate_age_keypair()
        rec_pub, _ = generate_age_keypair()
        resp = await auth_client.post(
            "/auth/keys/setup",
            json={"public_key": main_pub, "encrypted_private_key": age_encrypt(main_priv, rec_pub)},
        )
        assert resp.status_code == 200

    async def test_delete_keys_unauthenticated(self, client: AsyncClient):
        resp = await client.delete("/auth/keys")
        assert resp.status_code == 401


@pytest.mark.asyncio
class TestInviteAccept:
    async def test_accept_invalid_invite_token(self, client: AsyncClient):
        resp = await client.get("/auth/invite/accept?token=invalid")
        assert resp.status_code == 400

    async def test_accept_invite_user_not_found(self, client: AsyncClient):
        from app.services.auth import create_signed_token

        token = create_signed_token(
            {"email": "nobody@example.com", "team_id": 999}, salt="team-invite"
        )
        resp = await client.get(f"/auth/invite/accept?token={token}")
        assert resp.status_code == 404

    async def test_accept_invite_already_member(self, auth_client: AsyncClient):
        from app.services.auth import create_signed_token

        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]

        token = create_signed_token(
            {"email": "test@example.com", "team_id": team_id}, salt="team-invite"
        )
        resp = await auth_client.get(f"/auth/invite/accept?token={token}")
        assert resp.status_code == 200
        assert "Already a member" in resp.json()["message"]


@pytest.mark.asyncio
class TestSessionInvalidation:
    async def test_session_invalid_after_password_change(self, client: AsyncClient, db_engine):
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
        from sqlalchemy import select
        from app.models.user import User
        from app.services.auth import create_signed_token
        from datetime import datetime, timedelta

        email = "sesstest@example.com"
        password = "Password123!"

        await client.post("/auth/register", json={"email": email, "password": password})
        token = create_signed_token({"email": email}, salt="email-verify")
        await client.get(f"/auth/verify?token={token}")
        resp = await client.post("/auth/login", json={"email": email, "password": password})
        assert resp.status_code == 200

        # Simulate password change (set password_change to future)
        session_factory = async_sessionmaker(db_engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            result = await session.execute(select(User).where(User.email == email))
            user = result.scalar_one()
            user.password_change = datetime.utcnow() + timedelta(hours=1)
            await session.commit()

        # Session should now be invalid
        resp = await client.get("/auth/me")
        assert resp.status_code == 401
