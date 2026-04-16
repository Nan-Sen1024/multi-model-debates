"""
LLM gateway abstractions and provider/auth helpers.
"""
from __future__ import annotations

import base64
import json
import logging
import subprocess
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Tuple

from .enums import APIFormat, AuthType, ProviderType
from .exceptions import (
    AuthenticationError,
    ProviderUnavailableError,
    ValidationError,
)
from .models import AuthConfig, OAuthToken, ProviderConfig

logger = logging.getLogger(__name__)

litellm = None  # type: ignore
_LITELLM_AVAILABLE = False

try:
    import httpx  # type: ignore

    _HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    httpx = None  # type: ignore
    _HTTPX_AVAILABLE = False


def _load_litellm() -> bool:
    global litellm, _LITELLM_AVAILABLE

    if litellm is not None:
        return _LITELLM_AVAILABLE

    try:
        import litellm as litellm_module  # type: ignore

        litellm = litellm_module  # type: ignore
        _LITELLM_AVAILABLE = True
    except Exception as exc:  # pragma: no cover - depends on installed package state
        litellm = None  # type: ignore
        _LITELLM_AVAILABLE = False
        logger.warning(
            "litellm import failed, falling back to httpx mode: %s: %s",
            type(exc).__name__,
            exc,
        )

    return _LITELLM_AVAILABLE


def validate_model_ref(model_ref: str) -> Tuple[str, str]:
    if not model_ref or not isinstance(model_ref, str):
        raise ValidationError(
            "Model_Ref cannot be empty; expected provider/model",
            field="model_ref",
        )

    parts = model_ref.split("/")
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        raise ValidationError(
            f"Invalid Model_Ref '{model_ref}'; expected provider/model",
            field="model_ref",
        )

    return parts[0].strip(), parts[1].strip()


async def check_provider_connectivity(provider_config: ProviderConfig) -> bool:
    gateway = LLMGatewayClient()
    return await gateway.health_check(provider_config)


async def discover_ollama_models(base_url: str = "http://127.0.0.1:11434") -> List[str]:
    gateway = LLMGatewayClient()
    return await gateway.get_local_models(base_url)


def _obfuscate(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("utf-8")


def _deobfuscate(value: str) -> str:
    try:
        return base64.b64decode(value.encode("utf-8")).decode("utf-8")
    except Exception:
        return value


def _normalize_auth_type(value: Any) -> AuthType:
    if isinstance(value, AuthType):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("AuthType."):
            raw = raw.split(".", 1)[1]
        return AuthType(raw.lower())
    return AuthType.IAM


def serialize_auth_config(auth_config: AuthConfig) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"auth_type": auth_config.auth_type.value}
    if auth_config.api_key:
        payload["api_key"] = AuthManager.store_api_key(auth_config.api_key)
    if auth_config.bearer_token:
        payload["bearer_token"] = auth_config.bearer_token
    if auth_config.helper_script:
        payload["helper_script"] = auth_config.helper_script
    if auth_config.oauth_token:
        payload["oauth_token"] = {
            "access_token": auth_config.oauth_token.access_token,
            "refresh_token": auth_config.oauth_token.refresh_token,
            "expires_at": auth_config.oauth_token.expires_at.isoformat()
            if auth_config.oauth_token.expires_at
            else None,
            "token_type": auth_config.oauth_token.token_type,
        }
    if auth_config.metadata:
        payload["metadata"] = auth_config.metadata
    return payload


def deserialize_auth_config(auth_type: Any, payload: Optional[Any]) -> AuthConfig:
    normalized_auth_type = _normalize_auth_type(auth_type)
    raw_payload: Dict[str, Any] = {}

    if isinstance(payload, str) and payload.strip():
        try:
            loaded = json.loads(payload)
        except json.JSONDecodeError:
            loaded = {}
        if isinstance(loaded, dict):
            raw_payload = loaded
    elif isinstance(payload, dict):
        raw_payload = payload

    token = None
    raw_token = raw_payload.get("oauth_token")
    if isinstance(raw_token, dict):
        expires_at = raw_token.get("expires_at")
        parsed_expires_at = None
        if isinstance(expires_at, str) and expires_at:
            try:
                from datetime import datetime

                parsed_expires_at = datetime.fromisoformat(expires_at)
            except ValueError:
                parsed_expires_at = None
        token = OAuthToken(
            access_token=raw_token.get("access_token", ""),
            refresh_token=raw_token.get("refresh_token"),
            expires_at=parsed_expires_at,
            token_type=raw_token.get("token_type", "Bearer"),
        )

    return AuthConfig(
        auth_type=normalized_auth_type,
        api_key=raw_payload.get("api_key"),
        bearer_token=raw_payload.get("bearer_token"),
        helper_script=raw_payload.get("helper_script"),
        oauth_token=token,
    )


@dataclass
class SSOOIDCClient:
    sso_start_url: str
    sso_region: str

    def _is_govcloud(self) -> bool:
        return "us-gov-home" in self.sso_start_url

    def _oidc_endpoint(self) -> str:
        suffix = "amazonaws-us-gov.com" if self._is_govcloud() else "amazonaws.com"
        return f"https://oidc.{self.sso_region}.{suffix}"

    def _sso_endpoint(self) -> str:
        suffix = "amazonaws-us-gov.com" if self._is_govcloud() else "amazonaws.com"
        return f"https://portal.sso.{self.sso_region}.{suffix}"

    def register_client(self) -> Dict[str, Any]:
        if not _HTTPX_AVAILABLE:
            raise ProviderUnavailableError("httpx is required for SSO OIDC registration")
        with httpx.Client() as client:
            response = client.post(
                f"{self._oidc_endpoint()}/client/register",
                json={
                    "clientName": "multi-model-debate",
                    "clientType": "public",
                    "scopes": ["sso:account:access"],
                },
                timeout=10,
            )
            response.raise_for_status()
            return response.json()

    def start_device_authorization(self, client_id: str, client_secret: str) -> Dict[str, Any]:
        if not _HTTPX_AVAILABLE:
            raise ProviderUnavailableError("httpx is required for device authorization")
        with httpx.Client() as client:
            response = client.post(
                f"{self._oidc_endpoint()}/device_authorization",
                json={
                    "clientId": client_id,
                    "clientSecret": client_secret,
                    "startUrl": self.sso_start_url,
                },
                timeout=10,
            )
            response.raise_for_status()
            return response.json()

    def create_token(
        self,
        client_id: str,
        client_secret: str,
        device_code: str,
        interval: int = 5,
        timeout: int = 300,
    ) -> Dict[str, Any]:
        if not _HTTPX_AVAILABLE:
            raise ProviderUnavailableError("httpx is required to create SSO tokens")
        deadline = time.time() + timeout
        with httpx.Client() as client:
            while time.time() < deadline:
                response = client.post(
                    f"{self._oidc_endpoint()}/token",
                    json={
                        "clientId": client_id,
                        "clientSecret": client_secret,
                        "grantType": "urn:ietf:params:oauth:grant-type:device_code",
                        "deviceCode": device_code,
                    },
                    timeout=10,
                )
                if response.status_code == 200:
                    return response.json()
                body = response.json() if response.content else {}
                error = body.get("error")
                if error == "authorization_pending":
                    time.sleep(interval)
                    continue
                if error == "slow_down":
                    interval += 5
                    time.sleep(interval)
                    continue
                raise AuthenticationError(f"Device authorization failed: {error}", provider="aws_iam")
        raise AuthenticationError("Device authorization timed out", provider="aws_iam")

    def get_role_credentials(self, access_token: str, account_id: str, role_name: str) -> Dict[str, Any]:
        if not _HTTPX_AVAILABLE:
            raise ProviderUnavailableError("httpx is required to fetch role credentials")
        with httpx.Client() as client:
            response = client.get(
                f"{self._sso_endpoint()}/federation/credentials",
                params={"account_id": account_id, "role_name": role_name},
                headers={"x-amz-sso_bearer_token": access_token},
                timeout=10,
            )
            response.raise_for_status()
            return response.json().get("roleCredentials", {})

    def list_accounts(self, access_token: str) -> List[Dict[str, Any]]:
        if not _HTTPX_AVAILABLE:
            raise ProviderUnavailableError("httpx is required to list SSO accounts")
        with httpx.Client() as client:
            response = client.get(
                f"{self._sso_endpoint()}/assignment/accounts",
                headers={"x-amz-sso_bearer_token": access_token},
                timeout=10,
            )
            response.raise_for_status()
            return response.json().get("accountList", [])

    def list_account_roles(self, access_token: str, account_id: str) -> List[Dict[str, Any]]:
        if not _HTTPX_AVAILABLE:
            raise ProviderUnavailableError("httpx is required to list SSO roles")
        with httpx.Client() as client:
            response = client.get(
                f"{self._sso_endpoint()}/assignment/roles",
                params={"account_id": account_id},
                headers={"x-amz-sso_bearer_token": access_token},
                timeout=10,
            )
            response.raise_for_status()
            return response.json().get("roleList", [])


class AuthManager:
    _token_cache: Dict[str, Dict[str, Any]] = {}

    def get_headers(self, auth_config: AuthConfig) -> Dict[str, str]:
        auth_type = auth_config.auth_type
        try:
            if auth_type == AuthType.API_KEY:
                return self._headers_api_key(auth_config)
            if auth_type == AuthType.BEARER:
                return self._headers_bearer(auth_config)
            if auth_type == AuthType.OAUTH:
                return self._headers_oauth(auth_config)
            if auth_type == AuthType.IAM:
                return {}
            if auth_type == AuthType.ADC:
                return {}
            if auth_type == AuthType.HELPER:
                return self._headers_helper(auth_config)
            raise AuthenticationError(f"Unsupported auth type: {auth_type}")
        except AuthenticationError:
            raise
        except Exception as exc:
            raise AuthenticationError(
                f"Failed to process auth config for {auth_type}"
            ) from exc

    def _headers_api_key(self, auth_config: AuthConfig) -> Dict[str, str]:
        if not auth_config.api_key:
            raise AuthenticationError("API Key is not configured")
        return {"Authorization": f"Bearer {_deobfuscate(auth_config.api_key)}"}

    def _headers_bearer(self, auth_config: AuthConfig) -> Dict[str, str]:
        if not auth_config.bearer_token:
            raise AuthenticationError("Bearer token is not configured")
        return {"Authorization": f"Bearer {auth_config.bearer_token}"}

    def _headers_oauth(self, auth_config: AuthConfig) -> Dict[str, str]:
        token = auth_config.oauth_token
        if token is None:
            raise AuthenticationError("OAuth token is not configured")
        if self._is_token_expired(token):
            token = self._refresh_oauth_token(token)
        return {"Authorization": f"Bearer {token.access_token}"}

    def _headers_helper(self, auth_config: AuthConfig) -> Dict[str, str]:
        if not auth_config.helper_script:
            raise AuthenticationError("Helper script is not configured")
        try:
            result = subprocess.run(
                auth_config.helper_script,
                shell=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except subprocess.TimeoutExpired as exc:
            raise AuthenticationError("Helper script timed out") from exc

        if result.returncode != 0:
            raise AuthenticationError("Helper script failed")

        key = result.stdout.strip()
        if not key:
            raise AuthenticationError("Helper script returned an empty API key")
        return {"Authorization": f"Bearer {key}"}

    def _is_token_expired(self, token: OAuthToken) -> bool:
        if token.expires_at is None:
            return False
        from datetime import datetime, timezone

        expires_at = token.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= expires_at

    def _refresh_oauth_token(self, token: OAuthToken) -> OAuthToken:
        if not token.refresh_token:
            raise AuthenticationError("OAuth token expired and no refresh token is available")
        raise AuthenticationError("OAuth token expired; provider-specific refresh is not implemented")

    @classmethod
    def store_api_key(cls, api_key: str) -> str:
        return _obfuscate(api_key)

    @classmethod
    def load_api_key(cls, stored_key: str) -> str:
        return _deobfuscate(stored_key)


class ProviderRouter:
    def __init__(self) -> None:
        self._round_robin_counters: Dict[str, int] = {}

    def route(self, model_ref: str, providers: List[ProviderConfig]) -> ProviderConfig:
        if not providers:
            raise ProviderUnavailableError(f"No providers available for {model_ref}")

        active = [provider for provider in providers if provider.is_active]
        if not active:
            raise ProviderUnavailableError(f"All providers are inactive for {model_ref}")

        counter = self._round_robin_counters.get(model_ref, 0)
        selected = active[counter % len(active)]
        self._round_robin_counters[model_ref] = counter + 1
        return selected

    async def with_fallback(
        self,
        model_ref: str,
        providers: List[ProviderConfig],
        call_fn: Callable[[ProviderConfig], Any],
    ) -> Any:
        if not providers:
            raise ProviderUnavailableError(f"No providers available for {model_ref}")

        last_error: Optional[Exception] = None
        for provider in providers:
            try:
                return await call_fn(provider)
            except (ProviderUnavailableError, AuthenticationError) as exc:
                last_error = exc
                logger.warning("provider %s failed, trying fallback", provider.name)
            except Exception as exc:
                last_error = exc
                logger.warning("provider %s raised unexpected error, trying fallback", provider.name)

        raise ProviderUnavailableError(
            f"All providers failed for {model_ref}. Last error: {last_error}",
            provider=model_ref,
        )


class LLMGatewayClient:
    def __init__(self) -> None:
        self._auth_manager = AuthManager()

    async def chat_stream(
        self,
        model_ref: str,
        messages: List[Dict[str, str]],
        auth_config: Optional[AuthConfig] = None,
        provider_config: Optional[ProviderConfig] = None,
    ) -> AsyncIterator[str]:
        provider, model = validate_model_ref(model_ref)
        effective_auth = provider_config.auth_config if provider_config else (auth_config or AuthConfig(auth_type=AuthType.IAM))
        headers = self._auth_manager.get_headers(effective_auth)

        if provider_config is not None:
            if self._should_use_httpx(provider_config) or not _load_litellm():
                async for chunk in self._provider_httpx_stream(
                    provider=provider,
                    model=model,
                    messages=messages,
                    headers=headers,
                    auth_config=effective_auth,
                    provider_config=provider_config,
                ):
                    yield chunk
                return

            async for chunk in self._litellm_stream(
                provider,
                model,
                messages,
                headers,
                effective_auth,
                provider_config=provider_config,
            ):
                yield chunk
            return

        if _load_litellm():
            async for chunk in self._litellm_stream(provider, model, messages, headers, effective_auth):
                yield chunk
            return

        async for chunk in self._httpx_stream(provider, model, messages, headers, effective_auth):
            yield chunk

    async def _litellm_stream(
        self,
        provider: str,
        model: str,
        messages: List[Dict[str, str]],
        headers: Dict[str, str],
        auth_config: AuthConfig,
        provider_config: Optional[ProviderConfig] = None,
    ) -> AsyncIterator[str]:
        effective_provider = provider_config.provider_type.value if provider_config else provider
        if not _load_litellm():
            raise ProviderUnavailableError("LiteLLM is unavailable", provider=effective_provider)

        kwargs: Dict[str, Any] = {
            "model": f"{effective_provider}/{model}",
            "messages": messages,
            "stream": True,
        }

        if provider_config and provider_config.base_url:
            kwargs["api_base"] = provider_config.base_url
        if auth_config.auth_type == AuthType.API_KEY and auth_config.api_key:
            kwargs["api_key"] = _deobfuscate(auth_config.api_key)
        elif "Authorization" in headers:
            kwargs["api_key"] = headers["Authorization"].replace("Bearer ", "", 1)

        try:
            response = await litellm.acompletion(**kwargs)  # type: ignore[union-attr]
            async for chunk in response:
                delta = chunk.choices[0].delta if getattr(chunk, "choices", None) else None
                content = getattr(delta, "content", None) if delta else None
                if content:
                    yield content
        except Exception as exc:
            raise ProviderUnavailableError(
                f"LiteLLM invocation failed: {type(exc).__name__}",
                provider=effective_provider,
            ) from exc

    async def _httpx_stream(
        self,
        provider: str,
        model: str,
        messages: List[Dict[str, str]],
        headers: Dict[str, str],
        auth_config: AuthConfig,
    ) -> AsyncIterator[str]:
        if not _HTTPX_AVAILABLE:
            raise ProviderUnavailableError("Neither litellm nor httpx is available", provider=provider)

        endpoint = f"{self._get_base_url(provider, auth_config).rstrip('/')}/chat/completions"
        async for chunk in self._stream_openai_compatible(
            endpoint=endpoint,
            model=model,
            messages=messages,
            headers=headers,
            provider_name=provider,
        ):
            yield chunk

    async def _provider_httpx_stream(
        self,
        provider: str,
        model: str,
        messages: List[Dict[str, str]],
        headers: Dict[str, str],
        auth_config: AuthConfig,
        provider_config: ProviderConfig,
    ) -> AsyncIterator[str]:
        if not _HTTPX_AVAILABLE:
            raise ProviderUnavailableError(
                "httpx is required for configured provider streaming",
                provider=provider_config.name,
            )

        base_url = provider_config.base_url or self._get_default_base_url(provider_config.provider_type)
        if provider_config.api_format == APIFormat.ANTHROPIC_MESSAGES:
            endpoint = f"{base_url.rstrip('/')}/messages"
            async for chunk in self._stream_anthropic_compatible(
                endpoint=endpoint,
                model=model,
                messages=messages,
                headers=headers,
                auth_config=auth_config,
                provider_name=provider_config.name,
            ):
                yield chunk
            return

        endpoint = f"{base_url.rstrip('/')}/chat/completions"
        async for chunk in self._stream_openai_compatible(
            endpoint=endpoint,
            model=model,
            messages=messages,
            headers=headers,
            provider_name=provider_config.name or provider,
        ):
            yield chunk

    async def _stream_openai_compatible(
        self,
        endpoint: str,
        model: str,
        messages: List[Dict[str, str]],
        headers: Dict[str, str],
        provider_name: str,
    ) -> AsyncIterator[str]:
        payload = {"model": model, "messages": messages, "stream": True}

        async with httpx.AsyncClient() as client:  # type: ignore[union-attr]
            try:
                async with client.stream(
                    "POST",
                    endpoint,
                    json=payload,
                    headers=headers,
                    timeout=60,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:]
                        if data.strip() == "[DONE]":
                            break
                        try:
                            payload = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        delta = payload.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            yield content
            except Exception as exc:
                raise ProviderUnavailableError(
                    f"httpx invocation failed: {type(exc).__name__}",
                    provider=provider_name,
                ) from exc

    async def _stream_anthropic_compatible(
        self,
        endpoint: str,
        model: str,
        messages: List[Dict[str, str]],
        headers: Dict[str, str],
        auth_config: AuthConfig,
        provider_name: str,
    ) -> AsyncIterator[str]:
        payload = self._build_anthropic_payload(model, messages)
        anthropic_headers = self._build_anthropic_headers(headers, auth_config)

        async with httpx.AsyncClient() as client:  # type: ignore[union-attr]
            try:
                async with client.stream(
                    "POST",
                    endpoint,
                    json=payload,
                    headers=anthropic_headers,
                    timeout=60,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data = line[6:]
                        if data.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        delta = chunk.get("delta", {})
                        content = delta.get("text")
                        if not content and chunk.get("type") == "content_block_start":
                            content = chunk.get("content_block", {}).get("text")
                        if content:
                            yield content
            except Exception as exc:
                raise ProviderUnavailableError(
                    f"anthropic httpx invocation failed: {type(exc).__name__}",
                    provider=provider_name,
                ) from exc

    def _should_use_httpx(self, provider_config: ProviderConfig) -> bool:
        return provider_config.provider_type in {
            ProviderType.CUSTOM,
            ProviderType.GATEWAY,
            ProviderType.OLLAMA,
            ProviderType.LM_STUDIO,
            ProviderType.VLLM,
            ProviderType.LITELLM,
        } or provider_config.base_url is not None or provider_config.api_format == APIFormat.ANTHROPIC_MESSAGES

    def _build_anthropic_headers(
        self,
        headers: Dict[str, str],
        auth_config: AuthConfig,
    ) -> Dict[str, str]:
        anthropic_headers = {
            "content-type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if auth_config.auth_type == AuthType.API_KEY and auth_config.api_key:
            anthropic_headers["x-api-key"] = _deobfuscate(auth_config.api_key)
        else:
            anthropic_headers.update(headers)
        return anthropic_headers

    def _build_anthropic_payload(
        self,
        model: str,
        messages: List[Dict[str, str]],
    ) -> Dict[str, Any]:
        system_parts = [
            message.get("content", "")
            for message in messages
            if message.get("role") == "system" and message.get("content")
        ]
        payload_messages = []
        for message in messages:
            role = message.get("role", "user")
            if role == "system":
                continue
            if role not in {"user", "assistant"}:
                role = "user"
            payload_messages.append(
                {
                    "role": role,
                    "content": message.get("content", ""),
                }
            )
        if not payload_messages:
            payload_messages.append({"role": "user", "content": ""})

        payload: Dict[str, Any] = {
            "model": model,
            "messages": payload_messages,
            "max_tokens": 1024,
            "stream": True,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        return payload

    async def health_check(self, provider_config: ProviderConfig) -> bool:
        if not _HTTPX_AVAILABLE:
            logger.warning("httpx unavailable, skipping provider health check")
            return True

        base_url = provider_config.base_url or self._get_default_base_url(provider_config.provider_type)
        if provider_config.provider_type == ProviderType.OLLAMA:
            check_url = base_url.rstrip("/").replace("/v1", "") + "/api/tags"
        else:
            check_url = base_url.rstrip("/") + "/models"

        try:
            async with httpx.AsyncClient() as client:  # type: ignore[union-attr]
                response = await client.get(check_url, timeout=5)
            return response.status_code < 500
        except Exception as exc:
            logger.debug("provider health check failed for %s: %s", provider_config.name, exc)
            return False

    async def get_local_models(self, base_url: str) -> List[str]:
        if not _HTTPX_AVAILABLE:
            logger.warning("httpx unavailable, cannot discover local models")
            return []

        normalized = base_url.rstrip("/").replace("/v1", "")
        endpoint = f"{normalized}/api/tags"
        try:
            async with httpx.AsyncClient() as client:  # type: ignore[union-attr]
                response = await client.get(endpoint, timeout=5)
            if response.status_code != 200:
                return []
            data = response.json()
            return [item["name"] for item in data.get("models", []) if item.get("name")]
        except Exception as exc:
            logger.debug("local model discovery failed: %s", exc)
            return []

    def _get_base_url(self, provider: str, auth_config: AuthConfig) -> str:
        del auth_config
        provider_map = {
            "openai": self._get_default_base_url(ProviderType.OPENAI),
            "anthropic": self._get_default_base_url(ProviderType.ANTHROPIC),
            "google": self._get_default_base_url(ProviderType.GOOGLE),
            "groq": self._get_default_base_url(ProviderType.GROQ),
            "mistral": self._get_default_base_url(ProviderType.MISTRAL),
            "xai": self._get_default_base_url(ProviderType.XAI),
            "openrouter": self._get_default_base_url(ProviderType.OPENROUTER),
            "ollama": self._get_default_base_url(ProviderType.OLLAMA),
        }
        return provider_map.get(provider, self._get_default_base_url(ProviderType.OPENAI))

    def _get_default_base_url(self, provider_type: ProviderType) -> str:
        defaults = {
            ProviderType.OPENAI: "https://api.openai.com/v1",
            ProviderType.ANTHROPIC: "https://api.anthropic.com/v1",
            ProviderType.GOOGLE: "https://generativelanguage.googleapis.com/v1beta",
            ProviderType.GROQ: "https://api.groq.com/openai/v1",
            ProviderType.MISTRAL: "https://api.mistral.ai/v1",
            ProviderType.XAI: "https://api.x.ai/v1",
            ProviderType.OPENROUTER: "https://openrouter.ai/api/v1",
            ProviderType.OLLAMA: "http://127.0.0.1:11434/v1",
            ProviderType.LM_STUDIO: "http://127.0.0.1:1234/v1",
            ProviderType.VLLM: "http://127.0.0.1:8001/v1",
            ProviderType.LITELLM: "http://127.0.0.1:4000/v1",
            ProviderType.GATEWAY: "http://127.0.0.1:4000/v1",
            ProviderType.CUSTOM: "https://api.openai.com/v1",
        }
        return defaults.get(provider_type, "https://api.openai.com/v1")
