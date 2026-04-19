import { SessionListItem } from "./types";

const LAST_SESSION_ID_KEY = "mmdebate.lastSessionId";
const ACTIVE_TAB_KEY = "mmdebate.activeTab";
const COMPOSER_DRAFTS_KEY = "mmdebate.composerDrafts";

export function pickRestoredSessionId(
  sessions: SessionListItem[],
  lastSessionId: string | null,
): string | null {
  if (lastSessionId && sessions.some((session) => session.id === lastSessionId)) {
    return lastSessionId;
  }
  return sessions[0]?.id || null;
}

export function loadLastSessionId(): string | null {
  return localStorage.getItem(LAST_SESSION_ID_KEY);
}

export function saveLastSessionId(sessionId: string | null): void {
  if (!sessionId) {
    localStorage.removeItem(LAST_SESSION_ID_KEY);
    return;
  }
  localStorage.setItem(LAST_SESSION_ID_KEY, sessionId);
}

export function loadActiveTab(): number | null {
  const raw = localStorage.getItem(ACTIVE_TAB_KEY);
  if (raw === null) return null;
  const parsed = Number(raw);
  return Number.isInteger(parsed) ? parsed : null;
}

export function saveActiveTab(tabIndex: number): void {
  localStorage.setItem(ACTIVE_TAB_KEY, String(tabIndex));
}

function readComposerDrafts(): Record<string, string> {
  const raw = localStorage.getItem(COMPOSER_DRAFTS_KEY);
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw) as unknown;
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, string>)
      : {};
  } catch {
    return {};
  }
}

function writeComposerDrafts(drafts: Record<string, string>): void {
  localStorage.setItem(COMPOSER_DRAFTS_KEY, JSON.stringify(drafts));
}

export function loadComposerDraft(sessionId: string): string {
  return readComposerDrafts()[sessionId] || "";
}

export function saveComposerDraft(sessionId: string, draft: string): void {
  const drafts = readComposerDrafts();
  drafts[sessionId] = draft;
  writeComposerDrafts(drafts);
}
