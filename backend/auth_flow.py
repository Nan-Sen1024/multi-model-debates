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
import json
import logging
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
FLOW_GENERIC_OAUTH = "generic_oauth"

STATUS_PENDING = "pending"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_EXPIRED = "expired"
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
    status: str              # pending / completed / failed / expired / awaiting_role
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
OPENAI_CODEX_CLIENT_ID = "app_EMkd4QpJpUGnomFMzFgMIk3l"  # codex CLI public client id
OPENAI_CODEX_SCOPE = "openid profile email offline_access"
OPENAI_CODEX_AUDIENCE = "https://api.openai.com/v1"


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
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        scope: Optional[str] = None,
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
        elif flow_type in (FLOW_OPENAI_CODEX, FLOW_GENERIC_OAUTH):
            return await self._start_oauth_flow(
                provider_id=provider_id,
                flow_type=flow_type,
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

    # ------------------------------------------------------------------
    # AWS IAM Identity Center 流程
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
        client_id: Optional[str],
        client_secret: Optional[str],
        scope: Optional[str],
    ) -> FlowStartResult:
        try:
            import httpx as _httpx  # type: ignore
        except ImportError:
            raise ProviderUnavailableError("httpx is required for OAuth device flow")

        # openai_codex 使用固定配置
        if flow_type == FLOW_OPENAI_CODEX:
            effective_client_id = OPENAI_CODEX_CLIENT_ID
            effective_scope = OPENAI_CODEX_SCOPE
            # 从 OIDC discovery 获取 device_authorization_endpoint
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
            # 对于 generic_oauth，token_endpoint 就是 device_authorization_endpoint
            device_auth_endpoint = token_endpoint.replace("/token", "/device/code")
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
            token_data = resp.json()
            access_token = token_data.get("access_token", "")
            refresh_token = token_data.get("refresh_token")
            expires_in = token_data.get("expires_in", 3600)
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

            # 将 token 写回 provider_configs.auth_config
            await self._write_oauth_token_to_provider(
                row["provider_id"],
                access_token=access_token,
                refresh_token=refresh_token,
                expires_in=expires_in,
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
        from .llm_gateway import serialize_auth_config

        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
        oauth_token = OAuthToken(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )
        auth_config = AuthConfig(auth_type=AuthType.OAUTH, oauth_token=oauth_token)
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
        from .llm_gateway import serialize_auth_config

        # 将 Sigv4 凭证存入 metadata，auth_type 保持 IAM
        # 实际调用时由 LiteLLM 通过环境变量或 boto3 处理
        auth_config = AuthConfig(
            auth_type=AuthType.IAM,
            metadata={
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
