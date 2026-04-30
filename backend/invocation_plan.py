from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .enums import APIFormat, AuthType, ProviderType
from .exceptions import ValidationError
from .models import ProviderConfig


class InvocationRuntimeKind(str, Enum):
    CHATGPT_OAUTH_RESPONSES = "chatgpt_oauth_responses"
    HTTPX_OPENAI_COMPATIBLE = "httpx_openai_compatible"
    HTTPX_ANTHROPIC_MESSAGES = "httpx_anthropic_messages"
    LITELLM_COMPLETION = "litellm_completion"


@dataclass(frozen=True)
class ResolvedInvocationPlan:
    requested_model_ref: str
    provider_key: str
    model_name: str
    provider_id: Optional[str]
    provider_name: str
    provider_type: ProviderType
    api_format: APIFormat
    auth_type: AuthType
    base_url: Optional[str]
    runtime_kind: InvocationRuntimeKind


def _provider_key_from_config(provider_config: ProviderConfig) -> str:
    return (provider_config.name or "").strip() or provider_config.provider_type.value


def _parse_model_ref(
    model_ref: str,
    provider_config: Optional[ProviderConfig],
) -> tuple[str, str]:
    if not model_ref or not isinstance(model_ref, str):
        raise ValidationError("Model_Ref cannot be empty; expected provider/model", field="model_ref")

    normalized = model_ref.strip()
    if not normalized:
        raise ValidationError("Model_Ref cannot be empty; expected provider/model", field="model_ref")

    if "/" not in normalized:
        if provider_config is None:
            raise ValidationError(
                f"Invalid Model_Ref '{model_ref}'; expected provider/model",
                field="model_ref",
            )
        return _provider_key_from_config(provider_config), normalized

    provider_key, model_name = normalized.split("/", 1)
    provider_key = provider_key.strip()
    model_name = model_name.strip()
    if not provider_key or not model_name or "/" in model_name:
        raise ValidationError(
            f"Invalid Model_Ref '{model_ref}'; expected provider/model",
            field="model_ref",
        )
    return provider_key, model_name


def _provider_type_from_key(provider_key: str) -> ProviderType:
    normalized = provider_key.strip().lower()
    for provider_type in ProviderType:
        if provider_type.value == normalized:
            return provider_type
    return ProviderType.CUSTOM


def _should_use_httpx(provider_config: ProviderConfig) -> bool:
    return provider_config.provider_type in {
        ProviderType.CUSTOM,
        ProviderType.GATEWAY,
        ProviderType.OLLAMA,
        ProviderType.LM_STUDIO,
        ProviderType.VLLM,
        ProviderType.LITELLM,
    } or provider_config.base_url is not None or provider_config.api_format == APIFormat.ANTHROPIC_MESSAGES


def _is_chatgpt_oauth_runtime(provider_config: ProviderConfig) -> bool:
    if provider_config.provider_type != ProviderType.OPENAI:
        return False
    if provider_config.auth_type != AuthType.OAUTH:
        return False
    token = provider_config.auth_config.oauth_token
    return bool(token and token.access_token)


def build_invocation_plan(
    model_ref: str,
    provider_config: Optional[ProviderConfig] = None,
) -> ResolvedInvocationPlan:
    provider_key, model_name = _parse_model_ref(model_ref, provider_config)

    if provider_config is None:
        provider_type = _provider_type_from_key(provider_key)
        return ResolvedInvocationPlan(
            requested_model_ref=model_ref,
            provider_key=provider_key,
            model_name=model_name,
            provider_id=None,
            provider_name=provider_key,
            provider_type=provider_type,
            api_format=APIFormat.OPENAI_COMPLETIONS,
            auth_type=AuthType.API_KEY,
            base_url=None,
            runtime_kind=InvocationRuntimeKind.LITELLM_COMPLETION,
        )

    runtime_kind = InvocationRuntimeKind.LITELLM_COMPLETION
    if _is_chatgpt_oauth_runtime(provider_config):
        runtime_kind = InvocationRuntimeKind.CHATGPT_OAUTH_RESPONSES
    elif provider_config.api_format == APIFormat.ANTHROPIC_MESSAGES:
        runtime_kind = InvocationRuntimeKind.HTTPX_ANTHROPIC_MESSAGES
    elif _should_use_httpx(provider_config):
        runtime_kind = InvocationRuntimeKind.HTTPX_OPENAI_COMPATIBLE

    return ResolvedInvocationPlan(
        requested_model_ref=model_ref,
        provider_key=provider_key,
        model_name=model_name,
        provider_id=provider_config.id,
        provider_name=provider_config.name,
        provider_type=provider_config.provider_type,
        api_format=provider_config.api_format,
        auth_type=provider_config.auth_type,
        base_url=provider_config.base_url,
        runtime_kind=runtime_kind,
    )
