import { ProviderRecord } from "./types";

export type ProviderAuthMethod = "browser" | "device_code" | "api_key" | "bearer";
export type InteractiveFlowType =
  | "aws_iam"
  | "aws_sso_pkce"
  | "openai_codex"
  | "generic_oauth"
  | "browser_oauth";

export interface ProviderAuthOption {
  id: ProviderAuthMethod;
  label: string;
  disabled?: boolean;
  helpText?: string;
}

export interface ProviderInteractiveAuthRequest {
  flowType: InteractiveFlowType;
  extra?: Record<string, string>;
}

function readMetadataString(provider: ProviderRecord, key: string): string | null {
  const value = provider.auth_metadata?.[key];
  if (typeof value !== "string") {
    return null;
  }
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function isCodexLike(provider: ProviderRecord): boolean {
  const name = provider.name.toLowerCase();
  return provider.provider_type === "openai" || name.includes("codex");
}

function isClaudeLike(provider: ProviderRecord): boolean {
  const name = provider.name.toLowerCase();
  return provider.provider_type === "anthropic" || name.includes("claude");
}

function hasBrowserOauthMetadata(provider: ProviderRecord): boolean {
  return Boolean(
    readMetadataString(provider, "authorization_endpoint") &&
      readMetadataString(provider, "token_endpoint") &&
      readMetadataString(provider, "client_id"),
  );
}

function hasDeviceCodeMetadata(provider: ProviderRecord): boolean {
  return Boolean(
    readMetadataString(provider, "token_endpoint") &&
      readMetadataString(provider, "client_id"),
  );
}

export function defaultProviderAuthMethod(provider: ProviderRecord): ProviderAuthMethod {
  if (provider.auth_type === "api_key") {
    return "api_key";
  }
  if (provider.auth_type === "bearer") {
    return "bearer";
  }
  if (isCodexLike(provider)) {
    return "browser";
  }
  return "api_key";
}

export function getProviderAuthOptions(provider: ProviderRecord): ProviderAuthOption[] {
  if (isCodexLike(provider)) {
    return [
      { id: "browser", label: "浏览器登录" },
      { id: "device_code", label: "Device Code" },
      { id: "api_key", label: "API Key" },
    ];
  }

  const supportsBrowser = hasBrowserOauthMetadata(provider);
  const supportsDeviceCode = hasDeviceCodeMetadata(provider);

  if (isClaudeLike(provider)) {
    return [
      {
        id: "browser",
        label: "浏览器登录",
        disabled: !supportsBrowser,
        helpText: supportsBrowser ? undefined : "需要在 Auth Metadata 中提供 authorization_endpoint / token_endpoint / client_id",
      },
      {
        id: "device_code",
        label: "Device Code",
        disabled: !supportsDeviceCode,
        helpText: supportsDeviceCode ? undefined : "需要在 Auth Metadata 中提供 token_endpoint / client_id",
      },
      { id: "api_key", label: "API Key" },
      { id: "bearer", label: "Bearer Token" },
    ];
  }

  if (provider.auth_type === "oauth" || supportsBrowser || supportsDeviceCode) {
    return [
      {
        id: "browser",
        label: "浏览器登录",
        disabled: !supportsBrowser,
        helpText: supportsBrowser ? undefined : "当前 Provider 未配置浏览器 OAuth 元数据",
      },
      {
        id: "device_code",
        label: "Device Code",
        disabled: !supportsDeviceCode,
        helpText: supportsDeviceCode ? undefined : "当前 Provider 未配置 Device Code 元数据",
      },
      { id: "api_key", label: "API Key" },
      { id: "bearer", label: "Bearer Token" },
    ];
  }

  return [
    { id: "api_key", label: "API Key" },
    { id: "bearer", label: "Bearer Token" },
  ];
}

export function buildInteractiveAuthRequest(
  provider: ProviderRecord,
  method: ProviderAuthMethod,
): ProviderInteractiveAuthRequest | null {
  if (method === "browser" && isCodexLike(provider)) {
    return { flowType: "openai_codex", extra: { login_variant: "browser" } };
  }
  if (method === "device_code" && isCodexLike(provider)) {
    return { flowType: "openai_codex", extra: { login_variant: "device_code" } };
  }
  if (method === "browser" && hasBrowserOauthMetadata(provider)) {
    return {
      flowType: "browser_oauth",
      extra: {
        authorization_endpoint: readMetadataString(provider, "authorization_endpoint") || "",
        token_endpoint: readMetadataString(provider, "token_endpoint") || "",
        client_id: readMetadataString(provider, "client_id") || "",
        client_secret: readMetadataString(provider, "client_secret") || "",
        scope: readMetadataString(provider, "scope") || "",
      },
    };
  }
  if (method === "device_code" && hasDeviceCodeMetadata(provider)) {
    return {
      flowType: "generic_oauth",
      extra: {
        device_authorization_endpoint: readMetadataString(provider, "device_authorization_endpoint") || "",
        token_endpoint: readMetadataString(provider, "token_endpoint") || "",
        client_id: readMetadataString(provider, "client_id") || "",
        client_secret: readMetadataString(provider, "client_secret") || "",
        scope: readMetadataString(provider, "scope") || "",
      },
    };
  }
  return null;
}

export function authMethodToProviderAuthType(method: ProviderAuthMethod): "api_key" | "bearer" | null {
  if (method === "api_key") {
    return "api_key";
  }
  if (method === "bearer") {
    return "bearer";
  }
  return null;
}
