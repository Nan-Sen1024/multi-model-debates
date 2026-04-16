"""
单元测试：LLM 网关层
覆盖：validate_model_ref、AuthManager.get_headers、ProviderRouter 轮询/fallback、GovCloud 检测
需求：26.1-26.7、27.1-27.14、28.1-28.5、29.1-29.5、30.1-30.5
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
    validate_model_ref,
)
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


# ===========================================================================
# 1. validate_model_ref 格式校验（10.4）
# ===========================================================================

class TestValidateModelRef:
    """需求：26.5、26.6"""

    def test_valid_format_returns_tuple(self):
        provider, model = validate_model_ref("openai/gpt-4o")
        assert provider == "openai"
        assert model == "gpt-4o"

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
# 9. validate_model_ref 属性测试（Property 12）
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
