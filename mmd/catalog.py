from __future__ import annotations

from typing import Any, Iterable, Mapping, MutableMapping, Optional

from .models import ModelCatalogEntry


class ModelResolutionError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        candidate_provider_ids: Iterable[str] = (),
    ) -> None:
        super().__init__(message)
        self.candidate_provider_ids = tuple(candidate_provider_ids)


def normalize_model_ref(model_ref: str) -> str:
    return str(model_ref or "").strip()


def _normalize_provider_id(provider_id: object) -> str:
    return str(provider_id or "").strip()


def dedupe_models(models: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for model in models:
        normalized = normalize_model_ref(model)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return tuple(result)


def load_model_catalogs(client: Any) -> list[ModelCatalogEntry]:
    catalogs: list[ModelCatalogEntry] = []
    for provider in client.list_providers():
        provider_id = str(provider.get("id") or "").strip()
        if not provider_id:
            continue
        try:
            discovery = client.discover_models(provider_id=provider_id)
        except Exception:
            discovery = {"models": []}
        catalogs.append(
            ModelCatalogEntry(
                provider_id=provider_id,
                provider_name=str(provider.get("name") or provider_id).strip(),
                provider_type=str(provider.get("provider_type") or "").strip(),
                models=tuple(
                    str(model).strip()
                    for model in discovery.get("models", [])
                    if str(model).strip()
                ),
                detected_at=discovery.get("detected_at"),
            )
        )
    return catalogs


def format_participant_model_selection(
    provider_id: Optional[str],
    model_ref: str,
) -> str:
    normalized_model = normalize_model_ref(model_ref)
    if not normalized_model:
        return ""
    normalized_provider = _normalize_provider_id(provider_id)
    if normalized_provider:
        return f"{normalized_provider}::{normalized_model}"
    return normalized_model


def format_backend_model_ref(
    provider_id: Optional[str],
    model_ref: str,
) -> str:
    normalized_model = normalize_model_ref(model_ref)
    if not normalized_model:
        return ""
    normalized_provider = _normalize_provider_id(provider_id)
    if normalized_provider:
        return f"{normalized_provider}/{normalized_model}"
    return normalized_model


def resolve_backend_model_ref(
    catalogs: Iterable[ModelCatalogEntry],
    selection: Mapping[str, object],
) -> str:
    normalized_model = normalize_model_ref(selection.get("model_ref", ""))
    explicit_provider = _normalize_provider_id(selection.get("provider_id"))

    if not normalized_model:
        raise ModelResolutionError("model_ref is required")

    if explicit_provider and "/" not in normalized_model:
        return format_backend_model_ref(explicit_provider, normalized_model)

    if "/" in normalized_model:
        provider_key, model_name = normalized_model.split("/", 1)
        provider_key = provider_key.strip()
        model_name = model_name.strip()
        if not provider_key or not model_name:
            raise ModelResolutionError(f"Invalid model ref: {normalized_model}")
        return f"{provider_key}/{model_name}"

    normalized_catalogs = [
        ModelCatalogEntry(
            provider_id=_normalize_provider_id(catalog.provider_id),
            provider_name=str(catalog.provider_name or "").strip(),
            provider_type=str(catalog.provider_type or "").strip(),
            models=dedupe_models(catalog.models),
            detected_at=catalog.detected_at,
        )
        for catalog in catalogs
        if _normalize_provider_id(catalog.provider_id)
    ]

    matches = [
        catalog
        for catalog in normalized_catalogs
        if normalized_model in catalog.models
    ]

    if len(matches) == 1:
        catalog = matches[0]
        provider_key = (catalog.provider_type or catalog.provider_name or catalog.provider_id).strip()
        if not provider_key:
            raise ModelResolutionError(f"Model '{normalized_model}' does not match any discovered provider")
        return f"{provider_key}/{normalized_model}"

    if len(matches) > 1:
        raise ModelResolutionError(
            f"Model '{normalized_model}' matches multiple providers",
            candidate_provider_ids=[catalog.provider_id for catalog in matches],
        )

    raise ModelResolutionError(
        f"Model '{normalized_model}' does not match any discovered provider",
    )


def parse_participant_model_selection(value: str) -> dict[str, str]:
    normalized = normalize_model_ref(value)
    separator_index = normalized.find("::")
    if separator_index > 0:
        provider_id = normalized[:separator_index].strip()
        model_ref = normalized[separator_index + 2 :].strip()
        result: dict[str, str] = {"model_ref": model_ref}
        if provider_id:
            result["provider_id"] = provider_id
        return result
    return {"model_ref": normalized}


def resolve_participant_model_selection(
    catalogs: Iterable[ModelCatalogEntry],
    selection: Mapping[str, object],
) -> MutableMapping[str, str]:
    normalized_model = normalize_model_ref(selection.get("model_ref", ""))
    explicit_provider = _normalize_provider_id(selection.get("provider_id"))

    if not normalized_model:
        raise ModelResolutionError("model_ref is required")

    if explicit_provider:
        return {
            "provider_id": explicit_provider,
            "model_ref": normalized_model,
        }

    if "/" in normalized_model:
        provider_key, model_name = normalized_model.split("/", 1)
        provider_key = provider_key.strip()
        model_name = model_name.strip()
        if not provider_key or not model_name:
            raise ModelResolutionError(f"Invalid model ref: {normalized_model}")
        return {"model_ref": f"{provider_key}/{model_name}"}

    normalized_catalogs = [
        ModelCatalogEntry(
            provider_id=_normalize_provider_id(catalog.provider_id),
            provider_name=str(catalog.provider_name or "").strip(),
            provider_type=str(catalog.provider_type or "").strip(),
            models=dedupe_models(catalog.models),
            detected_at=catalog.detected_at,
        )
        for catalog in catalogs
        if _normalize_provider_id(catalog.provider_id)
    ]

    matches = [
        catalog.provider_id
        for catalog in normalized_catalogs
        if normalized_model in catalog.models
    ]

    if len(matches) == 1:
        return {
            "provider_id": matches[0],
            "model_ref": normalized_model,
        }

    if len(matches) > 1:
        raise ModelResolutionError(
            f"Model '{normalized_model}' matches multiple providers",
            candidate_provider_ids=matches,
        )

    raise ModelResolutionError(
        f"Model '{normalized_model}' does not match any discovered provider",
    )
