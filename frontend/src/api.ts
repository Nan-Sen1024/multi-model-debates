import {
  CollaborationMode,
  ParticipantConfig,
  ProviderRecord,
  SessionDetail,
  SessionResponse,
  SessionSnapshot,
  StreamPayload,
} from "./types";

const API_BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
    ...init,
  });

  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json")
    ? await response.json().catch(() => null)
    : null;

  if (!response.ok) {
    const message =
      data?.error?.message ||
      data?.detail ||
      `Request failed with status ${response.status}`;
    throw new Error(message);
  }

  if (data === null) {
    throw new Error(
      `Expected JSON from ${path}, but received ${contentType || "an empty response"}`,
    );
  }

  return data as T;
}

export async function createSession(input: {
  topic: string;
  mode: CollaborationMode;
  participants: ParticipantConfig[];
}): Promise<SessionResponse> {
  return request<SessionResponse>("/sessions", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function getSession(sessionId: string): Promise<SessionDetail> {
  return request<SessionDetail>(`/sessions/${sessionId}`);
}

export async function sendUserMessage(
  sessionId: string,
  content: string,
): Promise<{ status: string }> {
  return request(`/sessions/${sessionId}/messages`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });
}

export async function getSnapshot(sessionId: string): Promise<SessionSnapshot> {
  return request<SessionSnapshot>(`/sessions/${sessionId}/snapshot`);
}

export async function patchSnapshot(
  sessionId: string,
  snapshot: Partial<SessionSnapshot>,
): Promise<SessionSnapshot> {
  return request<SessionSnapshot>(`/sessions/${sessionId}/snapshot`, {
    method: "PATCH",
    body: JSON.stringify(snapshot),
  });
}

export async function exportSessionHistory(
  sessionId: string,
): Promise<{ content: string }> {
  return request<{ content: string }>(`/sessions/${sessionId}/export`);
}

export async function listProviders(): Promise<ProviderRecord[]> {
  const data = await request<unknown>("/providers");
  return Array.isArray(data) ? (data as ProviderRecord[]) : [];
}

export async function createProvider(payload: {
  name: string;
  provider_type: string;
  base_url?: string;
  api_format: string;
  auth_type: string;
  auth_value?: string;
  fallback_ids: string[];
}): Promise<{ id: string }> {
  return request<{ id: string }>("/providers", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function healthCheckProvider(
  providerId: string,
): Promise<{ healthy: boolean }> {
  return request<{ healthy: boolean }>(`/providers/${providerId}/health`, {
    method: "POST",
  });
}

export async function discoverLocalModels(): Promise<{
  provider: string;
  models: string[];
  detected_at: number;
}> {
  const data = await request<{
    provider?: string;
    models?: string[] | null;
    detected_at?: number;
  }>("/providers/local/discover");
  return {
    provider: data.provider || "ollama",
    models: Array.isArray(data.models) ? data.models : [],
    detected_at: typeof data.detected_at === "number" ? data.detected_at : Date.now(),
  };
}

export function openSessionStream(
  sessionId: string,
  onEvent: (event: string, payload: StreamPayload) => void,
): () => void {
  const source = new EventSource(`${API_BASE}/sessions/${sessionId}/stream`);

  const bind = (eventName: string) => {
    source.addEventListener(eventName, (event) => {
      const payload = JSON.parse((event as MessageEvent).data) as StreamPayload;
      onEvent(eventName, payload);
    });
  };

  [
    "chunk",
    "turn_end",
    "drift_alert",
    "compression",
    "session_end",
    "error",
  ].forEach(bind);

  source.onerror = () => {
    onEvent("error", {
      message: "SSE 连接中断，请检查后端服务状态。",
    });
  };

  return () => source.close();
}

// ---------------------------------------------------------------------------
// 认证流程 API（Device_Code_Flow）
// ---------------------------------------------------------------------------

export interface AuthFlowStartPayload {
  flow_type: "aws_iam" | "openai_codex" | "generic_oauth";
  // openai_codex / generic_oauth
  token_endpoint?: string;
  client_id?: string;
  client_secret?: string;
  scope?: string;
  // aws_iam
  sso_start_url?: string;
  sso_region?: string;
}

export interface AuthFlowStartResult {
  auth_session_id: string;
  verification_uri: string;
  user_code: string;
  expires_in: number;
  interval: number;
  flow_type: string;
}

export interface AuthFlowStatusResult {
  auth_session_id: string;
  status: "pending" | "completed" | "failed" | "expired" | "awaiting_role";
  flow_type: string;
  accounts?: Array<{ accountId: string; accountName: string; emailAddress: string }>;
  error_message?: string;
}

export async function startAuthFlow(
  providerId: string,
  payload: AuthFlowStartPayload,
): Promise<AuthFlowStartResult> {
  return request<AuthFlowStartResult>(`/providers/${providerId}/auth/start`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function getAuthStatus(
  providerId: string,
  authSessionId: string,
): Promise<AuthFlowStatusResult> {
  return request<AuthFlowStatusResult>(
    `/providers/${providerId}/auth/status/${authSessionId}`,
  );
}

export async function bindAwsRole(
  providerId: string,
  authSessionId: string,
  accountId: string,
  roleName: string,
): Promise<{ auth_session_id: string; account_id: string; role_name: string; status: string }> {
  return request(`/providers/${providerId}/auth/bind-role/${authSessionId}`, {
    method: "POST",
    body: JSON.stringify({ account_id: accountId, role_name: roleName }),
  });
}

export async function deleteProvider(providerId: string): Promise<{ deleted: string }> {
  return request<{ deleted: string }>(`/providers/${providerId}`, { method: "DELETE" });
}

export async function updateProvider(
  providerId: string,
  payload: {
    name: string; provider_type: string; base_url?: string;
    api_format: string; auth_type: string; auth_value?: string; fallback_ids: string[];
  },
): Promise<{ updated: string }> {
  return request<{ updated: string }>(`/providers/${providerId}`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}
