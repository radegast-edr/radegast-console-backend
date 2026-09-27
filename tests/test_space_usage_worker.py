import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy import select

import app.services.space_usage as space_service
from app.config import Settings, settings
from app.models.associations import device_group_devices, team_device_groups, team_users
from app.models.device import Device
from app.models.device_group import DeviceGroup
from app.models.team import PermissionAdmin, Team
from app.models.user import User, UserRole


def test_space_usage_settings_defaults_and_env(monkeypatch):
    default_settings = Settings()
    assert default_settings.enable_space_usage_worker is True
    assert default_settings.space_usage_interval_minutes == 15

    monkeypatch.setenv("RADEGAST_ENABLE_SPACE_USAGE_WORKER", "false")
    monkeypatch.setenv("RADEGAST_SPACE_USAGE_INTERVAL_MINUTES", "30")
    overridden = Settings()
    assert overridden.enable_space_usage_worker is False
    assert overridden.space_usage_interval_minutes == 30


@pytest.mark.asyncio
async def test_process_space_usage_loop_respects_lock(monkeypatch, tmp_path):
    lock_path = tmp_path / "radegast-space-test.lock"
    monkeypatch.setattr(settings, "worker_lock_path", str(lock_path))

    first_started = asyncio.Event()
    second_started = asyncio.Event()
    continue_first = asyncio.Event()
    call_count = 0

    async def fake_compute_all_space_usage(session=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            first_started.set()
            await continue_first.wait()
        else:
            second_started.set()

    real_sleep = asyncio.sleep
    monkeypatch.setattr(space_service, "compute_all_space_usage", fake_compute_all_space_usage)
    monkeypatch.setattr(space_service.asyncio, "sleep", lambda s: real_sleep(0.01))

    task1 = asyncio.create_task(space_service.process_space_usage_loop())
    await asyncio.wait_for(first_started.wait(), timeout=2.0)

    task2 = asyncio.create_task(space_service.process_space_usage_loop())
    await asyncio.sleep(0.1)
    assert call_count == 1
    assert not second_started.is_set()

    continue_first.set()
    task1.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task1

    await asyncio.wait_for(second_started.wait(), timeout=2.0)
    task2.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task2


@pytest.mark.asyncio
async def test_compute_space_usage_calculations(db_session):
    # Create users
    user1 = User(
        email="user1@example.com",
        password="hashedpassword",
        role=UserRole.user,
        verified=True,
    )
    user2 = User(
        email="user2@example.com",
        password="hashedpassword",
        role=UserRole.user,
        verified=True,
    )
    user3 = User(
        email="user3@example.com",
        password="hashedpassword",
        role=UserRole.user,
        verified=True,
    )
    db_session.add_all([user1, user2, user3])
    await db_session.flush()

    # Create devices with known total_space_used
    dev1 = Device(name="dev1", token="tok1", total_space_used=100)
    dev2 = Device(name="dev2", token="tok2", total_space_used=200)
    dev3 = Device(name="dev3", token="tok3", total_space_used=400)
    dev4 = Device(name="dev4", token="tok4", total_space_used=800)
    db_session.add_all([dev1, dev2, dev3, dev4])
    await db_session.flush()

    # Create device groups
    # Group 1: dev1 (100) + dev2 (200) = 300
    grp1 = DeviceGroup(name="grp1")
    # Group 2: dev2 (200) + dev3 (400) = 600 (dev2 is shared with grp1)
    grp2 = DeviceGroup(name="grp2")
    # Group 3: empty = 0
    grp3 = DeviceGroup(name="grp3")
    # Group 4: dev3 (400) + dev4 (800) = 1200
    grp4 = DeviceGroup(name="grp4")
    db_session.add_all([grp1, grp2, grp3, grp4])
    await db_session.flush()

    # Link devices to groups
    await db_session.execute(
        device_group_devices.insert(),
        [
            {"device_group_id": grp1.id, "device_id": dev1.id},
            {"device_group_id": grp1.id, "device_id": dev2.id},
            {"device_group_id": grp2.id, "device_id": dev2.id},
            {"device_group_id": grp2.id, "device_id": dev3.id},
            {"device_group_id": grp4.id, "device_id": dev3.id},
            {"device_group_id": grp4.id, "device_id": dev4.id},
        ],
    )

    # Create teams:
    # Team 1: admin=write, managing_team_id=None (QUALIFYING)
    # Linked to grp1 (dev1, dev2) and grp2 (dev2, dev3)
    # Unique devices in team 1: dev1 (100) + dev2 (200) + dev3 (400) = 700 (dev2 deduplicated!)
    team1 = Team(name="team1", permission_admin=PermissionAdmin.write, managing_team_id=None)

    # Team 2: admin=None (NON-QUALIFYING because no admin=write)
    # Linked to grp1 -> total_space_used should be 0
    team2 = Team(name="team2", permission_admin=None, managing_team_id=None)

    db_session.add_all([team1, team2])
    await db_session.flush()

    # Team 3: admin=write, managing_team_id=team1.id (NON-QUALIFYING because managing team set)
    # Linked to grp2 -> total_space_used should be 0
    team3 = Team(name="team3", permission_admin=PermissionAdmin.write, managing_team_id=team1.id)

    # Team 4: admin=write, managing_team_id=None (QUALIFYING)
    # Linked to grp4 (dev3, dev4)
    team4 = Team(name="team4", permission_admin=PermissionAdmin.write, managing_team_id=None)

    db_session.add_all([team3, team4])
    await db_session.flush()

    # Link groups to teams
    await db_session.execute(
        team_device_groups.insert(),
        [
            {"team_id": team1.id, "device_group_id": grp1.id},
            {"team_id": team1.id, "device_group_id": grp2.id},
            {"team_id": team2.id, "device_group_id": grp1.id},
            {"team_id": team3.id, "device_group_id": grp2.id},
            {"team_id": team4.id, "device_group_id": grp4.id},
        ],
    )

    # Link users to teams:
    # user1 is member of team1 (qualifying) AND team4 (qualifying)
    # Unique devices across qualifying teams for user1:
    # team1 groups: dev1 (100), dev2 (200), dev3 (400)
    # team4 groups: dev3 (400), dev4 (800)
    # Total unique devices: dev1 (100) + dev2 (200) + dev3 (400) + dev4 (800) = 1500 (dev3 deduplicated!)
    # user2 is member of team2 (non-qualifying) and team3 (non-qualifying) -> total should be 0
    # user3 is member of no teams -> total should be 0
    await db_session.execute(
        team_users.insert(),
        [
            {"team_id": team1.id, "user_id": user1.id},
            {"team_id": team4.id, "user_id": user1.id},
            {"team_id": team2.id, "user_id": user2.id},
            {"team_id": team3.id, "user_id": user2.id},
        ],
    )
    await db_session.commit()

    # Execute space calculation
    await space_service.compute_all_space_usage(db_session)

    # Verify Device Groups
    g1 = (await db_session.execute(select(DeviceGroup).where(DeviceGroup.id == grp1.id))).scalar_one()
    g2 = (await db_session.execute(select(DeviceGroup).where(DeviceGroup.id == grp2.id))).scalar_one()
    g3 = (await db_session.execute(select(DeviceGroup).where(DeviceGroup.id == grp3.id))).scalar_one()
    g4 = (await db_session.execute(select(DeviceGroup).where(DeviceGroup.id == grp4.id))).scalar_one()

    assert g1.total_space_used == 300  # 100 + 200
    assert g2.total_space_used == 600  # 200 + 400
    assert g3.total_space_used == 0  # empty
    assert g4.total_space_used == 1200  # 400 + 800

    # Verify Teams
    t1 = (await db_session.execute(select(Team).where(Team.id == team1.id))).scalar_one()
    t2 = (await db_session.execute(select(Team).where(Team.id == team2.id))).scalar_one()
    t3 = (await db_session.execute(select(Team).where(Team.id == team3.id))).scalar_one()
    t4 = (await db_session.execute(select(Team).where(Team.id == team4.id))).scalar_one()

    assert t1.total_space_used == 700  # dev1(100) + dev2(200) + dev3(400), dev2 not counted twice
    assert t2.total_space_used == 0  # non-qualifying (no admin=write)
    assert t3.total_space_used == 0  # non-qualifying (has managing team)
    assert t4.total_space_used == 1200  # dev3(400) + dev4(800)

    # Verify Users
    u1 = (await db_session.execute(select(User).where(User.id == user1.id))).scalar_one()
    u2 = (await db_session.execute(select(User).where(User.id == user2.id))).scalar_one()
    u3 = (await db_session.execute(select(User).where(User.id == user3.id))).scalar_one()

    # 100 + 200 + 400 + 800 (dev3 is in both team1 and team4, counted only once)
    assert u1.total_space_used == 1500
    assert u2.total_space_used == 0  # only member of non-qualifying teams
    assert u3.total_space_used == 0  # not a member of any teams


@pytest.mark.asyncio
async def test_api_endpoints_space_used_returned(auth_client: AsyncClient, db_session):
    # Retrieve authenticated user
    res_user = await db_session.execute(select(User).where(User.email == "test@example.com"))
    test_user = res_user.scalar_one()

    # Setup team, group, device
    team = Team(name="Alpha Team", permission_admin=PermissionAdmin.write, managing_team_id=None)
    db_session.add(team)
    await db_session.flush()

    group = DeviceGroup(name="Alpha Group")
    db_session.add(group)
    await db_session.flush()

    dev = Device(name="Alpha Dev", token="tok-alpha", total_space_used=2048)
    db_session.add(dev)
    await db_session.flush()

    await db_session.execute(team_users.insert().values(team_id=team.id, user_id=test_user.id))
    await db_session.execute(team_device_groups.insert().values(team_id=team.id, device_group_id=group.id))
    await db_session.execute(device_group_devices.insert().values(device_group_id=group.id, device_id=dev.id))
    await db_session.commit()

    # Compute usage
    await space_service.compute_all_space_usage(db_session)

    # 1. Test /api/v1/user/me
    resp = await auth_client.get("/api/v1/user/me")
    assert resp.status_code == 200
    assert resp.json()["total_space_used"] == 2048

    # 2. Test /api/v1/groups/
    resp = await auth_client.get("/api/v1/groups/")
    assert resp.status_code == 200
    groups_data = resp.json()
    assert any(g["id"] == group.id and g["total_space_used"] == 2048 for g in groups_data)

    # 3. Test /api/v1/groups/{id}
    resp = await auth_client.get(f"/api/v1/groups/{group.id}")
    assert resp.status_code == 200
    group_detail = resp.json()
    assert group_detail["total_space_used"] == 2048
    assert group_detail["devices"][0]["total_space_used"] == 2048
    assert any(t["id"] == team.id and t["total_space_used"] == 2048 for t in group_detail["teams"])

    # 4. Test /api/v1/teams/
    resp = await auth_client.get("/api/v1/teams/")
    assert resp.status_code == 200
    teams_data = resp.json()
    assert any(t["id"] == team.id and t["total_space_used"] == 2048 for t in teams_data)

    # 5. Test /api/v1/teams/{id}
    resp = await auth_client.get(f"/api/v1/teams/{team.id}")
    assert resp.status_code == 200
    assert resp.json()["total_space_used"] == 2048

    # 6. Test /api/v1/teams/{id}/devices
    resp = await auth_client.get(f"/api/v1/teams/{team.id}/devices")
    assert resp.status_code == 200
    devices_data = resp.json()
    assert any(d["id"] == dev.id and d["total_space_used"] == 2048 for d in devices_data)

    # 7. Test /api/v1/dashboard/
    resp = await auth_client.get("/api/v1/dashboard/")
    assert resp.status_code == 200
    dash_data = resp.json()
    assert any(t["id"] == team.id and t["total_space_used"] == 2048 for t in dash_data["teams"])
    assert any(g["id"] == group.id and g["total_space_used"] == 2048 for g in dash_data["groups"])
    assert any(d["id"] == dev.id and d["total_space_used"] == 2048 for d in dash_data["devices"])


@pytest.mark.asyncio
async def test_compute_space_usage_managing_team_rollup(db_session):
    # 1. Create users
    superuser = User(email="super@example.com", password="hash", role=UserRole.user, verified=True)
    sub_admin = User(email="subadmin@example.com", password="hash", role=UserRole.user, verified=True)
    normal_user = User(email="normal@example.com", password="hash", role=UserRole.user, verified=True)
    db_session.add_all([superuser, sub_admin, normal_user])
    await db_session.flush()

    # 2. Create devices
    dev_a = Device(name="dev_a", token="tok_a", total_space_used=50)
    dev_b = Device(name="dev_b", token="tok_b", total_space_used=150)
    dev_c = Device(name="dev_c", token="tok_c", total_space_used=250)
    dev_shared = Device(name="dev_shared", token="tok_shared", total_space_used=100)
    db_session.add_all([dev_a, dev_b, dev_c, dev_shared])
    await db_session.flush()

    # 3. Create groups
    grp_root = DeviceGroup(name="grp_root")
    grp_managed = DeviceGroup(name="grp_managed")
    grp_sub = DeviceGroup(name="grp_sub")
    db_session.add_all([grp_root, grp_managed, grp_sub])
    await db_session.flush()

    await db_session.execute(
        device_group_devices.insert(),
        [
            {"device_group_id": grp_root.id, "device_id": dev_a.id},
            {"device_group_id": grp_root.id, "device_id": dev_shared.id},
            {"device_group_id": grp_managed.id, "device_id": dev_b.id},
            {"device_group_id": grp_managed.id, "device_id": dev_shared.id},
            {"device_group_id": grp_sub.id, "device_id": dev_c.id},
        ],
    )

    # 4. Create team hierarchy:
    # Root Team (admin=write, managing_team_id=None)
    # -> Managed Team (admin=write, managing_team_id=root_team.id)
    #    -> Sub-Managed Team (admin=None, managing_team_id=managed_team.id)
    root_team = Team(name="Root Team", permission_admin=PermissionAdmin.write, managing_team_id=None)
    db_session.add(root_team)
    await db_session.flush()

    managed_team = Team(name="Managed Team", permission_admin=PermissionAdmin.write, managing_team_id=root_team.id)
    db_session.add(managed_team)
    await db_session.flush()

    sub_managed = Team(name="Sub Managed Team", permission_admin=None, managing_team_id=managed_team.id)
    db_session.add(sub_managed)
    await db_session.flush()

    # 5. Link groups to teams
    await db_session.execute(
        team_device_groups.insert(),
        [
            {"team_id": root_team.id, "device_group_id": grp_root.id},
            {"team_id": managed_team.id, "device_group_id": grp_managed.id},
            {"team_id": sub_managed.id, "device_group_id": grp_sub.id},
        ],
    )

    # 6. Link users to teams
    await db_session.execute(
        team_users.insert(),
        [
            {"team_id": root_team.id, "user_id": superuser.id},
            {"team_id": managed_team.id, "user_id": sub_admin.id},
            {"team_id": sub_managed.id, "user_id": normal_user.id},
        ],
    )
    await db_session.commit()

    # Run space calculations
    await space_service.compute_all_space_usage(db_session)

    # Check teams
    t_root = (await db_session.execute(select(Team).where(Team.id == root_team.id))).scalar_one()
    t_managed = (await db_session.execute(select(Team).where(Team.id == managed_team.id))).scalar_one()
    t_sub = (await db_session.execute(select(Team).where(Team.id == sub_managed.id))).scalar_one()

    # Root team takes storage of its own groups and all managed teams' groups (transitively), deduplicated:
    # dev_a(50) + dev_shared(100) + dev_b(150) + dev_c(250) = 550
    assert t_root.total_space_used == 550
    # Managed teams take 0 storage space because their managing team takes it
    assert t_managed.total_space_used == 0
    assert t_sub.total_space_used == 0

    # Check users
    u_super = (await db_session.execute(select(User).where(User.id == superuser.id))).scalar_one()
    u_sub = (await db_session.execute(select(User).where(User.id == sub_admin.id))).scalar_one()
    u_norm = (await db_session.execute(select(User).where(User.id == normal_user.id))).scalar_one()

    # Superuser has root team, so gets all 550 quota counted
    assert u_super.total_space_used == 550
    # Sub-admin is only in managed team (has a managing team), so quota is NOT counted
    assert u_sub.total_space_used == 0
    assert u_norm.total_space_used == 0
