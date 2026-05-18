import { ProviderRecord } from "./types";

export interface ProviderStatusSummary {
  authLabel: string;
  routeLabel: string;
  routeTone: "ok" | "warning" | "error" | "neutral";
  diagnosticTimeLabel: string | null;
  diagnosticReasonLabel: string | null;
  diagnosticSummary: string | null;
  diagnosticDetail: string | null;
  currentRouteLabel: string | null;
  nextRouteLabel: string | null;
  fallbackTopologyLabel: string | null;
  recentEvents: string[];
  showInteractiveAuth: boolean;
}

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

export function getProviderRouteLabel(provider: ProviderRecord): string {
  const diagnostic = provider.last_diagnostic;
  if (diagnostic?.fallback_provider_name) {
    return "Fallback 生效中";
  }
  if (diagnostic?.healthy === true) {
    return "主路正常";
  }
  if (diagnostic?.healthy === false) {
    return "主路降级";
  }
  if (provider.auth_status === "missing" || provider.auth_status === "expired") {
    return "未就绪";
  }
  return "待验证";
}

export function getProviderRouteTone(provider: ProviderRecord): "ok" | "warning" | "error" | "neutral" {
  const diagnostic = provider.last_diagnostic;
  if (diagnostic?.fallback_provider_name) {
    return "warning";
  }
  if (diagnostic?.healthy === true) {
    return "ok";
  }
  if (diagnostic?.healthy === false) {
    return "error";
  }
  if (provider.auth_status === "missing" || provider.auth_status === "expired") {
    return "error";
  }
  return "neutral";
}

export function getProviderDiagnosticTimeLabel(provider: ProviderRecord): string | null {
  const checkedAt = provider.last_diagnostic?.checked_at;
  if (typeof checkedAt !== "number") {
    return null;
  }
  const prefix = provider.last_diagnostic?.fallback_provider_name ? "最近切换" : "最近诊断";
  return `${prefix}: ${formatProviderTimestamp(checkedAt)}`;
}

export function getProviderDiagnosticReasonLabel(provider: ProviderRecord): string | null {
  const code = provider.last_diagnostic?.code;
  return code ? `原因: ${code}` : null;
}

export function getProviderRecentEvents(provider: ProviderRecord): string[] {
  const history = provider.last_diagnostic?.history;
  if (!Array.isArray(history) || history.length === 0) {
    return [];
  }
  return history
    .slice(-3)
    .reverse()
    .map((event) => {
      const time = typeof event.checked_at === "number"
        ? formatProviderTimestamp(event.checked_at)
        : null;
      const label = event.summary || event.message || event.code || "事件";
      return time ? `${time} ${label}` : label;
    });
}

export function buildProviderStatusSummary(
  provider: ProviderRecord,
  providersById?: Map<string, ProviderRecord>,
): ProviderStatusSummary {
  const diagnostic = provider.last_diagnostic;
  const authLabel = getProviderAuthLabel(provider);
  const routeLabel = getProviderRouteLabel(provider);
  const routeTone = getProviderRouteTone(provider);
  const diagnosticTimeLabel = getProviderDiagnosticTimeLabel(provider);
  const diagnosticReasonLabel = getProviderDiagnosticReasonLabel(provider);
  const recentEvents = getProviderRecentEvents(provider);
  const showInteractiveAuth = shouldShowInteractiveAuth(provider);

  const fallbackNames = provider.fallback_ids
    .map((fallbackId) => providersById?.get(fallbackId)?.name || fallbackId)
    .filter(Boolean);

  const effectiveFallbackName = diagnostic?.fallback_provider_name || fallbackNames[0] || null;
  const currentRouteLabel = effectiveFallbackName
    ? `${provider.name} -> ${effectiveFallbackName}`
    : null;
  const nextRouteLabel = effectiveFallbackName
    ? effectiveFallbackName
    : fallbackNames.length > 0
      ? fallbackNames[0]
      : null;
  const fallbackTopologyLabel = fallbackNames.length > 0
    ? `${provider.name} -> ${fallbackNames.join(" -> ")}`
    : null;

  return {
    authLabel,
    routeLabel,
    routeTone,
    diagnosticTimeLabel,
    diagnosticReasonLabel,
    diagnosticSummary: diagnostic?.summary || (diagnostic?.healthy ? "最近检查通过" : null),
    diagnosticDetail: diagnostic?.message || null,
    currentRouteLabel,
    nextRouteLabel,
    fallbackTopologyLabel,
    recentEvents,
    showInteractiveAuth,
  };
}

function formatProviderTimestamp(value: number): string {
  const normalized = value < 1_000_000_000_000 ? value * 1000 : value;
  return new Date(normalized).toLocaleString("zh-CN", { hour12: false });
}
