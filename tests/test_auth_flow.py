"""
单元测试：AuthFlowManager
覆盖：Device_Code_Flow 状态机、AWS IAM、openai_codex、generic_oauth
"""
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
from backend.llm_gateway import _obfuscate, serialize_auth_config
from backend.models import AuthConfig, OAuthToken
from backend.enums import AuthType


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

            # 取消后台轮询任务，避免干扰
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

            # 状态是 pending，不是 awaiting_role，应该抛 ValueError
            with pytest.raises(ValueError, match="awaiting_role"):
                run(manager.bind_aws_role(result.auth_session_id, "123456789", "AdministratorAccess"))
        finally:
            os.unlink(db_path)

    def test_bind_aws_role_writes_sigv4_to_provider(self):
        """bind_aws_role 完成后，provider_configs.auth_config 应包含 Sigv4 凭证。"""
        import aiosqlite
        db_path = tmp_db()
        try:
            provider_id = run(_insert_provider(db_path))
            manager = make_manager(db_path)

            # 直接插入一个 awaiting_role 状态的 auth_session
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
                            _obfuscate("dc"),
                            "https://mycompany.awsapps.com/start",  # sso_start_url
                            "us-east-1",  # sso_region
                            5,
                            now + 300,
                            _obfuscate(fake_access_token),
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
            assert result.access_key_id == "AKIAIOSFODNN7EXAMPLE"

            # 验证 provider_configs 已更新
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


# ---------------------------------------------------------------------------
# OpenAI Codex / Generic OAuth flow
# ---------------------------------------------------------------------------

class TestOAuthFlow:
    def test_start_openai_codex_flow(self):
        """openai_codex flow 使用固定 client_id，从 OIDC discovery 获取端点。"""
        db_path = tmp_db()
        try:
            provider_id = run(_insert_provider(db_path))
            manager = make_manager(db_path)

            mock_discovery = {
                "device_authorization_endpoint": "https://auth.openai.com/oauth/device/code",
                "token_endpoint": "https://auth.openai.com/oauth/token",
            }
            mock_device_resp = {
                "verification_uri_complete": "https://auth.openai.com/activate?user_code=WXYZ-5678",
                "user_code": "WXYZ-5678",
                "device_code": "dc-openai",
                "expires_in": 300,
                "interval": 5,
            }

            import httpx as _httpx

            def fake_get(url, **kwargs):
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = mock_discovery
                resp.raise_for_status = MagicMock()
                return resp

            def fake_post(url, **kwargs):
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = mock_device_resp
                resp.raise_for_status = MagicMock()
                return resp

            with patch("httpx.get", side_effect=fake_get), \
                 patch("httpx.post", side_effect=fake_post):
                result = run(manager.start_flow(
                    provider_id=provider_id,
                    flow_type=FLOW_OPENAI_CODEX,
                ))

            for task in list(manager._poll_tasks.values()):
                task.cancel()

            assert result.user_code == "WXYZ-5678"
            assert result.flow_type == FLOW_OPENAI_CODEX
            assert "auth.openai.com" in result.verification_uri
        finally:
            os.unlink(db_path)

    def test_start_openai_codex_flow_falls_back_to_browser_pkce_on_403(self):
        """device code 未启用时，应回退到浏览器 PKCE 登录而不是直接失败。"""
        db_path = tmp_db()
        try:
            provider_id = run(_insert_provider(db_path))
            manager = make_manager(db_path)

            mock_discovery = {
                "device_authorization_endpoint": "https://auth0.openai.com/oauth/device/code",
                "token_endpoint": "https://auth.openai.com/oauth/token",
            }

            import httpx as _httpx

            def fake_get(url, **kwargs):
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = mock_discovery
                resp.raise_for_status = MagicMock()
                return resp

            def fake_post(url, **kwargs):
                request = _httpx.Request("POST", url)
                response = _httpx.Response(403, request=request)
                raise _httpx.HTTPStatusError("403 Forbidden", request=request, response=response)

            fallback_result = type("FlowResult", (), {
                "auth_session_id": "pkce-session",
                "verification_uri": "https://auth.openai.com/oauth/authorize?client_id=test",
                "user_code": "请在浏览器登录...",
                "expires_in": 900,
                "interval": 2,
                "flow_type": FLOW_OPENAI_CODEX,
            })()
            fallback = AsyncMock(return_value=fallback_result)

            with patch("httpx.get", side_effect=fake_get), \
                 patch("httpx.post", side_effect=fake_post), \
                 patch.object(manager, "_start_openai_pkce_flow", side_effect=fallback):
                result = run(manager.start_flow(
                    provider_id=provider_id,
                    flow_type=FLOW_OPENAI_CODEX,
                ))

            assert result.auth_session_id == "pkce-session"
            assert "oauth/authorize" in result.verification_uri
            fallback.assert_called_once_with(provider_id)
        finally:
            os.unlink(db_path)

    def test_start_openai_pkce_flow_matches_current_codex_cli_params(self):
        """浏览器登录默认参数应与本机官方 Codex CLI 当前版本保持一致。"""
        db_path = tmp_db()
        try:
            provider_id = run(_insert_provider(db_path))
            manager = make_manager(db_path)

            result = run(manager.start_flow(
                provider_id=provider_id,
                flow_type=FLOW_OPENAI_CODEX,
                login_variant="browser",
            ))

            query = parse_qs(urlparse(result.verification_uri).query)

            assert query["client_id"] == ["app_EMoamEEZ73f0CkXaXp7hrann"]
            assert query["redirect_uri"] == ["http://localhost:1455/auth/callback"]
            assert query["scope"] == ["openid profile email offline_access"]
            assert query["id_token_add_organizations"] == ["true"]
            assert query["codex_cli_simplified_flow"] == ["true"]
            assert query["originator"] == ["codex_cli_rs"]
            assert "api.connectors.read" not in query["scope"][0]
            assert "api.connectors.invoke" not in query["scope"][0]
        finally:
            os.unlink(db_path)

    def test_start_openai_pkce_flow_uses_windows_bridge_under_wsl(self):
        """WSL 中应启动 Windows localhost 桥接，而不是只在 Linux 侧监听 1455。"""
        db_path = tmp_db()
        try:
            provider_id = run(_insert_provider(db_path))
            manager = make_manager(db_path)
            created = []

            class _FakeTask:
                def add_done_callback(self, callback):
                    return None

            def fake_create_task(coro):
                created.append(coro.cr_code.co_name)
                coro.close()
                return _FakeTask()

            with patch("backend.auth_flow._should_use_windows_loopback_bridge", return_value=True), \
                 patch("backend.auth_flow.asyncio.create_task", side_effect=fake_create_task):
                run(manager.start_flow(
                    provider_id=provider_id,
                    flow_type=FLOW_OPENAI_CODEX,
                    login_variant="browser",
                ))

            assert created == ["_spawn_windows_callback_bridge"]
        finally:
            os.unlink(db_path)

    def test_handle_interactive_callback_completes_openai_codex_browser_flow(self):
        """Codex 浏览器登录回调应走后端 callback 完成 token 交换。"""
        import aiosqlite

        db_path = tmp_db()
        try:
            provider_id = run(_insert_provider(db_path))
            manager = make_manager(db_path)
            auth_session_id = str(uuid.uuid4())
            now = int(time.time())
            context_json = json.dumps(
                {
                    "provider_id": provider_id,
                    "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
                    "token_endpoint": "https://auth.openai.com/oauth/token",
                    "redirect_uri": "http://localhost:1455/auth/callback",
                    "code_verifier": "verifier-123",
                },
                ensure_ascii=False,
            )

            async def _insert_session():
                async with aiosqlite.connect(db_path) as db:
                    await db.execute(
                        """INSERT INTO auth_sessions
                           (id, provider_id, flow_type, status, verification_uri, user_code,
                            interval, expires_at, context_json, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            auth_session_id,
                            provider_id,
                            FLOW_OPENAI_CODEX,
                            STATUS_PENDING,
                            "https://auth.openai.com/oauth/authorize?state=test",
                            "请在浏览器登录...",
                            2,
                            now + 900,
                            context_json,
                            now,
                            now,
                        ),
                    )
                    await db.commit()

            run(_insert_session())

            mock_token_resp = MagicMock()
            mock_token_resp.status_code = 200
            mock_token_resp.json.return_value = {
                "access_token": "access-token-xyz",
                "refresh_token": "refresh-token-abc",
                "expires_in": 3600,
            }
            mock_token_resp.raise_for_status = MagicMock()

            with patch("httpx.post", return_value=mock_token_resp) as mock_post:
                run(manager.handle_interactive_callback(auth_session_id, "code-123"))

            mock_post.assert_called_once_with(
                "https://auth.openai.com/oauth/token",
                json={
                    "grant_type": "authorization_code",
                    "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
                    "code": "code-123",
                    "redirect_uri": "http://localhost:1455/auth/callback",
                    "code_verifier": "verifier-123",
                },
                timeout=15,
            )

            status = run(manager.get_status(auth_session_id))
            assert status.status == STATUS_COMPLETED

            async def _check_provider():
                async with aiosqlite.connect(db_path) as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute(
                        "SELECT auth_type, auth_config FROM provider_configs WHERE id = ?",
                        (provider_id,),
                    ) as cur:
                        return await cur.fetchone()

            row = run(_check_provider())
            assert row["auth_type"] == AuthType.OAUTH.value
            auth_config = json.loads(row["auth_config"])
            assert auth_config["oauth_token"]["access_token"]
        finally:
            os.unlink(db_path)

    def test_handle_interactive_callback_retries_direct_when_proxy_path_refused(self):
        """Codex token exchange 首次连接失败时应绕过环境代理重试直连。"""
        import aiosqlite
        import httpx

        db_path = tmp_db()
        try:
            provider_id = run(_insert_provider(db_path))
            manager = make_manager(db_path)
            auth_session_id = str(uuid.uuid4())
            now = int(time.time())
            context_json = json.dumps(
                {
                    "provider_id": provider_id,
                    "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
                    "token_endpoint": "https://auth.openai.com/oauth/token",
                    "redirect_uri": "http://localhost:1455/auth/callback",
                    "code_verifier": "verifier-123",
                },
                ensure_ascii=False,
            )

            async def _insert_session():
                async with aiosqlite.connect(db_path) as db:
                    await db.execute(
                        """INSERT INTO auth_sessions
                           (id, provider_id, flow_type, status, verification_uri, user_code,
                            interval, expires_at, context_json, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            auth_session_id,
                            provider_id,
                            FLOW_OPENAI_CODEX,
                            STATUS_PENDING,
                            "https://auth.openai.com/oauth/authorize?state=test",
                            "请在浏览器登录...",
                            2,
                            now + 900,
                            context_json,
                            now,
                            now,
                        ),
                    )
                    await db.commit()

            run(_insert_session())

            request = httpx.Request("POST", "https://auth.openai.com/oauth/token")
            connect_error = httpx.ConnectError("[Errno 111] Connection refused", request=request)
            success = MagicMock()
            success.status_code = 200
            success.json.return_value = {
                "access_token": "access-token-xyz",
                "refresh_token": "refresh-token-abc",
                "expires_in": 3600,
            }
            success.raise_for_status = MagicMock()

            with patch("httpx.post", side_effect=[connect_error, success]) as mock_post:
                run(manager.handle_interactive_callback(auth_session_id, "code-123"))

            assert mock_post.call_count == 2
            first_call = mock_post.call_args_list[0]
            second_call = mock_post.call_args_list[1]
            assert first_call.kwargs["timeout"] == 15
            assert "trust_env" not in first_call.kwargs
            assert second_call.kwargs["timeout"] == 15
            assert second_call.kwargs["trust_env"] is False

            status = run(manager.get_status(auth_session_id))
            assert status.status == STATUS_COMPLETED
        finally:
            os.unlink(db_path)

    def test_oauth_poll_completes_and_writes_token(self):
        """轮询完成后，provider_configs.auth_config 应包含 OAuth token。"""
        import aiosqlite
        db_path = tmp_db()
        try:
            provider_id = run(_insert_provider(db_path))
            manager = make_manager(db_path)

            auth_session_id = str(uuid.uuid4())
            now = int(time.time())

            async def _insert_session():
                async with aiosqlite.connect(db_path) as db:
                    await db.execute(
                        """INSERT INTO auth_sessions
                           (id, provider_id, flow_type, status, verification_uri, user_code,
                            device_code, client_id, client_secret, interval, expires_at,
                            created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            auth_session_id,
                            provider_id,
                            FLOW_GENERIC_OAUTH,
                            STATUS_PENDING,
                            "https://verify.example.com",
                            "AB-CD",
                            _obfuscate("dc-generic"),
                            "my-client-id",
                            _obfuscate("my-client-secret"),
                            5,
                            now + 300,
                            now,
                            now,
                        ),
                    )
                    await db.commit()

            run(_insert_session())

            mock_token_resp = MagicMock()
            mock_token_resp.status_code = 200
            mock_token_resp.json.return_value = {
                "access_token": "access-token-xyz",
                "refresh_token": "refresh-token-abc",
                "expires_in": 3600,
            }
            mock_token_resp.content = b'{"access_token": "access-token-xyz"}'

            ctx = {
                "client_id": "my-client-id",
                "client_secret": "my-client-secret",
                "device_code": "dc-generic",
                "token_endpoint": "https://auth.example.com/oauth/token",
                "interval": 0,
                "expires_at": now + 300,
            }

            with patch("httpx.post", return_value=mock_token_resp):
                done = run(manager._poll_oauth(auth_session_id, ctx))

            assert done is True

            # 验证 auth_session 状态
            status = run(manager.get_status(auth_session_id))
            assert status.status == STATUS_COMPLETED

            # 验证 provider_configs 已更新
            async def _check_provider():
                async with aiosqlite.connect(db_path) as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute(
                        "SELECT auth_type, auth_config FROM provider_configs WHERE id = ?",
                        (provider_id,),
                    ) as cur:
                        return await cur.fetchone()

            row = run(_check_provider())
            assert row["auth_type"] == "oauth"
            auth_config = json.loads(row["auth_config"])
            assert "oauth_token" in auth_config
        finally:
            os.unlink(db_path)

    def test_oauth_poll_authorization_pending_returns_false(self):
        """authorization_pending 时返回 False，继续轮询。"""
        db_path = tmp_db()
        try:
            provider_id = run(_insert_provider(db_path))
            manager = make_manager(db_path)

            auth_session_id = str(uuid.uuid4())
            now = int(time.time())

            async def _insert_session():
                import aiosqlite
                async with aiosqlite.connect(db_path) as db:
                    await db.execute(
                        """INSERT INTO auth_sessions
                           (id, provider_id, flow_type, status, verification_uri, user_code,
                            device_code, client_id, client_secret, interval, expires_at,
                            created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            auth_session_id, provider_id, FLOW_GENERIC_OAUTH, STATUS_PENDING,
                            "https://v.example.com", "AB", _obfuscate("dc"),
                            "cid", _obfuscate(""), 5, now + 300, now, now,
                        ),
                    )
                    await db.commit()

            run(_insert_session())

            mock_resp = MagicMock()
            mock_resp.status_code = 400
            mock_resp.json.return_value = {"error": "authorization_pending"}
            mock_resp.content = b'{"error": "authorization_pending"}'

            ctx = {
                "client_id": "cid",
                "client_secret": "",
                "device_code": "dc",
                "token_endpoint": "https://auth.example.com/oauth/token",
                "interval": 5,
                "expires_at": now + 300,
            }

            with patch("httpx.post", return_value=mock_resp):
                done = run(manager._poll_oauth(auth_session_id, ctx))

            assert done is False
        finally:
            os.unlink(db_path)

    def test_oauth_poll_error_marks_failed(self):
        """非 pending 错误时标记为 failed。"""
        db_path = tmp_db()
        try:
            provider_id = run(_insert_provider(db_path))
            manager = make_manager(db_path)

            auth_session_id = str(uuid.uuid4())
            now = int(time.time())

            async def _insert_session():
                import aiosqlite
                async with aiosqlite.connect(db_path) as db:
                    await db.execute(
                        """INSERT INTO auth_sessions
                           (id, provider_id, flow_type, status, verification_uri, user_code,
                            device_code, client_id, client_secret, interval, expires_at,
                            created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            auth_session_id, provider_id, FLOW_GENERIC_OAUTH, STATUS_PENDING,
                            "https://v.example.com", "AB", _obfuscate("dc"),
                            "cid", _obfuscate(""), 5, now + 300, now, now,
                        ),
                    )
                    await db.commit()

            run(_insert_session())

            mock_resp = MagicMock()
            mock_resp.status_code = 400
            mock_resp.json.return_value = {"error": "access_denied"}
            mock_resp.content = b'{"error": "access_denied"}'

            ctx = {
                "client_id": "cid",
                "client_secret": "",
                "device_code": "dc",
                "token_endpoint": "https://auth.example.com/oauth/token",
                "interval": 5,
                "expires_at": now + 300,
            }

            with patch("httpx.post", return_value=mock_resp):
                done = run(manager._poll_oauth(auth_session_id, ctx))

            assert done is True
            status = run(manager.get_status(auth_session_id))
            assert status.status == STATUS_FAILED
        finally:
            os.unlink(db_path)


# ---------------------------------------------------------------------------
# _update_auth_session — 超时标记
# ---------------------------------------------------------------------------

def test_update_auth_session_expired():
    db_path = tmp_db()
    try:
        provider_id = run(_insert_provider(db_path))
        manager = make_manager(db_path)

        auth_session_id = str(uuid.uuid4())
        now = int(time.time())

        async def _insert():
            import aiosqlite
            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    """INSERT INTO auth_sessions
                       (id, provider_id, flow_type, status, verification_uri, user_code,
                        device_code, client_id, client_secret, interval, expires_at,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        auth_session_id, provider_id, FLOW_AWS_IAM, STATUS_PENDING,
                        "https://v.example.com", "AB", _obfuscate("dc"),
                        "url", "region", 5, now + 300, now, now,
                    ),
                )
                await db.commit()

        run(_insert())
        run(manager._update_auth_session(auth_session_id, STATUS_EXPIRED, "timed out"))

        status = run(manager.get_status(auth_session_id))
        assert status.status == STATUS_EXPIRED
        assert status.error_message == "timed out"
    finally:
        os.unlink(db_path)


def test_cancel_pending_auth_flow_marks_cancelled_and_stops_task():
    db_path = tmp_db()
    try:
        provider_id = run(_insert_provider(db_path))
        manager = make_manager(db_path)
        auth_session_id = str(uuid.uuid4())
        now = int(time.time())

        async def _insert():
            import aiosqlite
            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    """INSERT INTO auth_sessions
                       (id, provider_id, flow_type, status, verification_uri, user_code,
                        device_code, client_id, client_secret, interval, expires_at,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        auth_session_id, provider_id, FLOW_GENERIC_OAUTH, STATUS_PENDING,
                        "https://v.example.com", "AB", _obfuscate("dc"),
                        "cid", _obfuscate(""), 5, now + 300, now, now,
                    ),
                )
                await db.commit()

        run(_insert())

        loop = asyncio.new_event_loop()
        try:
            task = loop.create_task(asyncio.sleep(300))
            manager._poll_tasks[auth_session_id] = task
            loop.run_until_complete(manager.cancel_flow(auth_session_id))
            loop.run_until_complete(asyncio.sleep(0))
            assert task.cancelled()
        finally:
            loop.close()

        status = run(manager.get_status(auth_session_id))
        assert status.status == STATUS_CANCELLED
        assert status.error_message == "用户已取消登录"
    finally:
        os.unlink(db_path)


def test_logout_provider_clears_oauth_token_and_cancels_pending_sessions():
    db_path = tmp_db()
    try:
        provider_id = run(_insert_provider(db_path))
        manager = make_manager(db_path)
        auth_session_id = str(uuid.uuid4())
        now = int(time.time())

        async def _seed():
            import aiosqlite
            auth_config = AuthConfig(
                auth_type=AuthType.OAUTH,
                oauth_token=OAuthToken(access_token="token-123"),
                metadata={
                    "authorization_endpoint": "https://example.com/authorize",
                    "token_endpoint": "https://example.com/token",
                    "client_id": "client-123",
                },
            )
            async with aiosqlite.connect(db_path) as db:
                await db.execute(
                    "UPDATE provider_configs SET auth_type = ?, auth_config = ? WHERE id = ?",
                    (
                        AuthType.OAUTH.value,
                        json.dumps(serialize_auth_config(auth_config), ensure_ascii=False),
                        provider_id,
                    ),
                )
                await db.execute(
                    """INSERT INTO auth_sessions
                       (id, provider_id, flow_type, status, verification_uri, user_code,
                        device_code, client_id, client_secret, interval, expires_at,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        auth_session_id, provider_id, FLOW_GENERIC_OAUTH, STATUS_PENDING,
                        "https://v.example.com", "AB", _obfuscate("dc"),
                        "cid", _obfuscate(""), 5, now + 300, now, now,
                    ),
                )
                await db.commit()

        run(_seed())
        run(manager.logout_provider(provider_id))

        async def _check():
            import aiosqlite
            async with aiosqlite.connect(db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT auth_type, auth_config FROM provider_configs WHERE id = ?",
                    (provider_id,),
                ) as cur:
                    provider_row = await cur.fetchone()
                async with db.execute(
                    "SELECT status, error_message FROM auth_sessions WHERE id = ?",
                    (auth_session_id,),
                ) as cur:
                    auth_row = await cur.fetchone()
                return provider_row, auth_row

        provider_row, auth_row = run(_check())
        provider_auth = json.loads(provider_row["auth_config"])
        assert provider_row["auth_type"] == AuthType.OAUTH.value
        assert "oauth_token" not in provider_auth
        assert provider_auth["metadata"]["client_id"] == "client-123"
        assert auth_row["status"] == STATUS_CANCELLED
        assert auth_row["error_message"] == "用户已退出登录"
    finally:
        os.unlink(db_path)
