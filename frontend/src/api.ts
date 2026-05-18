import {
  CollaborationMode,
  ParticipantConfig,
  ProviderRecord,
  SessionDetail,
  SessionListItem,
  SessionMessageRecord,
  SessionResponse,
  SessionSnapshot,
  SessionWorkspaceView,
  WorkspaceFileContentRecord,
  WorkspaceConfigRecord,
  WorkspaceCapabilityManifest,
  StreamPayload,
} from "./types";

const API_BASE = "/api";

function resolveSessionStreamBase(): string {
  const configured = process.env.REACT_APP_SSE_BASE_URL?.trim();
  if (configured) {
    return configured.replace(/\/+$/, "");
  }
  if (
    typeof window !== "undefined" &&
    window.location.protocol.startsWith("http") &&
    window.location.port === "3000"
  ) {
    return "http://127.0.0.1:8000";
  }
  return "";
}

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
  workspace?: WorkspaceConfigRecord | null;
}): Promise<SessionResponse> {
  return request<SessionResponse>("/sessions", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function getSession(sessionId: string): Promise<SessionDetail> {
  return request<SessionDetail>(`/sessions/${sessionId}`);
}

export async function getSessionWorkspace(
  sessionId: string,
): Promise<SessionWorkspaceView> {
  return request<SessionWorkspaceView>(`/sessions/${sessionId}/workspace`);
}

export async function getWorkspaceFileContent(
  sessionId: string,
  path: string,
): Promise<WorkspaceFileContentRecord> {
  return request<WorkspaceFileContentRecord>(
    `/sessions/${sessionId}/workspace/file?path=${encodeURIComponent(path)}`,
  );
}

export async function previewWorkspace(input: {
  root_path: string;
  scan_excludes: string[];
  capabilities?: WorkspaceCapabilityManifest | null;
}): Promise<SessionWorkspaceView> {
  return request<SessionWorkspaceView>("/workspace/preview", {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export async function updateSession(
  sessionId: string,
  payload: { title?: string | null },
): Promise<SessionDetail> {
  return request<SessionDetail>(`/sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function updateSessionWorkspaceCanWrite(
  sessionId: string,
  canWrite: boolean,
): Promise<SessionDetail> {
  return request<SessionDetail>(`/sessions/${sessionId}/workspace/can-write`, {
    method: "PATCH",
    body: JSON.stringify({ can_write: canWrite }),
  });
}

export async function appendSessionParticipant(
  sessionId: string,
  payload: ParticipantConfig,
): Promise<SessionDetail> {
  return request<SessionDetail>(`/sessions/${sessionId}/participants`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function appendSessionParticipants(
  sessionId: string,
  participants: ParticipantConfig[],
): Promise<SessionDetail> {
  return request<SessionDetail>(`/sessions/${sessionId}/participants/batch`, {
    method: "POST",
    body: JSON.stringify({ participants }),
  });
}

export async function deleteSession(
  sessionId: string,
): Promise<{ reason: string; summary: string }> {
  return request<{ reason: string; summary: string }>(`/sessions/${sessionId}`, {
    method: "DELETE",
  });
}

export async function listSessions(): Promise<SessionListItem[]> {
  const data = await request<unknown>("/sessions");
  return Array.isArray(data) ? (data as SessionListItem[]) : [];
}

export async function getSessionMessages(
  sessionId: string,
): Promise<SessionMessageRecord[]> {
  const data = await request<unknown>(`/sessions/${sessionId}/messages`);
  return Array.isArray(data) ? (data as SessionMessageRecord[]) : [];
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
  auth_metadata?: Record<string, unknown>;
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

export interface ModelCatalogDiscoverProviderPayload {
  name: string;
  provider_type: string;
  base_url?: string;
  api_format: string;
  auth_type: string;
  auth_value?: string;
  auth_metadata?: Record<string, unknown>;
  fallback_ids: string[];
}

export interface ModelCatalogDiscoverRequest {
  provider_id?: string;
  provider?: ModelCatalogDiscoverProviderPayload;
}

export interface ModelCatalogDiscoverResult {
  provider_id: string;
  provider_name: string;
  provider_type: string;
  models: string[];
  detected_at: number;
}

export async function discoverModelCatalog(
  payload: ModelCatalogDiscoverRequest,
): Promise<ModelCatalogDiscoverResult> {
  return request<ModelCatalogDiscoverResult>("/model-catalog/discover", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function openSessionStream(
  sessionId: string,
  onEvent: (event: string, payload: StreamPayload) => void,
): () => void {
  const streamBase = resolveSessionStreamBase();
  const source = new EventSource(
    `${streamBase}${API_BASE}/sessions/${sessionId}/stream`,
  );
  const terminalEvents = new Set(["round_end", "session_end", "error"]);
  let closed = false;
  let terminalEventSeen = false;
  let nonPingEventSeen = false;

  const bind = (eventName: string) => {
    source.addEventListener(eventName, (event) => {
      const messageEvent = event as MessageEvent;
      if (typeof messageEvent.data !== "string") return;
      try {
        const payload = JSON.parse(messageEvent.data) as StreamPayload;
        if (eventName !== "ping") {
          nonPingEventSeen = true;
        }
        if (terminalEvents.has(eventName)) {
          terminalEventSeen = true;
        }
        onEvent(eventName, payload);
      } catch {}
    });
  };

  [
    "ping",
    "turn_start",
    "phase_start",
    "phase_end",
    "research_search",
    "research_open_pages",
    "research_note",
    "chunk",
    "reasoning_note",
    "model_request",
    "model_output",
    "model_response",
    "agent_plan",
    "tool_call",
    "tool_output",
    "tool_result",
    "state_write",
    "provider_fallback",
    "turn_end",
    "participant_error",
    "round_end",
    "drift_alert",
    "compression",
    "session_end",
    "error",
  ].forEach(bind);

  source.onerror = () => {
    if (closed || terminalEventSeen) {
      return;
    }
    onEvent("error", {
      code: nonPingEventSeen ? "SSE_INTERRUPTED_AFTER_PROGRESS" : "SSE_CONNECTION_FAILED",
      message: nonPingEventSeen
        ? "SSE 连接在执行过程中中断，后端可能被热重载或重启。已刷新会话历史，请检查后端日志和最新工具输出。"
        : "SSE 连接中断，请检查后端服务状态。",
    });
  };

  return () => {
    closed = true;
    source.close();
  };
}

// ---------------------------------------------------------------------------
// 认证流程 API（Device_Code_Flow）
// ---------------------------------------------------------------------------

export interface AuthFlowStartPayload {
  flow_type: "aws_iam" | "aws_sso_pkce" | "openai_codex" | "generic_oauth" | "browser_oauth";
  // openai_codex / generic_oauth / browser_oauth
  token_endpoint?: string;
  authorization_endpoint?: string;
  device_authorization_endpoint?: string;
  client_id?: string;
  client_secret?: string;
  scope?: string;
  login_variant?: "browser" | "device_code";
  // aws_iam / aws_sso_pkce
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
  status: "pending" | "completed" | "failed" | "expired" | "cancelled" | "awaiting_role";
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

export async function cancelAuthFlow(
  providerId: string,
  authSessionId: string,
): Promise<AuthFlowStatusResult> {
  return request<AuthFlowStatusResult>(
    `/providers/${providerId}/auth/cancel/${authSessionId}`,
    { method: "POST" },
  );
}

export async function logoutProviderAuth(
  providerId: string,
): Promise<{ provider_id: string; status: string }> {
  return request<{ provider_id: string; status: string }>(
    `/providers/${providerId}/auth/logout`,
    { method: "POST" },
  );
}

export async function deleteProvider(providerId: string): Promise<{ deleted: string }> {
  return request<{ deleted: string }>(`/providers/${providerId}`, { method: "DELETE" });
}

export async function updateProvider(
  providerId: string,
  payload: {
    name: string; provider_type: string; base_url?: string;
    api_format: string; auth_type: string; auth_value?: string;
    auth_metadata?: Record<string, unknown>; fallback_ids: string[];
  },
): Promise<{ updated: string }> {
  return request<{ updated: string }>(`/providers/${providerId}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}
