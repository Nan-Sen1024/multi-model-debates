import { useEffect, useMemo, useState, type SelectHTMLAttributes } from "react";

export interface ProviderModelCatalog {
  provider_id: string;
  provider_name: string;
  provider_type: string;
  models: string[];
  detected_at?: number;
}

export interface ModelRefOption {
  value: string;
  label: string;
}

export interface ModelRefGroup {
  label: string;
  options: ModelRefOption[];
}

type ProviderCatalogCollection = ProviderModelCatalog[] | Record<string, ProviderModelCatalog>;

function normalizeModelRef(modelRef: string): string {
  return modelRef.trim();
}

function dedupeModels(models: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const model of models) {
    const normalized = normalizeModelRef(model);
    if (!normalized || seen.has(normalized)) {
      continue;
    }
    seen.add(normalized);
    result.push(normalized);
  }
  return result;
}

export function formatParticipantModelSelection(
  providerId: string | undefined | null,
  modelRef: string,
): string {
  const normalizedModel = normalizeModelRef(modelRef);
  if (!normalizedModel) {
    return "";
  }
  const normalizedProvider = providerId?.trim();
  if (normalizedProvider) {
    return `${normalizedProvider}::${normalizedModel}`;
  }
  return normalizedModel;
}

export function parseParticipantModelSelection(value: string): {
  provider_id?: string;
  model_ref: string;
} {
  const normalized = normalizeModelRef(value);
  const separatorIndex = normalized.indexOf("::");
  if (separatorIndex > 0) {
    return {
      provider_id: normalized.slice(0, separatorIndex).trim() || undefined,
      model_ref: normalized.slice(separatorIndex + 2).trim(),
    };
  }
  return {
    model_ref: normalized,
  };
}

export function resolveParticipantModelSelection(
  catalogs: ProviderCatalogCollection,
  selection: {
    provider_id?: string | null;
    model_ref: string;
  },
): {
  provider_id?: string;
  model_ref: string;
} {
  const normalizedModel = normalizeModelRef(selection.model_ref);
  const explicitProvider = selection.provider_id?.trim();
  if (!normalizedModel) {
    return {
      provider_id: explicitProvider || undefined,
      model_ref: "",
    };
  }
  if (explicitProvider) {
    return {
      provider_id: explicitProvider,
      model_ref: normalizedModel,
    };
  }

  const normalizedCatalogs = normalizeProviderCatalogs(catalogs);
  const matchingProviderIds = normalizedCatalogs
    .filter((catalog) =>
      dedupeModels(catalog.models).some((model) => model === normalizedModel),
    )
    .map((catalog) => catalog.provider_id.trim())
    .filter(Boolean);

  if (matchingProviderIds.length === 1) {
    return {
      provider_id: matchingProviderIds[0],
      model_ref: normalizedModel,
    };
  }

  if (normalizedCatalogs.length === 1) {
    const fallbackProvider = normalizedCatalogs[0].provider_id.trim();
    if (fallbackProvider) {
      return {
        provider_id: fallbackProvider,
        model_ref: normalizedModel,
      };
    }
  }

  return {
    model_ref: normalizedModel,
  };
}

function appendCurrentValueGroup(
  groups: ModelRefGroup[],
  selectedValue: string,
  label = "当前值",
): ModelRefGroup[] {
  const normalized = normalizeModelRef(selectedValue);
  if (!normalized) {
    return groups;
  }
  const exists = groups.some((group) =>
    group.options.some((option) => option.value === normalized),
  );
  if (exists) {
    return groups;
  }
  return [
    ...groups,
    {
      label,
      options: [{ value: normalized, label: normalized }],
    },
  ];
}

export function buildDraftModelGroups(
  models: string[],
  selectedValue = "",
): ModelRefGroup[] {
  const normalizedModels = dedupeModels(models);
  const groups: ModelRefGroup[] = [
    {
      label: "已发现模型",
      options: normalizedModels.map((model) => ({
        value: model,
        label: model,
      })),
    },
  ];
  return appendCurrentValueGroup(groups, selectedValue);
}

export function buildParticipantModelGroups(
  catalogs: ProviderCatalogCollection,
  selectedProviderId?: string | null,
  selectedValue = "",
): ModelRefGroup[] {
  const normalizedCatalogs = normalizeProviderCatalogs(catalogs);
  const groups: ModelRefGroup[] = [];
  const normalizedSelectedProviderId = selectedProviderId?.trim() || "";

  for (const catalog of normalizedCatalogs) {
    const providerId = catalog.provider_id.trim();
    if (normalizedSelectedProviderId && providerId !== normalizedSelectedProviderId) {
      continue;
    }

    const models = dedupeModels(catalog.models);
    if (models.length === 0) {
      continue;
    }

    groups.push({
      label: catalog.provider_name || catalog.provider_type || providerId,
      options: models.map((model) => ({
        value: formatParticipantModelSelection(providerId, model),
        label: model,
      })),
    });
  }

  return appendCurrentValueGroup(groups, selectedValue);
}

function normalizeProviderCatalogs(catalogs: ProviderCatalogCollection): ProviderModelCatalog[] {
  if (Array.isArray(catalogs)) {
    return catalogs;
  }
  return Object.values(catalogs);
}

export function getDefaultModelRefForProvider(
  providerType: string,
  authType?: string,
  providerName = "",
): string {
  void providerType;
  void authType;
  void providerName;
  return "";
}

export function readDefaultModelRef(metadata?: Record<string, unknown> | null): string | null {
  const value = metadata?.default_model_ref;
  if (typeof value !== "string") {
    return null;
  }
  const normalized = normalizeModelRef(value);
  return normalized || null;
}

export function getResolvedDefaultModelRef(
  providerType: string,
  authType?: string,
  providerName = "",
  metadata?: Record<string, unknown> | null,
): string {
  void providerType;
  void authType;
  void providerName;
  const stored = readDefaultModelRef(metadata);
  return stored || "";
}

export function mergeDefaultModelRef(
  metadata: Record<string, unknown>,
  defaultModelRef: string,
): Record<string, unknown> {
  const next = { ...metadata };
  const normalized = normalizeModelRef(defaultModelRef);
  if (normalized) {
    next.default_model_ref = normalized;
  } else {
    delete next.default_model_ref;
  }
  return next;
}

export interface ModelRefSelectProps
  extends Omit<SelectHTMLAttributes<HTMLSelectElement>, "value" | "onChange"> {
  value: string;
  onChange: (value: string) => void;
  groups: ModelRefGroup[];
  placeholder?: string;
}

export function ModelRefSelect({
  value,
  onChange,
  groups,
  placeholder = "请选择模型",
  ...props
}: ModelRefSelectProps): JSX.Element {
  const [filter, setFilter] = useState("");

  const selectedLabel = useMemo(() => {
    for (const group of groups) {
      const match = group.options.find((option) => option.value === value);
      if (match) {
        return match.label;
      }
    }
    return value;
  }, [groups, value]);

  useEffect(() => {
    setFilter(selectedLabel || "");
  }, [selectedLabel]);

  const filteredGroups = useMemo(() => {
    const query = filter.trim().toLowerCase();
    if (!query) {
      return groups;
    }
    return groups
      .map((group) => ({
        ...group,
        options: group.options.filter((option) =>
          option.label.toLowerCase().includes(query) ||
          option.value.toLowerCase().includes(query),
        ),
      }))
      .filter((group) => group.options.length > 0);
  }, [filter, groups]);

  return (
    <div className="model-ref-select">
      <input
        type="text"
        className="model-ref-filter"
        value={filter}
        onChange={(event) => setFilter(event.target.value)}
        placeholder="输入关键字筛选模型"
        disabled={props.disabled}
      />
      <select
        {...props}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">
          {filteredGroups.length > 0 ? placeholder : "没有匹配的模型"}
        </option>
        {filteredGroups.map((group) => (
          <optgroup key={group.label} label={group.label}>
            {group.options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
    </div>
  );
}
