"""
单元测试：LLM 网关层
覆盖：validate_model_ref、AuthManager.get_headers、ProviderRouter 轮询/fallback、GovCloud 检测
需求：26.1-26.7、27.1-27.14、28.1-28.5、29.1-29.5、30.1-30.5
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from backend.enums import APIFormat, AuthType, ProviderType
from backend.exceptions import (
    AuthenticationError,
    ProviderUnavailableError,
    ValidationError,
)
from backend.llm_gateway import (
    AuthManager,
    LLMGatewayClient,
    ProviderRouter,
    SSOOIDCClient,
    _deobfuscate,
    _obfuscate,
    _is_chatgpt_authentication_error,
    _llm_httpx_timeout,
    _without_litellm_proxy_environment,
    _without_proxy_environment,
    deserialize_auth_config,
    serialize_auth_config,
    validate_model_ref,
)
from backend.invocation_plan import InvocationRuntimeKind, build_invocation_plan
from backend.models import AuthConfig, OAuthToken, ProviderConfig


# ---------------------------------------------------------------------------
# 辅助工厂
# ---------------------------------------------------------------------------

def make_provider(
    name: str = "test-provider",
    provider_type: ProviderType = ProviderType.OPENAI,
    base_url: str = "https://api.openai.com/v1",
    auth_type: AuthType = AuthType.API_KEY,
    api_key: str = "sk-test",
    is_active: bool = True,
) -> ProviderConfig:
    auth_config = AuthConfig(auth_type=auth_type, api_key=api_key)
    return ProviderConfig(
        id=name,
        name=name,
        provider_type=provider_type,
        base_url=base_url,
        api_format=APIFormat.OPENAI_COMPLETIONS,
        auth_type=auth_type,
        auth_config=auth_config,
        is_active=is_active,
    )


def make_oauth_provider(
    name: str = "openai-browser",
    provider_type: ProviderType = ProviderType.OPENAI,
    base_url: str = "https://api.openai.com/v1",
) -> ProviderConfig:
    auth_config = AuthConfig(
        auth_type=AuthType.OAUTH,
        oauth_token=OAuthToken(access_token="oauth-access"),
    )
    return ProviderConfig(
        id=name,
        name=name,
        provider_type=provider_type,
        base_url=base_url,
        api_format=APIFormat.OPENAI_COMPLETIONS,
        auth_type=AuthType.OAUTH,
        auth_config=auth_config,
    )


# ===========================================================================
# 1. validate_model_ref 格式校验（10.4）
# ===========================================================================

class TestValidateModelRef:
    """需求：26.5、26.6"""

    def test_valid_format_returns_tuple(self):
        provider, model = validate_model_ref("openai/gpt-4o")
        assert provider == "openai"
        assert model == "gpt-4o"


class TestInvocationPlan:
    def test_builds_plan_for_bare_model_with_bound_provider(self):
        provider = make_provider(name="openai-browser")

        plan = build_invocation_plan("gpt-5.4", provider_config=provider)

        assert plan.provider_id == "openai-browser"
        assert plan.model_name == "gpt-5.4"
        assert plan.runtime_kind == InvocationRuntimeKind.HTTPX_OPENAI_COMPATIBLE

    def test_classifies_chatgpt_oauth_responses_runtime(self):
        provider = make_oauth_provider()

        plan = build_invocation_plan("gpt-5.4", provider_config=provider)

        assert plan.runtime_kind == InvocationRuntimeKind.CHATGPT_OAUTH_RESPONSES
        assert plan.provider_type == ProviderType.OPENAI

    def test_classifies_anthropic_messages_runtime(self):
        provider = make_provider(
            name="anthropic-primary",
            provider_type=ProviderType.ANTHROPIC,
            base_url="https://api.anthropic.com/v1",
        )
        provider.api_format = APIFormat.ANTHROPIC_MESSAGES

        plan = build_invocation_plan("claude-4.7", provider_config=provider)

        assert plan.runtime_kind == InvocationRuntimeKind.HTTPX_ANTHROPIC_MESSAGES
        assert plan.provider_name == "anthropic-primary"

    def test_valid_anthropic(self):
        provider, model = validate_model_ref("anthropic/claude-opus-4")
        assert provider == "anthropic"
        assert model == "claude-opus-4"

    def test_valid_ollama(self):
        provider, model = validate_model_ref("ollama/llama3.3")
        assert provider == "ollama"
        assert model == "llama3.3"

    def test_valid_model_with_colon(self):
        """模型名可以包含冒号（如版本标签）"""
        provider, model = validate_model_ref("ollama/llama3:latest")
        assert provider == "ollama"
        assert model == "llama3:latest"

    def test_invalid_no_slash_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            validate_model_ref("openai-gpt4")
        assert "model_ref" in str(exc_info.value.field or "")

    def test_invalid_empty_string_raises(self):
        with pytest.raises(ValidationError):
            validate_model_ref("")

    def test_invalid_none_raises(self):
        with pytest.raises(ValidationError):
            validate_model_ref(None)  # type: ignore

    def test_invalid_only_slash_raises(self):
        with pytest.raises(ValidationError):
            validate_model_ref("/")

    def test_invalid_empty_provider_raises(self):
        with pytest.raises(ValidationError):
            validate_model_ref("/gpt-4o")

    def test_invalid_empty_model_raises(self):
        with pytest.raises(ValidationError):
            validate_model_ref("openai/")

    def test_invalid_multiple_slashes_raises(self):
        """多个斜杠不符合 provider/model 格式"""
        with pytest.raises(ValidationError):
            validate_model_ref("openai/gpt/4o")

    def test_whitespace_only_provider_raises(self):
        with pytest.raises(ValidationError):
            validate_model_ref("   /gpt-4o")

    def test_whitespace_only_model_raises(self):
        with pytest.raises(ValidationError):
            validate_model_ref("openai/   ")

    def test_strips_whitespace(self):
        """前后空格应被去除"""
        provider, model = validate_model_ref(" openai / gpt-4o ")
        assert provider == "openai"
        assert model == "gpt-4o"


# ===========================================================================
# 2. AuthManager.get_headers 各认证类型（10.3）
# ===========================================================================

class TestProxyContexts:
    def test_without_proxy_environment_clears_lowercase_and_uppercase_proxy_vars(self, monkeypatch):
        proxy_keys = (
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "all_proxy",
            "no_proxy",
        )
        for key in proxy_keys:
            monkeypatch.setenv(key, "http://127.0.0.1:7897")

        with _without_proxy_environment():
            for key in proxy_keys:
                assert os.environ.get(key) is None

    def test_without_litellm_proxy_environment_disables_trust_env(self, monkeypatch):
        fake_litellm = SimpleNamespace(
            disable_aiohttp_trust_env=False,
            aiohttp_trust_env=True,
        )
        monkeypatch.setattr("backend.llm_gateway.litellm", fake_litellm)
        monkeypatch.setenv("DISABLE_AIOHTTP_TRUST_ENV", "False")
        monkeypatch.setenv("AIOHTTP_TRUST_ENV", "True")

        with _without_litellm_proxy_environment():
            assert fake_litellm.disable_aiohttp_trust_env is True
            assert fake_litellm.aiohttp_trust_env is False
            assert os.environ["DISABLE_AIOHTTP_TRUST_ENV"] == "True"
            assert os.environ["AIOHTTP_TRUST_ENV"] == "False"

        assert fake_litellm.disable_aiohttp_trust_env is False
        assert fake_litellm.aiohttp_trust_env is True
        assert os.environ["DISABLE_AIOHTTP_TRUST_ENV"] == "False"
        assert os.environ["AIOHTTP_TRUST_ENV"] == "True"


class TestAuthManagerGetHeaders:
    """需求：27.1-27.7"""

    def setup_method(self):
        self.manager = AuthManager()

    # --- API_KEY ---
    def test_api_key_returns_authorization_header(self):
        config = AuthConfig(auth_type=AuthType.API_KEY, api_key="sk-plaintext")
        headers = self.manager.get_headers(config)
        assert "Authorization" in headers
        assert "sk-plaintext" in headers["Authorization"]

    def test_api_key_obfuscated_is_deobfuscated(self):
        stored = _obfuscate("sk-secret")
        config = AuthConfig(auth_type=AuthType.API_KEY, api_key=stored)
        headers = self.manager.get_headers(config)
        assert "sk-secret" in headers["Authorization"]

    def test_api_key_missing_raises_auth_error(self):
        config = AuthConfig(auth_type=AuthType.API_KEY, api_key=None)
        with pytest.raises(AuthenticationError):
            self.manager.get_headers(config)

    # --- BEARER ---
    def test_bearer_returns_authorization_header(self):
        config = AuthConfig(auth_type=AuthType.BEARER, bearer_token="my-token")
        headers = self.manager.get_headers(config)
        assert headers["Authorization"] == "Bearer my-token"

    def test_bearer_missing_raises_auth_error(self):
        config = AuthConfig(auth_type=AuthType.BEARER, bearer_token=None)
        with pytest.raises(AuthenticationError):
            self.manager.get_headers(config)

    # --- OAUTH ---
    def test_oauth_valid_token_returns_header(self):
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        token = OAuthToken(access_token="oauth-access", expires_at=future)
        config = AuthConfig(auth_type=AuthType.OAUTH, oauth_token=token)
        headers = self.manager.get_headers(config)
        assert headers["Authorization"] == "Bearer oauth-access"

    def test_oauth_no_expiry_returns_header(self):
        token = OAuthToken(access_token="oauth-no-expiry")
        config = AuthConfig(auth_type=AuthType.OAUTH, oauth_token=token)
        headers = self.manager.get_headers(config)
        assert "oauth-no-expiry" in headers["Authorization"]

    def test_oauth_expired_without_refresh_raises(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        token = OAuthToken(access_token="expired", expires_at=past)
        config = AuthConfig(auth_type=AuthType.OAUTH, oauth_token=token)
        with pytest.raises(AuthenticationError):
            self.manager.get_headers(config)

    def test_oauth_missing_raises_auth_error(self):
        config = AuthConfig(auth_type=AuthType.OAUTH, oauth_token=None)
        with pytest.raises(AuthenticationError):
            self.manager.get_headers(config)

    # --- IAM ---
    def test_iam_returns_empty_headers(self):
        config = AuthConfig(auth_type=AuthType.IAM)
        headers = self.manager.get_headers(config)
        assert isinstance(headers, dict)
        # IAM 由环境变量/实例角色处理，headers 可以为空
        assert "Authorization" not in headers

    # --- ADC ---
    def test_adc_returns_empty_headers(self):
        config = AuthConfig(auth_type=AuthType.ADC)
        headers = self.manager.get_headers(config)
        assert isinstance(headers, dict)

    # --- HELPER ---
    def test_helper_executes_script_and_returns_key(self):
        config = AuthConfig(
            auth_type=AuthType.HELPER,
            helper_script="echo sk-from-helper",
        )
        headers = self.manager.get_headers(config)
        assert "sk-from-helper" in headers["Authorization"]

    def test_helper_missing_script_raises(self):
        config = AuthConfig(auth_type=AuthType.HELPER, helper_script=None)
        with pytest.raises(AuthenticationError):
            self.manager.get_headers(config)

    def test_helper_failing_script_raises(self):
        config = AuthConfig(
            auth_type=AuthType.HELPER,
            helper_script="exit 1",
        )
        with pytest.raises(AuthenticationError):
            self.manager.get_headers(config)

    # --- 凭证不暴露 ---
    def test_auth_error_does_not_expose_credentials(self):
        """认证失败时错误信息不包含原始凭证，需求：27.7"""
        config = AuthConfig(auth_type=AuthType.API_KEY, api_key=None)
        try:
            self.manager.get_headers(config)
        except AuthenticationError as exc:
            # 错误信息不应包含 api_key 的原始值
            assert "None" not in str(exc) or "API Key" in str(exc)


# ===========================================================================
# 3. API Key 加密存储（10.3）
# ===========================================================================

class TestApiKeyObfuscation:
    """需求：27.1"""

    def test_obfuscate_deobfuscate_roundtrip(self):
        original = "sk-super-secret-key"
        stored = _obfuscate(original)
        assert stored != original
        recovered = _deobfuscate(stored)
        assert recovered == original

    def test_store_and_load_api_key(self):
        key = "sk-test-12345"
        stored = AuthManager.store_api_key(key)
        loaded = AuthManager.load_api_key(stored)
        assert loaded == key


class TestAuthConfigSerialization:
    def test_serialize_and_deserialize_preserves_metadata(self):
        config = AuthConfig(
            auth_type=AuthType.OAUTH,
            oauth_token=OAuthToken(access_token="oauth-access"),
            metadata={
                "authorization_endpoint": "https://example.com/authorize",
                "token_endpoint": "https://example.com/token",
                "client_id": "client-123",
            },
        )

        payload = serialize_auth_config(config)
        restored = deserialize_auth_config(AuthType.OAUTH, payload)

        assert restored.metadata == config.metadata

    def test_obfuscated_key_is_not_plaintext(self):
        key = "sk-plaintext"
        stored = _obfuscate(key)
        assert key not in stored


# ===========================================================================
# 4. ProviderRouter 轮询逻辑（10.2）
# ===========================================================================

class TestProviderRouterRoundRobin:
    """需求：28.4"""

    def setup_method(self):
        self.router = ProviderRouter()

    def test_single_provider_always_selected(self):
        providers = [make_provider("p1")]
        for _ in range(5):
            selected = self.router.route("openai/gpt-4o", providers)
            assert selected.name == "p1"

    def test_two_providers_alternate(self):
        providers = [make_provider("p1"), make_provider("p2")]
        results = [
            self.router.route("openai/gpt-4o", providers).name for _ in range(4)
        ]
        assert results == ["p1", "p2", "p1", "p2"]

    def test_three_providers_cycle(self):
        providers = [make_provider("p1"), make_provider("p2"), make_provider("p3")]
        results = [
            self.router.route("openai/gpt-4o", providers).name for _ in range(6)
        ]
        assert results == ["p1", "p2", "p3", "p1", "p2", "p3"]

    def test_inactive_providers_excluded(self):
        providers = [
            make_provider("p1", is_active=False),
            make_provider("p2", is_active=True),
        ]
        selected = self.router.route("openai/gpt-4o", providers)
        assert selected.name == "p2"

    def test_all_inactive_raises(self):
        providers = [make_provider("p1", is_active=False)]
        with pytest.raises(ProviderUnavailableError):
            self.router.route("openai/gpt-4o", providers)

    def test_empty_providers_raises(self):
        with pytest.raises(ProviderUnavailableError):
            self.router.route("openai/gpt-4o", [])

    def test_different_model_refs_have_independent_counters(self):
        """不同 model_ref 的轮询计数器互相独立"""
        providers = [make_provider("p1"), make_provider("p2")]
        router = ProviderRouter()
        # model A 调用 1 次
        r1 = router.route("openai/gpt-4o", providers)
        # model B 调用 1 次（应从 p1 开始，不受 model A 影响）
        r2 = router.route("anthropic/claude-3", providers)
        assert r1.name == "p1"
        assert r2.name == "p1"


# ===========================================================================
# 5. ProviderRouter fallback 切换逻辑（10.2）
# ===========================================================================

class TestProviderRouterFallback:
    """需求：28.3、28.5"""

    def setup_method(self):
        self.router = ProviderRouter()

    def test_first_provider_succeeds_no_fallback(self):
        providers = [make_provider("p1"), make_provider("p2")]

        async def call_fn(provider):
            return f"ok-{provider.name}"

        result = asyncio.run(
            self.router.with_fallback("openai/gpt-4o", providers, call_fn)
        )
        assert result == "ok-p1"

    def test_first_fails_second_succeeds(self):
        providers = [make_provider("p1"), make_provider("p2")]
        call_count = {"n": 0}

        async def call_fn(provider):
            call_count["n"] += 1
            if provider.name == "p1":
                raise ProviderUnavailableError("p1 不可用")
            return f"ok-{provider.name}"

        result = asyncio.run(
            self.router.with_fallback("openai/gpt-4o", providers, call_fn)
        )
        assert result == "ok-p2"
        assert call_count["n"] == 2

    def test_all_fail_raises_provider_unavailable(self):
        providers = [make_provider("p1"), make_provider("p2")]

        async def call_fn(provider):
            raise ProviderUnavailableError(f"{provider.name} 不可用")

        with pytest.raises(ProviderUnavailableError):
            asyncio.run(
                self.router.with_fallback("openai/gpt-4o", providers, call_fn)
            )

    def test_empty_providers_raises(self):
        async def call_fn(provider):
            return "ok"

        with pytest.raises(ProviderUnavailableError):
            asyncio.run(self.router.with_fallback("openai/gpt-4o", [], call_fn))

    def test_fallback_order_is_preserved(self):
        """fallback 按列表顺序切换，需求：28.3"""
        providers = [
            make_provider("p1"),
            make_provider("p2"),
            make_provider("p3"),
        ]
        tried = []

        async def call_fn(provider):
            tried.append(provider.name)
            if provider.name != "p3":
                raise ProviderUnavailableError(f"{provider.name} 失败")
            return "ok"

        result = asyncio.run(
            self.router.with_fallback("openai/gpt-4o", providers, call_fn)
        )
        assert result == "ok"
        assert tried == ["p1", "p2", "p3"]

    def test_auth_error_also_triggers_fallback(self):
        """认证失败也应触发 fallback，需求：28.3"""
        providers = [make_provider("p1"), make_provider("p2")]

        async def call_fn(provider):
            if provider.name == "p1":
                raise AuthenticationError("认证失败")
            return "ok"

        result = asyncio.run(
            self.router.with_fallback("openai/gpt-4o", providers, call_fn)
        )
        assert result == "ok"


# ===========================================================================
# 6. GovCloud URL 检测（10.3）
# ===========================================================================

class TestGovCloudDetection:
    """需求：27.9、30.1"""

    def test_standard_url_is_not_govcloud(self):
        client = SSOOIDCClient(
            sso_start_url="https://mycompany.awsapps.com/start",
            sso_region="us-east-1",
        )
        assert client._is_govcloud() is False

    def test_govcloud_url_detected(self):
        client = SSOOIDCClient(
            sso_start_url="https://start.us-gov-home.awsapps.com/directory/abc123",
            sso_region="us-gov-west-1",
        )
        assert client._is_govcloud() is True

    def test_govcloud_oidc_endpoint_uses_gov_domain(self):
        client = SSOOIDCClient(
            sso_start_url="https://start.us-gov-home.awsapps.com/directory/abc",
            sso_region="us-gov-west-1",
        )
        endpoint = client._oidc_endpoint()
        assert "amazonaws-us-gov.com" in endpoint

    def test_standard_oidc_endpoint_uses_standard_domain(self):
        client = SSOOIDCClient(
            sso_start_url="https://mycompany.awsapps.com/start",
            sso_region="us-east-1",
        )
        endpoint = client._oidc_endpoint()
        assert "amazonaws.com" in endpoint
        assert "amazonaws-us-gov.com" not in endpoint

    def test_govcloud_sso_endpoint_uses_gov_domain(self):
        client = SSOOIDCClient(
            sso_start_url="https://start.us-gov-home.awsapps.com/directory/abc",
            sso_region="us-gov-west-1",
        )
        endpoint = client._sso_endpoint()
        assert "amazonaws-us-gov.com" in endpoint

    def test_standard_sso_endpoint_uses_standard_domain(self):
        client = SSOOIDCClient(
            sso_start_url="https://mycompany.awsapps.com/start",
            sso_region="us-east-1",
        )
        endpoint = client._sso_endpoint()
        assert "amazonaws.com" in endpoint
        assert "amazonaws-us-gov.com" not in endpoint

    def test_govcloud_region_in_endpoint(self):
        client = SSOOIDCClient(
            sso_start_url="https://start.us-gov-home.awsapps.com/directory/abc",
            sso_region="us-gov-east-1",
        )
        endpoint = client._oidc_endpoint()
        assert "us-gov-east-1" in endpoint


# ===========================================================================
# 7. LLMGatewayClient health_check（10.1）
# ===========================================================================

class TestLLMGatewayClientHealthCheck:
    """需求：26.7、29.1"""

    def test_health_check_returns_true_on_200(self):
        provider = make_provider(
            "ollama-local",
            provider_type=ProviderType.OLLAMA,
            base_url="http://127.0.0.1:11434/v1",
        )
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        async def run():
            client = LLMGatewayClient()
            with patch("backend.llm_gateway._HTTPX_AVAILABLE", True):
                with patch("httpx.AsyncClient") as mock_client_cls:
                    mock_client = AsyncMock()
                    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_client.__aexit__ = AsyncMock(return_value=False)
                    mock_client.get = AsyncMock(return_value=mock_resp)
                    mock_client_cls.return_value = mock_client
                    return await client.health_check(provider)

        result = asyncio.run(run())
        assert result is True

    def test_health_check_returns_false_on_connection_error(self):
        provider = make_provider(
            "bad-provider",
            base_url="http://nonexistent.local/v1",
        )

        async def run():
            client = LLMGatewayClient()
            with patch("backend.llm_gateway._HTTPX_AVAILABLE", True):
                with patch("httpx.AsyncClient") as mock_client_cls:
                    mock_client = AsyncMock()
                    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_client.__aexit__ = AsyncMock(return_value=False)
                    mock_client.get = AsyncMock(
                        side_effect=Exception("Connection refused")
                    )
                    mock_client_cls.return_value = mock_client
                    return await client.health_check(provider)

        result = asyncio.run(run())
        assert result is False

    def test_health_check_returns_true_when_httpx_unavailable(self):
        """httpx 未安装时跳过检查，返回 True，需求：29.3"""
        provider = make_provider("any")

        async def run():
            client = LLMGatewayClient()
            with patch("backend.llm_gateway._HTTPX_AVAILABLE", False):
                return await client.health_check(provider)

        result = asyncio.run(run())
        assert result is True

    def test_health_check_returns_false_when_default_model_auth_fails(self):
        provider = make_provider(
            "waicc",
            provider_type=ProviderType.OPENAI,
            base_url="http://api.example.test/v1",
        )
        provider.auth_config.metadata["default_model_ref"] = "gpt-5.4"

        mock_resp = MagicMock()
        mock_resp.status_code = 200

        async def run():
            client = LLMGatewayClient()
            with patch("backend.llm_gateway._HTTPX_AVAILABLE", True):
                with patch("httpx.AsyncClient") as mock_client_cls:
                    mock_client = AsyncMock()
                    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_client.__aexit__ = AsyncMock(return_value=False)
                    mock_client.get = AsyncMock(return_value=mock_resp)
                    mock_client_cls.return_value = mock_client
                    with patch.object(
                        client,
                        "_stream_openai_compatible",
                        side_effect=AuthenticationError("token invalid", provider="waicc"),
                    ):
                        return await client.health_check(provider)

        result = asyncio.run(run())
        assert result is False


# ===========================================================================
# 8. LLMGatewayClient get_local_models（10.1）
# ===========================================================================

class TestGetLocalModels:
    """需求：29.1"""

    def test_returns_model_names_from_ollama(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "models": [
                {"name": "llama3.3:latest"},
                {"name": "mistral:7b"},
            ]
        }

        async def run():
            client = LLMGatewayClient()
            with patch("backend.llm_gateway._HTTPX_AVAILABLE", True):
                with patch("httpx.AsyncClient") as mock_client_cls:
                    mock_client = AsyncMock()
                    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_client.__aexit__ = AsyncMock(return_value=False)
                    mock_client.get = AsyncMock(return_value=mock_resp)
                    mock_client_cls.return_value = mock_client
                    return await client.get_local_models("http://127.0.0.1:11434")

        models = asyncio.run(run())
        assert "llama3.3:latest" in models
        assert "mistral:7b" in models

    def test_returns_empty_on_connection_error(self):
        async def run():
            client = LLMGatewayClient()
            with patch("backend.llm_gateway._HTTPX_AVAILABLE", True):
                with patch("httpx.AsyncClient") as mock_client_cls:
                    mock_client = AsyncMock()
                    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_client.__aexit__ = AsyncMock(return_value=False)
                    mock_client.get = AsyncMock(side_effect=Exception("refused"))
                    mock_client_cls.return_value = mock_client
                    return await client.get_local_models("http://127.0.0.1:11434")

        models = asyncio.run(run())
        assert models == []

    def test_returns_empty_when_httpx_unavailable(self):
        async def run():
            client = LLMGatewayClient()
            with patch("backend.llm_gateway._HTTPX_AVAILABLE", False):
                return await client.get_local_models("http://127.0.0.1:11434")

        models = asyncio.run(run())
        assert models == []

    def test_normalizes_url_with_v1_suffix(self):
        """URL 末尾的 /v1 应被去除后再拼接 /api/tags"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"models": [{"name": "llama3"}]}
        called_urls = []

        async def run():
            client = LLMGatewayClient()
            with patch("backend.llm_gateway._HTTPX_AVAILABLE", True):
                with patch("httpx.AsyncClient") as mock_client_cls:
                    mock_client = AsyncMock()
                    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_client.__aexit__ = AsyncMock(return_value=False)

                    async def fake_get(url, **kwargs):
                        called_urls.append(url)
                        return mock_resp

                    mock_client.get = fake_get
                    mock_client_cls.return_value = mock_client
                    return await client.get_local_models("http://127.0.0.1:11434/v1")

        asyncio.run(run())
        assert any("/api/tags" in url for url in called_urls)
        assert not any("/v1/api/tags" in url for url in called_urls)


# ===========================================================================
# 9. LLMGatewayClient chat_stream（显式 provider 绑定）
# ===========================================================================

class TestLLMGatewayClientChatStream:
    """需求：26.5、28.1"""

    def test_explicit_provider_binding_accepts_bare_model_name(self):
        provider = make_provider(
            "deepseek",
            provider_type=ProviderType.CUSTOM,
            base_url="https://api.deepseek.com/v1",
        )
        captured = {}

        async def fake_provider_httpx_stream(
            self,
            provider,
            model,
            messages,
            headers,
            auth_config,
            provider_config,
        ):
            captured["provider_name"] = provider
            captured["model"] = model
            captured["messages"] = messages
            captured["provider_config"] = provider_config
            yield "hello"

        async def run():
            client = LLMGatewayClient()
            with patch.object(
                LLMGatewayClient,
                "_provider_httpx_stream",
                fake_provider_httpx_stream,
            ):
                return [
                    chunk
                    async for chunk in client.chat_stream(
                        "deepseek-chat",
                        [{"role": "user", "content": "hi"}],
                        provider_config=provider,
                    )
                ]

        chunks = asyncio.run(run())

        assert chunks == ["hello"]
        assert captured["model"] == "deepseek-chat"
        assert captured["provider_config"] == provider

    def test_openai_oauth_runtime_uses_chatgpt_responses_entry(self):
        provider = make_provider(
            "openai-browser",
            provider_type=ProviderType.OPENAI,
            base_url="https://api.openai.com/v1",
            auth_type=AuthType.OAUTH,
            api_key=None,
        )
        provider.auth_type = AuthType.OAUTH
        provider.auth_config = AuthConfig(
            auth_type=AuthType.OAUTH,
            oauth_token=OAuthToken(
                access_token="oauth-access-token",
                refresh_token="oauth-refresh-token",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ),
        )

        captured: dict[str, object] = {}

        class FakeResponsesStream:
            def __init__(self, events):
                self._events = list(events)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._events:
                    raise StopAsyncIteration
                return self._events.pop(0)

        async def fake_aresponses(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return FakeResponsesStream(
                [
                    {"type": "response.output_text.delta", "delta": "hello"},
                    {"type": "response.completed", "response": {"output": []}},
                ]
            )

        async def run():
            client = LLMGatewayClient()
            with patch("backend.llm_gateway._load_litellm", return_value=True), patch(
                "backend.llm_gateway.litellm",
                SimpleNamespace(
                    aresponses=fake_aresponses,
                    acompletion=AsyncMock(side_effect=AssertionError("chat.completions should not be used")),
                ),
            ), patch(
                "httpx.AsyncClient",
                side_effect=AssertionError("browser OAuth OpenAI should not use /v1/chat/completions"),
            ):
                return [
                    chunk
                    async for chunk in client.chat_stream(
                        "openai/gpt-5.3-codex",
                        [{"role": "system", "content": "Stay focused"}, {"role": "user", "content": "hi"}],
                        provider_config=provider,
                    )
                ]

        chunks = asyncio.run(run())

        assert chunks == ["hello"]
        assert captured["kwargs"]["custom_llm_provider"] == "chatgpt"
        assert captured["kwargs"]["stream"] is True
        assert captured["kwargs"]["store"] is False
        assert captured["kwargs"]["model"] == "gpt-5.3-codex"
        assert "api_base" not in captured["kwargs"]
        assert captured["kwargs"]["instructions"] == "Stay focused"
        assert captured["kwargs"]["input"] == [
            {"type": "message", "role": "user", "content": "hi"}
        ]

    def test_chat_stream_without_provider_config_still_uses_oauth_chatgpt_runtime(self):
        auth_config = AuthConfig(
            auth_type=AuthType.OAUTH,
            oauth_token=OAuthToken(
                access_token="oauth-access-token",
                refresh_token="oauth-refresh-token",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ),
        )
        captured: dict[str, object] = {}

        class FakeResponsesStream:
            def __init__(self, events):
                self._events = list(events)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._events:
                    raise StopAsyncIteration
                return self._events.pop(0)

        async def fake_aresponses(*args, **kwargs):
            captured["kwargs"] = kwargs
            return FakeResponsesStream(
                [
                    {"type": "response.output_text.delta", "delta": "hello"},
                    {"type": "response.completed", "response": {"output": []}},
                ]
            )

        fake_plan = SimpleNamespace(
            provider_key="openai",
            model_name="gpt-5.4",
            provider_id=None,
            provider_name="openai",
            provider_type=ProviderType.OPENAI,
            api_format=APIFormat.OPENAI_COMPLETIONS,
            auth_type=AuthType.OAUTH,
            base_url=None,
            runtime_kind=InvocationRuntimeKind.LITELLM_COMPLETION,
        )

        async def run():
            client = LLMGatewayClient()
            with patch("backend.llm_gateway.build_invocation_plan", return_value=fake_plan), patch(
                "backend.llm_gateway._load_litellm",
                return_value=True,
            ), patch(
                "backend.llm_gateway.litellm",
                SimpleNamespace(
                    aresponses=fake_aresponses,
                    acompletion=AsyncMock(side_effect=AssertionError("chat.completions should not be used")),
                ),
            ), patch(
                "httpx.AsyncClient",
                side_effect=AssertionError("OAuth runtime should not use direct HTTPX"),
            ):
                return [
                    chunk
                    async for chunk in client.chat_stream(
                        "openai/gpt-5.4",
                        [{"role": "user", "content": "hi"}],
                        auth_config=auth_config,
                    )
                ]

        chunks = asyncio.run(run())

        assert chunks == ["hello"]
        assert captured["kwargs"]["custom_llm_provider"] == "chatgpt"
        assert captured["kwargs"]["model"] == "gpt-5.4"

    def test_openai_oauth_runtime_reports_refreshed_chatgpt_auth(self):
        provider = make_provider(
            "openai-browser",
            provider_type=ProviderType.OPENAI,
            base_url="https://api.openai.com/v1",
            auth_type=AuthType.OAUTH,
            api_key=None,
        )
        provider.auth_type = AuthType.OAUTH
        provider.auth_config = AuthConfig(
            auth_type=AuthType.OAUTH,
            oauth_token=OAuthToken(
                access_token="old-access-token",
                refresh_token="old-refresh-token",
                expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
            ),
        )
        refreshed_expires_at = int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())
        updates: list[AuthConfig] = []

        class FakeResponsesStream:
            def __init__(self, events):
                self._events = list(events)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._events:
                    raise StopAsyncIteration
                return self._events.pop(0)

        async def fake_aresponses(*args, **kwargs):
            del args, kwargs
            auth_path = os.path.join(
                os.environ["CHATGPT_TOKEN_DIR"],
                os.environ["CHATGPT_AUTH_FILE"],
            )
            with open(auth_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "access_token": "new-access-token",
                        "refresh_token": "new-refresh-token",
                        "expires_at": refreshed_expires_at,
                        "account_id": "acct-new",
                    },
                    handle,
                )
            return FakeResponsesStream(
                [
                    {"type": "response.output_text.delta", "delta": "hello"},
                    {"type": "response.completed", "response": {"output": []}},
                ]
            )

        async def on_auth_update(updated_auth_config: AuthConfig):
            updates.append(updated_auth_config)

        async def run():
            client = LLMGatewayClient()
            with patch("backend.llm_gateway._load_litellm", return_value=True), patch(
                "backend.llm_gateway.litellm",
                SimpleNamespace(
                    aresponses=fake_aresponses,
                    acompletion=AsyncMock(side_effect=AssertionError("chat.completions should not be used")),
                ),
            ):
                return [
                    chunk
                    async for chunk in client.chat_stream(
                        "openai/gpt-5.4",
                        [{"role": "user", "content": "hi"}],
                        provider_config=provider,
                        on_auth_update=on_auth_update,
                    )
                ]

        chunks = asyncio.run(run())

        assert chunks == ["hello"]
        assert len(updates) == 1
        assert updates[0].oauth_token is not None
        assert updates[0].oauth_token.access_token == "new-access-token"
        assert updates[0].oauth_token.refresh_token == "new-refresh-token"
        assert updates[0].metadata["account_id"] == "acct-new"
        assert provider.auth_config.oauth_token is not None
        assert provider.auth_config.oauth_token.access_token == "new-access-token"

    def test_openai_oauth_runtime_imports_fresher_shared_codex_auth(self, tmp_path):
        def fake_jwt(account_id: str, exp: int) -> str:
            payload = {
                "exp": exp,
                "https://api.openai.com/auth": {"chatgpt_account_id": account_id},
            }
            encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
            return f"header.{encoded}.signature"

        old_expires_at = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
        new_expires_at = int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp())
        shared_auth_file = tmp_path / "auth.json"
        shared_auth_file.write_text(
            json.dumps(
                {
                    "auth_mode": "chatgpt",
                    "tokens": {
                        "access_token": fake_jwt("acct-shared", new_expires_at),
                        "refresh_token": "shared-refresh-token",
                        "account_id": "acct-shared",
                    },
                }
            ),
            encoding="utf-8",
        )

        provider = make_provider(
            "openai-browser",
            provider_type=ProviderType.OPENAI,
            base_url="https://api.openai.com/v1",
            auth_type=AuthType.OAUTH,
            api_key=None,
        )
        provider.auth_type = AuthType.OAUTH
        provider.auth_config = AuthConfig(
            auth_type=AuthType.OAUTH,
            oauth_token=OAuthToken(
                access_token=fake_jwt("acct-shared", old_expires_at),
                refresh_token="old-refresh-token",
                expires_at=datetime.fromtimestamp(old_expires_at, timezone.utc),
            ),
            metadata={"account_id": "acct-shared", "codex_auth_file": str(shared_auth_file)},
        )
        bridge_payloads: list[dict[str, object]] = []
        updates: list[AuthConfig] = []

        class FakeResponsesStream:
            def __init__(self, events):
                self._events = list(events)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._events:
                    raise StopAsyncIteration
                return self._events.pop(0)

        async def fake_aresponses(*args, **kwargs):
            del args, kwargs
            auth_path = os.path.join(
                os.environ["CHATGPT_TOKEN_DIR"],
                os.environ["CHATGPT_AUTH_FILE"],
            )
            with open(auth_path, encoding="utf-8") as handle:
                bridge_payloads.append(json.load(handle))
            return FakeResponsesStream(
                [
                    {"type": "response.output_text.delta", "delta": "hello"},
                    {"type": "response.completed", "response": {"output": []}},
                ]
            )

        async def on_auth_update(updated_auth_config: AuthConfig):
            updates.append(updated_auth_config)

        async def run():
            client = LLMGatewayClient()
            with patch("backend.llm_gateway._load_litellm", return_value=True), patch(
                "backend.llm_gateway.litellm",
                SimpleNamespace(
                    aresponses=fake_aresponses,
                    acompletion=AsyncMock(side_effect=AssertionError("chat.completions should not be used")),
                ),
            ):
                return [
                    chunk
                    async for chunk in client.chat_stream(
                        "openai/gpt-5.4",
                        [{"role": "user", "content": "hi"}],
                        provider_config=provider,
                        on_auth_update=on_auth_update,
                    )
                ]

        chunks = asyncio.run(run())

        assert chunks == ["hello"]
        assert len(updates) == 1
        assert updates[0].oauth_token is not None
        assert updates[0].oauth_token.refresh_token == "shared-refresh-token"
        assert bridge_payloads[0]["access_token"] == updates[0].oauth_token.access_token
        assert provider.auth_config.oauth_token is not None
        assert provider.auth_config.oauth_token.refresh_token == "shared-refresh-token"

    def test_openai_oauth_runtime_does_not_import_shared_auth_for_other_account(self, tmp_path):
        def fake_jwt(account_id: str, exp: int) -> str:
            payload = {
                "exp": exp,
                "https://api.openai.com/auth": {"chatgpt_account_id": account_id},
            }
            encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
            return f"header.{encoded}.signature"

        old_expires_at = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
        new_expires_at = int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp())
        shared_auth_file = tmp_path / "auth.json"
        shared_auth_file.write_text(
            json.dumps(
                {
                    "auth_mode": "chatgpt",
                    "tokens": {
                        "access_token": fake_jwt("acct-other", new_expires_at),
                        "refresh_token": "shared-refresh-token",
                        "account_id": "acct-other",
                    },
                }
            ),
            encoding="utf-8",
        )

        provider = make_provider(
            "openai-browser",
            provider_type=ProviderType.OPENAI,
            base_url="https://api.openai.com/v1",
            auth_type=AuthType.OAUTH,
            api_key=None,
        )
        provider.auth_type = AuthType.OAUTH
        provider.auth_config = AuthConfig(
            auth_type=AuthType.OAUTH,
            oauth_token=OAuthToken(
                access_token=fake_jwt("acct-original", old_expires_at),
                refresh_token="old-refresh-token",
                expires_at=datetime.fromtimestamp(old_expires_at, timezone.utc),
            ),
            metadata={"account_id": "acct-original", "codex_auth_file": str(shared_auth_file)},
        )
        bridge_payloads: list[dict[str, object]] = []
        updates: list[AuthConfig] = []

        class FakeResponsesStream:
            def __init__(self, events):
                self._events = list(events)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._events:
                    raise StopAsyncIteration
                return self._events.pop(0)

        async def fake_aresponses(*args, **kwargs):
            del args, kwargs
            auth_path = os.path.join(
                os.environ["CHATGPT_TOKEN_DIR"],
                os.environ["CHATGPT_AUTH_FILE"],
            )
            with open(auth_path, encoding="utf-8") as handle:
                bridge_payloads.append(json.load(handle))
            return FakeResponsesStream(
                [
                    {"type": "response.output_text.delta", "delta": "hello"},
                    {"type": "response.completed", "response": {"output": []}},
                ]
            )

        async def on_auth_update(updated_auth_config: AuthConfig):
            updates.append(updated_auth_config)

        async def run():
            client = LLMGatewayClient()
            with patch("backend.llm_gateway._load_litellm", return_value=True), patch(
                "backend.llm_gateway.litellm",
                SimpleNamespace(
                    aresponses=fake_aresponses,
                    acompletion=AsyncMock(side_effect=AssertionError("chat.completions should not be used")),
                ),
            ):
                return [
                    chunk
                    async for chunk in client.chat_stream(
                        "openai/gpt-5.4",
                        [{"role": "user", "content": "hi"}],
                        provider_config=provider,
                        on_auth_update=on_auth_update,
                    )
                ]

        chunks = asyncio.run(run())

        assert chunks == ["hello"]
        assert updates == []
        assert bridge_payloads[0]["refresh_token"] == "old-refresh-token"

    def test_openai_oauth_runtime_retries_without_proxy_when_proxy_is_unreachable(self):
        provider = make_provider(
            "openai-browser",
            provider_type=ProviderType.OPENAI,
            base_url="https://api.openai.com/v1",
            auth_type=AuthType.OAUTH,
            api_key=None,
        )
        provider.auth_type = AuthType.OAUTH
        provider.auth_config = AuthConfig(
            auth_type=AuthType.OAUTH,
            oauth_token=OAuthToken(
                access_token="oauth-access-token",
                refresh_token="oauth-refresh-token",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ),
        )

        calls: list[dict[str, object]] = []

        class FakeResponsesStream:
            def __init__(self, events):
                self._events = list(events)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._events:
                    raise StopAsyncIteration
                return self._events.pop(0)

        async def fake_aresponses(*args, **kwargs):
            calls.append(
                {
                    "http_proxy": os.environ.get("HTTP_PROXY"),
                    "https_proxy": os.environ.get("HTTPS_PROXY"),
                    "all_proxy": os.environ.get("ALL_PROXY"),
                    "no_proxy": os.environ.get("NO_PROXY"),
                }
            )
            if len(calls) == 1:
                request = httpx.Request("POST", "https://chatgpt.com/backend-api/codex/responses")
                raise httpx.ProxyError("Cannot connect to host 127.0.0.1:7897", request=request)
            return FakeResponsesStream(
                [
                    {"type": "response.output_text.delta", "delta": "hello"},
                    {"type": "response.completed", "response": {"output": []}},
                ]
            )

        async def run():
            client = LLMGatewayClient()
            with patch("backend.llm_gateway._load_litellm", return_value=True), patch(
                "backend.llm_gateway.litellm",
                SimpleNamespace(
                    aresponses=fake_aresponses,
                    acompletion=AsyncMock(side_effect=AssertionError("chat.completions should not be used")),
                ),
            ):
                return [
                    chunk
                    async for chunk in client.chat_stream(
                        "openai/gpt-5.4",
                        [{"role": "user", "content": "hi"}],
                        provider_config=provider,
                    )
                ]

        with patch.dict(
            os.environ,
            {
                "HTTP_PROXY": "http://127.0.0.1:7897",
                "HTTPS_PROXY": "http://127.0.0.1:7897",
                "ALL_PROXY": "http://127.0.0.1:7897",
                "NO_PROXY": "",
            },
            clear=False,
        ):
            chunks = asyncio.run(run())

        assert chunks == ["hello"]
        assert len(calls) == 2
        assert calls[0]["http_proxy"] == "http://127.0.0.1:7897"
        assert calls[0]["https_proxy"] == "http://127.0.0.1:7897"
        assert calls[1]["http_proxy"] is None
        assert calls[1]["https_proxy"] is None
        assert calls[1]["all_proxy"] is None

    def test_chat_stream_uses_invocation_plan_runtime_kind_for_branch_selection(self):
        provider = make_provider(
            "openai-primary",
            provider_type=ProviderType.OPENAI,
            base_url="https://api.openai.com/v1",
            auth_type=AuthType.API_KEY,
            api_key="sk-test",
        )
        provider.auth_config = AuthConfig(auth_type=AuthType.API_KEY, api_key="sk-test")

        captured: dict[str, object] = {}

        fake_plan = SimpleNamespace(
            provider_key="anthropic",
            model_name="claude-4.7",
            provider_id="openai-primary",
            provider_name="openai-primary",
            provider_type=ProviderType.OPENAI,
            api_format=APIFormat.OPENAI_COMPLETIONS,
            auth_type=AuthType.API_KEY,
            base_url="https://api.openai.com/v1",
            runtime_kind=InvocationRuntimeKind.LITELLM_COMPLETION,
        )

        async def fake_litellm_stream(
            self,
            provider,
            model,
            messages,
            headers,
            auth_config,
            provider_config,
        ):
            captured["provider"] = provider
            captured["model"] = model
            captured["messages"] = messages
            captured["provider_config"] = provider_config
            yield "hello"

        async def run():
            client = LLMGatewayClient()
            with patch("backend.llm_gateway.build_invocation_plan", return_value=fake_plan), patch(
                "backend.llm_gateway._load_litellm",
                return_value=True,
            ), patch.object(
                LLMGatewayClient,
                "_litellm_stream",
                fake_litellm_stream,
            ), patch.object(
                LLMGatewayClient,
                "_provider_httpx_stream",
                AsyncMock(side_effect=AssertionError("httpx branch should not be used")),
            ):
                return [
                    chunk
                    async for chunk in client.chat_stream(
                        "openai/gpt-4.7",
                        [{"role": "user", "content": "hi"}],
                        provider_config=provider,
                    )
                ]

        chunks = asyncio.run(run())

        assert chunks == ["hello"]
        assert captured["provider"] == "anthropic"
        assert captured["model"] == "claude-4.7"
        assert captured["provider_config"] == provider

    def test_openai_oauth_runtime_maps_gpt_53_to_chatgpt_codex_alias(self):
        provider = make_provider(
            "openai-browser",
            provider_type=ProviderType.OPENAI,
            base_url="https://api.openai.com/v1",
            auth_type=AuthType.OAUTH,
            api_key=None,
        )
        provider.auth_type = AuthType.OAUTH
        provider.auth_config = AuthConfig(
            auth_type=AuthType.OAUTH,
            oauth_token=OAuthToken(
                access_token="oauth-access-token",
                refresh_token="oauth-refresh-token",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ),
        )

        captured: dict[str, object] = {}

        class FakeResponsesStream:
            def __init__(self, events):
                self._events = list(events)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._events:
                    raise StopAsyncIteration
                return self._events.pop(0)

        async def fake_aresponses(*args, **kwargs):
            captured["kwargs"] = kwargs
            return FakeResponsesStream(
                [
                    {"type": "response.output_text.delta", "delta": "hello"},
                    {"type": "response.completed", "response": {"output": []}},
                ]
            )

        async def run():
            client = LLMGatewayClient()
            with patch("backend.llm_gateway._load_litellm", return_value=True), patch(
                "backend.llm_gateway.litellm",
                SimpleNamespace(
                    aresponses=fake_aresponses,
                    acompletion=AsyncMock(side_effect=AssertionError("chat.completions should not be used")),
                ),
            ):
                return [
                    chunk
                    async for chunk in client.chat_stream(
                        "openai/gpt-5.3",
                        [{"role": "user", "content": "hi"}],
                        provider_config=provider,
                    )
                ]

        chunks = asyncio.run(run())

        assert chunks == ["hello"]
        assert captured["kwargs"]["custom_llm_provider"] == "chatgpt"
        assert captured["kwargs"]["model"] == "gpt-5.3-codex"

    def test_openai_oauth_runtime_emits_output_text_done_without_waiting_for_completed_response(self):
        provider = make_provider(
            "openai-browser",
            provider_type=ProviderType.OPENAI,
            base_url="https://api.openai.com/v1",
            auth_type=AuthType.OAUTH,
            api_key=None,
        )
        provider.auth_type = AuthType.OAUTH
        provider.auth_config = AuthConfig(
            auth_type=AuthType.OAUTH,
            oauth_token=OAuthToken(
                access_token="oauth-access-token",
                refresh_token="oauth-refresh-token",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ),
        )

        class FakeResponsesStream:
            def __init__(self, events):
                self._events = list(events)

            def __aiter__(self):
                return self

            async def __anext__(self):
                if not self._events:
                    raise StopAsyncIteration
                return self._events.pop(0)

        async def fake_aresponses(*args, **kwargs):
            del args, kwargs
            return FakeResponsesStream(
                [
                    {"type": "response.output_text.done", "text": "hello"},
                    {"type": "response.completed", "response": {"output": []}},
                ]
            )

        async def run():
            client = LLMGatewayClient()
            with patch("backend.llm_gateway._load_litellm", return_value=True), patch(
                "backend.llm_gateway.litellm",
                SimpleNamespace(
                    aresponses=fake_aresponses,
                    acompletion=AsyncMock(side_effect=AssertionError("chat.completions should not be used")),
                ),
            ):
                return [
                    chunk
                    async for chunk in client.chat_stream(
                        "openai/gpt-5.4",
                        [{"role": "user", "content": "hi"}],
                        provider_config=provider,
                    )
                ]

        chunks = asyncio.run(run())

        assert chunks == ["hello"]

    def test_openai_oauth_runtime_surfaces_litellm_detail_on_failure(self):
        provider = make_provider(
            "openai-browser",
            provider_type=ProviderType.OPENAI,
            base_url="https://api.openai.com/v1",
            auth_type=AuthType.OAUTH,
            api_key=None,
        )
        provider.auth_type = AuthType.OAUTH
        provider.auth_config = AuthConfig(
            auth_type=AuthType.OAUTH,
            oauth_token=OAuthToken(
                access_token="oauth-access-token",
                refresh_token="oauth-refresh-token",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ),
        )

        async def fake_aresponses(*args, **kwargs):
            raise RuntimeError("ChatgptException - {\"detail\":\"boom\"}")

        async def run():
            client = LLMGatewayClient()
            with patch("backend.llm_gateway._load_litellm", return_value=True), patch(
                "backend.llm_gateway.litellm",
                SimpleNamespace(
                    aresponses=fake_aresponses,
                    acompletion=AsyncMock(side_effect=AssertionError("chat.completions should not be used")),
                ),
            ):
                return [
                    chunk
                    async for chunk in client.chat_stream(
                        "openai/gpt-5.4",
                        [{"role": "user", "content": "hi"}],
                        provider_config=provider,
                    )
                ]

        with pytest.raises(ProviderUnavailableError) as exc_info:
            asyncio.run(run())

        assert "boom" in str(exc_info.value)

    def test_openai_oauth_runtime_maps_chatgpt_relogin_to_authentication_error(self):
        provider = make_provider(
            "openai-browser",
            provider_type=ProviderType.OPENAI,
            base_url="https://api.openai.com/v1",
            auth_type=AuthType.OAUTH,
            api_key=None,
        )
        provider.auth_type = AuthType.OAUTH
        provider.auth_config = AuthConfig(
            auth_type=AuthType.OAUTH,
            oauth_token=OAuthToken(
                access_token="oauth-access-token",
                refresh_token="oauth-refresh-token",
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            ),
        )

        async def fake_aresponses(*args, **kwargs):
            raise RuntimeError(
                "Interactive ChatGPT device-code login is disabled in backend; re-login required"
            )

        async def run():
            client = LLMGatewayClient()
            with patch("backend.llm_gateway._load_litellm", return_value=True), patch(
                "backend.llm_gateway.litellm",
                SimpleNamespace(
                    aresponses=fake_aresponses,
                    acompletion=AsyncMock(side_effect=AssertionError("chat.completions should not be used")),
                ),
            ):
                return [
                    chunk
                    async for chunk in client.chat_stream(
                        "openai/gpt-5.4",
                        [{"role": "user", "content": "hi"}],
                        provider_config=provider,
                    )
                ]

        with pytest.raises(AuthenticationError) as exc_info:
            asyncio.run(run())

        assert exc_info.value.provider == "openai-browser"
        assert "re-login" in str(exc_info.value)

    def test_detects_chatgpt_refresh_token_authentication_errors(self):
        exc = RuntimeError(
            "litellm.AuthenticationError: Polling failed after refresh token failed: 401 Unauthorized; re-login required"
        )
        assert _is_chatgpt_authentication_error(exc) is True

    def test_stream_openai_compatible_retries_direct_on_connect_error(self):
        endpoint = "https://api.openai.com/v1/chat/completions"
        created_clients = []
        stream_timeouts = []

        class FakeResponse:
            status_code = 200
            text = ""

            def raise_for_status(self):
                return None

            async def aiter_lines(self):
                yield 'data: {"choices":[{"delta":{"content":"hello"}}]}'
                yield "data: [DONE]"

        class FakeStreamContext:
            def __init__(self, outcome):
                self.outcome = outcome

            async def __aenter__(self):
                if isinstance(self.outcome, Exception):
                    raise self.outcome
                return self.outcome

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakeClient:
            def __init__(self, outcome, kwargs):
                self.outcome = outcome
                self.kwargs = kwargs

            async def __aenter__(self):
                created_clients.append(self.kwargs)
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def stream(self, *args, **kwargs):
                stream_timeouts.append(kwargs.get("timeout"))
                return FakeStreamContext(self.outcome)

        request = httpx.Request("POST", endpoint)
        connect_error = httpx.ConnectError("All connection attempts failed", request=request)
        outcomes = [connect_error, FakeResponse()]

        def fake_async_client(*args, **kwargs):
            return FakeClient(outcomes.pop(0), kwargs)

        async def run():
            client = LLMGatewayClient()
            with patch("httpx.AsyncClient", side_effect=fake_async_client):
                return [
                    chunk
                    async for chunk in client._stream_openai_compatible(
                        endpoint=endpoint,
                        model="gpt-5.4",
                        messages=[{"role": "user", "content": "hi"}],
                        headers={"Authorization": "Bearer token"},
                        provider_name="openai",
                    )
                ]

        chunks = asyncio.run(run())

        assert chunks == ["hello"]
        assert len(created_clients) == 2
        assert created_clients[0] == {}
        assert created_clients[1]["trust_env"] is False
        assert stream_timeouts[-1].read == 300

    def test_stream_openai_compatible_retries_direct_on_read_timeout_before_output(self):
        endpoint = "https://api.openai.com/v1/chat/completions"
        created_clients = []

        class FakeResponse:
            status_code = 200

            async def aiter_lines(self):
                yield 'data: {"choices":[{"delta":{"content":"after retry"}}]}'
                yield "data: [DONE]"

        class FakeStreamContext:
            def __init__(self, outcome):
                self.outcome = outcome

            async def __aenter__(self):
                if isinstance(self.outcome, Exception):
                    raise self.outcome
                return self.outcome

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakeClient:
            def __init__(self, outcome, kwargs):
                self.outcome = outcome
                self.kwargs = kwargs

            async def __aenter__(self):
                created_clients.append(self.kwargs)
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def stream(self, *args, **kwargs):
                return FakeStreamContext(self.outcome)

        request = httpx.Request("POST", endpoint)
        outcomes = [httpx.ReadTimeout("idle stream", request=request), FakeResponse()]

        def fake_async_client(*args, **kwargs):
            return FakeClient(outcomes.pop(0), kwargs)

        async def run():
            client = LLMGatewayClient()
            with patch("httpx.AsyncClient", side_effect=fake_async_client):
                return [
                    chunk
                    async for chunk in client._stream_openai_compatible(
                        endpoint=endpoint,
                        model="gpt-5.4",
                        messages=[{"role": "user", "content": "hi"}],
                        headers={"Authorization": "Bearer token"},
                        provider_name="openai",
                    )
                ]

        chunks = asyncio.run(run())

        assert chunks == ["after retry"]
        assert len(created_clients) == 2
        assert created_clients[1]["trust_env"] is False

    def test_stream_openai_compatible_read_timeout_after_output_is_not_retried(self):
        endpoint = "https://api.openai.com/v1/chat/completions"
        created_clients = []

        class FakeResponse:
            status_code = 200

            async def aiter_lines(self):
                yield 'data: {"choices":[{"delta":{"content":"partial"}}]}'
                request = httpx.Request("POST", endpoint)
                raise httpx.ReadTimeout("idle stream", request=request)

        class FakeStreamContext:
            async def __aenter__(self):
                return FakeResponse()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakeClient:
            def __init__(self, *args, **kwargs):
                self.kwargs = kwargs

            async def __aenter__(self):
                created_clients.append(self.kwargs)
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def stream(self, *args, **kwargs):
                return FakeStreamContext()

        async def run():
            client = LLMGatewayClient()
            with patch("httpx.AsyncClient", side_effect=FakeClient):
                return [
                    chunk
                    async for chunk in client._stream_openai_compatible(
                        endpoint=endpoint,
                        model="gpt-5.4",
                        messages=[{"role": "user", "content": "hi"}],
                        headers={"Authorization": "Bearer token"},
                        provider_name="openai",
                    )
                ]

        with pytest.raises(ProviderUnavailableError) as exc_info:
            asyncio.run(run())

        assert len(created_clients) == 1
        assert "ReadTimeout" in str(exc_info.value)
        assert "MMD_LLM_HTTPX_READ_TIMEOUT_SECONDS" in str(exc_info.value)

    def test_llm_httpx_timeout_can_be_configured_with_environment(self, monkeypatch):
        monkeypatch.setenv("MMD_LLM_HTTPX_READ_TIMEOUT_SECONDS", "123")

        timeout = _llm_httpx_timeout()

        assert timeout.connect == 30
        assert timeout.read == 123
        assert timeout.write == 60
        assert timeout.pool == 30

    def test_stream_openai_compatible_skips_empty_choices_payloads(self):
        endpoint = "https://api.openai.com/v1/chat/completions"

        class FakeResponse:
            status_code = 200
            text = ""

            def raise_for_status(self):
                return None

            async def aiter_lines(self):
                yield 'data: {"choices":[]}'
                yield 'data: {"choices":[{"delta":{"content":"hello"}}]}'
                yield "data: [DONE]"

        class FakeStreamContext:
            async def __aenter__(self):
                return FakeResponse()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def stream(self, *args, **kwargs):
                return FakeStreamContext()

        async def run():
            client = LLMGatewayClient()
            with patch("httpx.AsyncClient", return_value=FakeClient()):
                return [
                    chunk
                    async for chunk in client._stream_openai_compatible(
                        endpoint=endpoint,
                        model="gpt-5.4",
                        messages=[{"role": "user", "content": "hi"}],
                        headers={"Authorization": "Bearer token"},
                        provider_name="openai",
                    )
                ]

        chunks = asyncio.run(run())

        assert chunks == ["hello"]

    def test_stream_openai_compatible_surfaces_http_status_details(self):
        endpoint = "https://api.openai.com/v1/chat/completions"
        error_body = '{"error":{"message":"Your account is not active","code":"billing_not_active"}}'

        class FakeResponse:
            status_code = 429

            async def aread(self):
                return error_body.encode("utf-8")

            async def aiter_lines(self):
                if False:
                    yield ""

        class FakeStreamContext:
            async def __aenter__(self):
                return FakeResponse()

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakeClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            def stream(self, *args, **kwargs):
                return FakeStreamContext()

        async def run():
            client = LLMGatewayClient()
            with patch("httpx.AsyncClient", return_value=FakeClient()):
                return [
                    chunk
                    async for chunk in client._stream_openai_compatible(
                        endpoint=endpoint,
                        model="gpt-5.4",
                        messages=[{"role": "user", "content": "hi"}],
                        headers={"Authorization": "Bearer token"},
                        provider_name="openai",
                    )
                ]

        with pytest.raises(ProviderUnavailableError) as exc_info:
            asyncio.run(run())

        assert "429" in str(exc_info.value)
        assert "billing_not_active" in str(exc_info.value)


# ===========================================================================
# 10. validate_model_ref 属性测试（Property 12）
# ===========================================================================

class TestModelRefPropertyTest:
    """
    Property 12：Model_Ref 格式验证
    Validates: Requirements 26.5, 26.6
    """

    def test_valid_refs_accepted(self):
        """所有符合 provider/model 格式的字符串应被接受"""
        valid_cases = [
            "openai/gpt-4o",
            "anthropic/claude-3-5-sonnet",
            "ollama/llama3.3",
            "google/gemini-pro",
            "groq/llama-3.1-70b",
            "mistral/mistral-large",
            "xai/grok-2",
            "custom/my-model-v1",
        ]
        for ref in valid_cases:
            provider, model = validate_model_ref(ref)
            assert provider
            assert model
            assert "/" not in provider
            assert "/" not in model

    def test_invalid_refs_rejected(self):
        """不符合格式的字符串应被拒绝"""
        invalid_cases = [
            "",
            "no-slash",
            "/",
            "/model",
            "provider/",
            "a/b/c",
            "   ",
            "  /  ",
        ]
        for ref in invalid_cases:
            with pytest.raises(ValidationError):
                validate_model_ref(ref)

    def test_provider_model_reconstruction(self):
        """provider + '/' + model 应等于原始 model_ref（去除空格后）"""
        refs = [
            "openai/gpt-4o",
            "anthropic/claude-opus-4",
            "ollama/llama3:latest",
        ]
        for ref in refs:
            provider, model = validate_model_ref(ref)
            assert f"{provider}/{model}" == ref.strip()
