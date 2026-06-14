from httpx import AsyncClient
import pytest

from app.dependencies import rate_limit_login, rate_limit_mfa, rate_limit_mfa_otp
from app.main import app

@pytest.mark.asyncio
class TestRateLimits:
    async def test_login_rate_limit(self, client: AsyncClient):
        # 1. Register a user so we can try logging in
        email = "rate_limit_test@example.com"
        password = "TestPass123!"
        await client.post("/auth/register", json={"email": email, "password": password})

        # Temporarily enable rate limiting for login
        if rate_limit_login in app.dependency_overrides:
            del app.dependency_overrides[rate_limit_login]

        # Reset application rate limits dict for this test context
        app.state.rate_limits.clear()

        try:
            # Make 5 attempts - they should return 401/403 or succeed depending on input
            # Here we send invalid password so we get 401
            for _ in range(5):
                resp = await client.post("/auth/login", json={"email": email, "password": "WrongPassword"})
                assert resp.status_code == 401

            # The 6th attempt must trigger the rate limit and return 429
            resp = await client.post("/auth/login", json={"email": email, "password": "WrongPassword"})
            assert resp.status_code == 429
            assert "Too many attempts" in resp.json()["detail"]

        finally:
            # Restore the override so other tests aren't affected
            app.dependency_overrides[rate_limit_login] = lambda: None

    async def test_mfa_verify_rate_limit(self, client: AsyncClient):
        # Temporarily enable rate limiting for MFA verification
        if rate_limit_mfa in app.dependency_overrides:
            del app.dependency_overrides[rate_limit_mfa]

        # Reset application rate limits dict
        app.state.rate_limits.clear()

        try:
            # Make 5 attempts with dummy invalid tokens
            for _ in range(5):
                resp = await client.post("/auth/mfa/verify", json={"mfa_token": "dummy", "method": "otp", "otp_code": "000000"})
                assert resp.status_code == 400

            # The 6th attempt must return 429
            resp = await client.post("/auth/mfa/verify", json={"mfa_token": "dummy", "method": "otp", "otp_code": "000000"})
            assert resp.status_code == 429
            assert "Too many attempts" in resp.json()["detail"]

        finally:
            # Restore the override
            app.dependency_overrides[rate_limit_mfa] = lambda: None

    async def test_mfa_otp_verify_rate_limit(self, auth_client: AsyncClient):
        # auth_client is already logged in
        # Temporarily enable rate limiting for MFA OTP setup verification
        if rate_limit_mfa_otp in app.dependency_overrides:
            del app.dependency_overrides[rate_limit_mfa_otp]

        # Reset application rate limits dict
        app.state.rate_limits.clear()

        try:
            # Make 5 setup attempts with invalid OTP codes
            for _ in range(5):
                resp = await auth_client.post("/user/mfa/otp/verify", json={"code": "123456"})
                # Should fail because setup not initiated
                assert resp.status_code == 400

            # The 6th attempt must return 429
            resp = await auth_client.post("/user/mfa/otp/verify", json={"code": "123456"})
            assert resp.status_code == 429
            assert "Too many attempts" in resp.json()["detail"]

        finally:
            # Restore the override
            app.dependency_overrides[rate_limit_mfa_otp] = lambda: None
