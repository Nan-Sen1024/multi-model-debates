export type CollaborationMode =
  | "chat"
  | "brainstorm"
  | "code_collaboration"
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
  | "chunk"
  | "turn_end"
  | "drift_alert"
  | "compression"
  | "session_end"
  | "error";

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
  topic: string;
  mode: CollaborationMode;
  status: string;
  current_round: number;
  participants: Array<{
    id: string;
    custom_id: string;
    model_ref: string;
    provider_id?: string | null;
    role_desc?: string;
    is_active: boolean;
  }>;
}

export interface SessionSnapshot {
  topic: string;
  mode: CollaborationMode | string;
  participant_summaries: Record<string, string>;
  consensus_list: string[];
  key_events: string[];
}

export interface ProviderRecord {
  id: string;
  name: string;
  provider_type: string;
  base_url?: string;
  api_format: string;
  auth_type: string;
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
  status: "pending" | "completed" | "failed" | "expired" | "awaiting_role";
  flowType: string;
  accounts?: Array<{ accountId: string; accountName: string; emailAddress: string }>;
  errorMessage?: string;
}
