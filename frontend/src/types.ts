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
  | "chunk"
  | "turn_end"
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
  type: "user" | "model" | "system";
  content: string;
  round: number;
  driftScore?: number;
  status?: "streaming" | "done" | "warning" | "error";
}

export interface StreamPayload {
  participant_id?: string;
  content?: string;
  round?: number;
  score?: number;
  action?: string;
  masked_count?: number;
  checkpoint_id?: string;
  reason?: string;
  summary?: string;
  code?: string;
  message?: string;
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
