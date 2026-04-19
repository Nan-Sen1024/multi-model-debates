"""
认证流程状态机：Device_Code_Flow 适配器
支持：
  - openai_codex  — OpenAI Codex CLI 风格 OAuth device flow
  - aws_iam       — AWS IAM Identity Center (SSO OIDC) device flow
  - generic_oauth — 通用 OAuth 2.0 device flow (RFC 8628)

流程：
  1. start_flow()  → 返回 {auth_session_id, verification_uri, user_code, expires_in}
  2. poll_status() → 返回 {status, ...}  (pending / completed / failed / expired)
  3. bind_role()   → AWS 专用：选择账号和 Permission Set，换取 Sigv4_Credentials
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import aiosqlite

from .database import DB_PATH, init_db
from .llm_gateway import (
    AuthManager,
    SSOOIDCClient,
    _deobfuscate,
    _obfuscate,
    deserialize_auth_config,
    serialize_auth_config,
)
from .models import AuthConfig, OAuthToken
from .enums import AuthType
from .exceptions import AuthenticationError, ProviderUnavailableError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

FLOW_OPENAI_CODEX = "openai_codex"
FLOW_AWS_IAM = "aws_iam"
FLOW_AWS_SSO_PKCE = "aws_sso_pkce"
FLOW_GENERIC_OAUTH = "generic_oauth"
FLOW_BROWSER_OAUTH = "browser_oauth"

STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"
STATUS_CANCELLED = "cancelled"
STATUS_AWAITING_ROLE = "awaiting_role"  # AWS: 等待用户选择账号/角色


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class FlowStartResult:
    auth_session_id: str
    verification_uri: str
    user_code: str
    expires_in: int          # 秒
    interval: int = 5        # 建议轮询间隔（秒）
    flow_type: str = FLOW_GENERIC_OAUTH


@dataclass
class FlowStatusResult:
    auth_session_id: str
    status: str              # pending / completed / failed / expired / cancelled / awaiting_role
    flow_type: str = FLOW_GENERIC_OAUTH
    # completed 时填充
    access_token: Optional[str] = None
    # aws_iam awaiting_role 时填充
    accounts: Optional[List[Dict[str, Any]]] = None
    # failed / expired 时填充
    error_message: Optional[str] = None


@dataclass
class BindRoleResult:
    auth_session_id: str
    account_id: str
    role_name: str
    # Sigv4 临时凭证（不直接暴露给前端，仅写入 provider auth_config）
    access_key_id: str = ""
    secret_access_key: str = ""
    session_token: str = ""


# ---------------------------------------------------------------------------
# OpenAI Codex OAuth 配置
# ---------------------------------------------------------------------------

# openai-codex CLI 使用的 OIDC 端点（公开信息，来自 CLI 源码）
OPENAI_CODEX_OIDC_ISSUER = "https://auth.openai.com"
OPENAI_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"  # codex CLI 0.107.0-alpha.5 public client id
OPENAI_CODEX_SCOPE = "openid profile email offline_access"
OPENAI_CODEX_AUDIENCE = "https://api.openai.com/v1"


def _is_wsl_environment() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        with open("/proc/version", "r", encoding="utf-8") as handle:
            return "microsoft" in handle.read().lower()
    except OSError:
        return False


def _should_use_windows_loopback_bridge() -> bool:
    return _is_wsl_environment() and shutil.which("powershell.exe") is not None


# ---------------------------------------------------------------------------
# AuthFlowManager
# ---------------------------------------------------------------------------

class AuthFlowManager:
    """
    管理 Device_Code_Flow 的完整生命周期：
    - 启动流程（start_flow）
    - 异步后台轮询（_poll_loop）
    - 查询状态（get_status）
    - AWS 角色绑定（bind_aws_role）
    - 将完成的 token 写回 provider_configs.auth_config
    """

    def __init__(self, db_path: str = DB_PATH) -> None:
        self.db_path = db_path
        self._poll_tasks: Dict[str, asyncio.Task] = {}
        self._interactive_tasks: Dict[str, asyncio.Task] = {}
        # PKCE 会话上下文：auth_session_id -> {client_id, client_secret, code_verifier, ...}
        # 同时也写入 DB，防止重启丢失
        self._pkce_ctx: Dict[str, Dict[str, str]] = {}

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    async def start_flow(
        self,
        provider_id: str,
        flow_type: str,
        *,
        # openai_codex / generic_oauth 参数
        token_endpoint: Optional[str] = None,
        authorization_endpoint: Optional[str] = None,
        device_authorization_endpoint: Optional[str] = None,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        scope: Optional[str] = None,
        login_variant: Optional[str] = None,
        # aws_iam 参数
        sso_start_url: Optional[str] = None,
        sso_region: Optional[str] = None,
    ) -> FlowStartResult:
        """
        启动 Device_Code_Flow，返回展示给用户的 verification_uri 和 user_code。
        同时在后台启动轮询任务。
        """
        await init_db(self.db_path)

        if flow_type == FLOW_AWS_IAM:
            return await self._start_aws_iam_flow(
                provider_id=provider_id,
                sso_start_url=sso_start_url or "",
                sso_region=sso_region or "us-east-1",
            )
        elif flow_type == FLOW_AWS_SSO_PKCE:
            return await self._start_aws_pkce_flow(
                provider_id=provider_id,
                sso_start_url=sso_start_url or "",
                sso_region=sso_region or "us-east-1",
            )
        elif flow_type in (FLOW_OPENAI_CODEX, FLOW_GENERIC_OAUTH):
            return await self._start_oauth_flow(
                provider_id=provider_id,
                flow_type=flow_type,
                token_endpoint=token_endpoint,
                authorization_endpoint=authorization_endpoint,
                device_authorization_endpoint=device_authorization_endpoint,
                client_id=client_id,
                client_secret=client_secret,
                scope=scope,
                login_variant=login_variant,
            )
        elif flow_type == FLOW_BROWSER_OAUTH:
            return await self._start_browser_oauth_flow(
                provider_id=provider_id,
                authorization_endpoint=authorization_endpoint,
                token_endpoint=token_endpoint,
                client_id=client_id,
                client_secret=client_secret,
                scope=scope,
            )
        else:
            raise ValueError(f"Unsupported flow_type: {flow_type}")

    async def get_status(self, auth_session_id: str) -> FlowStatusResult:
        """查询认证会话状态。"""
        await init_db(self.db_path)
        row = await self._load_auth_session(auth_session_id)
        if row is None:
            raise ValueError(f"Auth session not found: {auth_session_id}")

        status = row["status"]
        result = FlowStatusResult(
            auth_session_id=auth_session_id,
            status=status,
            flow_type=row["flow_type"],
            error_message=row["error_message"],
        )

        if status == STATUS_AWAITING_ROLE and row["accounts_json"]:
            result.accounts = json.loads(row["accounts_json"])

        if status == STATUS_COMPLETED and row["access_token"]:
            # 不暴露原始 token，只告知已完成
            result.access_token = "[REDACTED]"

        return result

    async def bind_aws_role(
        self,
        auth_session_id: str,
        account_id: str,
        role_name: str,
    ) -> BindRoleResult:
        """
        AWS 专用：用户选择账号和角色后，换取 Sigv4_Credentials 并写回 provider。
        """
        row = await self._load_auth_session(auth_session_id)
        if row is None:
            raise ValueError(f"Auth session not found: {auth_session_id}")
        if row["status"] != STATUS_AWAITING_ROLE:
            raise ValueError(f"Auth session is not in awaiting_role state: {row['status']}")

        access_token = _deobfuscate(row["access_token"]) if row["access_token"] else ""
        if not access_token:
            raise AuthenticationError("No access token available for role binding")

        # 从 auth_session 恢复 SSOOIDCClient
        # sso_start_url 和 sso_region 存在 client_id / client_secret 字段里（复用）
        sso_start_url = row["client_id"] or ""
        sso_region = row["client_secret"] or ""
        oidc = SSOOIDCClient(sso_start_url=sso_start_url, sso_region=sso_region)

        try:
            creds = oidc.get_role_credentials(access_token, account_id, role_name)
        except Exception as exc:
            raise AuthenticationError(f"Failed to get role credentials: {exc}") from exc

        access_key_id = creds.get("accessKeyId", "")
        secret_access_key = creds.get("secretAccessKey", "")
        session_token = creds.get("sessionToken", "")

        sigv4 = {
            "accessKeyId": access_key_id,
            "secretAccessKey": secret_access_key,
            "sessionToken": session_token,
        }

        # 更新 auth_session
        now = int(time.time())
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """UPDATE auth_sessions SET
                    status = ?, selected_account_id = ?, selected_role_name = ?,
                    sigv4_json = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    STATUS_COMPLETED,
                    account_id,
                    role_name,
                    _obfuscate(json.dumps(sigv4)),
                    now,
                    auth_session_id,
                ),
            )
            await db.commit()

        # 将 Sigv4 凭证写回 provider_configs.auth_config
        await self._write_sigv4_to_provider(row["provider_id"], sigv4, access_token, row)

        return BindRoleResult(
            auth_session_id=auth_session_id,
            account_id=account_id,
            role_name=role_name,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            session_token=session_token,
        )

    async def cancel_flow(
        self,
        auth_session_id: str,
        reason: str = "用户已取消登录",
    ) -> FlowStatusResult:
        row = await self._load_auth_session(auth_session_id)
        if row is None:
            raise ValueError(f"Auth session not found: {auth_session_id}")

        if row["status"] in (STATUS_PENDING, STATUS_AWAITING_ROLE):
            await self._cancel_tracked_task(self._poll_tasks, auth_session_id)
            await self._cancel_tracked_task(self._interactive_tasks, auth_session_id)
            self._pkce_ctx.pop(auth_session_id, None)
            await self._update_auth_session(auth_session_id, STATUS_CANCELLED, error_message=reason)
            row = await self._load_auth_session(auth_session_id)

        return FlowStatusResult(
            auth_session_id=auth_session_id,
            status=row["status"],
            flow_type=row["flow_type"],
            error_message=row["error_message"],
        )

    async def logout_provider(
        self,
        provider_id: str,
        reason: str = "用户已退出登录",
    ) -> None:
        row = await self._load_provider_row(provider_id)
        if row is None:
            raise ValueError(f"Provider not found: {provider_id}")

        auth_config = deserialize_auth_config(row["auth_type"], row["auth_config"])
        preserved_metadata = (
            dict(auth_config.metadata)
            if auth_config.auth_type
            in {AuthType.OAUTH, AuthType.API_KEY, AuthType.BEARER, AuthType.HELPER}
            else {}
        )
        cleared_auth = AuthConfig(
            auth_type=auth_config.auth_type,
            metadata=preserved_metadata,
        )

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE provider_configs SET auth_config = ? WHERE id = ?",
                (
                    json.dumps(serialize_auth_config(cleared_auth), ensure_ascii=False),
                    provider_id,
                ),
            )
            await db.commit()

        pending_session_ids: List[str] = []
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT id FROM auth_sessions
                   WHERE provider_id = ? AND status IN (?, ?)""",
                (provider_id, STATUS_PENDING, STATUS_AWAITING_ROLE),
            ) as cursor:
                rows = await cursor.fetchall()
                pending_session_ids = [item["id"] for item in rows]

        for auth_session_id in pending_session_ids:
            await self.cancel_flow(auth_session_id, reason=reason)

    # ------------------------------------------------------------------
    # AWS SSO PKCE (Authorization Code + PKCE) 流程
    # ------------------------------------------------------------------

    async def _start_aws_pkce_flow(
        self,
        provider_id: str,
        sso_start_url: str,
        sso_region: str,
    ) -> FlowStartResult:
        """
        启动 AWS IAM Identity Center Authorization Code + PKCE 流程。
        用户只需在浏览器完成 SSO 登录，无需手动输入设备码。
        """
        import hashlib
        import secrets

        if not sso_start_url:
            raise ValueError("sso_start_url is required for PKCE flow")

        # 生成 PKCE 参数
        code_verifier = secrets.token_urlsafe(64)
        digest = hashlib.sha256(code_verifier.encode()).digest()
        import base64 as _b64
        code_challenge = _b64.urlsafe_b64encode(digest).rstrip(b"=").decode()

        auth_session_id = str(uuid.uuid4())
        redirect_uri = f"http://127.0.0.1:8000/api/providers/{provider_id}/auth/callback"

        oidc = SSOOIDCClient(sso_start_url=sso_start_url, sso_region=sso_region)

        # 注册 PKCE 客户端
        try:
            reg = await asyncio.get_event_loop().run_in_executor(
                None, lambda: oidc.register_client_pkce(redirect_uri)
            )
        except Exception as exc:
            raise ProviderUnavailableError(
                f"AWS SSO PKCE register_client failed: {exc}"
            ) from exc

        client_id_real = reg.get("clientId", "")
        client_secret_real = reg.get("clientSecret", "")
        expires_in = max(300, int(reg.get("clientSecretExpiresAt", 0)) - int(time.time()))

        # 构建授权 URL（用户打开后直接登录）
        auth_url = oidc.build_authorize_url(
            client_id=client_id_real,
            redirect_uri=redirect_uri,
            state=auth_session_id,   # state = session ID，回调时用于对应会话
            code_challenge=code_challenge,
        )

        now = int(time.time())

        # 将敏感信息打包存入 device_code 字段（base64 混淆）
        # 格式: code_verifier::client_id_real::client_secret_real
        packed = f"{code_verifier}::{client_id_real}::{client_secret_real}"

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO auth_sessions
                   (id, provider_id, flow_type, status, verification_uri, user_code,
                    device_code, client_id, client_secret, interval, expires_at,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    auth_session_id,
                    provider_id,
                    FLOW_AWS_SSO_PKCE,
                    STATUS_PENDING,
                    auth_url,
                    "",                          # user_code 为空（无需手动输入）
                    _obfuscate(packed),           # 敏感数据
                    sso_start_url,               # 复用 client_id 字段
                    sso_region,                  # 复用 client_secret 字段
                    5,
                    now + expires_in,
                    now,
                    now,
                ),
            )
            await db.commit()

        # 内存缓存（同步备份）
        self._pkce_ctx[auth_session_id] = {
            "client_id_real": client_id_real,
            "client_secret_real": client_secret_real,
            "code_verifier": code_verifier,
            "sso_start_url": sso_start_url,
            "sso_region": sso_region,
            "redirect_uri": redirect_uri,
            "provider_id": provider_id,
        }

        return FlowStartResult(
            auth_session_id=auth_session_id,
            verification_uri=auth_url,   # 前端直接 window.open 这个 URL
            user_code="",               # PKCE 无需 user_code
            expires_in=expires_in,
            interval=5,
            flow_type=FLOW_AWS_SSO_PKCE,
        )

    async def handle_interactive_callback(
        self,
        auth_session_id: str,
        code: str,
    ) -> None:
        row = await self._load_auth_session(auth_session_id)
        if row is None:
            raise ValueError(f"PKCE session not found: {auth_session_id}")

        if row["flow_type"] == FLOW_AWS_SSO_PKCE:
            await self._handle_aws_pkce_callback(row, code)
            return
        if row["flow_type"] == FLOW_BROWSER_OAUTH:
            await self._handle_browser_oauth_callback(row, code)
            return
        if row["flow_type"] == FLOW_OPENAI_CODEX:
            await self._handle_openai_codex_callback(row, code)
            return
        raise ValueError(f"Unsupported interactive callback flow: {row['flow_type']}")

    async def _handle_aws_pkce_callback(
        self,
        row: Any,
        code: str,
    ) -> None:
        # 优先从内存取，其次从 DB 恢复
        auth_session_id = row["id"]
        ctx = self._pkce_ctx.get(auth_session_id)
        if ctx is None:
            packed_raw = _deobfuscate(row["device_code"]) if row["device_code"] else ""
            parts = packed_raw.split("::", 2)
            if len(parts) != 3:
                raise ValueError("PKCE session state is corrupted")
            code_verifier, client_id_real, client_secret_real = parts
            sso_start_url = row["client_id"] or ""
            sso_region = row["client_secret"] or ""
            provider_id = row["provider_id"]
            redirect_uri = f"http://127.0.0.1:8000/api/providers/{provider_id}/auth/callback"
        else:
            code_verifier = ctx["code_verifier"]
            client_id_real = ctx["client_id_real"]
            client_secret_real = ctx["client_secret_real"]
            sso_start_url = ctx["sso_start_url"]
            sso_region = ctx["sso_region"]
            redirect_uri = ctx["redirect_uri"]
            provider_id = ctx["provider_id"]

        oidc = SSOOIDCClient(sso_start_url=sso_start_url, sso_region=sso_region)

        # 换取 access_token
        try:
            token_data = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: oidc.exchange_auth_code(
                    client_id=client_id_real,
                    client_secret=client_secret_real,
                    code=code,
                    redirect_uri=redirect_uri,
                    code_verifier=code_verifier,
                ),
            )
        except Exception as exc:
            await self._update_auth_session(
                auth_session_id, STATUS_FAILED, error_message=str(exc)
            )
            raise

        access_token = token_data.get("accessToken", "")

        # 获取账号列表，进入 awaiting_role 状态
        try:
            accounts = oidc.list_accounts(access_token)
        except Exception:
            accounts = []

        now = int(time.time())
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """UPDATE auth_sessions SET
                    status = ?, access_token = ?,
                    accounts_json = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    STATUS_AWAITING_ROLE,
                    _obfuscate(access_token),
                    json.dumps(accounts),
                    now,
                    auth_session_id,
                ),
            )
            await db.commit()

        self._pkce_ctx.pop(auth_session_id, None)
        logger.info("PKCE callback completed for session %s", auth_session_id)

    async def _handle_browser_oauth_callback(
        self,
        row: Any,
        code: str,
    ) -> None:
        import httpx as _httpx

        auth_session_id = row["id"]
        ctx = await self._load_pkce_context(auth_session_id)
        if not ctx:
            raise ValueError("OAuth browser session context is missing")

        try:
            response = _httpx.post(
                ctx["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "client_id": ctx["client_id"],
                    "code": code,
                    "redirect_uri": ctx["redirect_uri"],
                    "code_verifier": ctx["code_verifier"],
                    **({"client_secret": ctx["client_secret"]} if ctx.get("client_secret") else {}),
                },
                timeout=15,
            )
            response.raise_for_status()
        except Exception as exc:
            await self._update_auth_session(
                auth_session_id,
                STATUS_FAILED,
                error_message=str(exc),
            )
            raise

        await self._complete_oauth_session(
            auth_session_id=auth_session_id,
            provider_id=row["provider_id"],
            token_data=response.json(),
        )
        self._pkce_ctx.pop(auth_session_id, None)

    async def _handle_openai_codex_callback(
        self,
        row: Any,
        code: str,
    ) -> None:
        import httpx as _httpx

        auth_session_id = row["id"]
        ctx = await self._load_pkce_context(auth_session_id)
        if not ctx:
            raise ValueError("OpenAI Codex browser session context is missing")
        payload = {
            "grant_type": "authorization_code",
            "client_id": ctx["client_id"],
            "code": code,
            "redirect_uri": ctx["redirect_uri"],
            "code_verifier": ctx["code_verifier"],
        }

        try:
            response = _httpx.post(ctx["token_endpoint"], json=payload, timeout=15)
            response.raise_for_status()
        except _httpx.ConnectError as exc:
            logger.warning(
                "OpenAI Codex token exchange failed via environment networking; retrying direct connection: %s",
                exc,
            )
            try:
                response = _httpx.post(
                    ctx["token_endpoint"],
                    json=payload,
                    timeout=15,
                    trust_env=False,
                )
                response.raise_for_status()
            except Exception as retry_exc:
                await self._update_auth_session(
                    auth_session_id,
                    STATUS_FAILED,
                    error_message=str(retry_exc),
                )
                raise
        except Exception as exc:
            await self._update_auth_session(
                auth_session_id,
                STATUS_FAILED,
                error_message=str(exc),
            )
            raise

        await self._complete_oauth_session(
            auth_session_id=auth_session_id,
            provider_id=row["provider_id"],
            token_data=response.json(),
        )
        self._pkce_ctx.pop(auth_session_id, None)

    # ------------------------------------------------------------------
    # AWS IAM Identity Center Device Code 流程
    # ------------------------------------------------------------------

    async def _start_aws_iam_flow(
        self,
        provider_id: str,
        sso_start_url: str,
        sso_region: str,
    ) -> FlowStartResult:
        oidc = SSOOIDCClient(sso_start_url=sso_start_url, sso_region=sso_region)

        try:
            reg = await asyncio.get_event_loop().run_in_executor(
                None, oidc.register_client
            )
        except Exception as exc:
            raise ProviderUnavailableError(f"AWS SSO OIDC register_client failed: {exc}") from exc

        client_id = reg.get("clientId", "")
        client_secret = reg.get("clientSecret", "")

        try:
            auth_resp = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: oidc.start_device_authorization(client_id, client_secret),
            )
        except Exception as exc:
            raise ProviderUnavailableError(f"AWS SSO start_device_authorization failed: {exc}") from exc

        verification_uri = auth_resp.get("verificationUriComplete") or auth_resp.get("verificationUri", "")
        user_code = auth_resp.get("userCode", "")
        device_code = auth_resp.get("deviceCode", "")
        expires_in = auth_resp.get("expiresIn", 300)
        interval = auth_resp.get("interval", 5)

        auth_session_id = str(uuid.uuid4())
        now = int(time.time())

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO auth_sessions
                   (id, provider_id, flow_type, status, verification_uri, user_code,
                    device_code, client_id, client_secret, interval, expires_at,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    auth_session_id,
                    provider_id,
                    FLOW_AWS_IAM,
                    STATUS_PENDING,
                    verification_uri,
                    user_code,
                    _obfuscate(device_code),
                    # 复用 client_id/client_secret 字段存 sso_start_url/sso_region
                    sso_start_url,
                    sso_region,
                    interval,
                    now + expires_in,
                    now,
                    now,
                ),
            )
            await db.commit()

        # 启动后台轮询
        self._start_poll_task(auth_session_id, FLOW_AWS_IAM, {
            "client_id_real": client_id,
            "client_secret_real": client_secret,
            "device_code": device_code,
            "sso_start_url": sso_start_url,
            "sso_region": sso_region,
            "interval": interval,
            "expires_at": now + expires_in,
        })

        return FlowStartResult(
            auth_session_id=auth_session_id,
            verification_uri=verification_uri,
            user_code=user_code,
            expires_in=expires_in,
            interval=interval,
            flow_type=FLOW_AWS_IAM,
        )

    # ------------------------------------------------------------------
    # OpenAI Codex / Generic OAuth 流程
    # ------------------------------------------------------------------

    async def _start_oauth_flow(
        self,
        provider_id: str,
        flow_type: str,
        token_endpoint: Optional[str],
        authorization_endpoint: Optional[str],
        device_authorization_endpoint: Optional[str],
        client_id: Optional[str],
        client_secret: Optional[str],
        scope: Optional[str],
        login_variant: Optional[str],
    ) -> FlowStartResult:
        try:
            import httpx as _httpx  # type: ignore
        except ImportError:
            raise ProviderUnavailableError("httpx is required for OAuth device flow")

        if flow_type == FLOW_OPENAI_CODEX:
            if login_variant == "browser":
                return await self._start_openai_pkce_flow(provider_id)
            effective_client_id = OPENAI_CODEX_CLIENT_ID
            effective_scope = OPENAI_CODEX_SCOPE
            discovery_url = f"{OPENAI_CODEX_OIDC_ISSUER}/.well-known/openid-configuration"
            try:
                resp = _httpx.get(discovery_url, timeout=10)
                resp.raise_for_status()
                discovery = resp.json()
                device_auth_endpoint = discovery.get(
                    "device_authorization_endpoint",
                    f"{OPENAI_CODEX_OIDC_ISSUER}/oauth/device/code",
                )
                effective_token_endpoint = discovery.get(
                    "token_endpoint",
                    f"{OPENAI_CODEX_OIDC_ISSUER}/oauth/token",
                )
            except Exception as exc:
                logger.warning("OIDC discovery failed, using defaults: %s", exc)
                device_auth_endpoint = f"{OPENAI_CODEX_OIDC_ISSUER}/oauth/device/code"
                effective_token_endpoint = f"{OPENAI_CODEX_OIDC_ISSUER}/oauth/token"
        else:
            effective_client_id = client_id or ""
            effective_scope = scope or "openid"
            if not token_endpoint:
                raise ValueError("token_endpoint is required for generic_oauth flow")
            device_auth_endpoint = (
                device_authorization_endpoint
                or token_endpoint.replace("/token", "/device/code")
            )
            effective_token_endpoint = token_endpoint

        # 发起 device authorization request
        try:
            resp = _httpx.post(
                device_auth_endpoint,
                data={
                    "client_id": effective_client_id,
                    "scope": effective_scope,
                    **({"audience": OPENAI_CODEX_AUDIENCE} if flow_type == FLOW_OPENAI_CODEX else {}),
                },
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            if (
                flow_type == FLOW_OPENAI_CODEX
                and login_variant != "device_code"
                and getattr(getattr(exc, "response", None), "status_code", None) == 403
            ):
                logger.info(
                    "OpenAI device authorization returned 403; falling back to browser PKCE flow"
                )
                return await self._start_openai_pkce_flow(provider_id)
            raise ProviderUnavailableError(f"Device authorization request failed: {exc}") from exc

        verification_uri = data.get("verification_uri_complete") or data.get("verification_uri", "")
        user_code = data.get("user_code", "")
        device_code = data.get("device_code", "")
        expires_in = data.get("expires_in", 300)
        interval = data.get("interval", 5)

        auth_session_id = str(uuid.uuid4())
        now = int(time.time())

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO auth_sessions
                   (id, provider_id, flow_type, status, verification_uri, user_code,
                    device_code, client_id, client_secret, interval, expires_at,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    auth_session_id,
                    provider_id,
                    flow_type,
                    STATUS_PENDING,
                    verification_uri,
                    user_code,
                    _obfuscate(device_code),
                    effective_client_id,
                    _obfuscate(client_secret or ""),
                    interval,
                    now + expires_in,
                    now,
                    now,
                ),
            )
            await db.commit()

        self._start_poll_task(auth_session_id, flow_type, {
            "client_id": effective_client_id,
            "client_secret": client_secret or "",
            "device_code": device_code,
            "token_endpoint": effective_token_endpoint,
            "interval": interval,
            "expires_at": now + expires_in,
        })

        return FlowStartResult(
            auth_session_id=auth_session_id,
            verification_uri=verification_uri,
            user_code=user_code,
            expires_in=expires_in,
            interval=interval,
            flow_type=flow_type,
        )

    async def _start_browser_oauth_flow(
        self,
        provider_id: str,
        authorization_endpoint: Optional[str],
        token_endpoint: Optional[str],
        client_id: Optional[str],
        client_secret: Optional[str],
        scope: Optional[str],
    ) -> FlowStartResult:
        import base64
        import hashlib
        import secrets
        from urllib.parse import urlencode

        if not authorization_endpoint:
            raise ValueError("authorization_endpoint is required for browser_oauth flow")
        if not token_endpoint:
            raise ValueError("token_endpoint is required for browser_oauth flow")
        if not client_id:
            raise ValueError("client_id is required for browser_oauth flow")

        auth_session_id = str(uuid.uuid4())
        code_verifier = secrets.token_urlsafe(48)
        code_challenge = base64.urlsafe_b64encode(
            hashlib.sha256(code_verifier.encode("utf-8")).digest()
        ).rstrip(b"=").decode("utf-8")
        redirect_uri = f"http://127.0.0.1:8000/api/providers/{provider_id}/auth/callback"
        effective_scope = scope or "openid"

        verification_uri = f"{authorization_endpoint}?{urlencode({
            'response_type': 'code',
            'client_id': client_id,
            'redirect_uri': redirect_uri,
            'scope': effective_scope,
            'state': auth_session_id,
            'code_challenge': code_challenge,
            'code_challenge_method': 'S256',
        })}"
        context_json = json.dumps(
            {
                "provider_id": provider_id,
                "client_id": client_id,
                "client_secret": client_secret or "",
                "token_endpoint": token_endpoint,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
            ensure_ascii=False,
        )

        now = int(time.time())
        expires_in = 900
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO auth_sessions
                   (id, provider_id, flow_type, status, verification_uri, user_code,
                    client_id, client_secret, interval, expires_at, context_json,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    auth_session_id,
                    provider_id,
                    FLOW_BROWSER_OAUTH,
                    STATUS_PENDING,
                    verification_uri,
                    "请在浏览器登录...",
                    client_id,
                    _obfuscate(client_secret or ""),
                    2,
                    now + expires_in,
                    context_json,
                    now,
                    now,
                ),
            )
            await db.commit()

        self._pkce_ctx[auth_session_id] = json.loads(context_json)
        return FlowStartResult(
            auth_session_id=auth_session_id,
            verification_uri=verification_uri,
            user_code="请在浏览器登录...",
            expires_in=expires_in,
            interval=2,
            flow_type=FLOW_BROWSER_OAUTH,
        )

    async def _start_openai_pkce_flow(self, provider_id: str) -> FlowStartResult:
        import hashlib
        import base64
        import secrets
        from urllib.parse import urlencode

        client_id = OPENAI_CODEX_CLIENT_ID
        code_verifier = secrets.token_urlsafe(32)
        code_challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).decode().rstrip("=")
        auth_session_id = str(uuid.uuid4())
        redirect_uri = "http://localhost:1455/auth/callback"

        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": OPENAI_CODEX_SCOPE,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "state": auth_session_id,
            "originator": "codex_cli_rs"
        }
        verification_uri = f"{OPENAI_CODEX_OIDC_ISSUER}/oauth/authorize?" + urlencode(params)
        
        if not hasattr(self, "_pkce_ctx"):
            self._pkce_ctx = {}

        self._pkce_ctx[auth_session_id] = {
            "client_id": client_id,
            "code_verifier": code_verifier,
            "redirect_uri": redirect_uri,
            "token_endpoint": f"{OPENAI_CODEX_OIDC_ISSUER}/oauth/token",
            "provider_id": provider_id,
        }
        context_json = json.dumps(self._pkce_ctx[auth_session_id], ensure_ascii=False)

        now = int(time.time())
        expires_at = now + 900
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO auth_sessions
                   (id, provider_id, flow_type, status, verification_uri,
                    user_code, interval, expires_at, context_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    auth_session_id, provider_id, FLOW_OPENAI_CODEX, STATUS_PENDING,
                    verification_uri, "请在浏览器登录...", 2, expires_at, context_json, now, now
                ),
            )
            await db.commit()

        task = self._start_openai_callback_listener(auth_session_id, provider_id)
        self._interactive_tasks[auth_session_id] = task
        task.add_done_callback(lambda _: self._interactive_tasks.pop(auth_session_id, None))

        return FlowStartResult(
            auth_session_id=auth_session_id,
            verification_uri=verification_uri,
            user_code="请在浏览器登录...",
            expires_in=900,
            interval=2,
            flow_type=FLOW_OPENAI_CODEX,
        )

    def _start_openai_callback_listener(
        self,
        auth_session_id: str,
        provider_id: str,
    ) -> asyncio.Task:
        if _should_use_windows_loopback_bridge():
            return asyncio.create_task(
                self._spawn_windows_callback_bridge(auth_session_id, provider_id)
            )
        return asyncio.create_task(self._spawn_local_callback_server(auth_session_id))

    async def _spawn_windows_callback_bridge(
        self,
        auth_session_id: str,
        provider_id: str,
    ) -> None:
        callback_url = f"http://127.0.0.1:8000/api/providers/{provider_id}/auth/callback"
        script = rf"""
$listener = New-Object System.Net.HttpListener
$listener.Prefixes.Add('http://localhost:1455/')
try {{
    $listener.Start()
    $context = $listener.GetContext()
    $request = $context.Request
    if ($request.Url.AbsolutePath -ne '/auth/callback') {{
        $body = '<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>路径错误</title></head><body><h2>路径错误</h2><p>请关闭此窗口并重新登录。</p></body></html>'
        $buffer = [System.Text.Encoding]::UTF8.GetBytes($body)
        $context.Response.StatusCode = 404
        $context.Response.ContentType = 'text/html; charset=utf-8'
        $context.Response.ContentLength64 = $buffer.Length
        $context.Response.OutputStream.Write($buffer, 0, $buffer.Length)
        $context.Response.OutputStream.Close()
        return
    }}

    $target = '{callback_url}' + $request.Url.Query
    try {{
        $upstream = Invoke-WebRequest -UseBasicParsing -Uri $target -TimeoutSec 30
        $statusCode = [int]$upstream.StatusCode
        $contentType = if ($upstream.Headers['Content-Type']) {{ $upstream.Headers['Content-Type'] }} else {{ 'text/html; charset=utf-8' }}
        $body = $upstream.Content
    }} catch {{
        $statusCode = 502
        $contentType = 'text/html; charset=utf-8'
        $body = '<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>回调失败</title></head><body><h2>回调失败</h2><p>无法将登录结果转发给后端。请关闭此窗口并重试。</p></body></html>'
    }}

    $buffer = [System.Text.Encoding]::UTF8.GetBytes($body)
    $context.Response.StatusCode = $statusCode
    $context.Response.ContentType = $contentType
    $context.Response.ContentLength64 = $buffer.Length
    $context.Response.OutputStream.Write($buffer, 0, $buffer.Length)
    $context.Response.OutputStream.Close()
}} finally {{
    if ($listener.IsListening) {{
        $listener.Stop()
    }}
    $listener.Close()
}}
"""
        proc = await asyncio.create_subprocess_exec(
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        )

        try:
            returncode = await proc.wait()
        except asyncio.CancelledError:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            with contextlib.suppress(Exception):
                await proc.wait()
            raise

        row = await self._load_auth_session(auth_session_id)
        if (
            returncode != 0
            and row is not None
            and row["status"] == STATUS_PENDING
        ):
            await self._update_auth_session(
                auth_session_id,
                STATUS_FAILED,
                error_message="Windows localhost 回调桥接启动失败",
            )

    async def _spawn_local_callback_server(self, auth_session_id: str):
        import traceback, httpx
        from urllib.parse import urlparse, parse_qs

        def _html_page(title: str, body: str, success: bool = True) -> bytes:
            color = "#22c55e" if success else "#ef4444"
            icon = "✅" if success else "❌"
            content = f"""HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n\r\n
<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>{title}</title>
<style>body{{font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#0f172a;color:#e2e8f0;}}
.card{{text-align:center;padding:48px;background:#1e293b;border-radius:16px;}}
h2{{color:{color};}}p{{color:#94a3b8;}}</style>
<script>setTimeout(() => window.close(), 2000);</script></head>
<body><div class="card"><div style="font-size:48px;">{icon}</div><h2>{title}</h2><p>{body}</p></div></body></html>"""
            return content.encode("utf-8")

        async def _handle_client(reader, writer):
            try:
                request = (await reader.read(4096)).decode("utf-8", errors="ignore")
                if not request.startswith("GET"):
                    writer.write(b"HTTP/1.1 400 Bad Request\r\n\r\n")
                    await writer.drain()
                    return

                first_line = request.split("\n")[0]
                path_query = first_line.split(" ")[1]
                parsed = urlparse(path_query)
                query = parse_qs(parsed.query)

                code = query.get("code", [None])[0]
                state = query.get("state", [None])[0]
                err = query.get("error", [None])[0]

                if err:
                    writer.write(_html_page("登录失败", f"Error: {err}", False))
                elif state != auth_session_id or not code:
                    writer.write(_html_page("参数错误", "验证通过失败", False))
                    await self._update_auth_session(auth_session_id, "failed", error_message="State or code invalid")
                else:
                    ctx = await self._load_pkce_context(auth_session_id)
                    if not ctx:
                        writer.write(_html_page("时效过期", "请重新发起链接", False))
                        await self._update_auth_session(auth_session_id, "failed", error_message="Context expired")
                    else:
                        async with httpx.AsyncClient() as client:
                            resp = await client.post(
                                ctx["token_endpoint"],
                                json={
                                    "grant_type": "authorization_code",
                                    "client_id": ctx["client_id"],
                                    "code": code,
                                    "redirect_uri": ctx["redirect_uri"],
                                    "code_verifier": ctx["code_verifier"],
                                },
                                timeout=15,
                            )
                        if resp.status_code == 200:
                            data = resp.json()
                            await self._complete_oauth_session(
                                auth_session_id=auth_session_id,
                                provider_id=ctx["provider_id"],
                                token_data=data,
                            )
                            writer.write(_html_page("登录成功", "授权已完成！窗口将自动关闭。"))
                        else:
                            await self._update_auth_session(auth_session_id, "failed", error_message=resp.text)
                            writer.write(_html_page("鉴权失败", "无法通过 code 置换 Token: " + resp.text, False))
            except Exception as e:
                traceback.print_exc()
                writer.write(_html_page("内部崩溃", str(e), False))
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                server.close()

        server = None
        try:
            server = await asyncio.start_server(_handle_client, '127.0.0.1', 1455)
            await server.serve_forever()
        except OSError as e:
            logger.error("Failed to start callback server: %s", e)
            await self._update_auth_session(auth_session_id, "failed", error_message="1455 端口被占用，无法回调")
        except asyncio.CancelledError:
            pass
        finally:
            if server:
                server.close()
                try:
                    await server.wait_closed()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # 后台轮询任务
    # ------------------------------------------------------------------

    def _start_poll_task(
        self,
        auth_session_id: str,
        flow_type: str,
        ctx: Dict[str, Any],
    ) -> None:
        task = asyncio.ensure_future(
            self._poll_loop(auth_session_id, flow_type, ctx)
        )
        self._poll_tasks[auth_session_id] = task
        task.add_done_callback(lambda t: self._poll_tasks.pop(auth_session_id, None))

    async def _poll_loop(
        self,
        auth_session_id: str,
        flow_type: str,
        ctx: Dict[str, Any],
    ) -> None:
        interval = ctx.get("interval", 5)
        expires_at = ctx.get("expires_at", time.time() + 300)

        try:
            while time.time() < expires_at:
                await asyncio.sleep(interval)

                try:
                    if flow_type == FLOW_AWS_IAM:
                        done = await self._poll_aws_iam(auth_session_id, ctx)
                    else:
                        done = await self._poll_oauth(auth_session_id, ctx)
                    if done:
                        return
                except Exception as exc:
                    logger.warning("poll_loop error for %s: %s", auth_session_id, exc)
                    await self._update_auth_session(auth_session_id, STATUS_FAILED, error_message=str(exc))
                    return

            # 超时
            await self._update_auth_session(auth_session_id, STATUS_EXPIRED, error_message="Device authorization timed out")
        except asyncio.CancelledError:
            logger.info("poll_loop cancelled for session %s", auth_session_id)
            return

    async def _poll_aws_iam(self, auth_session_id: str, ctx: Dict[str, Any]) -> bool:
        """轮询 AWS SSO OIDC CreateToken，返回 True 表示完成（成功或失败）。"""
        try:
            import httpx as _httpx  # type: ignore
        except ImportError:
            return False

        row = await self._load_auth_session(auth_session_id)
        if row is None or row["status"] not in (STATUS_PENDING,):
            return True  # 已被外部更新

        sso_start_url = row["client_id"]  # 复用字段
        sso_region = row["client_secret"]  # 复用字段
        client_id_real = ctx["client_id_real"]
        client_secret_real = ctx["client_secret_real"]
        device_code = ctx["device_code"]

        oidc = SSOOIDCClient(sso_start_url=sso_start_url, sso_region=sso_region)
        endpoint = oidc._oidc_endpoint()

        try:
            resp = _httpx.post(
                f"{endpoint}/token",
                json={
                    "clientId": client_id_real,
                    "clientSecret": client_secret_real,
                    "grantType": "urn:ietf:params:oauth:grant-type:device_code",
                    "deviceCode": device_code,
                },
                timeout=10,
            )
        except Exception as exc:
            logger.debug("AWS poll request failed: %s", exc)
            return False

        if resp.status_code == 200:
            token_data = resp.json()
            access_token = token_data.get("accessToken", "")
            refresh_token = token_data.get("refreshToken")
            expires_in = token_data.get("expiresIn", 3600)
            token_expires_at = int(time.time()) + expires_in

            # 获取账号列表，进入 awaiting_role 状态
            try:
                accounts = oidc.list_accounts(access_token)
            except Exception:
                accounts = []

            now = int(time.time())
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """UPDATE auth_sessions SET
                        status = ?, access_token = ?, refresh_token = ?,
                        token_expires_at = ?, accounts_json = ?, updated_at = ?
                       WHERE id = ?""",
                    (
                        STATUS_AWAITING_ROLE,
                        _obfuscate(access_token),
                        _obfuscate(refresh_token) if refresh_token else None,
                        token_expires_at,
                        json.dumps(accounts),
                        now,
                        auth_session_id,
                    ),
                )
                await db.commit()
            return True

        body = resp.json() if resp.content else {}
        error = body.get("error", "")
        if error == "authorization_pending":
            return False
        if error == "slow_down":
            ctx["interval"] = ctx.get("interval", 5) + 5
            return False

        # 其他错误
        await self._update_auth_session(auth_session_id, STATUS_FAILED, error_message=f"AWS SSO error: {error}")
        return True

    async def _poll_oauth(self, auth_session_id: str, ctx: Dict[str, Any]) -> bool:
        """轮询通用 OAuth token endpoint，返回 True 表示完成。"""
        try:
            import httpx as _httpx  # type: ignore
        except ImportError:
            return False

        row = await self._load_auth_session(auth_session_id)
        if row is None or row["status"] != STATUS_PENDING:
            return True

        client_id = ctx["client_id"]
        client_secret = ctx.get("client_secret", "")
        device_code = ctx["device_code"]
        token_endpoint = ctx["token_endpoint"]

        try:
            resp = _httpx.post(
                token_endpoint,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                    "client_id": client_id,
                    **({"client_secret": client_secret} if client_secret else {}),
                },
                timeout=10,
            )
        except Exception as exc:
            logger.debug("OAuth poll request failed: %s", exc)
            return False

        if resp.status_code == 200:
            await self._complete_oauth_session(
                auth_session_id=auth_session_id,
                provider_id=row["provider_id"],
                token_data=resp.json(),
            )
            return True

        body = resp.json() if resp.content else {}
        error = body.get("error", "")
        if error in ("authorization_pending", "slow_down"):
            if error == "slow_down":
                ctx["interval"] = ctx.get("interval", 5) + 5
            return False

        await self._update_auth_session(auth_session_id, STATUS_FAILED, error_message=f"OAuth error: {error}")
        return True

    # ------------------------------------------------------------------
    # 写回 provider_configs
    # ------------------------------------------------------------------

    async def _write_oauth_token_to_provider(
        self,
        provider_id: str,
        access_token: str,
        refresh_token: Optional[str],
        expires_in: int,
    ) -> None:
        """将 OAuth token 写入 provider_configs.auth_config。"""
        from datetime import datetime, timezone, timedelta

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        oauth_token = OAuthToken(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )
        existing_auth = await self._load_provider_auth_config(provider_id)
        auth_config = AuthConfig(
            auth_type=AuthType.OAUTH,
            oauth_token=oauth_token,
            metadata=dict(existing_auth.metadata),
        )
        auth_json = json.dumps(serialize_auth_config(auth_config), ensure_ascii=False)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE provider_configs SET auth_type = ?, auth_config = ? WHERE id = ?",
                (AuthType.OAUTH.value, auth_json, provider_id),
            )
            await db.commit()
        logger.info("OAuth token written to provider %s", provider_id)

    async def _write_sigv4_to_provider(
        self,
        provider_id: str,
        sigv4: Dict[str, str],
        access_token: str,
        row: Any,
    ) -> None:
        """将 AWS Sigv4 凭证写入 provider_configs.auth_config（以 IAM 类型存储）。"""
        existing_auth = await self._load_provider_auth_config(provider_id)
        metadata = dict(existing_auth.metadata)
        # 将 Sigv4 凭证存入 metadata，auth_type 保持 IAM
        # 实际调用时由 LiteLLM 通过环境变量或 boto3 处理
        auth_config = AuthConfig(
            auth_type=AuthType.IAM,
            metadata={
                **metadata,
                "accessKeyId": sigv4.get("accessKeyId", ""),
                "secretAccessKey": _obfuscate(sigv4.get("secretAccessKey", "")),
                "sessionToken": _obfuscate(sigv4.get("sessionToken", "")),
                "sso_access_token": _obfuscate(access_token),
            },
        )
        auth_json = json.dumps(serialize_auth_config(auth_config), ensure_ascii=False)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE provider_configs SET auth_type = ?, auth_config = ? WHERE id = ?",
                (AuthType.IAM.value, auth_json, provider_id),
            )
            await db.commit()
        logger.info("Sigv4 credentials written to provider %s", provider_id)

    async def _complete_oauth_session(
        self,
        auth_session_id: str,
        provider_id: str,
        token_data: Dict[str, Any],
    ) -> None:
        access_token = token_data.get("access_token", "")
        refresh_token = token_data.get("refresh_token")
        expires_in = int(token_data.get("expires_in", 3600) or 3600)
        token_expires_at = int(time.time()) + expires_in
        now = int(time.time())

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """UPDATE auth_sessions SET
                    status = ?, access_token = ?, refresh_token = ?,
                    token_expires_at = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    STATUS_COMPLETED,
                    _obfuscate(access_token),
                    _obfuscate(refresh_token) if refresh_token else None,
                    token_expires_at,
                    now,
                    auth_session_id,
                ),
            )
            await db.commit()

        await self._write_oauth_token_to_provider(
            provider_id,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
        )

    async def _load_pkce_context(self, auth_session_id: str) -> Optional[Dict[str, Any]]:
        ctx = self._pkce_ctx.get(auth_session_id)
        if ctx is not None:
            return ctx

        row = await self._load_auth_session(auth_session_id)
        if row is None or not row["context_json"]:
            return None

        try:
            loaded = json.loads(row["context_json"])
        except json.JSONDecodeError:
            return None
        if not isinstance(loaded, dict):
            return None

        self._pkce_ctx[auth_session_id] = loaded
        return loaded

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    async def _load_auth_session(self, auth_session_id: str) -> Optional[Any]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM auth_sessions WHERE id = ?", (auth_session_id,)
            ) as cursor:
                return await cursor.fetchone()

    async def _load_provider_row(self, provider_id: str) -> Optional[Any]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM provider_configs WHERE id = ?",
                (provider_id,),
            ) as cursor:
                return await cursor.fetchone()

    async def _load_provider_auth_config(self, provider_id: str) -> AuthConfig:
        row = await self._load_provider_row(provider_id)
        if row is None:
            raise ValueError(f"Provider not found: {provider_id}")
        if row["auth_config"]:
            return deserialize_auth_config(row["auth_type"], row["auth_config"])
        return AuthConfig(auth_type=AuthType(row["auth_type"]))

    async def _cancel_tracked_task(
        self,
        task_map: Dict[str, asyncio.Task],
        task_id: str,
    ) -> None:
        task = task_map.pop(task_id, None)
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, RuntimeError):
            if task.get_loop() is asyncio.get_running_loop():
                await task

    async def _update_auth_session(
        self,
        auth_session_id: str,
        status: str,
        error_message: Optional[str] = None,
    ) -> None:
        now = int(time.time())
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE auth_sessions SET status = ?, error_message = ?, updated_at = ? WHERE id = ?",
                (status, error_message, now, auth_session_id),
            )
            await db.commit()
