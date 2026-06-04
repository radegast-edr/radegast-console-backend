import pytest
from datetime import datetime, timezone
from httpx import AsyncClient


@pytest.mark.asyncio
class TestLogSubmission:
    async def test_submit_log(self, auth_client: AsyncClient, client: AsyncClient):
        # Get default group first
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        # Create device (automatically added to group)
        resp = await auth_client.post("/devices/", json={"name": "Logger-01", "group_id": group_id})
        token = resp.json()["token"]

        # Login as device
        await client.post("/auth/device/login", json={"token": token})

        # Submit log
        resp = await client.post(
            "/logs/",
            json={
                "time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "content": "encrypted-log-content-here",
                "signature": "sig-data",
                "severity": "low",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["content"] == "encrypted-log-content-here"
        assert resp.json()["severity"] == "low"

    async def test_submit_log_invalid_severity(self, auth_client: AsyncClient, client: AsyncClient):
        # Get default group
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        # Create device
        resp = await auth_client.post("/devices/", json={"name": "Logger-01b", "group_id": group_id})
        token = resp.json()["token"]

        # Login as device
        await client.post("/auth/device/login", json={"token": token})

        # Submit log with invalid severity
        resp = await client.post(
            "/logs/",
            json={
                "time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "content": "encrypted-log-content-here",
                "severity": "invalid-severity",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["severity"] is None
    async def test_submit_log_requires_device_session(self, auth_client: AsyncClient):
        resp = await auth_client.post(
            "/logs/",
            json={
                "time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "content": "some-content",
            },
        )
        assert resp.status_code == 403


@pytest.mark.asyncio
class TestLogRetrieval:
    async def test_list_logs_with_permission(self, auth_client: AsyncClient, client: AsyncClient):
        # Get default group first
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        # Create device (automatically added to group)
        resp = await auth_client.post("/devices/", json={"name": "Logger-02", "group_id": group_id})
        token = resp.json()["token"]

        # Login as device (overwrites user session on shared client)
        await client.post("/auth/device/login", json={"token": token})
        await client.post(
            "/logs/",
            json={
                "time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "content": "test-log-data",
                "severity": "critical",
            },
        )

        # Re-login as user to check logs (client == auth_client, device login overwrote session)
        await client.post("/auth/login", json={"email": "test@example.com", "password": "TestPass123!"})

        # List logs as user (has permission_logs: read on default team)
        resp = await auth_client.get("/logs/")
        assert resp.status_code == 200
        logs = resp.json()
        assert len(logs) >= 1
        
        # Check that severity is returned correctly in log list
        test_log = next(log for log in logs if log["content"] == "test-log-data")
        assert test_log["severity"] == "critical"

    async def test_list_logs_unauthenticated(self, client: AsyncClient):
        resp = await client.get("/logs/")
        assert resp.status_code == 401

    async def test_list_logs_filtered_by_device(
        self, auth_client: AsyncClient, client: AsyncClient
    ):
        # Get default group first
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        # Create device (automatically added to group)
        resp = await auth_client.post("/devices/", json={"name": "Logger-03", "group_id": group_id})
        token = resp.json()["token"]
        device_id = resp.json()["id"]

        # Login as device and submit log (overwrites user session on shared client)
        await client.post("/auth/device/login", json={"token": token})
        await client.post(
            "/logs/",
            json={"time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), "content": "filtered-log"},
        )

        # Re-login as user to query logs (client == auth_client)
        await client.post("/auth/login", json={"email": "test@example.com", "password": "TestPass123!"})

        resp = await auth_client.get(f"/logs/?device_id={device_id}")
        assert resp.status_code == 200
        logs = resp.json()
        assert len(logs) >= 1
        assert all(log["device_id"] == device_id for log in logs)


@pytest.mark.asyncio
class TestEncryptionKeys:
    async def test_get_encryption_keys(self, auth_client: AsyncClient, client: AsyncClient):
        # Get default group first
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        # Create device (automatically added to group)
        resp = await auth_client.post("/devices/", json={"name": "Logger-04", "group_id": group_id})
        token = resp.json()["token"]

        # Login as device
        await client.post("/auth/device/login", json={"token": token})

        resp = await client.get("/logs/encryption-keys")
        assert resp.status_code == 200
        # May be empty if no keys set up yet, but should not error
        assert isinstance(resp.json(), list)

    async def test_encryption_keys_requires_device_session(self, auth_client: AsyncClient):
        resp = await auth_client.get("/logs/encryption-keys")
        assert resp.status_code == 403


@pytest.mark.asyncio
class TestLogSeenAndResolution:
    async def test_seen_and_resolution_basic_vs_extended(self, auth_client: AsyncClient, client: AsyncClient):
        # 1. Get default group first
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        # 2. Create device
        resp = await auth_client.post("/devices/", json={"name": "Logger-SeenTest", "group_id": group_id})
        token = resp.json()["token"]

        # 3. Login as device and submit log
        await client.post("/auth/device/login", json={"token": token})
        resp = await client.post(
            "/logs/",
            json={
                "time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "content": "seen-test-log",
                "severity": "high",
            },
        )
        assert resp.status_code == 200
        log_id = resp.json()["id"]

        # 4. Re-login as user (default: basic mode, extended_edr_enabled=False)
        await client.post("/auth/login", json={"email": "test@example.com", "password": "TestPass123!"})

        # Check logs initially
        resp = await auth_client.get("/logs/")
        assert resp.status_code == 200
        logs = resp.json()
        log = next(l for l in logs if l["id"] == log_id)
        assert log["seen"] is False

        # Unread count should be >= 1 for this test log
        resp = await auth_client.get("/logs/unread-count")
        assert resp.status_code == 200
        initial_unread = resp.json()["unread_count"]
        assert initial_unread >= 1

        # Mark seen (basic mode: seeing = reading, no resolution required)
        resp = await auth_client.post(f"/logs/{log_id}/seen")
        assert resp.status_code == 200

        # Now seen should be True
        resp = await auth_client.get("/logs/")
        log = next(l for l in resp.json() if l["id"] == log_id)
        assert log["seen"] is True

        # In basic mode, marking seen immediately removes the log from the active count
        # (basic mode does NOT require a resolution, seeing the alert is enough).
        resp = await auth_client.get("/logs/unread-count")
        assert resp.json()["unread_count"] == initial_unread - 1
        after_seen_unread = resp.json()["unread_count"]

        # Resolve log
        resp = await auth_client.patch(
            f"/logs/{log_id}/resolve",
            json={"alert_resolution": "true_positive", "triage_note": "A note"},
        )
        assert resp.status_code == 200
        assert resp.json()["seen"] is True

        # Seen status should remain True after resolution
        resp = await auth_client.get("/logs/")
        log = next(l for l in resp.json() if l["id"] == log_id)
        assert log["seen"] is True

        # Unread count should remain the same (log was already removed when seen)
        resp = await auth_client.get("/logs/unread-count")
        assert resp.json()["unread_count"] == after_seen_unread

        # 5. Enable Extended EDR Mode
        resp = await auth_client.put("/auth/extended-edr", json={"extended_edr_enabled": True})
        assert resp.status_code == 200
        assert resp.json()["extended_edr_enabled"] is True

        # In extended EDR, the resolved log should NOT be counted as active
        resp = await auth_client.get("/logs/unread-count")
        edr_after_resolved = resp.json()["unread_count"]

        # Clear resolution (set to None) — in extended EDR, this should re-activate the log
        resp = await auth_client.patch(
            f"/logs/{log_id}/resolve",
            json={"alert_resolution": None, "triage_note": ""},
        )
        assert resp.status_code == 200
        # Clearing resolution in extended EDR mode should NOT mark as seen
        assert resp.json()["seen"] is True  # was already seen before; stays seen

        # Unread count in extended EDR should increase by 1 (log is now unresolved)
        resp = await auth_client.get("/logs/unread-count")
        assert resp.json()["unread_count"] == edr_after_resolved + 1

        resp = await auth_client.get("/logs/")
        log = next(l for l in resp.json() if l["id"] == log_id)
        assert log["seen"] is True

        # Resolve again
        resp = await auth_client.patch(
            f"/logs/{log_id}/resolve",
            json={"alert_resolution": "false_positive", "triage_note": "FP note"},
        )
        assert resp.status_code == 200
        assert resp.json()["seen"] is True

        resp = await auth_client.get("/logs/")
        log = next(l for l in resp.json() if l["id"] == log_id)
        assert log["seen"] is True

        # After resolving in extended EDR, unread count should go back down
        resp = await auth_client.get("/logs/unread-count")
        assert resp.json()["unread_count"] == edr_after_resolved

        # Cleanup: restore basic EDR mode
        await auth_client.put("/auth/extended-edr", json={"extended_edr_enabled": False})

    async def test_basic_mode_unread_count_tracks_seen_not_resolution(
        self, auth_client: AsyncClient, client: AsyncClient
    ):
        """In basic mode the unread counter tracks 'seen' status only.
        A log that has been seen but not resolved should NOT appear in the count."""
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        resp = await auth_client.post("/devices/", json={"name": "Logger-BasicCount", "group_id": group_id})
        token = resp.json()["token"]

        await client.post("/auth/device/login", json={"token": token})
        resp = await client.post(
            "/logs/",
            json={
                "time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "content": "basic-count-test-log",
                "severity": "medium",
            },
        )
        log_id = resp.json()["id"]

        await client.post("/auth/login", json={"email": "test@example.com", "password": "TestPass123!"})

        # Initial unread count
        resp = await auth_client.get("/logs/unread-count")
        before = resp.json()["unread_count"]

        # Mark seen (NO resolution)
        await auth_client.post(f"/logs/{log_id}/seen")

        # Count should decrease immediately — basic mode only tracks seen status
        resp = await auth_client.get("/logs/unread-count")
        assert resp.json()["unread_count"] == before - 1

    async def test_extended_edr_unread_count_tracks_resolution_not_seen(
        self, auth_client: AsyncClient, client: AsyncClient
    ):
        """In extended EDR mode the unread counter tracks resolution status.
        A log that has been seen but not resolved should STILL appear in the count."""
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        resp = await auth_client.post("/devices/", json={"name": "Logger-EDRCount", "group_id": group_id})
        token = resp.json()["token"]

        await client.post("/auth/device/login", json={"token": token})
        resp = await client.post(
            "/logs/",
            json={
                "time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "content": "edr-count-test-log",
                "severity": "high",
            },
        )
        log_id = resp.json()["id"]

        await client.post("/auth/login", json={"email": "test@example.com", "password": "TestPass123!"})

        # Enable extended EDR
        await auth_client.put("/auth/extended-edr", json={"extended_edr_enabled": True})

        resp = await auth_client.get("/logs/unread-count")
        before = resp.json()["unread_count"]

        # Mark seen (NO resolution) — in extended EDR, count must NOT change
        await auth_client.post(f"/logs/{log_id}/seen")

        resp = await auth_client.get("/logs/unread-count")
        assert resp.json()["unread_count"] == before  # unchanged

        # Now resolve — count should decrease
        await auth_client.patch(
            f"/logs/{log_id}/resolve",
            json={"alert_resolution": "true_positive", "triage_note": ""},
        )

        resp = await auth_client.get("/logs/unread-count")
        assert resp.json()["unread_count"] == before - 1

        # Cleanup
        await auth_client.put("/auth/extended-edr", json={"extended_edr_enabled": False})

    async def test_get_log_encryption_keys_for_user(self, auth_client: AsyncClient, client: AsyncClient):
        # 1. Get default group first
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        # 2. Create device
        resp = await auth_client.post("/devices/", json={"name": "Logger-KeysTest", "group_id": group_id})
        token = resp.json()["token"]

        # 3. Login as device and submit log
        await client.post("/auth/device/login", json={"token": token})
        resp = await client.post(
            "/logs/",
            json={
                "time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "content": "key-test-log",
            },
        )
        assert resp.status_code == 200
        log_id = resp.json()["id"]

        # 4. Re-login as user
        await client.post("/auth/login", json={"email": "test@example.com", "password": "TestPass123!"})

        # 5. Fetch keys as user
        resp = await auth_client.get(f"/logs/{log_id}/encryption-keys")
        assert resp.status_code == 200
        keys = resp.json()
        assert isinstance(keys, list)

    async def test_get_log_device_keys_for_triage(self, auth_client: AsyncClient, client: AsyncClient):
        """The new /device-keys endpoint (used for triage note encryption) should return
        the same set of device-based public keys as the existing /encryption-keys endpoint,
        because both use the shared get_device_encryption_keys_list utility."""
        # 1. Get default group
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        # 2. Create device
        resp = await auth_client.post("/devices/", json={"name": "Logger-DeviceKeys", "group_id": group_id})
        token = resp.json()["token"]

        # 3. Login as device and submit log
        await client.post("/auth/device/login", json={"token": token})
        resp = await client.post(
            "/logs/",
            json={
                "time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "content": "device-keys-test-log",
            },
        )
        assert resp.status_code == 200
        log_id = resp.json()["id"]

        # 4. Re-login as user
        await client.post("/auth/login", json={"email": "test@example.com", "password": "TestPass123!"})

        # 5. Fetch via new /device-keys endpoint
        resp = await auth_client.get(f"/logs/{log_id}/device-keys")
        assert resp.status_code == 200
        device_keys = resp.json()
        assert isinstance(device_keys, list)

        # 6. Fetch via existing /encryption-keys endpoint and compare
        resp2 = await auth_client.get(f"/logs/{log_id}/encryption-keys")
        assert resp2.status_code == 200
        enc_keys = resp2.json()

        # Both endpoints share the same utility and must return identical results
        assert sorted(device_keys, key=lambda k: k["user_id"]) == sorted(enc_keys, key=lambda k: k["user_id"])

    async def test_device_keys_requires_authentication(self, auth_client: AsyncClient, client: AsyncClient):
        """The /device-keys endpoint must reject unauthenticated users."""
        # 1. Get default group
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        # 2. Create device and submit a log
        resp = await auth_client.post("/devices/", json={"name": "Logger-DeviceKeys-Auth", "group_id": group_id})
        token = resp.json()["token"]
        await client.post("/auth/device/login", json={"token": token})
        resp = await client.post(
            "/logs/",
            json={
                "time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                "content": "auth-test-log",
            },
        )
        assert resp.status_code == 200
        log_id = resp.json()["id"]

        # 3. Access without a user session (still has device session) must be rejected
        resp = await client.get(f"/logs/{log_id}/device-keys")
        assert resp.status_code in (401, 403)

