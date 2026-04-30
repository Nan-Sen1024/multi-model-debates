export type CollaborationMode =
  | "chat"
  | "brainstorm"
  | "code_collaboration"
  | "code_workspace"
  | "data_analysis"
  | "debate"
  | "werewolf"
  | "murder_mystery"
  | "undercover"
  | "mock_trial"
  | "role_play"
  | "socratic_dialogue"
  | "peer_review"
  | "mock_interview"
  | "story_chain"
  | "negotiation";

export type StreamEventType =
  | "ping"
  | "turn_start"
  | "phase_start"
  | "phase_end"
  | "chunk"
  | "reasoning_note"
  | "model_request"
  | "model_output"
  | "model_response"
  | "agent_plan"
  | "tool_call"
  | "tool_output"
  | "tool_result"
  | "state_write"
  | "turn_end"
  | "participant_error"
  | "round_end"
  | "drift_alert"
  | "compression"
  | "session_end"
  | "error";

export type StreamState = "idle" | "connecting" | "streaming" | "completed" | "failed";

export interface ParticipantConfig {
  custom_id: string;
  model_ref: string;
  provider_id?: string | null;
  role_desc?: string;
}

export interface SessionResponse {
  id: string;
  status: string;
  mode: CollaborationMode;
}

export interface SessionDetail {
  id: string;
  title: string;
  topic: string;
  mode: CollaborationMode;
  status: string;
  current_round: number;
  workspace?: WorkspaceConfigRecord | null;
  participants: Array<{
    id: string;
    custom_id: string;
    model_ref: string;
    provider_id?: string | null;
    role_desc?: string;
    is_active: boolean;
  }>;
}

export interface SessionListItem {
  id: string;
  title: string;
  topic: string;
  mode: CollaborationMode;
  status: string;
  current_round: number;
  updated_at: number;
  participant_count: number;
  last_message_preview: string;
}

export interface SessionMessageRecord {
  id: string;
  sender_id: string;
  message_type: string;
  content: string;
  is_masked: boolean;
  is_compressed: boolean;
  drift_score?: number | null;
  round_number: number;
  created_at: number;
}

export interface SessionSnapshot {
  topic: string;
  mode: CollaborationMode | string;
  participant_summaries: Record<string, string>;
  consensus_list: string[];
  key_events: string[];
}

export interface WorkspaceConfigRecord {
  root_path: string;
  display_name?: string | null;
  repo_fingerprint?: string | null;
  scan_excludes: string[];
  selected_paths: string[];
  index_status: string;
  last_scanned_at?: number | null;
  summary?: string | null;
  capabilities?: WorkspaceCapabilityManifest | null;
}

export interface WorkspaceTreeEntry {
  name: string;
  path: string;
  kind: "file" | "dir";
  children: WorkspaceTreeEntry[];
}

export interface SessionWorkspaceView extends WorkspaceConfigRecord {
  files: string[];
  tree: WorkspaceTreeEntry[];
}

export interface WorkspaceFileContentRecord {
  path: string;
  content: string;
  truncated: boolean;
}

export interface ProviderRecord {
  id: string;
  name: string;
  provider_type: string;
  base_url?: string;
  api_format: string;
  auth_type: string;
  auth_metadata: Record<string, unknown>;
  auth_status?: "ready" | "refreshable" | "expired" | "missing";
  auth_expires_at?: number | null;
  fallback_ids: string[];
  is_active: boolean;
}

export interface ChatMessage {
  id: string;
  senderId: string;
  type: "user" | "model" | "system" | "execution";
  content: string;
  round: number;
  driftScore?: number;
  status?: "streaming" | "done" | "warning" | "error";
  executionKind?: ExecutionEventRecord["kind"];
  executionPhase?: string;
  executionTitle?: string;
  executionDetail?: string;
}

export interface ExecutionEventRecord {
  id: string;
  event: Exclude<StreamEventType, "ping" | "chunk" | "compression" | "drift_alert">;
  correlationKey?: string;
  participantId?: string;
  round: number;
  summary: string;
  detail?: string;
  status: "running" | "done" | "error" | "warning" | "info";
  phase?: string;
  kind?: "phase" | "model" | "tool" | "output" | "state" | "note" | "turn" | "session";
  metadata?: Record<string, unknown>;
}

export interface SkillSourceConfig {
  path: string;
  source_type: string;
  label?: string | null;
  recursive: boolean;
  enabled: boolean;
}

export interface MCPServerConfig {
  name: string;
  transport: string;
  command?: string | null;
  args: string[];
  url?: string | null;
  env: Record<string, string>;
  tools_allowlist: string[];
  enabled: boolean;
}

export interface AgentProfileConfig {
  mode: string;
  max_steps: number;
  can_write: boolean;
  allowed_skills: string[];
  allowed_mcp_servers: string[];
  memory_scope: string;
}

export interface ParticipantCapabilityConfig {
  agent?: AgentProfileConfig | null;
  skills: string[];
  mcp_servers: string[];
}

export interface WorkspaceCapabilityManifest {
  skill_sources: SkillSourceConfig[];
  mcp_servers: MCPServerConfig[];
  agent_defaults: AgentProfileConfig;
  participant_overrides: Record<string, ParticipantCapabilityConfig>;
}

export interface StreamPayload {
  participant_id?: string;
  content?: string;
  round?: number;
  execution_mode?: string;
  phase?: string;
  target?: string;
  score?: number;
  action?: string;
  masked_count?: number;
  checkpoint_id?: string;
  reason?: string;
  summary?: string;
  input_message_count?: number;
  target_count?: number;
  file_count?: number;
  step?: number;
  code?: string;
  message?: string;
  provider_id?: string;
  provider_name?: string;
  auth_type?: string;
  remediation?: string;
  server_name?: string;
  tool_name?: string;
  arguments?: Record<string, unknown>;
  stream?: string;
  command?: string;
  cwd?: string;
  exit_code?: number;
  model_ref?: string;
  text?: string;
  ts?: number;
}

export interface AuthFlowState {
  authSessionId: string;
  verificationUri: string;
  userCode: string;
  expiresIn: number;
  status: "pending" | "completed" | "failed" | "expired" | "cancelled" | "awaiting_role";
  flowType: string;
  accounts?: Array<{ accountId: string; accountName: string; emailAddress: string }>;
  errorMessage?: string;
}
