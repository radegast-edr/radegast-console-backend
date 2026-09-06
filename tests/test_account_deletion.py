from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pyotp
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.device import Device
from app.models.device_group import DeviceGroup
from app.models.log import Log, LogSeverity
from app.models.pack import Pack
from app.models.team import PermissionAdmin, Team
from app.models.user import User, UserRole
from app.services.account_deletion import (
    perform_user_deletion,
    process_account_deletions,
)
from app.services.auth import create_signed_token, hash_password
from app.services.email import send_account_deletion_cancelled_email
from app.utils import utc_now


@pytest.mark.asyncio
class TestAccountDeletionRequest:
    async def test_request_deletion_solo_user(self, auth_client: AsyncClient, db_session: AsyncSession):
        resp = await auth_client.post("/user/delete-account/request")
        assert resp.status_code == 200
        data = resp.json()
        assert "confirmation email has been sent" in data["message"].lower()
        assert data["grace_days"] == settings.account_deletion_grace_days

        # Verify DB
        res = await db_session.execute(select(User).where(User.email == "test@example.com"))
        user = res.scalar_one()
        assert user.deletion_requested_at is not None
        assert user.deletion_scheduled_at is None

    async def test_request_deletion_blocked_by_last_admin_of_multi_member_team(self, auth_client: AsyncClient, db_session: AsyncSession):
        res = await db_session.execute(select(User).options(selectinload(User.teams)).where(User.email == "test@example.com"))
        user = res.scalar_one()

        # Add another user
        other_user = User(
            email="colleague@example.com",
            password=hash_password("password123"),
            role=UserRole.user,
            verified=True,
        )
        db_session.add(other_user)
        await db_session.flush()

        res_team = await db_session.execute(select(Team).options(selectinload(Team.users)).where(Team.id == user.teams[0].id))
        user_team = res_team.scalar_one()
        user_team.permission_admin = PermissionAdmin.write

        # Create a managed team where other_user is a member and user_team is the managing team
        managed_team = Team(
            name="Managed Subteam",
            permission_admin=None,
            managing_team_id=user_team.id,
        )
        managed_team.users.append(other_user)
        db_session.add(managed_team)
        await db_session.commit()

        # User is the sole admin of the subteam which has other_user as member
        resp = await auth_client.post("/user/delete-account/request")
        assert resp.status_code == 409
        err = resp.json()["detail"]
        assert "Cannot delete account" in err["message"]
        assert any("last admin of team" in r for r in err["reasons"])

    async def test_request_deletion_allowed_if_other_admin_exists(self, auth_client: AsyncClient, db_session: AsyncSession):
        res = await db_session.execute(select(User).options(selectinload(User.teams)).where(User.email == "test@example.com"))
        user = res.scalar_one()

        # Create another admin member in the team
        other_admin = User(
            email="otheradmin@example.com",
            password=hash_password("password123"),
            role=UserRole.admin,
            verified=True,
        )
        db_session.add(other_admin)
        await db_session.flush()

        res_team = await db_session.execute(select(Team).options(selectinload(Team.users)).where(Team.id == user.teams[0].id))
        user_team = res_team.scalar_one()
        user_team.users.append(other_admin)
        await db_session.commit()

        resp = await auth_client.post("/user/delete-account/request")
        assert resp.status_code == 200


@pytest.mark.asyncio
class TestAccountDeletionConfirm:
    async def test_confirm_deletion_valid_token(self, auth_client: AsyncClient, db_session: AsyncSession):
        # 1. Request deletion
        await auth_client.post("/user/delete-account/request")

        # 2. Generate signed token
        token = create_signed_token({"email": "test@example.com"}, salt="account-delete")

        # 3. Confirm
        resp = await auth_client.post(f"/user/delete-account/confirm?token={token}")
        assert resp.status_code == 200
        data = resp.json()
        assert "scheduled" in data["message"].lower()
        assert data["grace_days"] == settings.account_deletion_grace_days

        # Verify DB
        res = await db_session.execute(select(User).where(User.email == "test@example.com"))
        user = res.scalar_one()
        assert user.deletion_scheduled_at is not None

    async def test_confirm_deletion_invalid_token(self, auth_client: AsyncClient):
        resp = await auth_client.post("/user/delete-account/confirm?token=bad_token_123")
        assert resp.status_code == 400
        assert "invalid or expired" in resp.json()["detail"].lower()

    async def test_confirm_without_prior_request(self, auth_client: AsyncClient, db_session: AsyncSession):
        res = await db_session.execute(select(User).where(User.email == "test@example.com"))
        user = res.scalar_one()
        user.deletion_requested_at = None
        await db_session.commit()

        token = create_signed_token({"email": "test@example.com"}, salt="account-delete")
        resp = await auth_client.post(f"/user/delete-account/confirm?token={token}")
        assert resp.status_code == 400
        assert "was not requested" in resp.json()["detail"].lower()


@pytest.mark.asyncio
class TestAccountDeletionStatusAndLoginCancellation:
    async def test_deletion_status_endpoint(self, auth_client: AsyncClient, db_session: AsyncSession):
        # Initially false
        resp = await auth_client.get("/user/delete-account/status")
        assert resp.status_code == 200
        assert resp.json()["deletion_requested"] is False
        assert resp.json()["deletion_scheduled_at"] is None

        # After request
        await auth_client.post("/user/delete-account/request")
        resp = await auth_client.get("/user/delete-account/status")
        assert resp.json()["deletion_requested"] is True

        # After confirm
        token = create_signed_token({"email": "test@example.com"}, salt="account-delete")
        await auth_client.post(f"/user/delete-account/confirm?token={token}")
        resp = await auth_client.get("/user/delete-account/status")
        assert resp.json()["deletion_requested"] is True
        assert resp.json()["deletion_scheduled_at"] is not None

    async def test_login_cancels_scheduled_deletion(self, auth_client: AsyncClient, db_session: AsyncSession):
        # Set scheduled deletion on user
        res = await db_session.execute(select(User).where(User.email == "test@example.com"))
        user = res.scalar_one()
        user.deletion_requested_at = utc_now()
        user.deletion_scheduled_at = utc_now() + timedelta(days=14)
        await db_session.commit()

        # Login with email dispatch mocked
        with patch("app.routers.auth.send_account_deletion_cancelled_email", new_callable=AsyncMock) as mock_send_email:
            resp = await auth_client.post(
                "/auth/login",
                json={"email": "test@example.com", "password": "TestPass123!"},
            )
            assert resp.status_code == 200
            assert resp.json().get("deletion_cancelled") is True
            mock_send_email.assert_awaited_once_with("test@example.com")

        # Check DB
        db_session.expire_all()
        res = await db_session.execute(select(User).where(User.email == "test@example.com"))
        user_after = res.scalar_one()
        assert user_after.deletion_requested_at is None
        assert user_after.deletion_scheduled_at is None

    async def test_mfa_verify_cancels_scheduled_deletion(self, client: AsyncClient, db_session: AsyncSession):
        secret = pyotp.random_base32()
        user = User(
            email="mfa_del@example.com",
            password=hash_password("TestPass123!"),
            role=UserRole.user,
            verified=True,
            otp_enabled=True,
            otp_secret=secret,
            deletion_requested_at=utc_now(),
            deletion_scheduled_at=utc_now() + timedelta(days=14),
        )
        db_session.add(user)
        await db_session.commit()

        login_resp = await client.post(
            "/auth/login",
            json={"email": "mfa_del@example.com", "password": "TestPass123!"},
        )
        assert login_resp.status_code == 200
        mfa_token = login_resp.json()["mfa_token"]

        with patch("app.routers.auth.send_account_deletion_cancelled_email", new_callable=AsyncMock) as mock_send_email:
            totp = pyotp.TOTP(secret)
            resp = await client.post(
                "/auth/mfa/verify",
                json={"mfa_token": mfa_token, "method": "otp", "otp_code": totp.now()},
            )
            assert resp.status_code == 200
            assert resp.json().get("deletion_cancelled") is True
            mock_send_email.assert_awaited_once_with("mfa_del@example.com")

        # Check DB
        db_session.expire_all()
        res = await db_session.execute(select(User).where(User.email == "mfa_del@example.com"))
        user_after = res.scalar_one()
        assert user_after.deletion_requested_at is None
        assert user_after.deletion_scheduled_at is None

    async def test_send_account_deletion_cancelled_email(self):
        with patch("app.services.email.send_email_direct", new_callable=AsyncMock) as mock_direct:
            await send_account_deletion_cancelled_email("user@example.com")
            mock_direct.assert_awaited_once()
            args, kwargs = mock_direct.call_args
            assert args[0] == "user@example.com"
            assert "Account Deletion Canceled" in args[1]
            assert "Account Deletion Canceled" in args[2]
            assert kwargs.get("email_type") == "verify"


@pytest.mark.asyncio
class TestAccountDeletionCleanupAndWorker:
    async def test_worker_performs_deletion_and_prunes_orphans(self, db_session: AsyncSession):
        # Create user with a solo team, group, device, log, and pack
        user = User(
            email="solo@example.com",
            password=hash_password("password123"),
            role=UserRole.user,
            verified=True,
            deletion_requested_at=utc_now() - timedelta(days=15),
            deletion_scheduled_at=utc_now() - timedelta(days=1),
        )
        team = Team(name="Solo Team", permission_admin=PermissionAdmin.write)
        group = DeviceGroup(name="Solo Group")
        device = Device(name="Solo Device", token="token123")
        pack = Pack(pack_id="solo-pack-1", name="Solo Pack")

        team.users.append(user)
        team.groups.append(group)
        team.packs.append(pack)
        group.devices.append(device)
        user.public_keys = []

        db_session.add_all([user, team, group, device, pack])
        await db_session.flush()

        log = Log(
            device_id=device.id,
            time=utc_now(),
            content="alert event",
            severity=LogSeverity.high,
        )
        db_session.add(log)
        await db_session.commit()

        user_id = user.id
        team_id = team.id
        group_id = group.id
        device_id = device.id
        pack_id = pack.id
        log_id = log.id

        # Run worker
        await process_account_deletions()

        # Verify all solo / orphaned resources are deleted
        assert (await db_session.execute(select(User).where(User.id == user_id))).scalar_one_or_none() is None
        assert (await db_session.execute(select(Team).where(Team.id == team_id))).scalar_one_or_none() is None
        assert (await db_session.execute(select(DeviceGroup).where(DeviceGroup.id == group_id))).scalar_one_or_none() is None
        assert (await db_session.execute(select(Device).where(Device.id == device_id))).scalar_one_or_none() is None
        assert (await db_session.execute(select(Log).where(Log.id == log_id))).scalar_one_or_none() is None
        assert (await db_session.execute(select(Pack).where(Pack.id == pack_id))).scalar_one_or_none() is None

    async def test_worker_preserves_shared_teams_and_devices(self, db_session: AsyncSession):
        # Create user A (to be deleted) and user B (co-member)
        user_a = User(
            email="usera@example.com",
            password=hash_password("pw"),
            role=UserRole.admin,
            verified=True,
            deletion_scheduled_at=utc_now() - timedelta(days=1),
        )
        user_b = User(
            email="userb@example.com",
            password=hash_password("pw"),
            role=UserRole.admin,
            verified=True,
        )
        shared_team = Team(name="Shared Team", permission_admin=PermissionAdmin.write)
        shared_team.users.extend([user_a, user_b])
        shared_group = DeviceGroup(name="Shared Group")
        shared_team.groups.append(shared_group)
        shared_device = Device(name="Shared Device", token="token_shared")
        shared_group.devices.append(shared_device)

        db_session.add_all([user_a, user_b, shared_team, shared_group, shared_device])
        await db_session.commit()

        team_id = shared_team.id
        device_id = shared_device.id
        user_b_id = user_b.id

        # Perform deletion on user_a
        await perform_user_deletion(user_a.id, db_session)

        # User A deleted
        assert (await db_session.execute(select(User).where(User.id == user_a.id))).scalar_one_or_none() is None
        # Shared team preserved, user B still a member
        res_t = await db_session.execute(select(Team).options(selectinload(Team.users)).where(Team.id == team_id))
        team_obj = res_t.scalar_one_or_none()
        assert team_obj is not None
        assert [u.id for u in team_obj.users] == [user_b_id]
        # Device preserved
        res_d = await db_session.execute(select(Device).where(Device.id == device_id))
        assert res_d.scalar_one_or_none() is not None

    async def test_request_deletion_blocked_by_pack_lockout(self, auth_client: AsyncClient, db_session: AsyncSession):
        res = await db_session.execute(select(User).options(selectinload(User.teams)).where(User.email == "test@example.com"))
        user = res.scalar_one()

        other_user = User(
            email="packreader@example.com",
            password=hash_password("pw"),
            role=UserRole.user,
            verified=True,
        )
        # Team with read-only pack permission for other_user
        read_team = Team(name="Read Team", permission_pack="read", permission_admin=PermissionAdmin.write)
        read_team.users.append(other_user)

        res_team = await db_session.execute(select(Team).options(selectinload(Team.packs)).where(Team.id == user.teams[0].id))
        user_team = res_team.scalar_one()
        user_team.permission_pack = "write"

        pack = Pack(pack_id="shared-pack", name="Shared Pack")
        user_team.packs.append(pack)
        read_team.packs.append(pack)

        db_session.add_all([other_user, read_team, pack])
        await db_session.commit()

        resp = await auth_client.post("/user/delete-account/request")
        assert resp.status_code == 409
        err = resp.json()["detail"]
        assert any("Pack 'Shared Pack' is used by other users" in r for r in err["reasons"])

    async def test_request_deletion_blocked_by_device_group_lockout(self, auth_client: AsyncClient, db_session: AsyncSession):
        res = await db_session.execute(select(User).options(selectinload(User.teams)).where(User.email == "test@example.com"))
        user = res.scalar_one()

        other_user = User(
            email="groupuser@example.com",
            password=hash_password("pw"),
            role=UserRole.user,
            verified=True,
        )
        # Managed team with no admin
        non_admin_team = Team(name="Non Admin Team", permission_admin=None)
        non_admin_team.users.append(other_user)

        res_team = await db_session.execute(select(Team).options(selectinload(Team.groups)).where(Team.id == user.teams[0].id))
        user_team = res_team.scalar_one()
        user_team.permission_admin = PermissionAdmin.write

        # Shared group linked to user's admin team and other_user's non-admin team
        shared_group = DeviceGroup(name="Shared Group Lockout")
        user_team.groups.append(shared_group)
        non_admin_team.groups.append(shared_group)

        db_session.add_all([other_user, non_admin_team, shared_group])
        await db_session.commit()

        resp = await auth_client.post("/user/delete-account/request")
        assert resp.status_code == 409
        err = resp.json()["detail"]
        assert any("Device group 'Shared Group Lockout' is shared with other users" in r for r in err["reasons"])

    async def test_perform_user_deletion_clears_creator_id_on_kept_packs(self, db_session: AsyncSession):
        user_a = User(
            email="author@example.com",
            password=hash_password("pw"),
            role=UserRole.user,
            verified=True,
        )
        user_b = User(
            email="admin_co@example.com",
            password=hash_password("pw"),
            role=UserRole.admin,
            verified=True,
        )
        team = Team(name="Shared Pack Team", permission_admin=PermissionAdmin.write, permission_pack="write")
        team.users.extend([user_a, user_b])
        pack = Pack(pack_id="pack-with-author", name="Author Pack", creator=user_a)
        team.packs.append(pack)

        db_session.add_all([user_a, user_b, team, pack])
        await db_session.commit()

        pack_id = pack.id
        await perform_user_deletion(user_a.id, db_session)

        # Pack survives, but creator_id is cleared
        res = await db_session.execute(select(Pack).where(Pack.id == pack_id))
        p = res.scalar_one()
        assert p.creator_id is None
