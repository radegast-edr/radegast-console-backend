from datetime import UTC, datetime

import pytest
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
                "time": datetime.now(UTC).replace(tzinfo=None).isoformat(),
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
                "time": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                "content": "encrypted-log-content-here",
                "severity": "invalid-severity",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["severity"] is None

    async def test_submit_log_with_exclusions(self, auth_client: AsyncClient, client: AsyncClient):
        # 1. Get default group first
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        # 2. Enable extended EDR mode
        resp = await auth_client.put("/user/extended-edr", json={"extended_edr_enabled": True})
        assert resp.status_code == 200

        # 3. Create a soft exclusion and a hard exclusion
        resp = await auth_client.post(
            f"/exclusions/groups/{group_id}",
            json={"name": "Soft Exclusion", "jsonata_query": "$contains(alert.rule.name, 'soft')", "exclusion_type": "soft"},
        )
        assert resp.status_code == 200
        soft_exclusion_id = resp.json()["id"]

        resp = await auth_client.post(
            f"/exclusions/groups/{group_id}",
            json={"name": "Hard Exclusion", "jsonata_query": "$contains(alert.rule.name, 'hard')", "exclusion_type": "hard"},
        )
        assert resp.status_code == 200
        hard_exclusion_id = resp.json()["id"]

        # 4. Create device and login
        resp = await auth_client.post("/devices/", json={"name": "Exclusion-Logger", "group_id": group_id})
        token = resp.json()["token"]
        await client.post("/auth/device/login", json={"token": token})

        # 5. Submit log with soft exclusion id
        resp = await client.post(
            "/logs/",
            json={
                "time": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                "content": "encrypted-log-content-here",
                "severity": "high",
                "excluded_by": soft_exclusion_id,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["severity"] == "informational"
        assert data["excluded_by"]["id"] == soft_exclusion_id
        assert data["excluded_by"]["group"]["id"] == group_id

        # 6. Submit log with hard exclusion id
        resp = await client.post(
            "/logs/",
            json={
                "time": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                "content": "encrypted-log-content-here",
                "severity": "high",
                "excluded_by": hard_exclusion_id,
            },
        )
        assert resp.status_code == 400
        assert "Cannot submit log matching a hard exclusion" in resp.json()["detail"]

        # 7. Disable extended EDR mode
        await auth_client.put("/user/extended-edr", json={"extended_edr_enabled": False})

    async def test_submit_log_requires_device_session(self, auth_client: AsyncClient):
        resp = await auth_client.post(
            "/logs/",
            json={
                "time": datetime.now(UTC).replace(tzinfo=None).isoformat(),
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
                "time": datetime.now(UTC).replace(tzinfo=None).isoformat(),
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

    async def test_list_logs_filtered_by_device(self, auth_client: AsyncClient, client: AsyncClient):
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
            json={"time": datetime.now(UTC).replace(tzinfo=None).isoformat(), "content": "filtered-log"},
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
                "time": datetime.now(UTC).replace(tzinfo=None).isoformat(),
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
        resp = await auth_client.get("/logs/count?unread_only=true")
        assert resp.status_code == 200
        initial_unread = resp.json()["total_count"]
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
        resp = await auth_client.get("/logs/count?unread_only=true")
        assert resp.json()["total_count"] == initial_unread - 1
        after_seen_unread = resp.json()["total_count"]

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
        resp = await auth_client.get("/logs/count?unread_only=true")
        assert resp.json()["total_count"] == after_seen_unread

        # 5. Enable Extended EDR Mode
        resp = await auth_client.put("/user/extended-edr", json={"extended_edr_enabled": True})
        assert resp.status_code == 200
        assert resp.json()["extended_edr_enabled"] is True

        # In extended EDR, the resolved log should NOT be counted as active
        resp = await auth_client.get("/logs/count?unread_only=true")
        edr_after_resolved = resp.json()["total_count"]

        # Clear resolution (set to None) — in extended EDR, this should re-activate the log
        resp = await auth_client.patch(
            f"/logs/{log_id}/resolve",
            json={"alert_resolution": None, "triage_note": ""},
        )
        assert resp.status_code == 200
        # Clearing resolution in extended EDR mode should NOT mark as seen
        assert resp.json()["seen"] is True  # was already seen before; stays seen

        # Unread count in extended EDR should increase by 1 (log is now unresolved)
        resp = await auth_client.get("/logs/count?unread_only=true")
        assert resp.json()["total_count"] == edr_after_resolved + 1

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
        resp = await auth_client.get("/logs/count?unread_only=true")
        assert resp.json()["total_count"] == edr_after_resolved

        # Cleanup: restore basic EDR mode
        await auth_client.put("/user/extended-edr", json={"extended_edr_enabled": False})

    async def test_basic_mode_unread_count_tracks_seen_not_resolution(self, auth_client: AsyncClient, client: AsyncClient):
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
                "time": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                "content": "basic-count-test-log",
                "severity": "medium",
            },
        )
        log_id = resp.json()["id"]

        await client.post("/auth/login", json={"email": "test@example.com", "password": "TestPass123!"})

        # Initial unread count
        resp = await auth_client.get("/logs/count?unread_only=true")
        before = resp.json()["total_count"]

        # Mark seen (NO resolution)
        await auth_client.post(f"/logs/{log_id}/seen")

        # Count should decrease immediately — basic mode only tracks seen status
        resp = await auth_client.get("/logs/count?unread_only=true")
        assert resp.json()["total_count"] == before - 1

    async def test_extended_edr_unread_count_tracks_resolution_not_seen(self, auth_client: AsyncClient, client: AsyncClient):
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
                "time": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                "content": "edr-count-test-log",
                "severity": "high",
            },
        )
        log_id = resp.json()["id"]

        await client.post("/auth/login", json={"email": "test@example.com", "password": "TestPass123!"})

        # Enable extended EDR
        await auth_client.put("/user/extended-edr", json={"extended_edr_enabled": True})

        resp = await auth_client.get("/logs/count?unread_only=true")
        before = resp.json()["total_count"]

        # Mark seen (NO resolution) — in extended EDR, count must NOT change
        await auth_client.post(f"/logs/{log_id}/seen")

        resp = await auth_client.get("/logs/count?unread_only=true")
        assert resp.json()["total_count"] == before  # unchanged

        # Now resolve — count should decrease
        await auth_client.patch(
            f"/logs/{log_id}/resolve",
            json={"alert_resolution": "true_positive", "triage_note": ""},
        )

        resp = await auth_client.get("/logs/count?unread_only=true")
        assert resp.json()["total_count"] == before - 1

        # Cleanup
        await auth_client.put("/user/extended-edr", json={"extended_edr_enabled": False})

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
                "time": datetime.now(UTC).replace(tzinfo=None).isoformat(),
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
                "time": datetime.now(UTC).replace(tzinfo=None).isoformat(),
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
                "time": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                "content": "auth-test-log",
            },
        )
        assert resp.status_code == 200
        log_id = resp.json()["id"]

        # 3. Access without a user session (still has device session) must be rejected
        resp = await client.get(f"/logs/{log_id}/device-keys")
        assert resp.status_code in (401, 403)


@pytest.mark.asyncio
class TestTriggeredRule:
    """Tests for rule_type submission and triggered_rule lookup from enabled pack zips."""

    def _make_pack_zip(self, pack_files: dict[str, str]) -> bytes:
        """Create a valid pack zip containing the specified files."""
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for path, content in pack_files.items():
                zf.writestr(path, content)
        return buf.getvalue()

    async def _setup_device_with_pack(
        self,
        auth_client: AsyncClient,
        client: AsyncClient,
        pack_files: dict[str, str],
        device_name: str = "Logger-Rule",
        pack_name: str = "Rule-Pack",
    ) -> tuple[str, int]:
        """
        Create a device, create a pack with a sigma rule, enable the pack for the
        device's group, and return (device_token, group_id).
        """
        # Get group
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        # Create device
        resp = await auth_client.post("/devices/", json={"name": device_name, "group_id": group_id})
        assert resp.status_code == 200
        token = resp.json()["token"]

        # Create pack + upload version
        resp = await auth_client.post("/packs/", json={"name": pack_name, "description": "Test pack"})
        assert resp.status_code == 200
        pack_id = resp.json()["id"]

        zip_bytes = self._make_pack_zip(pack_files)
        resp = await auth_client.post(
            f"/packs/{pack_id}/versions?version=1.0.0",
            files={"file": ("pack.zip", zip_bytes, "application/zip")},
        )
        assert resp.status_code == 200
        pack_version_id = resp.json()["id"]

        # Enable pack for the device group
        resp = await auth_client.post(
            f"/packs/groups/{group_id}/enable",
            json={"pack_version_id": pack_version_id, "autoupdate": False},
        )
        assert resp.status_code == 200

        return token, group_id

    async def test_submit_log_with_rule_type_stored(self, auth_client: AsyncClient, client: AsyncClient) -> None:
        """Submitting a log with rule_id + rule_type persists rule_type on the log row."""
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        resp = await auth_client.post("/devices/", json={"name": "Logger-RuleType", "group_id": group_id})
        token = resp.json()["token"]

        await client.post("/auth/device/login", json={"token": token})
        resp = await client.post(
            "/logs/",
            json={
                "time": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                "content": "encrypted-content",
                "rule_id": "test_rule",
                "rule_type": "sigma",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["rule_id"] == "test_rule"
        assert data["rule_type"] == "sigma"

    async def test_submit_log_with_invalid_rule_type_ignored(self, auth_client: AsyncClient, client: AsyncClient) -> None:
        """An unrecognised rule_type value is silently ignored (None stored)."""
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        resp = await auth_client.post("/devices/", json={"name": "Logger-BadType", "group_id": group_id})
        token = resp.json()["token"]

        await client.post("/auth/device/login", json={"token": token})
        resp = await client.post(
            "/logs/",
            json={
                "time": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                "content": "encrypted-content",
                "rule_id": "some_rule",
                "rule_type": "unknown_type",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # rule_type was invalid so it gets coerced to None
        assert data["rule_type"] is None

    async def test_triggered_rule_from_pack_all_types(
        self, auth_client: AsyncClient, client: AsyncClient, maintainer_client: AsyncClient
    ) -> None:
        """When rule_id + rule_type match a rule in an enabled pack, triggered_rule is returned inline."""
        sigma_id = "a3c5c821-004a-4e52-8684-0f7f9ea0404c"
        sigma_content = f"id: {sigma_id}\ntitle: Linux Reverse Shell via /dev/tcp\ndetection:\n  condition: selection\n"

        yara_id = "lnx_susp_xmrig_coinminer"
        yara_content = f'rule {yara_id} {{\n    strings:\n        $a = "xmrig"\n    condition:\n        $a\n}}\n'

        ioc_id = "domain::example.com"
        ioc_content = "example.com\n"

        pack_files = {
            "sigma/proc_creation_lnx_shell_dev_tcp_reverse_shell.yml": sigma_content,
            "yara/lnx_susp_xmrig_coinminer.yar": yara_content,
            "ioc/domains.txt": ioc_content,
        }

        token, _ = await self._setup_device_with_pack(
            maintainer_client,
            client,
            pack_files=pack_files,
            device_name="Logger-TriggeredRule",
            pack_name="TriggeredRule-Pack",
        )

        # Submit log as device
        await client.post("/auth/device/login", json={"token": token})

        # 1) Sigma
        resp = await client.post(
            "/logs/",
            json={
                "time": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                "content": "encrypted-content-1",
                "rule_id": sigma_id,
                "rule_type": "sigma",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["triggered_rule"]["rule_content"] == sigma_content

        # 2) Yara
        resp = await client.post(
            "/logs/",
            json={
                "time": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                "content": "encrypted-content-2",
                "rule_id": yara_id,
                "rule_type": "yara",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["triggered_rule"]["rule_content"] == yara_content

        # 3) IOC
        resp = await client.post(
            "/logs/",
            json={
                "time": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                "content": "encrypted-content-3",
                "rule_id": ioc_id,
                "rule_type": "ioc",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["triggered_rule"]["rule_content"] == ioc_content

        # Re-login as maintainer and verify triggered_rules come back in list endpoint
        await client.post("/auth/login", json={"email": "maintainer@example.com", "password": "MaintainerPass123!"})
        resp = await maintainer_client.get("/logs/")
        assert resp.status_code == 200
        logs = resp.json()

        matching_sigma = [lg for lg in logs if lg.get("rule_id") == sigma_id]
        assert len(matching_sigma) >= 1
        assert matching_sigma[0]["triggered_rule"] is not None
        assert matching_sigma[0]["triggered_rule"]["rule_content"] == sigma_content

        matching_yara = [lg for lg in logs if lg.get("rule_id") == yara_id]
        assert len(matching_yara) >= 1
        assert matching_yara[0]["triggered_rule"] is not None
        assert matching_yara[0]["triggered_rule"]["rule_content"] == yara_content

        matching_ioc = [lg for lg in logs if lg.get("rule_id") == ioc_id]
        assert len(matching_ioc) >= 1
        assert matching_ioc[0]["triggered_rule"] is not None
        assert matching_ioc[0]["triggered_rule"]["rule_content"] == ioc_content

    async def test_triggered_rule_not_found_returns_null(self, auth_client: AsyncClient, client: AsyncClient) -> None:
        """If the rule_id doesn't exist in any enabled pack, triggered_rule is null."""
        resp = await auth_client.get("/teams/")
        team_id = resp.json()[0]["id"]
        resp = await auth_client.get(f"/teams/{team_id}/groups")
        group_id = resp.json()[0]["id"]

        resp = await auth_client.post("/devices/", json={"name": "Logger-NoRule", "group_id": group_id})
        token = resp.json()["token"]

        await client.post("/auth/device/login", json={"token": token})
        resp = await client.post(
            "/logs/",
            json={
                "time": datetime.now(UTC).replace(tzinfo=None).isoformat(),
                "content": "encrypted-content",
                "rule_id": "nonexistent_rule_xyz",
                "rule_type": "sigma",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["triggered_rule"] is None
