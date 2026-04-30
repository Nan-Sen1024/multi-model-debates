from __future__ import annotations

import asyncio
import json
import os
import tempfile
import time
import uuid
from urllib.parse import parse_qs, urlparse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.auth_flow import (
    FLOW_AWS_IAM,
    FLOW_GENERIC_OAUTH,
    FLOW_OPENAI_CODEX,
    STATUS_AWAITING_ROLE,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_EXPIRED,
    STATUS_FAILED,
    STATUS_PENDING,
    AuthFlowManager,
)
from backend.database import init_db
from backend.llm_gateway import encrypt_sensitive_value, _obfuscate, serialize_auth_config
from backend.models import AuthConfig, OAuthToken
from backend.enums import AuthType


# ... keep existing content above unchanged ...


def run(coro):
    return asyncio.run(coro)


def tmp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def make_manager(db_path: str) -> AuthFlowManager:
    return AuthFlowManager(db_path=db_path)


async def _insert_provider(db_path: str) -> str:
    """插入一个测试 provider，返回 provider_id。"""
    import aiosqlite
    await init_db(db_path)
    provider_id = str(uuid.uuid4())
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """INSERT INTO provider_configs
               (id, name, provider_type, base_url, api_format, auth_type, is_active)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (provider_id, "test-provider", "custom", None, "openai-completions", "iam"),
        )
        await db.commit()
    return provider_id


# ---------------------------------------------------------------------------
# SSOOIDCClient GovCloud 检测（已有测试，这里补充 AuthFlowManager 层面）
# ---------------------------------------------------------------------------

class TestGovCloudDetection:
    def test_govcloud_url_detected(self):
        from backend.llm_gateway import SSOOIDCClient
        client = SSOOIDCClient(
            sso_start_url="https://start.us-gov-home.awsapps.com/directory/abc",
            sso_region="us-gov-west-1",
        )
        assert client._is_govcloud() is True
        assert "amazonaws-us-gov.com" in client._oidc_endpoint()

    def test_standard_url_not_govcloud(self):
        from backend.llm_gateway import SSOOIDCClient
        client = SSOOIDCClient(
            sso_start_url="https://mycompany.awsapps.com/start",
            sso_region="us-east-1",
        )
        assert client._is_govcloud() is False
        assert "amazonaws-us-gov.com" not in client._oidc_endpoint()


# ---------------------------------------------------------------------------
# AuthFlowManager.get_status — 不存在的 session 抛 ValueError
# ---------------------------------------------------------------------------

def test_get_status_not_found():
    db_path = tmp_db()
    try:
        manager = make_manager(db_path)
        run(init_db(db_path))
        with pytest.raises(ValueError, match="not found"):
            run(manager.get_status("nonexistent-id"))
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# AWS IAM flow — mock httpx，验证状态机流转
# ---------------------------------------------------------------------------

class TestAwsIamFlow:
    def test_start_flow_returns_verification_uri(self):
        db_path = tmp_db()
        try:
            provider_id = run(_insert_provider(db_path))
            manager = make_manager(db_path)

            mock_reg = {"clientId": "cid", "clientSecret": "csec"}
            mock_auth = {
                "verificationUriComplete": "https://device.sso.us-east-1.amazonaws.com/verify?user_code=ABCD-1234",
                "userCode": "ABCD-1234",
                "deviceCode": "device-code-xyz",
                "expiresIn": 300,
                "interval": 5,
            }

            with patch("backend.auth_flow.SSOOIDCClient") as MockOIDC:
                instance = MockOIDC.return_value
                instance.register_client.return_value = mock_reg
                instance.start_device_authorization.return_value = mock_auth
                instance._oidc_endpoint.return_value = "https://oidc.us-east-1.amazonaws.com"

                result = run(manager.start_flow(
                    provider_id=provider_id,
                    flow_type=FLOW_AWS_IAM,
                    sso_start_url="https://mycompany.awsapps.com/start",
                    sso_region="us-east-1",
                ))

            assert result.verification_uri == mock_auth["verificationUriComplete"]
            assert result.user_code == "ABCD-1234"
            assert result.flow_type == FLOW_AWS_IAM
            assert result.auth_session_id
        finally:
            os.unlink(db_path)

    def test_start_flow_persists_to_db(self):
        import aiosqlite
        db_path = tmp_db()
        try:
            provider_id = run(_insert_provider(db_path))
            manager = make_manager(db_path)

            mock_reg = {"clientId": "cid", "clientSecret": "csec"}
            mock_auth = {
                "verificationUriComplete": "https://verify.example.com?code=XY",
                "userCode": "XY-1234",
                "deviceCode": "dc-abc",
                "expiresIn": 300,
                "interval": 5,
            }

            with patch("backend.auth_flow.SSOOIDCClient") as MockOIDC:
                instance = MockOIDC.return_value
                instance.register_client.return_value = mock_reg
                instance.start_device_authorization.return_value = mock_auth
                instance._oidc_endpoint.return_value = "https://oidc.us-east-1.amazonaws.com"

                result = run(manager.start_flow(
                    provider_id=provider_id,
                    flow_type=FLOW_AWS_IAM,
                    sso_start_url="https://mycompany.awsapps.com/start",
                    sso_region="us-east-1",
                ))

            async def _check():
                async with aiosqlite.connect(db_path) as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute(
                        "SELECT * FROM auth_sessions WHERE id = ?", (result.auth_session_id,)
                    ) as cur:
                        return await cur.fetchone()

            row = run(_check())
            assert row is not None
            assert row["status"] == STATUS_PENDING
            assert row["flow_type"] == FLOW_AWS_IAM
            assert row["user_code"] == "XY-1234"
            assert row["provider_id"] == provider_id
        finally:
            os.unlink(db_path)

    def test_get_status_pending(self):
        db_path = tmp_db()
        try:
            provider_id = run(_insert_provider(db_path))
            manager = make_manager(db_path)

            mock_reg = {"clientId": "cid", "clientSecret": "csec"}
            mock_auth = {
                "verificationUriComplete": "https://verify.example.com",
                "userCode": "AB-CD",
                "deviceCode": "dc",
                "expiresIn": 300,
                "interval": 5,
            }

            with patch("backend.auth_flow.SSOOIDCClient") as MockOIDC:
                instance = MockOIDC.return_value
                instance.register_client.return_value = mock_reg
                instance.start_device_authorization.return_value = mock_auth
                instance._oidc_endpoint.return_value = "https://oidc.us-east-1.amazonaws.com"

                result = run(manager.start_flow(
                    provider_id=provider_id,
                    flow_type=FLOW_AWS_IAM,
                    sso_start_url="https://mycompany.awsapps.com/start",
                    sso_region="us-east-1",
                ))

            for task in list(manager._poll_tasks.values()):
                task.cancel()

            status = run(manager.get_status(result.auth_session_id))
            assert status.status == STATUS_PENDING
            assert status.flow_type == FLOW_AWS_IAM
        finally:
            os.unlink(db_path)

    def test_bind_aws_role_requires_awaiting_role_status(self):
        db_path = tmp_db()
        try:
            provider_id = run(_insert_provider(db_path))
            manager = make_manager(db_path)

            mock_reg = {"clientId": "cid", "clientSecret": "csec"}
            mock_auth = {
                "verificationUriComplete": "https://verify.example.com",
                "userCode": "AB-CD",
                "deviceCode": "dc",
                "expiresIn": 300,
                "interval": 5,
            }

            with patch("backend.auth_flow.SSOOIDCClient") as MockOIDC:
                instance = MockOIDC.return_value
                instance.register_client.return_value = mock_reg
                instance.start_device_authorization.return_value = mock_auth
                instance._oidc_endpoint.return_value = "https://oidc.us-east-1.amazonaws.com"

                result = run(manager.start_flow(
                    provider_id=provider_id,
                    flow_type=FLOW_AWS_IAM,
                    sso_start_url="https://mycompany.awsapps.com/start",
                    sso_region="us-east-1",
                ))

            for task in list(manager._poll_tasks.values()):
                task.cancel()

            with pytest.raises(ValueError, match="awaiting_role"):
                run(manager.bind_aws_role(result.auth_session_id, "123456789", "AdministratorAccess"))
        finally:
            os.unlink(db_path)

    def test_bind_aws_role_writes_sigv4_to_provider(self):
        import aiosqlite
        db_path = tmp_db()
        try:
            provider_id = run(_insert_provider(db_path))
            manager = make_manager(db_path)

            auth_session_id = str(uuid.uuid4())
            now = int(time.time())
            fake_access_token = "fake-access-token"

            async def _insert_session():
                async with aiosqlite.connect(db_path) as db:
                    await db.execute(
                        """INSERT INTO auth_sessions
                           (id, provider_id, flow_type, status, verification_uri, user_code,
                            device_code, client_id, client_secret, interval, expires_at,
                            access_token, accounts_json, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            auth_session_id,
                            provider_id,
                            FLOW_AWS_IAM,
                            STATUS_AWAITING_ROLE,
                            "https://verify.example.com",
                            "AB-CD",
                            encrypt_sensitive_value("dc"),
                            "https://mycompany.awsapps.com/start",
                            "us-east-1",
                            5,
                            now + 300,
                            encrypt_sensitive_value(fake_access_token),
                            json.dumps([{"accountId": "123456789", "accountName": "Test", "emailAddress": "test@example.com"}]),
                            now,
                            now,
                        ),
                    )
                    await db.commit()

            run(_insert_session())

            mock_creds = {
                "accessKeyId": "AKIAIOSFODNN7EXAMPLE",
                "secretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                "sessionToken": "AQoXnyc4lcK4w...",
            }

            with patch("backend.auth_flow.SSOOIDCClient") as MockOIDC:
                instance = MockOIDC.return_value
                instance.get_role_credentials.return_value = mock_creds

                result = run(manager.bind_aws_role(auth_session_id, "123456789", "AdministratorAccess"))

            assert result.account_id == "123456789"
            assert result.role_name == "AdministratorAccess"

            async def _check_provider():
                async with aiosqlite.connect(db_path) as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute(
                        "SELECT auth_type, auth_config FROM provider_configs WHERE id = ?",
                        (provider_id,),
                    ) as cur:
                        return await cur.fetchone()

            row = run(_check_provider())
            assert row["auth_type"] == "iam"
            auth_config = json.loads(row["auth_config"])
            assert "metadata" in auth_config
            assert auth_config["metadata"]["accessKeyId"] == "AKIAIOSFODNN7EXAMPLE"
        finally:
            os.unlink(db_path)

    def test_start_flow_rejects_concurrent_provider_flow(self):
        db_path = tmp_db()
        try:
            provider_id = run(_insert_provider(db_path))
            manager = make_manager(db_path)

            mock_reg = {"clientId": "cid", "clientSecret": "csec"}
            mock_auth = {
                "verificationUriComplete": "https://verify.example.com?code=XY",
                "userCode": "XY-1234",
                "deviceCode": "dc-abc",
                "expiresIn": 300,
                "interval": 5,
            }

            with patch("backend.auth_flow.SSOOIDCClient") as MockOIDC:
                instance = MockOIDC.return_value
                instance.register_client.return_value = mock_reg
                instance.start_device_authorization.return_value = mock_auth
                instance._oidc_endpoint.return_value = "https://oidc.us-east-1.amazonaws.com"

                run(manager.start_flow(
                    provider_id=provider_id,
                    flow_type=FLOW_AWS_IAM,
                    sso_start_url="https://mycompany.awsapps.com/start",
                    sso_region="us-east-1",
                ))

                with pytest.raises(ValueError, match="already has an active authentication flow"):
                    run(manager.start_flow(
                        provider_id=provider_id,
                        flow_type=FLOW_AWS_IAM,
                        sso_start_url="https://mycompany.awsapps.com/start",
                        sso_region="us-east-1",
                    ))
        finally:
            os.unlink(db_path)


# ... keep rest of file unchanged ...
