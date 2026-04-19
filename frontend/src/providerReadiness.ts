import { ProviderRecord } from "./types";

export function getProviderAuthLabel(provider: ProviderRecord): string {
  switch (provider.auth_status) {
    case "ready":
      return "已认证";
    case "refreshable":
      return "可自动刷新";
    case "expired":
      return "凭据已过期";
    default:
      return "未认证";
  }
}

export function shouldShowInteractiveAuth(provider: ProviderRecord): boolean {
  return provider.auth_status === "missing" || provider.auth_status === "expired";
}
