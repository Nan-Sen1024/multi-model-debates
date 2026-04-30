"""
LLM gateway abstractions and provider/auth helpers.
"""
from __future__ import annotations

import base64
import contextlib
import inspect
import json
import logging
import os
import subprocess
import time
import tempfile
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Dict, Iterable, List, Optional, Tuple

from .enums import APIFormat, AuthType, ProviderType
from .exceptions import (
    AuthenticationError,
    ProviderUnavailableError,
    ValidationError,
)
from .invocation_plan import InvocationRuntimeKind, build_invocation_plan
from .models import AuthConfig, OAuthToken, ProviderConfig
from .security import decrypt_value, encrypt_value

logger = logging.getLogger(__name__)

AuthUpdateCallback = Callable[[AuthConfig], Any]

litellm = None  # type: ignore
_LITELLM_AVAILABLE = False
_LLM_HTTPX_CONNECT_TIMEOUT_ENV = "MMD_LLM_HTTPX_CONNECT_TIMEOUT_SECONDS"
_LLM_HTTPX_READ_TIMEOUT_ENV = "MMD_LLM_HTTPX_READ_TIMEOUT_SECONDS"
_LLM_HTTPX_WRITE_TIMEOUT_ENV = "MMD_LLM_HTTPX_WRITE_TIMEOUT_SECONDS"
_LLM_HTTPX_POOL_TIMEOUT_ENV = "MMD_LLM_HTTPX_POOL_TIMEOUT_SECONDS"
_DEFAULT_LLM_HTTPX_CONNECT_TIMEOUT_SECONDS = 30.0
_DEFAULT_LLM_HTTPX_READ_TIMEOUT_SECONDS = 300.0
_DEFAULT_LLM_HTTPX_WRITE_TIMEOUT_SECONDS = 60.0
_DEFAULT_LLM_HTTPX_POOL_TIMEOUT_SECONDS = 30.0

try:
    import httpx  # type: ignore

    _HTTPX_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    httpx = None  # type: ignore
    _HTTPX_AVAILABLE = False


def _read_positive_float_env(name: str, default: float) -> float:
    raw_value = os.getenv(name)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        parsed = float(raw_value)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using %.1fs", name, raw_value, default)
        return default
    if parsed <= 0:
        logger.warning("Ignoring non-positive %s=%r; using %.1fs", name, raw_value, default)
        return default
    return parsed


def _llm_httpx_timeout_values() -> Dict[str, float]:
    return {
        "connect": _read_positive_float_env(
            _LLM_HTTPX_CONNECT_TIMEOUT_ENV,
            _DEFAULT_LLM_HTTPX_CONNECT_TIMEOUT_SECONDS,
        ),
        "read": _read_positive_float_env(
            _LLM_HTTPX_READ_TIMEOUT_ENV,
            _DEFAULT_LLM_HTTPX_READ_TIMEOUT_SECONDS,
        ),
        "write": _read_positive_float_env(
            _LLM_HTTPX_WRITE_TIMEOUT_ENV,
            _DEFAULT_LLM_HTTPX_WRITE_TIMEOUT_SECONDS,
        ),
        "pool": _read_positive_float_env(
            _LLM_HTTPX_POOL_TIMEOUT_ENV,
            _DEFAULT_LLM_HTTPX_POOL_TIMEOUT_SECONDS,
        ),
    }


def _llm_httpx_timeout():
    values = _llm_httpx_timeout_values()
    return httpx.Timeout(**values)  # type: ignore[union-attr]


def _llm_httpx_timeout_summary() -> str:
    values = _llm_httpx_timeout_values()
    return ", ".join(f"{key}={value:g}s" for key, value in values.items())


def _is_httpx_retryable_before_output(exc: BaseException) -> bool:
    if not _HTTPX_AVAILABLE:
        return False
    retryable_classes = tuple(
        cls
        for cls in (
            getattr(httpx, "ConnectError", None),
            getattr(httpx, "ConnectTimeout", None),
            getattr(httpx, "ProxyError", None),
            getattr(httpx, "ReadError", None),
            getattr(httpx, "ReadTimeout", None),
        )
        if cls is not None
    )
    return isinstance(exc, retryable_classes)


def _httpx_invocation_error_message(exc: BaseException, *, prefix: str = "httpx invocation failed") -> str:
    message = f"{prefix}: {type(exc).__name__}"
    detail = str(exc).strip()
    if detail and detail != type(exc).__name__:
        message = f"{message}: {detail}"
    if _HTTPX_AVAILABLE and isinstance(exc, getattr(httpx, "ReadTimeout", ())):
        message = (
            f"{message}; no response chunk arrived before read timeout "
            f"({_llm_httpx_timeout_summary()}). "
            f"Increase {_LLM_HTTPX_READ_TIMEOUT_ENV} for slower providers."
        )
    return message


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
    if len(parts) != 2:
        raise ValidationError(
            f"Invalid Model_Ref '{model_ref}'; expected provider/model",
            field="model_ref",
        )

    provider = parts[0].strip()
    model = parts[1].strip()
    if not provider or not model:
        raise ValidationError(
            f"Invalid Model_Ref '{model_ref}'; expected provider/model",
            field="model_ref",
        )

    return provider, model


def resolve_model_target(
    model_ref: str,
    provider_config: Optional[ProviderConfig] = None,
) -> Tuple[str, str]:
    plan = build_invocation_plan(model_ref, provider_config)
    return plan.provider_key, plan.model_name


async def check_provider_connectivity(provider_config: ProviderConfig) -> bool:
    gateway = LLMGatewayClient()
    return await gateway.health_check(provider_config)


async def discover_ollama_models(base_url: str = "http://127.0.0.1:11434") -> List[str]:
    gateway = LLMGatewayClient()
    return await gateway.get_local_models(base_url)


def _normalize_discovery_base_url(base_url: str, provider_type: ProviderType) -> str:
    normalized = base_url.rstrip("/")
    if provider_type == ProviderType.OLLAMA:
        normalized = normalized.removesuffix("/v1")
    return normalized


def _extract_discovered_model_ids(payload: Any) -> List[str]:
    collected: List[str] = []

    if isinstance(payload, list):
        for item in payload:
            collected.extend(_extract_discovered_model_ids(item))
        return collected

    if isinstance(payload, dict):
        for key in ("data", "models", "items", "results"):
            nested = payload.get(key)
            if isinstance(nested, list):
                collected.extend(_extract_discovered_model_ids(nested))

        for key in ("id", "name", "model"):
            value = payload.get(key)
            if isinstance(value, str):
                normalized = value.strip()
                if normalized:
                    collected.append(normalized)
        return collected

    if isinstance(payload, str):
        normalized = payload.strip()
        if normalized:
            collected.append(normalized)

    return collected


def _dedupe_model_ids(model_ids: List[str]) -> List[str]:
    seen = set()
    deduped: List[str] = []
    for model_id in model_ids:
        normalized = model_id.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


_LITELLM_MODEL_PROVIDER_ALIASES: Dict[ProviderType, Tuple[str, ...]] = {
    ProviderType.OPENAI: ("openai", "chatgpt"),
    ProviderType.ANTHROPIC: ("anthropic",),
    ProviderType.GOOGLE: ("gemini",),
    ProviderType.GROQ: ("groq",),
    ProviderType.MISTRAL: ("mistral",),
    ProviderType.XAI: ("xai",),
    ProviderType.OLLAMA: ("ollama",),
    ProviderType.LM_STUDIO: ("lm_studio",),
    ProviderType.VLLM: ("hosted_vllm", "vllm"),
    ProviderType.OPENROUTER: ("openrouter",),
    ProviderType.LITELLM: ("litellm_proxy", "litellm"),
    ProviderType.GATEWAY: ("litellm_proxy", "litellm"),
}

_LITELLM_EXTRA_MODEL_ATTRS: Dict[ProviderType, Tuple[str, ...]] = {
    ProviderType.OPENAI: ("open_ai_chat_completion_models", "chatgpt_models"),
    ProviderType.ANTHROPIC: ("anthropic_models",),
    ProviderType.GOOGLE: ("gemini_models",),
    ProviderType.GROQ: ("groq_models",),
    ProviderType.XAI: ("xai_models",),
    ProviderType.OLLAMA: ("ollama_models",),
    ProviderType.OPENROUTER: ("openrouter_models",),
}


def _normalize_litellm_model_id(model_id: str, provider_aliases: Tuple[str, ...]) -> str:
    normalized = model_id.strip()
    if not normalized:
        return ""

    for alias in provider_aliases:
        prefix = f"{alias}/"
        if normalized.startswith(prefix):
            return normalized[len(prefix):].strip()
    return normalized


def _collect_litellm_model_ids(provider_config: ProviderConfig) -> List[str]:
    if not _load_litellm() or litellm is None:
        return []

    provider_aliases = _LITELLM_MODEL_PROVIDER_ALIASES.get(
        provider_config.provider_type,
        (provider_config.provider_type.value,),
    )
    collected: List[str] = []

    models_by_provider = getattr(litellm, "models_by_provider", None)
    if isinstance(models_by_provider, dict):
        for alias in provider_aliases:
            models = models_by_provider.get(alias)
            if isinstance(models, (list, tuple, set)):
                for model in models:
                    if isinstance(model, str):
                        normalized = _normalize_litellm_model_id(model, provider_aliases)
                        if normalized:
                            collected.append(normalized)

    for attr_name in _LITELLM_EXTRA_MODEL_ATTRS.get(provider_config.provider_type, ()):
        models = getattr(litellm, attr_name, None)
        if isinstance(models, (list, tuple, set)):
            for model in models:
                if isinstance(model, str):
                    normalized = _normalize_litellm_model_id(model, provider_aliases)
                    if normalized:
                        collected.append(normalized)

    return _dedupe_model_ids(collected)


def encrypt_sensitive_value(value: str) -> str:
    return encrypt_value(value)


def decrypt_sensitive_value(value: str) -> str:
    return decrypt_value(value)


def _obfuscate(value: str) -> str:
    return encrypt_sensitive_value(value)


def _deobfuscate(value: str) -> str:
    return decrypt_sensitive_value(value)


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
        metadata=raw_payload.get("metadata")
        if isinstance(raw_payload.get("metadata"), dict)
        else {},
    )


def _is_chatgpt_oauth_runtime(
    provider: str,
    provider_config: Optional[ProviderConfig],
    auth_config: AuthConfig,
) -> bool:
    if auth_config.auth_type != AuthType.OAUTH:
        return False
    if auth_config.oauth_token is None or not auth_config.oauth_token.access_token:
        return False
    if provider_config is not None:
        return provider_config.provider_type == ProviderType.OPENAI
    return provider == ProviderType.OPENAI.value


def _normalize_chatgpt_model_name(model: str) -> str:
    normalized = model.strip()
    if normalized.startswith("gpt-5.3") and "codex" not in normalized:
        return "gpt-5.3-codex"
    return normalized


def _build_chatgpt_responses_input(
    messages: List[Dict[str, str]],
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    instructions: List[str] = []
    input_items: List[Dict[str, Any]] = []

    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user").strip().lower()
        content = message.get("content")

        if role == "system":
            if isinstance(content, str):
                normalized_content = content.strip()
            elif content is None:
                normalized_content = ""
            else:
                normalized_content = json.dumps(content, ensure_ascii=False)
            if normalized_content:
                instructions.append(normalized_content)
            continue

        if role not in {"user", "assistant", "developer"}:
            role = "user"

        if content is None:
            normalized_content = ""
        elif isinstance(content, str):
            normalized_content = content
        else:
            normalized_content = json.dumps(content, ensure_ascii=False)

        input_items.append(
            {
                "type": "message",
                "role": role,
                "content": normalized_content,
            }
        )

    if not input_items:
        input_items.append({"type": "message", "role": "user", "content": ""})

    return ("\n\n".join(instructions) if instructions else None, input_items)


def _extract_chunk_value(chunk: Any, key: str) -> Any:
    if isinstance(chunk, dict):
        return chunk.get(key)
    return getattr(chunk, key, None)


def _extract_chatgpt_completed_text(response: Any) -> Optional[str]:
    direct_text = _extract_chunk_value(response, "text")
    if isinstance(direct_text, str) and direct_text:
        return direct_text

    output = _extract_chunk_value(response, "output")
    if not isinstance(output, list):
        return None

    texts: List[str] = []
    for item in output:
        item_type = _extract_chunk_value(item, "type")
        if item_type != "message":
            continue
        content = _extract_chunk_value(item, "content")
        if not isinstance(content, list):
            continue
        for part in content:
            part_type = _extract_chunk_value(part, "type")
            if part_type not in {"output_text", "text"}:
                continue
            text = _extract_chunk_value(part, "text")
            if isinstance(text, str) and text:
                texts.append(text)

    combined = "".join(texts)
    return combined or None


def _extract_chatgpt_delta(chunk: Any) -> Optional[str]:
    event_type = _extract_chunk_value(chunk, "type")
    if event_type not in {"response.output_text.delta", "response.text.delta"}:
        return None

    delta = _extract_chunk_value(chunk, "delta")
    if delta is None:
        return None
    if isinstance(delta, str):
        return delta
    return str(delta)


_PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


@dataclass
class _ChatGPTAuthBridgeState:
    auth_file: Path
    updated_auth_config: Optional[AuthConfig] = None


def _parse_chatgpt_auth_expires_at(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), timezone.utc)
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        try:
            return datetime.fromtimestamp(float(normalized), timezone.utc)
        except ValueError:
            pass
        try:
            if normalized.endswith("Z"):
                normalized = f"{normalized[:-1]}+00:00"
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


def _decode_jwt_claims(token: Any) -> Dict[str, Any]:
    if not isinstance(token, str) or token.count(".") < 2:
        return {}
    try:
        payload = token.split(".", 2)[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")).decode("utf-8"))
    except Exception:
        return {}
    return claims if isinstance(claims, dict) else {}


def _extract_jwt_expiration(token: Any) -> Optional[datetime]:
    claims = _decode_jwt_claims(token)
    if not claims:
        return None
    return _parse_chatgpt_auth_expires_at(claims.get("exp"))


def _extract_chatgpt_account_id_from_jwt(token: Any) -> Optional[str]:
    claims = _decode_jwt_claims(token)
    auth_claims = claims.get("https://api.openai.com/auth")
    if isinstance(auth_claims, dict):
        account_id = auth_claims.get("chatgpt_account_id")
        if isinstance(account_id, str) and account_id.strip():
            return account_id.strip()
    return None


def _expires_at_timestamp(value: Optional[datetime]) -> Optional[int]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp())


def _normalize_chatgpt_auth_payload(raw_payload: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw_payload, dict):
        return None
    tokens = raw_payload.get("tokens")
    if isinstance(tokens, dict):
        return tokens
    return raw_payload


def _chatgpt_auth_update_from_payload(
    raw_payload: Any,
    original_auth_config: AuthConfig,
) -> Optional[AuthConfig]:
    original_token = original_auth_config.oauth_token
    if original_token is None:
        return None
    payload = _normalize_chatgpt_auth_payload(raw_payload)
    if payload is None:
        return None

    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        return None

    refresh_token = payload.get("refresh_token")
    if not isinstance(refresh_token, str) or not refresh_token:
        refresh_token = original_token.refresh_token

    expires_at = _parse_chatgpt_auth_expires_at(payload.get("expires_at"))
    if expires_at is None:
        expires_at = _extract_jwt_expiration(access_token)
    if expires_at is None:
        expires_at = original_token.expires_at

    metadata = dict(original_auth_config.metadata)
    account_id = payload.get("account_id")
    if not isinstance(account_id, str) or not account_id.strip():
        account_id = _extract_chatgpt_account_id_from_jwt(
            payload.get("id_token") or access_token
        )
    if isinstance(account_id, str) and account_id.strip():
        metadata["account_id"] = account_id.strip()

    updated_token = OAuthToken(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        token_type=original_token.token_type,
    )
    changed = (
        updated_token.access_token != original_token.access_token
        or updated_token.refresh_token != original_token.refresh_token
        or _expires_at_timestamp(updated_token.expires_at)
        != _expires_at_timestamp(original_token.expires_at)
        or metadata != original_auth_config.metadata
    )
    if not changed:
        return None

    return AuthConfig(
        auth_type=AuthType.OAUTH,
        oauth_token=updated_token,
        metadata=metadata,
    )


def _chatgpt_auth_update_from_file(
    auth_file: Path,
    original_auth_config: AuthConfig,
) -> Optional[AuthConfig]:
    try:
        raw_payload = json.loads(auth_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _chatgpt_auth_update_from_payload(raw_payload, original_auth_config)


def _shared_chatgpt_auth_candidate_paths(metadata: Dict[str, object]) -> List[Path]:
    paths: List[Path] = []
    for key in ("codex_auth_file", "chatgpt_auth_file", "shared_auth_file"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            paths.append(Path(value).expanduser())

    env_auth_file = os.environ.get("CODEX_AUTH_FILE")
    if env_auth_file:
        paths.append(Path(env_auth_file).expanduser())

    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    paths.append(codex_home / "auth.json")
    paths.append(Path.home() / ".codex" / "auth.json")
    paths.append(Path.home() / ".config" / "litellm" / "chatgpt" / "auth.json")

    deduped: List[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _is_shared_chatgpt_auth_candidate_usable(
    original_auth_config: AuthConfig,
    candidate_auth_config: AuthConfig,
) -> bool:
    original_token = original_auth_config.oauth_token
    candidate_token = candidate_auth_config.oauth_token
    if original_token is None or candidate_token is None:
        return False

    candidate_expires_at = _expires_at_timestamp(candidate_token.expires_at)
    if candidate_expires_at is not None and candidate_expires_at <= int(time.time()) + 60:
        return False

    original_account_id = _chatgpt_auth_config_account_id(original_auth_config)
    candidate_account_id = _chatgpt_auth_config_account_id(candidate_auth_config)
    if not original_account_id:
        allow_without_account_id = original_auth_config.metadata.get(
            "allow_shared_auth_import_without_account_id"
        )
        if allow_without_account_id is not True:
            return False
    if original_account_id and not candidate_account_id:
        return False
    if original_account_id and candidate_account_id and original_account_id != candidate_account_id:
        return False

    if (
        candidate_token.access_token == original_token.access_token
        and candidate_token.refresh_token == original_token.refresh_token
    ):
        return False

    original_expires_at = _expires_at_timestamp(original_token.expires_at)
    if original_expires_at is None:
        return candidate_expires_at is not None
    if candidate_expires_at is None:
        return False
    return candidate_expires_at >= original_expires_at


def _chatgpt_auth_config_account_id(auth_config: AuthConfig) -> Optional[str]:
    account_id = auth_config.metadata.get("account_id")
    if isinstance(account_id, str) and account_id.strip():
        return account_id.strip()
    token = auth_config.oauth_token
    if token is None:
        return None
    return _extract_chatgpt_account_id_from_jwt(token.access_token)


def _load_shared_chatgpt_auth_update(
    original_auth_config: AuthConfig,
) -> Optional[AuthConfig]:
    metadata = original_auth_config.metadata if isinstance(original_auth_config.metadata, dict) else {}
    for path in _shared_chatgpt_auth_candidate_paths(metadata):
        if not path.exists() or not path.is_file():
            continue
        candidate = _chatgpt_auth_update_from_file(path, original_auth_config)
        if candidate and _is_shared_chatgpt_auth_candidate_usable(original_auth_config, candidate):
            logger.info("Imported fresher ChatGPT OAuth auth from shared auth source %s", path)
            return candidate
    return None


def _apply_auth_config_update(target: AuthConfig, updated: AuthConfig) -> None:
    target.auth_type = updated.auth_type
    target.api_key = updated.api_key
    target.bearer_token = updated.bearer_token
    target.helper_script = updated.helper_script
    target.oauth_token = updated.oauth_token
    target.metadata = dict(updated.metadata)


@contextmanager
def _chatgpt_auth_bridge(auth_config: AuthConfig):
    token = auth_config.oauth_token
    if token is None or not token.access_token:
        raise AuthenticationError("OAuth token is not configured")

    previous_env = {
        "CHATGPT_TOKEN_DIR": os.environ.get("CHATGPT_TOKEN_DIR"),
        "CHATGPT_AUTH_FILE": os.environ.get("CHATGPT_AUTH_FILE"),
    }

    with tempfile.TemporaryDirectory(prefix="chatgpt-token-") as token_dir:
        auth_payload: Dict[str, Any] = {"access_token": token.access_token}
        if token.refresh_token:
            auth_payload["refresh_token"] = token.refresh_token
        if token.expires_at is not None:
            from datetime import timezone

            expires_at = token.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            auth_payload["expires_at"] = int(expires_at.timestamp())

        metadata = auth_config.metadata if isinstance(auth_config.metadata, dict) else {}
        account_id = None
        for key in ("account_id", "accountId", "chatgpt_account_id", "chatgptAccountId"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                account_id = value.strip()
                break
            if value is not None:
                value_str = str(value).strip()
                if value_str:
                    account_id = value_str
                    break
        if account_id:
            auth_payload["account_id"] = account_id

        auth_file = Path(token_dir) / "auth.json"
        auth_file.write_text(json.dumps(auth_payload, ensure_ascii=False), encoding="utf-8")
        bridge_state = _ChatGPTAuthBridgeState(auth_file=auth_file)

        os.environ["CHATGPT_TOKEN_DIR"] = token_dir
        os.environ["CHATGPT_AUTH_FILE"] = "auth.json"
        try:
            yield bridge_state
        finally:
            bridge_state.updated_auth_config = _chatgpt_auth_update_from_file(
                auth_file,
                auth_config,
            )
            for key, value in previous_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


@contextmanager
def _without_proxy_environment():
    previous_env = {key: os.environ.get(key) for key in _PROXY_ENV_KEYS}
    try:
        for key in _PROXY_ENV_KEYS:
            os.environ.pop(key, None)
        yield
    finally:
        for key, value in previous_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def _without_litellm_proxy_environment():
    if litellm is None:
        yield
        return

    previous_disable_aiohttp_trust_env = getattr(litellm, "disable_aiohttp_trust_env", False)
    previous_aiohttp_trust_env = getattr(litellm, "aiohttp_trust_env", False)
    previous_disable_env = os.environ.get("DISABLE_AIOHTTP_TRUST_ENV")
    previous_trust_env = os.environ.get("AIOHTTP_TRUST_ENV")
    try:
        setattr(litellm, "disable_aiohttp_trust_env", True)
        setattr(litellm, "aiohttp_trust_env", False)
        os.environ["DISABLE_AIOHTTP_TRUST_ENV"] = "True"
        os.environ["AIOHTTP_TRUST_ENV"] = "False"
        yield
    finally:
        setattr(litellm, "disable_aiohttp_trust_env", previous_disable_aiohttp_trust_env)
        setattr(litellm, "aiohttp_trust_env", previous_aiohttp_trust_env)

        if previous_disable_env is None:
            os.environ.pop("DISABLE_AIOHTTP_TRUST_ENV", None)
        else:
            os.environ["DISABLE_AIOHTTP_TRUST_ENV"] = previous_disable_env

        if previous_trust_env is None:
            os.environ.pop("AIOHTTP_TRUST_ENV", None)
        else:
            os.environ["AIOHTTP_TRUST_ENV"] = previous_trust_env


def _iter_exception_chain(exc: BaseException) -> Iterable[BaseException]:
    current: Optional[BaseException] = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _is_proxy_connection_error(exc: BaseException) -> bool:
    if httpx is None:
        return False

    proxy_type_names = {
        name
        for name in (
            getattr(httpx, "ProxyError", None).__name__ if getattr(httpx, "ProxyError", None) else None,
            getattr(httpx, "ConnectError", None).__name__ if getattr(httpx, "ConnectError", None) else None,
        )
        if name
    }

    for current in _iter_exception_chain(exc):
        current_type_name = type(current).__name__
        current_text = str(current).lower()
        if current_type_name in proxy_type_names:
            return True
        if "proxy" in current_type_name.lower() and ("connect" in current_text or "refused" in current_text):
            return True
        if "127.0.0.1:7897" in current_text and "connect" in current_text:
            return True
    return False


def _is_chatgpt_authentication_error(exc: BaseException) -> bool:
    auth_markers = (
        "401 unauthorized",
        "refresh token failed",
        "re-login required",
        "relogin required",
        "oauth token",
        "chatgpt oauth refresh failed",
        "interactive device-code login is disabled",
        "device-code login is disabled",
        "interactive chatgpt device-code login is disabled",
        "chatgpt device-code login is disabled",
    )
    for current in _iter_exception_chain(exc):
        current_text = str(current).lower()
        if any(marker in current_text for marker in auth_markers):
            return True
    return False


@contextmanager
def _disable_litellm_chatgpt_device_login():
    """Do not let a backend request block waiting for an interactive device login."""
    try:
        from litellm.llms.chatgpt import authenticator as chatgpt_authenticator  # type: ignore
    except Exception:
        yield
        return

    authenticator_cls = getattr(chatgpt_authenticator, "Authenticator", None)
    if authenticator_cls is None:
        yield
        return

    original_login = getattr(authenticator_cls, "_login_device_code", None)
    if original_login is None:
        yield
        return

    def _raise_relogin_required(self):  # type: ignore[no-untyped-def]
        raise AuthenticationError(
            "ChatGPT OAuth refresh failed; interactive device-code login is disabled in backend. Re-login this provider from the UI.",
            provider="chatgpt",
        )

    setattr(authenticator_cls, "_login_device_code", _raise_relogin_required)
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            setattr(authenticator_cls, "_login_device_code", original_login)


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

    def register_client_pkce(self, redirect_uri: str) -> Dict[str, Any]:
        """注册支持 authorization_code + PKCE 的 OIDC 客户端。"""
        if not _HTTPX_AVAILABLE:
            raise ProviderUnavailableError("httpx is required for SSO OIDC PKCE registration")
        with httpx.Client() as client:
            response = client.post(
                f"{self._oidc_endpoint()}/client/register",
                json={
                    "clientName": "multi-model-debate-pkce",
                    "clientType": "public",
                    "scopes": ["sso:account:access"],
                    "grantTypes": ["authorization_code", "refresh_token"],
                    "redirectUris": [redirect_uri],
                },
                timeout=10,
            )
            response.raise_for_status()
            return response.json()

    def build_authorize_url(
        self,
        client_id: str,
        redirect_uri: str,
        state: str,
        code_challenge: str,
    ) -> str:
        """构造 PKCE 授权 URL，用户在浏览器中打开此 URL 完成 SSO 登录。"""
        from urllib.parse import urlencode
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "sso:account:access",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{self._oidc_endpoint()}/authorize?" + urlencode(params)

    def exchange_auth_code(
        self,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
        code_verifier: str,
    ) -> Dict[str, Any]:
        """用授权码换取 access_token（PKCE flow）。"""
        if not _HTTPX_AVAILABLE:
            raise ProviderUnavailableError("httpx is required for auth code exchange")
        with httpx.Client() as client:
            response = client.post(
                f"{self._oidc_endpoint()}/token",
                json={
                    "clientId": client_id,
                    "clientSecret": client_secret,
                    "grantType": "authorization_code",
                    "redirectUri": redirect_uri,
                    "code": code,
                    "codeVerifier": code_verifier,
                },
                timeout=10,
            )
            response.raise_for_status()
            return response.json()

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
        on_auth_update: Optional[AuthUpdateCallback] = None,
    ) -> AsyncIterator[str]:
        plan = build_invocation_plan(model_ref, provider_config)
        provider = plan.provider_key
        model = plan.model_name
        effective_auth = provider_config.auth_config if provider_config else (auth_config or AuthConfig(auth_type=AuthType.IAM))
        runtime_kind = plan.runtime_kind
        if runtime_kind == InvocationRuntimeKind.LITELLM_COMPLETION and _is_chatgpt_oauth_runtime(
            provider,
            provider_config,
            effective_auth,
        ):
            runtime_kind = InvocationRuntimeKind.CHATGPT_OAUTH_RESPONSES

        if runtime_kind == InvocationRuntimeKind.CHATGPT_OAUTH_RESPONSES:
            if not _load_litellm():
                raise ProviderUnavailableError("LiteLLM is required for ChatGPT responses streaming", provider=provider_config.name if provider_config else provider)
            async for chunk in self._chatgpt_responses_stream(
                provider=provider,
                model=model,
                messages=messages,
                auth_config=effective_auth,
                provider_config=provider_config,
                on_auth_update=on_auth_update,
            ):
                yield chunk
            return

        headers = self._auth_manager.get_headers(effective_auth)

        if runtime_kind in {
            InvocationRuntimeKind.HTTPX_OPENAI_COMPATIBLE,
            InvocationRuntimeKind.HTTPX_ANTHROPIC_MESSAGES,
        }:
            if provider_config is not None:
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

            async for chunk in self._httpx_stream(provider, model, messages, headers, effective_auth):
                yield chunk
            return

        if _load_litellm():
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

        if provider_config is not None:
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

    async def _chatgpt_responses_stream(
        self,
        provider: str,
        model: str,
        messages: List[Dict[str, str]],
        auth_config: AuthConfig,
        provider_config: Optional[ProviderConfig] = None,
        on_auth_update: Optional[AuthUpdateCallback] = None,
    ) -> AsyncIterator[str]:
        if not _load_litellm():
            raise ProviderUnavailableError("LiteLLM is unavailable", provider=provider)

        model = _normalize_chatgpt_model_name(model)
        instructions, input_messages = _build_chatgpt_responses_input(messages)
        kwargs: Dict[str, Any] = {
            "model": model,
            "input": input_messages,
            "stream": True,
            "store": False,
            "custom_llm_provider": "chatgpt",
        }
        if instructions:
            kwargs["instructions"] = instructions

        async def _notify_auth_update(updated_auth_config: Optional[AuthConfig]) -> None:
            if updated_auth_config is None:
                return
            _apply_auth_config_update(auth_config, updated_auth_config)
            if provider_config is not None:
                provider_config.auth_type = updated_auth_config.auth_type
                provider_config.auth_config = auth_config
            if on_auth_update is None:
                return
            try:
                result = on_auth_update(updated_auth_config)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.warning(
                    "ChatGPT OAuth auth update callback failed for provider %s",
                    provider_config.name if provider_config else provider,
                    exc_info=True,
                )

        await _notify_auth_update(_load_shared_chatgpt_auth_update(auth_config))

        async def _stream_once(disable_proxy_env: bool) -> AsyncIterator[str]:
            emitted_any = False
            proxy_context = _without_proxy_environment() if disable_proxy_env else nullcontext()
            litellm_proxy_context = (
                _without_litellm_proxy_environment() if disable_proxy_env else nullcontext()
            )
            bridge_state: Optional[_ChatGPTAuthBridgeState] = None
            try:
                with (
                    proxy_context,
                    litellm_proxy_context,
                    _chatgpt_auth_bridge(auth_config) as active_bridge,
                    _disable_litellm_chatgpt_device_login(),
                ):
                    bridge_state = active_bridge
                    response = await litellm.aresponses(**kwargs)  # type: ignore[union-attr]
                    async for chunk in response:
                        delta = _extract_chatgpt_delta(chunk)
                        if delta:
                            emitted_any = True
                            yield delta
                            continue

                        event_type = _extract_chunk_value(chunk, "type")
                        if emitted_any:
                            continue

                        if event_type in {
                            "response.output_text.done",
                            "response.text.done",
                            "response.completed",
                        }:
                            completed_text = _extract_chatgpt_completed_text(
                                _extract_chunk_value(chunk, "response")
                                if event_type == "response.completed"
                                else chunk
                            )
                            if completed_text:
                                emitted_any = True
                                yield completed_text
            finally:
                if bridge_state is not None:
                    await _notify_auth_update(bridge_state.updated_auth_config)

        try:
            async for chunk in _stream_once(disable_proxy_env=False):
                yield chunk
            return
        except Exception as exc:
            retry_exc: Optional[BaseException] = None
            if _is_proxy_connection_error(exc):
                logger.warning(
                    "ChatGPT responses failed via environment proxy; retrying direct connection: %s",
                    exc,
                )
                try:
                    async for chunk in _stream_once(disable_proxy_env=True):
                        yield chunk
                    return
                except Exception as direct_exc:
                    retry_exc = direct_exc

            final_exc: BaseException = retry_exc or exc
            provider_name = provider_config.name if provider_config and provider_config.name else provider
            detail = str(final_exc).strip()
            if _is_chatgpt_authentication_error(final_exc):
                message = "ChatGPT OAuth authentication failed; re-login this provider from the UI."
                if detail:
                    message = f"{message} Detail: {detail}"
                raise AuthenticationError(message, provider=provider_name) from final_exc
            message = f"LiteLLM ChatGPT responses invocation failed: {type(final_exc).__name__}"
            if detail and detail != type(final_exc).__name__:
                message = f"{message}: {detail}"
            raise ProviderUnavailableError(
                message,
                provider=provider_name,
            ) from final_exc

    async def discover_provider_models(self, provider_config: ProviderConfig) -> List[str]:
        if not _HTTPX_AVAILABLE:
            logger.warning("httpx unavailable, cannot discover provider models")
            return _collect_litellm_model_ids(provider_config)

        base_url = provider_config.base_url or self._get_default_base_url(provider_config.provider_type)
        normalized_base_url = _normalize_discovery_base_url(base_url, provider_config.provider_type)
        endpoint = (
            f"{normalized_base_url}/api/tags"
            if provider_config.provider_type == ProviderType.OLLAMA
            else f"{normalized_base_url}/models"
        )

        try:
            headers = self._auth_manager.get_headers(provider_config.auth_config)
        except AuthenticationError:
            headers = {}

        discovered_models: List[str] = []
        try:
            async with httpx.AsyncClient() as client:  # type: ignore[union-attr]
                response = await client.get(endpoint, headers=headers, timeout=5)
            if response.status_code != 200:
                discovered_models = []
            else:
                data = response.json()
                discovered_models = _dedupe_model_ids(_extract_discovered_model_ids(data))
        except Exception as exc:
            logger.debug("provider model discovery failed for %s: %s", provider_config.name, exc)
            discovered_models = []

        if discovered_models:
            return discovered_models

        fallback_models = _collect_litellm_model_ids(provider_config)
        if fallback_models:
            logger.debug(
                "provider model discovery for %s fell back to LiteLLM registry (%d models)",
                provider_config.name,
                len(fallback_models),
            )
        return fallback_models

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
        emitted_any = False
        try:
            async for chunk in self._stream_openai_compatible_once(
                endpoint=endpoint,
                payload=payload,
                headers=headers,
                provider_name=provider_name,
            ):
                emitted_any = True
                yield chunk
            return
        except Exception as exc:
            if isinstance(exc, ProviderUnavailableError):
                raise
            if emitted_any or not _is_httpx_retryable_before_output(exc):
                raise ProviderUnavailableError(
                    _httpx_invocation_error_message(exc),
                    provider=provider_name,
                ) from exc
            logger.warning(
                "OpenAI-compatible stream failed before output for %s; retrying direct connection: %s",
                provider_name,
                exc,
            )
            try:
                async for chunk in self._stream_openai_compatible_once(
                    endpoint=endpoint,
                    payload=payload,
                    headers=headers,
                    provider_name=provider_name,
                    trust_env=False,
                ):
                    yield chunk
                return
            except ProviderUnavailableError:
                raise
            except Exception as retry_exc:
                raise ProviderUnavailableError(
                    _httpx_invocation_error_message(retry_exc),
                    provider=provider_name,
                ) from retry_exc

    async def _stream_openai_compatible_once(
        self,
        endpoint: str,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        provider_name: str,
        trust_env: Optional[bool] = None,
    ) -> AsyncIterator[str]:
        client_kwargs: Dict[str, Any] = {}
        if trust_env is not None:
            client_kwargs["trust_env"] = trust_env

        async with httpx.AsyncClient(**client_kwargs) as client:  # type: ignore[union-attr]
            async with client.stream(
                "POST",
                endpoint,
                json=payload,
                headers=headers,
                timeout=_llm_httpx_timeout(),
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")
                    body = " ".join(body.split())[:400]
                    raise ProviderUnavailableError(
                        f"httpx invocation failed: HTTP {response.status_code}: {body}",
                        provider=provider_name,
                    )
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
                    choices = payload.get("choices")
                    if not isinstance(choices, list) or not choices:
                        continue
                    first_choice = choices[0]
                    if not isinstance(first_choice, dict):
                        continue
                    delta = first_choice.get("delta", {})
                    if not isinstance(delta, dict):
                        continue
                    content = delta.get("content")
                    if content:
                        yield content

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
                    timeout=_llm_httpx_timeout(),
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
                    _httpx_invocation_error_message(
                        exc,
                        prefix="anthropic httpx invocation failed",
                    ),
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
