import React, {
  FormEvent,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  appendSessionParticipants,
  bindAwsRole,
  cancelAuthFlow,
  createProvider,
  createSession,
  deleteSession,
  deleteProvider,
  exportSessionHistory,
  getAuthStatus,
  getSession,
  getSessionMessages,
  getSessionWorkspace,
  getSnapshot,
  discoverModelCatalog,
  healthCheckProvider,
  listSessions,
  listProviders,
  logoutProviderAuth,
  openSessionStream,
  patchSnapshot,
  sendUserMessage,
  startAuthFlow,
  updateSession,
  updateSessionWorkspaceCanWrite,
  updateProvider,
} from "./api";
import {
  API_FORMATS,
  AUTH_TYPES,
  DEFAULT_TASK_TEMPLATE_ID,
  MODE_OPTIONS,
  PRIMARY_TASK_TEMPLATES,
  PROVIDER_TYPES,
} from "./modeOptions";
import {
  buildDraftModelGroups,
  buildParticipantModelGroups,
  getDefaultModelRefForProvider,
  getResolvedDefaultModelRef,
  mergeDefaultModelRef,
  ModelRefSelect,
  formatParticipantModelSelection,
  parseParticipantModelSelection,
  resolveParticipantModelSelection,
} from "./modelCatalog";
import type { ProviderModelCatalog } from "./modelCatalog";
import {
  authMethodToProviderAuthType,
  buildInteractiveAuthRequest,
  defaultProviderAuthMethod,
  getProviderAuthOptions,
  ProviderAuthMethod,
} from "./providerAuthOptions";
import {
  buildProviderStatusSummary,
  shouldShowInteractiveAuth,
} from "./providerReadiness";
import {
  loadActiveTab,
  loadComposerDraft,
  loadLastSessionId,
  pickRestoredSessionId,
  saveActiveTab,
  saveComposerDraft,
  saveLastSessionId,
} from "./sessionPersistence";
import { ExecutionProgressPanel } from "./ExecutionProgress";
import { applyStreamEvent, SessionStreamViewState } from "./sessionStream";
import {
  buildWorkspaceCapabilitiesFromDraft,
  createEmptyWorkspaceMCPDraft,
  createEmptyWorkspaceParticipantOverrideDraft,
  WorkspaceCreatePanel,
  WorkspaceDraftState,
  WorkspaceDraftUpdate,
  WorkspaceSessionPanel,
} from "./WorkspaceMode";
import {
  applyWorkspaceTeamPreset,
  WorkspaceTaskPresetRecommendation,
  WorkspaceTeamPresetRecommendation,
} from "./workspacePresets";
import {
  AuthFlowState,
  ChatMessage,
  CollaborationMode,
  ParticipantConfig,
  ProviderRecord,
  SessionDetail,
  SessionListItem,
  SessionMessageRecord,
  SessionSnapshot,
  SessionWorkspaceView,
  ExecutionEventRecord,
  StreamPayload,
  StreamState,
} from "./types";

// ─── Mode icons ──────────────────────────────────────────────────────────────
const MODE_ICONS: Record<string, string> = {
  chat: "💬", brainstorm: "🧠", code_collaboration: "💻", code_workspace: "🧑‍💻", data_analysis: "📊",
  debate: "⚔️", werewolf: "🐺", murder_mystery: "🔍", undercover: "🕵️",
  mock_trial: "⚖️", role_play: "🎭", socratic_dialogue: "🏛️", peer_review: "📝",
  mock_interview: "🎤", story_chain: "📖", negotiation: "🤝",
};

const DEFAULT_PRIMARY_TEMPLATE = PRIMARY_TASK_TEMPLATES[0];
const TEMPLATE_DEFAULT_TOPICS = new Set(PRIMARY_TASK_TEMPLATES.map((template) => template.defaultTopic));

const initialWorkspaceDraft: WorkspaceDraftState = {
  rootPath: "",
  displayName: "",
  selectedPaths: "",
  scanExcludes: "",
  skillSources: "",
  mcpServers: [createEmptyWorkspaceMCPDraft()],
  agent: {
    mode: "tool_loop",
    maxSteps: "6",
    canWrite: false,
    allowedSkills: "",
    allowedMcpServers: "",
    memoryScope: "workspace_shared",
  },
  participantOverrides: {
    Model_A: createEmptyWorkspaceParticipantOverrideDraft(),
    Model_B: createEmptyWorkspaceParticipantOverrideDraft(),
  },
};

type ProviderDraft = {
  name: string;
  provider_type: string;
  base_url: string;
  api_format: string;
  auth_type: string;
  auth_value: string;
  fallback_ids: string;
  auth_metadata: string;
  default_model_ref: string;
};

const EMPTY_PROVIDER_DRAFT: ProviderDraft = {
  name: "",
  provider_type: "openai",
  base_url: "",
  api_format: API_FORMATS[0],
  auth_type: "api_key",
  auth_value: "",
  fallback_ids: "",
  auth_metadata: "{}",
  default_model_ref: "",
};

function remapWorkspaceParticipantOverrides(
  previousOverrides: WorkspaceDraftState["participantOverrides"],
  previousParticipants: ParticipantConfig[],
  nextParticipants: ParticipantConfig[],
): WorkspaceDraftState["participantOverrides"] {
  const remapped: WorkspaceDraftState["participantOverrides"] = {};
  nextParticipants.forEach((participant, index) => {
    const nextAlias = participant.custom_id.trim() || `Model_${index + 1}`;
    const previousAlias = previousParticipants[index]?.custom_id.trim() || nextAlias;
    remapped[nextAlias] =
      previousOverrides[nextAlias] ||
      previousOverrides[previousAlias] ||
      createEmptyWorkspaceParticipantOverrideDraft();
  });
  return remapped;
}

function createEmptySessionParticipantDraft(): ParticipantConfig {
  return {
    custom_id: "",
    model_ref: "",
    provider_id: undefined,
    role_desc: "",
  };
}

interface MentionRange {
  start: number;
  end: number;
  query: string;
}

interface MentionCandidate {
  id: string;
  customId: string;
  modelRef: string;
  roleDesc?: string;
}

function findActiveMentionRange(value: string, caretIndex: number): MentionRange | null {
  const prefix = value.slice(0, caretIndex);
  const asciiAtIndex = prefix.lastIndexOf("@");
  const fullwidthAtIndex = prefix.lastIndexOf("＠");
  const atIndex = Math.max(asciiAtIndex, fullwidthAtIndex);
  if (atIndex < 0) {
    return null;
  }
  const query = prefix.slice(atIndex + 1);
  if (/[\s@＠]/.test(query)) {
    return null;
  }
  const charBeforeAt = atIndex > 0 ? prefix[atIndex - 1] : "";
  if (charBeforeAt && /[A-Za-z0-9._%+-]/.test(charBeforeAt)) {
    return null;
  }
  return {
    start: atIndex,
    end: caretIndex,
    query,
  };
}

function buildMentionCandidates(
  participants: SessionDetail["participants"],
  query: string,
): MentionCandidate[] {
  const normalizedQuery = query.trim().toLowerCase();
  return participants
    .filter((participant) => participant.is_active !== false && participant.custom_id.trim())
    .map((participant) => ({
      id: participant.id,
      customId: participant.custom_id.trim(),
      modelRef: participant.model_ref,
      roleDesc: participant.role_desc || undefined,
    }))
    .filter((candidate) => {
      if (!normalizedQuery) {
        return true;
      }
      return [candidate.customId, candidate.modelRef, candidate.roleDesc || ""]
        .some((value) => value.toLowerCase().includes(normalizedQuery));
    });
}

function hasWorkspaceMention(value: string): boolean {
  return /(^|[^A-Za-z0-9._%+-])[@＠][A-Za-z0-9_.-]+/.test(value);
}

function isResearchStepHeadline(line: string): boolean {
  return /^(搜索到\s+\d+\s+个网页|浏览\s+\d+\s+个页面|查看全部)$/.test(line.trim());
}

function buildResearchStepCards(lines: string[]): React.ReactNode[] | null {
  const cards: Array<{
    title: string;
    description?: string;
    items: string[];
  }> = [];

  let index = 0;
  while (index < lines.length) {
    const trimmed = lines[index].trim();
    if (!isResearchStepHeadline(trimmed)) {
      index += 1;
      continue;
    }

    const card = {
      title: trimmed,
      description: undefined as string | undefined,
      items: [] as string[],
    };
    index += 1;

    while (index < lines.length) {
      const next = lines[index].trim();
      if (!next) {
        index += 1;
        break;
      }
      if (isResearchStepHeadline(next)) {
        break;
      }
      if (!card.description) {
        card.description = next;
      } else {
        card.items.push(next);
      }
      index += 1;
    }

    cards.push(card);
  }

  if (cards.length === 0) {
    return null;
  }

  return cards.map((card, cardIndex) => (
    <section key={`research-${cardIndex}`} className="research-step-card">
      <div className="research-step-head">
        <span className="research-step-badge">研究步骤</span>
        <strong>{card.title}</strong>
      </div>
      {card.description ? <p className="research-step-description">{card.description}</p> : null}
      {card.items.length > 0 ? (
        <div className="research-step-list">
          {card.items.map((item, itemIndex) => (
            <span key={`research-item-${cardIndex}-${itemIndex}`} className="research-step-item">{item}</span>
          ))}
        </div>
      ) : null}
    </section>
  ));
}

function renderStructuredText(content: string): React.ReactNode {
  const normalized = content.replace(/\r\n/g, "\n");
  const lines = normalized.split("\n");
  const researchCards = buildResearchStepCards(lines);
  if (researchCards) {
    return researchCards;
  }
  const blocks: React.ReactNode[] = [];
  let paragraphLines: string[] = [];
  let listItems: string[] = [];
  let codeLines: string[] = [];
  let codeLanguage = "";
  let inCodeBlock = false;

  const flushParagraph = () => {
    if (paragraphLines.length === 0) {
      return;
    }
    blocks.push(
      <p key={`p-${blocks.length}`} className="structured-paragraph">
        {paragraphLines.join("\n")}
      </p>,
    );
    paragraphLines = [];
  };

  const flushList = () => {
    if (listItems.length === 0) {
      return;
    }
    blocks.push(
      <ul key={`ul-${blocks.length}`} className="structured-list">
        {listItems.map((item, index) => (
          <li key={`li-${index}`}>{item}</li>
        ))}
      </ul>,
    );
    listItems = [];
  };

  const flushCode = () => {
    blocks.push(
      <pre key={`code-${blocks.length}`} className="structured-code-block">
        <code data-language={codeLanguage || ""}>{codeLines.join("\n")}</code>
      </pre>,
    );
    codeLines = [];
    codeLanguage = "";
  };

  for (const rawLine of lines) {
    const line = rawLine;
    const trimmed = line.trim();

    if (trimmed.startsWith("```")) {
      flushParagraph();
      flushList();
      if (inCodeBlock) {
        flushCode();
        inCodeBlock = false;
      } else {
        inCodeBlock = true;
        codeLanguage = trimmed.slice(3).trim();
      }
      continue;
    }

    if (inCodeBlock) {
      codeLines.push(line);
      continue;
    }

    if (!trimmed) {
      flushParagraph();
      flushList();
      continue;
    }

    const listMatch = line.match(/^\s*[-*]\s+(.+)$/);
    if (listMatch) {
      flushParagraph();
      listItems.push(listMatch[1]);
      continue;
    }

    flushList();
    paragraphLines.push(line);
  }

  if (inCodeBlock) {
    flushCode();
  }
  flushParagraph();
  flushList();

  if (blocks.length === 0) {
    return content;
  }
  return blocks;
}

function summarizeExecutionBubbleDetail(
  detail: string,
  maxLines = 3,
  maxChars = 180,
): { preview: string; collapsible: boolean } {
  const lines = detail.split(/\r?\n/);
  const truncatedByLines = lines.length > maxLines;
  const highlightedIndex = lines.findIndex((line) =>
    /^(问题摘要：|命令失败：)/.test(line) || /未找到 .+ 命令/.test(line),
  );
  const previewSource =
    highlightedIndex >= 0
      ? lines.slice(highlightedIndex, highlightedIndex + maxLines)
      : lines.slice(0, maxLines);
  let preview = previewSource.join("\n");
  const truncatedByChars = preview.length > maxChars;
  if (truncatedByChars) {
    preview = `${preview.slice(0, maxChars).trimEnd()}…`;
  } else if (truncatedByLines) {
    preview = `${preview}\n…`;
  }
  return {
    preview,
    collapsible: truncatedByLines || detail.length > maxChars,
  };
}

function labelForExecutionBubbleKind(kind?: ExecutionEventRecord["kind"]): string {
  if (kind === "phase") {
    return "阶段";
  }
  if (kind === "model") {
    return "模型";
  }
  if (kind === "tool") {
    return "工具";
  }
  if (kind === "output") {
    return "输出";
  }
  if (kind === "state") {
    return "更新";
  }
  if (kind === "note") {
    return "说明";
  }
  if (kind === "turn") {
    return "本轮";
  }
  if (kind === "session") {
    return "运行";
  }
  return "说明";
}

interface ParsedCommandDetail {
  command?: string;
  cwd?: string;
  exitCode?: string;
  stdout?: string;
  stderr?: string;
  highlights: string[];
}

interface OutputPreviewSummary {
  preview?: string;
  lineCount: number;
  hiddenLineCount: number;
}

interface RenderExecutionBubbleDetailOptions {
  expandedRawOutput?: boolean;
}

interface ParsedFileExecutionDetail {
  label: string;
  action: "read" | "browse" | "write_new" | "write_overwrite" | "write";
  path?: string;
  note?: string;
  preview?: string;
}

interface ParsedResearchExecutionDetail {
  query?: string;
  resultCount?: string;
  pageCount?: string;
  items: string[];
  note?: string;
}

interface ParsedAlertExecutionDetail {
  routes: string[];
  diagnostics: string[];
}

interface ParsedFileDetail {
  path?: string;
  highlights: string[];
  bodyLines: string[];
}

interface ParsedResearchDetail {
  query?: string;
  resultCount?: string;
  pageCount?: string;
  highlights: string[];
  items: string[];
}

interface ParsedAlertDetail {
  primaryProvider?: string;
  fallbackProvider?: string;
  provider?: string;
  authType?: string;
  code?: string;
  highlights: string[];
  bodyLines: string[];
}

function parseStructuredCommandDetail(detail?: string | null): ParsedCommandDetail | null {
  if (!detail) {
    return null;
  }
  const lines = detail.split(/\r?\n/);
  const highlights: string[] = [];
  let command: string | undefined;
  let cwd: string | undefined;
  let exitCode: string | undefined;
  let stdout: string | undefined;
  let stderr: string | undefined;

  let section: "stdout" | "stderr" | null = null;
  const stdoutLines: string[] = [];
  const stderrLines: string[] = [];

  for (const rawLine of lines) {
    const line = rawLine.trimEnd();
    if (/^(问题摘要：|建议：|命令失败：)/.test(line)) {
      highlights.push(line);
      section = null;
      continue;
    }
    if (line.startsWith("命令：")) {
      command = line.slice("命令：".length).trim();
      section = null;
      continue;
    }
    if (line.startsWith("目录：")) {
      cwd = line.slice("目录：".length).trim();
      section = null;
      continue;
    }
    if (line.startsWith("退出码：")) {
      exitCode = line.slice("退出码：".length).trim();
      section = null;
      continue;
    }
    if (line === "标准输出：") {
      section = "stdout";
      continue;
    }
    if (line === "标准错误：") {
      section = "stderr";
      continue;
    }
    if (section === "stdout") {
      stdoutLines.push(rawLine);
      continue;
    }
    if (section === "stderr") {
      stderrLines.push(rawLine);
    }
  }

  stdout = stdoutLines.join("\n").trim() || undefined;
  stderr = stderrLines.join("\n").trim() || undefined;

  if (!command && !cwd && !exitCode && !stdout && !stderr && highlights.length === 0) {
    return null;
  }

  return {
    command,
    cwd,
    exitCode,
    stdout,
    stderr,
    highlights,
  };
}

function summarizeRawOutput(detail?: string | null, maxLines = 3, maxChars = 180): string | undefined {
  if (!detail) {
    return undefined;
  }
  const lines = detail
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length === 0) {
    return undefined;
  }
  const preview = lines.slice(0, maxLines).join("\n");
  return preview.length > maxChars ? `${preview.slice(0, maxChars).trimEnd()}…` : preview;
}

function summarizeOutputPreview(detail?: string | null, maxLines = 3, maxChars = 180): OutputPreviewSummary {
  if (!detail) {
    return {
      lineCount: 0,
      hiddenLineCount: 0,
    };
  }
  const lines = detail
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length === 0) {
    return {
      lineCount: 0,
      hiddenLineCount: 0,
    };
  }
  const preview = lines.slice(0, maxLines).join("\n");
  return {
    preview: preview.length > maxChars ? `${preview.slice(0, maxChars).trimEnd()}…` : preview,
    lineCount: lines.length,
    hiddenLineCount: Math.max(0, lines.length - Math.min(lines.length, maxLines)),
  };
}

function summarizeStderrOutput(detail?: string | null): string | undefined {
  if (!detail) {
    return undefined;
  }
  const lines = detail
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length === 0) {
    return undefined;
  }
  return lines[0];
}

function normalizeExecutionText(value?: string | null): string {
  return (value || "").replace(/\r\n/g, "\n").trim();
}

function inferFileActionKind(title: string, detail?: string | null, toolName?: string): ParsedFileExecutionDetail["action"] {
  const normalizedTitle = normalizeExecutionText(title);
  const normalizedDetail = normalizeExecutionText(detail);
  const searchableText = `${normalizedTitle}\n${normalizedDetail}`;

  if (toolName === "read_file" || /读取文件|已读取文件/.test(searchableText)) {
    return "read";
  }
  if (toolName === "list_files" || /浏览目录|已列出目录|列出目录|目录列表/.test(searchableText)) {
    return "browse";
  }
  if (toolName === "write_file" || /写入文件|已写入文件/.test(searchableText)) {
    if (/(新建|创建|首次写入|create(?:d)?|new file|file created|不存在|尚未存在)/i.test(searchableText)) {
      return "write_new";
    }
    if (/(覆盖|overwrite|replace|替换|更新现有|已有文件|已存在|append)/i.test(searchableText)) {
      return "write_overwrite";
    }
    return "write";
  }
  if (/(新建|创建|首次写入|create(?:d)?|new file|file created)/i.test(searchableText)) {
    return "write_new";
  }
  if (/(覆盖|overwrite|replace|替换|更新现有|已有文件|已存在)/i.test(searchableText)) {
    return "write_overwrite";
  }
  return "write";
}

function fileActionLabel(action: ParsedFileExecutionDetail["action"]): string {
  if (action === "read") {
    return "文件读取";
  }
  if (action === "browse") {
    return "目录浏览";
  }
  if (action === "write_new") {
    return "新建文件";
  }
  if (action === "write_overwrite") {
    return "覆盖写入";
  }
  return "文件写入";
}

function fileActionStateLabel(action: ParsedFileExecutionDetail["action"]): string {
  if (action === "read") {
    return "已读取";
  }
  if (action === "browse") {
    return "已列出";
  }
  if (action === "write_new") {
    return "新建完成";
  }
  if (action === "write_overwrite") {
    return "已覆盖";
  }
  return "已写入";
}

function fileActionBadge(action: ParsedFileExecutionDetail["action"]): string {
  if (action === "read") {
    return "read";
  }
  if (action === "browse") {
    return "listed";
  }
  if (action === "write_new") {
    return "created";
  }
  if (action === "write_overwrite") {
    return "updated";
  }
  return "written";
}

function fileActionFieldLabel(action: ParsedFileExecutionDetail["action"], field: "path" | "note" | "preview"): string {
  if (field === "path") {
    return action === "browse" ? "目录路径" : "目标路径";
  }
  if (field === "note") {
    if (action === "read") {
      return "读取说明";
    }
    if (action === "browse") {
      return "目录说明";
    }
    return "动作说明";
  }
  if (action === "browse") {
    return "目录摘要";
  }
  if (action === "read") {
    return "内容摘要";
  }
  return "写入摘要";
}

function firstNonEmptyLine(detail?: string | null): string | undefined {
  if (!detail) {
    return undefined;
  }
  return detail
    .split(/\r?\n/)
    .map((line) => line.trim())
    .find(Boolean);
}

function parseFileExecutionDetail(title: string, detail?: string | null): ParsedFileExecutionDetail | null {
  const normalizedTitle = normalizeExecutionText(title);
  const normalizedDetail = normalizeExecutionText(detail);
  const pathFromTitle = normalizedTitle.match(/(?:读取文件|已读取文件|写入文件|已写入文件|浏览目录|已列出目录|新建文件|覆盖写入)\s+(.+)$/)?.[1]?.trim();
  const lines = (detail || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const pathLine =
    lines.find((line) => line.startsWith("路径：")) ||
    lines.find((line) => line.startsWith("path=")) ||
    lines.find((line) => /^Wrote \d+ characters to /.test(line)) ||
    lines.find((line) => /^written to /.test(line)) ||
    lines.find((line) => /^updated /.test(line));
  let resolvedPath = pathFromTitle;
  if (pathLine?.startsWith("路径：")) {
    resolvedPath = pathLine.slice("路径：".length).trim();
  } else if (pathLine?.startsWith("path=")) {
    resolvedPath = pathLine.slice("path=".length).trim();
  } else if (pathLine) {
    const writeMatch = pathLine.match(/to\s+(.+)$/);
    if (writeMatch?.[1]) {
      resolvedPath = writeMatch[1].trim();
    }
  }

  const note = lines.find((line) =>
    line !== pathLine &&
    line !== resolvedPath &&
    !/^line \d+/i.test(line) &&
    !/^(新建文件|覆盖写入|文件写入|文件读取|目录浏览)$/i.test(line),
  );
  const previewLines = lines
    .filter((line) =>
      line !== pathLine &&
      line !== note &&
      !/^(新建文件|覆盖写入|文件写入|文件读取|目录浏览)$/i.test(line),
    )
    .slice(0, 3);
  const preview = previewLines.length > 0 ? previewLines.join("\n") : undefined;
  const action = inferFileActionKind(normalizedTitle, normalizedDetail);

  if (!resolvedPath && !note && !preview) {
    return null;
  }

  return {
    label: fileActionLabel(action),
    action,
    path: resolvedPath,
    note,
    preview,
  };
}

function parseResearchExecutionDetail(detail?: string | null): ParsedResearchExecutionDetail | null {
  if (!detail) {
    return null;
  }
  const lines = detail
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const result: ParsedResearchExecutionDetail = {
    items: [],
  };

  for (const line of lines) {
    if (/^(搜索到\s+\d+\s+个网页|浏览\s+\d+\s+个页面|研究补充说明)$/.test(line)) {
      result.items.push(line);
      continue;
    }
    if (line.startsWith("查询：")) {
      result.query = line.slice("查询：".length).trim();
      continue;
    }
    if (line.startsWith("结果数：")) {
      result.resultCount = line.slice("结果数：".length).trim();
      continue;
    }
    if (line.startsWith("页面数：")) {
      result.pageCount = line.slice("页面数：".length).trim();
      continue;
    }
    if (!result.note) {
      result.note = line;
      continue;
    }
    result.items.push(line);
  }

  if (!result.query && !result.resultCount && !result.pageCount && !result.note && result.items.length === 0) {
    return null;
  }
  return result;
}

function parseAlertExecutionDetail(detail?: string | null): ParsedAlertExecutionDetail | null {
  if (!detail) {
    return null;
  }
  const lines = detail
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const routes = lines.filter((line) => line.startsWith("主 Provider：") || line.startsWith("Fallback Provider："));
  const diagnostics = lines.filter((line) => !routes.includes(line));
  if (routes.length === 0 && diagnostics.length === 0) {
    return null;
  }
  return {
    routes,
    diagnostics,
  };
}

function renderExecutionCardHeader(label: string): React.ReactNode {
  return <div className="execution-bubble-section-head">{label}</div>;
}

function renderFileExecutionBubble(title: string, detail?: string | null): React.ReactNode {
  const parsed = parseFileExecutionDetail(title, detail);
  if (!parsed) {
    return detail ? <pre className="execution-bubble-detail">{detail}</pre> : null;
  }
  return (
    <div className="execution-bubble-structured execution-bubble-file-card" data-file-action={parsed.action}>
      {renderExecutionCardHeader(parsed.label)}
      <div className="execution-bubble-file-status-row">
        <div className="execution-bubble-highlight execution-bubble-file-state">
          {fileActionStateLabel(parsed.action)}
        </div>
        <div className="execution-bubble-file-badge">{fileActionBadge(parsed.action)}</div>
      </div>
      <div className="execution-bubble-meta-grid">
        {parsed.path ? (
          <div className="execution-bubble-meta-item">
            <span className="execution-bubble-field-label">{fileActionFieldLabel(parsed.action, "path")}</span>
            <code>{parsed.path}</code>
          </div>
        ) : null}
        {parsed.note ? (
          <div className="execution-bubble-meta-item">
            <span className="execution-bubble-field-label">{fileActionFieldLabel(parsed.action, "note")}</span>
            <code>{parsed.note}</code>
          </div>
        ) : null}
      </div>
      {parsed.preview ? (
        <div className="execution-bubble-stream">
          <span className="execution-bubble-field-label">{fileActionFieldLabel(parsed.action, "preview")}</span>
          <pre className="execution-bubble-code">{parsed.preview}</pre>
        </div>
      ) : null}
    </div>
  );
}

function renderResearchExecutionBubble(detail?: string | null): React.ReactNode {
  const parsed = parseResearchExecutionDetail(detail);
  if (!parsed) {
    return detail ? <pre className="execution-bubble-detail">{detail}</pre> : null;
  }
  return (
    <div className="execution-bubble-structured execution-bubble-research-card">
      {renderExecutionCardHeader("Research 进展")}
      <div className="execution-bubble-meta-grid">
        {parsed.query ? (
          <div className="execution-bubble-meta-item">
            <span className="execution-bubble-field-label">查询</span>
            <code>{parsed.query}</code>
          </div>
        ) : null}
        {parsed.resultCount ? (
          <div className="execution-bubble-meta-item">
            <span className="execution-bubble-field-label">结果数</span>
            <code>{parsed.resultCount}</code>
          </div>
        ) : null}
        {parsed.pageCount ? (
          <div className="execution-bubble-meta-item">
            <span className="execution-bubble-field-label">页面数</span>
            <code>{parsed.pageCount}</code>
          </div>
        ) : null}
      </div>
      {parsed.note ? <div className="execution-bubble-highlight">{parsed.note}</div> : null}
      {parsed.items.length > 0 ? (
        <div className="execution-bubble-highlights">
          {parsed.items.map((item) => (
            <div key={item} className="execution-bubble-highlight">{item}</div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function renderAlertExecutionBubble(detail?: string | null): React.ReactNode {
  const parsed = parseAlertExecutionDetail(detail);
  if (!parsed) {
    return detail ? <pre className="execution-bubble-detail">{detail}</pre> : null;
  }
  return (
    <div className="execution-bubble-structured execution-bubble-alert-card">
      {renderExecutionCardHeader("路由 / 告警")}
      {parsed.routes.length > 0 ? (
        <div className="execution-bubble-highlights">
          {parsed.routes.map((route) => (
            <div key={route} className="execution-bubble-highlight">{route}</div>
          ))}
        </div>
      ) : null}
      {parsed.diagnostics.length > 0 ? (
        <div className="execution-bubble-stream">
          <span className="execution-bubble-field-label">诊断</span>
          <pre className="execution-bubble-code">{parsed.diagnostics.join("\n")}</pre>
        </div>
      ) : null}
    </div>
  );
}

function isStructuredExecutionCard(
  message: Pick<ChatMessage, "executionEvent" | "executionMetadata">,
): boolean {
  const executionEvent = message.executionEvent;
  if (executionEvent === "research_search" || executionEvent === "research_open_pages" || executionEvent === "research_note" || executionEvent === "provider_fallback" || executionEvent === "participant_error") {
    return true;
  }
  const metadata = (message.executionMetadata || {}) as Record<string, unknown>;
  const toolName = typeof metadata.tool_name === "string" ? metadata.tool_name : "";
  return toolName === "read_file" || toolName === "write_file" || toolName === "list_files";
}

function isCommandExecutionCard(message: Pick<ChatMessage, "executionEvent" | "executionMetadata">): boolean {
  const metadata = (message.executionMetadata || {}) as Record<string, unknown>;
  return typeof metadata.tool_name === "string" && metadata.tool_name === "run_command";
}

function renderExecutionBubbleDetail(
  title: string,
  detail?: string | null,
  message?: Pick<ChatMessage, "executionEvent" | "executionKind" | "executionMetadata">,
  options?: RenderExecutionBubbleDetailOptions,
): React.ReactNode {
  if (!detail) {
    return null;
  }
  const expandedRawOutput = options?.expandedRawOutput ?? false;
  const executionEvent = message?.executionEvent;
  const metadata = (message?.executionMetadata || {}) as Record<string, unknown>;
  if (executionEvent === "research_search" || executionEvent === "research_open_pages" || executionEvent === "research_note") {
    return renderResearchExecutionBubble(detail);
  }
  if (executionEvent === "provider_fallback" || executionEvent === "participant_error") {
    return renderAlertExecutionBubble(detail);
  }
  const toolName = typeof metadata.tool_name === "string" ? metadata.tool_name : "";
  if (toolName === "read_file" || toolName === "write_file" || toolName === "list_files") {
    return renderFileExecutionBubble(title, detail);
  }
  const parsed = parseStructuredCommandDetail(detail);
  if (!parsed) {
    return <pre className="execution-bubble-detail">{detail}</pre>;
  }

  const exitCodeIsZero = parsed.exitCode === "0";
  const rawOutput = parsed.stdout;
  const rawOutputSummary = summarizeOutputPreview(rawOutput);
  const rawOutputPreview = rawOutputSummary.preview || rawOutput;
  const stderrSummary = exitCodeIsZero ? undefined : summarizeStderrOutput(parsed.stderr);

  return (
    <div className="execution-bubble-structured">
      {parsed.highlights.length > 0 ? (
        <div className="execution-bubble-highlights">
          {parsed.highlights.map((line) => (
            <div key={line} className="execution-bubble-highlight">{line}</div>
          ))}
        </div>
      ) : null}
      {parsed.command ? (
        <div className="execution-bubble-command">
          <span className="execution-bubble-field-label">命令</span>
          <pre className="execution-bubble-code execution-bubble-code-shell">{parsed.command}</pre>
        </div>
      ) : null}
      <div className="execution-bubble-meta-grid">
        {parsed.exitCode ? (
          <div className="execution-bubble-meta-item">
            <span className="execution-bubble-field-label">退出码</span>
            <code>{parsed.exitCode}</code>
          </div>
        ) : null}
        {parsed.cwd ? (
          <div className="execution-bubble-meta-item">
            <span className="execution-bubble-field-label">目录</span>
            <code>{parsed.cwd}</code>
          </div>
        ) : null}
      </div>
      {stderrSummary ? (
        <div className="execution-bubble-stream execution-bubble-stream-stderr">
          <span className="execution-bubble-field-label">stderr 摘要</span>
          <pre className="execution-bubble-code execution-bubble-code-preview">{stderrSummary}</pre>
        </div>
      ) : null}
      {rawOutput ? (
        <>
          <div className="execution-bubble-stream execution-bubble-stream-stdout">
            <span className="execution-bubble-field-label">摘要</span>
            <div className="execution-bubble-meta-item execution-bubble-output-summary">
              <span className="execution-bubble-field-label">标准输出</span>
              <code>
                {`共 ${rawOutputSummary.lineCount} 行${
                  rawOutputSummary.hiddenLineCount > 0
                    ? `，已折叠 ${rawOutputSummary.hiddenLineCount} 行`
                    : ""
                }`}
              </code>
            </div>
            {rawOutputPreview ? (
              <pre className="execution-bubble-code execution-bubble-code-preview">{rawOutputPreview}</pre>
            ) : null}
          </div>
          <div className="execution-bubble-stream execution-bubble-stream-stdout">
            <span className="execution-bubble-field-label">原始输出</span>
            {expandedRawOutput ? (
              <pre className="execution-bubble-code execution-bubble-code-raw">{rawOutput}</pre>
            ) : null}
          </div>
        </>
      ) : null}
    </div>
  );
}

function parseAuthMetadataInput(raw: string): Record<string, unknown> {
  const trimmed = raw.trim();
  if (!trimmed) {
    return {};
  }
  const parsed = JSON.parse(trimmed);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("Auth Metadata 必须是 JSON 对象。");
  }
  return parsed as Record<string, unknown>;
}

function safeParseAuthMetadataInput(raw: string): Record<string, unknown> {
  try {
    return parseAuthMetadataInput(raw);
  } catch {
    return {};
  }
}

function parseTextareaLines(raw: string): string[] {
  return raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function mergeStoredMessagesIntoTranscript(
  currentMessages: ChatMessage[],
  storedMessages: ChatMessage[],
): ChatMessage[] {
  if (storedMessages.length === 0) {
    return currentMessages;
  }

  let storedIndex = 0;
  const merged: ChatMessage[] = [];

  for (const message of currentMessages) {
    if (message.type === "execution") {
      merged.push(message);
      continue;
    }
    if (storedIndex < storedMessages.length) {
      merged.push(storedMessages[storedIndex]);
      storedIndex += 1;
    }
  }

  while (storedIndex < storedMessages.length) {
    merged.push(storedMessages[storedIndex]);
    storedIndex += 1;
  }

  return merged;
}

// ─── Toast ────────────────────────────────────────────────────────────────────
interface Toast { id: number; message: string; kind: "success" | "error" | "info" }

let toastCounter = 0;

function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  function push(message: string, kind: Toast["kind"] = "info") {
    const id = ++toastCounter;
    setToasts((t) => [...t, { id, message, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3000);
  }
  return { toasts, push };
}

// ─── Initial state ────────────────────────────────────────────────────────────
const initialParticipants: ParticipantConfig[] = [
  { custom_id: "Model_A", model_ref: "", role_desc: "" },
  { custom_id: "Model_B", model_ref: "", role_desc: "" },
];

const initialSnapshot: SessionSnapshot = {
  topic: "", mode: "chat", participant_summaries: {}, consensus_list: [], key_events: [],
};

// ─── App ──────────────────────────────────────────────────────────────────────
export default function App(): JSX.Element {
  const [activeTab, setActiveTab] = useState<0 | 1 | 2 | 3>(() => {
    const restored = loadActiveTab();
    return restored === 0 || restored === 1 || restored === 2 || restored === 3 ? restored : 0;
  });

  // Session state
  const [topic, setTopic] = useState(DEFAULT_PRIMARY_TEMPLATE.defaultTopic);
  const [mode, setMode] = useState<CollaborationMode>(DEFAULT_PRIMARY_TEMPLATE.mode);
  const [selectedTemplateId, setSelectedTemplateId] = useState<string | null>(DEFAULT_TASK_TEMPLATE_ID);
  const [participants, setParticipants] = useState<ParticipantConfig[]>(initialParticipants);
  const [sessionList, setSessionList] = useState<SessionListItem[]>([]);
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [workspaceView, setWorkspaceView] = useState<SessionWorkspaceView | null>(null);
  const [streamView, setStreamView] = useState<SessionStreamViewState>({
    messages: [],
    liveMessage: null,
    streamState: "idle",
    executionEvents: [],
  });
  const [snapshot, setSnapshot] = useState<SessionSnapshot>(initialSnapshot);
  const [snapshotOpen, setSnapshotOpen] = useState(true);
  const [historyExport, setHistoryExport] = useState("");
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [streamAutoStartToken, setStreamAutoStartToken] = useState(0);
  const [workspaceDraft, setWorkspaceDraft] = useState<WorkspaceDraftState>(initialWorkspaceDraft);

  // Provider state
  const [providers, setProviders] = useState<ProviderRecord[]>([]);
  const [providerCatalogs, setProviderCatalogs] = useState<Record<string, ProviderModelCatalog>>({});
  const [providerHealth, setProviderHealth] = useState<Record<string, boolean | null>>({});
  const [providerDraft, setProviderDraft] = useState<ProviderDraft>(EMPTY_PROVIDER_DRAFT);
  const [providerDraftAuthMethod, setProviderDraftAuthMethod] = useState<ProviderAuthMethod>("api_key");

  // Auth flows
  const [authFlows, setAuthFlows] = useState<Record<string, AuthFlowState>>({});
  const [awsRoleSelection, setAwsRoleSelection] = useState<Record<string, { accountId: string; roleName: string }>>({});
  const pollTimers = useRef<Record<string, ReturnType<typeof setInterval>>>({});

  const { toasts, push } = useToasts();
  const visibleMessages = streamView.liveMessage
    ? [...streamView.messages, streamView.liveMessage]
    : streamView.messages;
  const isSessionStreaming =
    streamView.streamState === "connecting" || streamView.streamState === "streaming";

  useEffect(() => {
    return () => {
      Object.values(pollTimers.current).forEach(clearInterval);
      pollTimers.current = {};
    };
  }, []);

  useEffect(() => {
    void bootstrapConsole();
  }, []);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      await refreshProviderCatalogs(providers);
      if (cancelled) {
        return;
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [providers]);

  useEffect(() => {
    saveActiveTab(activeTab);
  }, [activeTab]);

  useEffect(() => {
    if (!session) return;
    saveComposerDraft(session.id, input);
  }, [session, input]);

  useEffect(() => {
    if (!session || activeTab !== 3 || session.mode !== "code_workspace" || isSessionStreaming) {
      return;
    }

    const syncSessionState = () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") {
        return;
      }
      void refreshSessionState(session.id, { reloadMessages: true });
    };

    const timer = window.setInterval(syncSessionState, 5000);
    const handleFocus = () => {
      syncSessionState();
    };
    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        syncSessionState();
      }
    };

    window.addEventListener("focus", handleFocus);
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", handleFocus);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [activeTab, isSessionStreaming, session]);

  function mapStoredMessage(message: SessionMessageRecord): ChatMessage {
    const isUserMessage =
      message.sender_id === "[用户]" || message.message_type === "user_intervention";
    const isToolMessage = message.message_type === "tool_output";
    const isSystemMessage = !isUserMessage && (message.sender_id === "system" || isToolMessage);
    return {
      id: message.id,
      senderId: message.sender_id,
      type: isUserMessage ? "user" : isSystemMessage ? "system" : "model",
      content: isToolMessage ? `[工具输出]\n${message.content}` : message.content,
      round: message.round_number,
      driftScore: typeof message.drift_score === "number" ? message.drift_score : undefined,
      status: typeof message.drift_score === "number" ? "warning" : "done",
    };
  }

  async function loadSessionWorkspace(sessionId: string) {
    const detail = await getSession(sessionId);
    const [snap, history, workspace] = await Promise.all([
      getSnapshot(sessionId),
      getSessionMessages(sessionId),
      detail.workspace ? getSessionWorkspace(sessionId).catch(() => null) : Promise.resolve(null),
    ]);
    setSession(detail);
    setWorkspaceView(workspace);
    setSnapshot(snap);
    setStreamView({
      messages: history.map(mapStoredMessage),
      liveMessage: null,
      streamState: "idle",
      executionEvents: [],
    });
    setInput(loadComposerDraft(sessionId));
    setActiveTab(3);
    saveLastSessionId(sessionId);
  }

  async function reloadSessions() {
    try {
      const list = await listSessions();
      const normalized = Array.isArray(list) ? list : [];
      setSessionList(normalized);
      return normalized;
    } catch (err) {
      push((err as Error).message, "error");
      setSessionList([]);
      return [];
    }
  }

  async function bootstrapConsole() {
    let normalizedSessions: SessionListItem[] = [];

    try {
      const providerRows = await listProviders();
      setProviders(Array.isArray(providerRows) ? providerRows : []);
    } catch (err) {
      push((err as Error).message, "error");
      setProviders([]);
    }

    try {
      const sessionRows = await listSessions();
      normalizedSessions = Array.isArray(sessionRows) ? sessionRows : [];
      setSessionList(normalizedSessions);
    } catch (err) {
      push((err as Error).message, "error");
      setSessionList([]);
      return;
    }

    const restoredSessionId = pickRestoredSessionId(
      normalizedSessions,
      loadLastSessionId(),
    );
    if (!restoredSessionId) {
      return;
    }

    try {
      await loadSessionWorkspace(restoredSessionId);
    } catch (err) {
      push((err as Error).message, "error");
      saveLastSessionId(null);
      setSession(null);
      setWorkspaceView(null);
      setStreamAutoStartToken(0);
      setStreamView({
        messages: [],
        liveMessage: null,
        streamState: "idle",
        executionEvents: [],
      });
      setSnapshot(initialSnapshot);
      setInput("");
      if (normalizedSessions.length > 0) {
        setActiveTab(3);
      }
    }
  }

  // ── Providers ──────────────────────────────────────────────────────────────
  async function reloadProviders() {
    try {
      const list = await listProviders();
      setProviders(Array.isArray(list) ? list : []);
    } catch (err) {
      push((err as Error).message, "error");
      setProviders([]);
    }
  }

  async function refreshProviderCatalogs(providerRows: ProviderRecord[]) {
    if (!providerRows.length) {
      setProviderCatalogs({});
      return;
    }

    const entries = await Promise.all(
      providerRows.map(async (provider) => {
        try {
          const catalog = await discoverModelCatalog({ provider_id: provider.id });
          return [
            provider.id,
            {
              provider_id: catalog.provider_id || provider.id,
              provider_name: catalog.provider_name || provider.name,
              provider_type: catalog.provider_type || provider.provider_type,
              models: Array.isArray(catalog.models) ? catalog.models : [],
              detected_at: catalog.detected_at,
            },
          ] as const;
        } catch {
          return [
            provider.id,
            {
              provider_id: provider.id,
              provider_name: provider.name,
              provider_type: provider.provider_type,
              models: [],
            },
          ] as const;
        }
      }),
    );

    setProviderCatalogs(Object.fromEntries(entries));
  }

  async function handleCreateProvider(e: FormEvent) {
    e.preventDefault();
    try {
      const authMetadata = mergeDefaultModelRef(
        parseAuthMetadataInput(providerDraft.auth_metadata),
        providerDraft.default_model_ref,
      );
      const { default_model_ref: _defaultModelRef, ...providerPayload } = providerDraft;
      await createProvider({
        ...providerPayload,
        auth_metadata: authMetadata,
        fallback_ids: providerDraft.fallback_ids.split(",").map((s) => s.trim()).filter(Boolean),
      });
      setProviderDraft(EMPTY_PROVIDER_DRAFT);
      setProviderDraftAuthMethod("api_key");
      await reloadProviders();
      push("Provider 已创建", "success");
    } catch (err) {
      push((err as Error).message, "error");
    }
  }

  function handleProviderDraftAuthMethod(method: ProviderAuthMethod) {
    setProviderDraftAuthMethod(method);
    const authType = authMethodToProviderAuthType(method);
    setProviderDraft((draft) => ({
      ...draft,
      auth_type: authType ?? "oauth",
      auth_value: authType ? draft.auth_value : "",
      default_model_ref: getDefaultModelRefForProvider(
        draft.provider_type,
        authType ?? "oauth",
        draft.name,
      ),
    }));
  }

  async function handleProviderHealth(providerId: string) {
    setProviderHealth((h) => ({ ...h, [providerId]: null }));
    try {
      const result = await healthCheckProvider(providerId);
      setProviderHealth((h) => ({ ...h, [providerId]: result.healthy }));
    } catch {
      setProviderHealth((h) => ({ ...h, [providerId]: false }));
    }
  }

  async function handleDeleteProvider(providerId: string) {
    if (!window.confirm("确认删除该 Provider？")) return;
    try {
      await deleteProvider(providerId);
      setAuthFlows((prev) => { const next = { ...prev }; delete next[providerId]; return next; });
      await reloadProviders();
      push("Provider 已删除", "success");
    } catch (err) { push((err as Error).message, "error"); }
  }

  async function handleUpdateProvider(providerId: string, draft: ProviderDraft) {
    try {
      const authMetadata = mergeDefaultModelRef(
        parseAuthMetadataInput(draft.auth_metadata),
        draft.default_model_ref,
      );
      const { default_model_ref: _defaultModelRef, ...providerPayload } = draft;
      await updateProvider(providerId, {
        ...providerPayload,
        auth_metadata: authMetadata,
        fallback_ids: draft.fallback_ids.split(",").map((s) => s.trim()).filter(Boolean),
      });
      // 清掉旧的认证流状态，让登录按钮重新出现
      setAuthFlows((prev) => { const next = { ...prev }; delete next[providerId]; return next; });
      await reloadProviders();
      push("Provider 已更新", "success");
    } catch (err) { push((err as Error).message, "error"); }
  }

  async function handleQuickProviderAuthSave(
    provider: ProviderRecord,
    method: ProviderAuthMethod,
    authValue: string,
  ) {
    const authType = authMethodToProviderAuthType(method);
    if (!authType) {
      push("当前认证方式不支持直接保存凭据。", "error");
      return;
    }
    if (!authValue.trim()) {
      push("请输入凭据内容。", "error");
      return;
    }
    await handleUpdateProvider(provider.id, {
      name: provider.name,
      provider_type: provider.provider_type,
      base_url: provider.base_url || "",
      api_format: provider.api_format,
      auth_type: authType,
      auth_value: authValue.trim(),
      fallback_ids: provider.fallback_ids.join(","),
      auth_metadata: JSON.stringify(provider.auth_metadata || {}, null, 2),
      default_model_ref: getResolvedDefaultModelRef(
        provider.provider_type,
        provider.auth_type,
        provider.name,
        provider.auth_metadata,
      ),
    });
  }

  // ── Auth flows ─────────────────────────────────────────────────────────────
  function clearAuthPollTimer(providerId: string) {
    if (pollTimers.current[providerId]) {
      clearInterval(pollTimers.current[providerId]);
      delete pollTimers.current[providerId];
    }
  }

  async function handleStartAuthFlow(
    providerId: string,
    flowType: "aws_iam" | "aws_sso_pkce" | "openai_codex" | "generic_oauth" | "browser_oauth",
    extra: Record<string, string> = {},
  ) {
    try {
      clearAuthPollTimer(providerId);
      const result = await startAuthFlow(providerId, { flow_type: flowType, ...extra } as Parameters<typeof startAuthFlow>[1]);
      const flowState: AuthFlowState = {
        authSessionId: result.auth_session_id, verificationUri: result.verification_uri,
        userCode: result.user_code, expiresIn: result.expires_in, status: "pending", flowType: result.flow_type,
      };
      setAuthFlows((prev) => ({ ...prev, [providerId]: flowState }));
      const shouldOpenBrowser =
        flowType === "aws_sso_pkce" ||
        flowType === "browser_oauth" ||
        (flowType === "openai_codex" && extra.login_variant === "browser");
      if (shouldOpenBrowser && result.verification_uri) {
        window.open(result.verification_uri, "_blank", "width=600,height=800");
      }
      const interval = result.interval * 1000;
      const timer = setInterval(async () => {
        try {
          const status = await getAuthStatus(providerId, result.auth_session_id);
          setAuthFlows((prev) => ({ ...prev, [providerId]: { ...prev[providerId], status: status.status, accounts: status.accounts, errorMessage: status.error_message } }));
          if (status.status !== "pending") {
            clearAuthPollTimer(providerId);
            if (status.status === "completed") { push(`Provider ${providerId} 认证完成！`, "success"); await reloadProviders(); }
          }
        } catch { /* ignore */ }
      }, interval);
      pollTimers.current[providerId] = timer;
    } catch (err) { push((err as Error).message, "error"); }
  }

  async function handleCancelProviderAuth(providerId: string) {
    const flow = authFlows[providerId];
    if (!flow) {
      return;
    }
    clearAuthPollTimer(providerId);
    try {
      const result = await cancelAuthFlow(providerId, flow.authSessionId);
      setAuthFlows((prev) => ({
        ...prev,
        [providerId]: {
          ...prev[providerId],
          status: result.status,
          errorMessage: result.error_message,
        },
      }));
      push("认证已取消", "info");
      await reloadProviders();
    } catch (err) {
      push((err as Error).message, "error");
    }
  }

  async function handleLogoutProvider(providerId: string) {
    clearAuthPollTimer(providerId);
    try {
      await logoutProviderAuth(providerId);
      setAuthFlows((prev) => {
        const next = { ...prev };
        delete next[providerId];
        return next;
      });
      await reloadProviders();
      push("已退出登录", "success");
    } catch (err) {
      push((err as Error).message, "error");
    }
  }

  async function refreshSessionState(
    sessionId: string,
    options?: { reloadMessages?: boolean },
  ) {
    try {
      const [detail, snap, history] = await Promise.all([
        getSession(sessionId),
        getSnapshot(sessionId),
        options?.reloadMessages
          ? getSessionMessages(sessionId).catch(() => null)
          : Promise.resolve(null),
      ]);
      const workspace = detail.workspace ? await getSessionWorkspace(sessionId).catch(() => null) : null;
      setSession(detail);
      setSnapshot(snap);
      setWorkspaceView(workspace);
      if (history) {
        const storedMessages = history.map(mapStoredMessage);
        setStreamView((current) => ({
          ...current,
          messages: mergeStoredMessagesIntoTranscript(current.messages, storedMessages),
          liveMessage: null,
        }));
      }
      void reloadSessions();
    } catch (err) {
      push((err as Error).message, "error");
    }
  }

  async function handleToggleWorkspaceWriteMode(sessionId: string, canWrite: boolean) {
    try {
      await updateSessionWorkspaceCanWrite(sessionId, canWrite);
      await refreshSessionState(sessionId);
      push(canWrite ? "已开启修复与执行" : "已关闭修复与执行", "success");
    } catch (err) {
      push((err as Error).message, "error");
    }
  }

  async function handleSelectSession(sessionId: string) {
    try {
      setHistoryExport("");
      await loadSessionWorkspace(sessionId);
    } catch (err) {
      push((err as Error).message, "error");
      setWorkspaceView(null);
      setStreamAutoStartToken(0);
    }
  }

  async function handleRenameSession(sessionId: string) {
    const current = sessionList.find((item) => item.id === sessionId);
    const currentLabel = current?.title || current?.topic || "未命名任务";
    const nextTitle = window.prompt("输入新的任务名称", currentLabel);
    if (nextTitle === null) {
      return;
    }

    try {
      const updated = await updateSession(sessionId, { title: nextTitle });
      if (session?.id === sessionId) {
        setSession(updated);
      }
      await reloadSessions();
      push("任务名称已更新", "success");
    } catch (err) {
      push((err as Error).message, "error");
    }
  }

  async function handleDeleteSession(sessionId: string) {
    const current = sessionList.find((item) => item.id === sessionId);
    const currentLabel = current?.title || current?.topic || "该任务";
    if (!window.confirm(`确认删除任务“${currentLabel}”？`)) {
      return;
    }

    try {
      await deleteSession(sessionId);
      const remainingSessions = await reloadSessions();
      if (session?.id === sessionId) {
        const nextSessionId = remainingSessions[0]?.id || null;
        if (nextSessionId) {
          await loadSessionWorkspace(nextSessionId);
        } else {
          saveLastSessionId(null);
          setSession(null);
          setWorkspaceView(null);
          setStreamAutoStartToken(0);
          setStreamView({
            messages: [],
            liveMessage: null,
            streamState: "idle",
            executionEvents: [],
          });
          setSnapshot(initialSnapshot);
          setHistoryExport("");
          setInput("");
        }
      }
      push("任务已删除", "success");
    } catch (err) {
      push((err as Error).message, "error");
    }
  }

  async function handleAppendParticipantToSession(
    sessionId: string,
    payloads: ParticipantConfig[],
  ) {
    try {
      const normalizedPayloads = payloads.map((item) => ({
        ...item,
        ...resolveParticipantModelSelection(providerCatalogs, item),
      }));
      const updated = await appendSessionParticipants(sessionId, normalizedPayloads);
      if (session?.id === sessionId) {
        setSession(updated);
      }
      await reloadSessions();
      const appendedLabels = normalizedPayloads
        .map((item) => item.custom_id?.trim())
        .filter((item): item is string => Boolean(item));
      push(
        `已添加参与者 ${updated.participants[updated.participants.length - 1]?.custom_id || ""}`,
        "success",
      );
    } catch (err) {
      push((err as Error).message, "error");
      throw err;
    }
  }

  async function handleBindAwsRole(providerId: string) {
    const flow = authFlows[providerId];
    const sel = awsRoleSelection[providerId];
    if (!flow || !sel?.accountId || !sel?.roleName) { push("请先选择账号和角色", "error"); return; }
    try {
      await bindAwsRole(providerId, flow.authSessionId, sel.accountId, sel.roleName);
      setAuthFlows((prev) => ({ ...prev, [providerId]: { ...prev[providerId], status: "completed" } }));
      push(`AWS 角色绑定成功：${sel.accountId} / ${sel.roleName}`, "success");
      await reloadProviders();
    } catch (err) { push((err as Error).message, "error"); }
  }

  // ── Session ────────────────────────────────────────────────────────────────
  async function handleCreateSession(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    try {
      const normalizedParticipants = participants.map((item) => ({
        ...item,
        ...resolveParticipantModelSelection(providerCatalogs, item),
      }));
      const workspace =
        mode === "code_workspace"
          ? {
              root_path: workspaceDraft.rootPath.trim(),
              display_name: workspaceDraft.displayName.trim() || undefined,
              selected_paths: parseTextareaLines(workspaceDraft.selectedPaths),
              scan_excludes: parseTextareaLines(workspaceDraft.scanExcludes),
              index_status: "pending",
              capabilities: buildWorkspaceCapabilitiesFromDraft(workspaceDraft),
            }
          : undefined;
      if (mode === "code_workspace" && !workspace?.root_path) {
        throw new Error("代码工作区模式需要填写本地仓库路径。");
      }

      const created = await createSession({ topic, mode, participants: normalizedParticipants, workspace });
      const detail = await getSession(created.id);
      const [snap, workspaceData] = await Promise.all([
        getSnapshot(created.id),
        detail.workspace ? getSessionWorkspace(created.id).catch(() => null) : Promise.resolve(null),
      ]);
      setSession(detail);
      setWorkspaceView(workspaceData);
      setSnapshot(snap);
      setStreamView({
        messages: [
          {
            id: `sys-${Date.now()}`,
            senderId: "system",
            type: "system",
            content: `任务 ${created.id} 已创建，模板：${created.mode}`,
            round: 0,
            status: "done",
          },
        ],
        liveMessage: null,
        streamState: "idle",
        executionEvents: [],
      });
      setHistoryExport("");
      setInput(loadComposerDraft(created.id));
      setWorkspaceDraft(initialWorkspaceDraft);
      setActiveTab(3);
      saveLastSessionId(created.id);
      await reloadSessions();
      push("任务已创建，已跳转到运行详情", "success");
    } catch (err) {
      push((err as Error).message, "error");
    } finally {
      setLoading(false);
    }
  }

  function handleApplyWorkspaceTaskPreset(preset: WorkspaceTaskPresetRecommendation) {
    setSelectedTemplateId(preset.template_id);
    setMode(preset.mode);
    setTopic(preset.topic);
    push(`已应用 Task Preset：${preset.label}`, "info");
  }

  function handleApplyWorkspaceTeamPreset(preset: WorkspaceTeamPresetRecommendation) {
    const nextParticipants = applyWorkspaceTeamPreset(participants, preset);
    const nextOverrides = remapWorkspaceParticipantOverrides(
      workspaceDraft.participantOverrides,
      participants,
      nextParticipants,
    );
    setParticipants(nextParticipants);
    setWorkspaceDraft((current) => ({
      ...current,
      participantOverrides: nextOverrides,
    }));
    push(`已填充 Team Preset：${preset.label}`, "info");
  }

  async function handleSendMessage(e: FormEvent) {
    e.preventDefault();
    if (!session || !input.trim()) return;
    const content = input.trim();
    setInput("");
    setStreamView((current) => ({
      ...current,
      messages: [
        ...current.messages,
        {
          id: `user-${Date.now()}`,
          senderId: "[用户]",
          type: "user",
          content,
          round: session.current_round,
          status: "done",
        },
      ],
    }));
    try {
      await sendUserMessage(session.id, content);
      const [detail, snap, workspaceData] = await Promise.all([
        getSession(session.id),
        getSnapshot(session.id),
        session.workspace ? getSessionWorkspace(session.id).catch(() => null) : Promise.resolve(null),
      ]);
      setSession(detail);
      setWorkspaceView(workspaceData);
      setSnapshot(snap);
      if (detail.mode === "code_workspace" && hasWorkspaceMention(content)) {
        setStreamAutoStartToken((value) => value + 1);
      }
    } catch (err) { push((err as Error).message, "error"); }
  }

  async function handleSaveSnapshot() {
    if (!session) return;
    try {
      const updated = await patchSnapshot(session.id, snapshot);
      setSnapshot(updated);
      push("任务快照已保存", "success");
    } catch (err) { push((err as Error).message, "error"); }
  }

  async function handleExportHistory() {
    if (!session) return;
    try {
      const data = await exportSessionHistory(session.id);
      setHistoryExport(data.content);
      push("运行记录已导出", "success");
    } catch (err) { push((err as Error).message, "error"); }
  }

  // ── Stream events ──────────────────────────────────────────────────────────
  function handleStreamEvent(eventName: string, payload: StreamPayload) {
    setStreamView((current) => applyStreamEvent(current, eventName, payload));
    if (eventName === "participant_error" && payload.provider_id) {
      void reloadProviders();
    }
    if (eventName === "drift_alert") {
      push(`检测到 ${payload.participant_id} 可能偏题，分数 ${payload.score?.toFixed(2) ?? "未知"}`, "info");
      return;
    }
    if (eventName === "compression") { push(`上下文压缩：${payload.action || "unknown"}`, "info"); return; }
    if (eventName === "round_end") {
      if (session) void refreshSessionState(session.id, { reloadMessages: true });
      return;
    }
    if (eventName === "session_end") {
      if (session) void refreshSessionState(session.id, { reloadMessages: true });
      return;
    }
    if (eventName === "error") {
      if (session) void refreshSessionState(session.id, { reloadMessages: true });
      return;
    }
  }

  // ── Participants ───────────────────────────────────────────────────────────
  function updateParticipant(index: number, patch: Partial<ParticipantConfig>) {
    const nextParticipants = participants.map((participant, participantIndex) =>
      participantIndex === index ? { ...participant, ...patch } : participant,
    );
    setParticipants(nextParticipants);
    if (Object.prototype.hasOwnProperty.call(patch, "custom_id")) {
      const nextOverrides = remapWorkspaceParticipantOverrides(
        workspaceDraft.participantOverrides,
        participants,
        nextParticipants,
      );
      setWorkspaceDraft((current) => ({
        ...current,
        participantOverrides: nextOverrides,
      }));
    }
  }
  function addParticipant() {
    const nextParticipants = [
      ...participants,
      {
        custom_id: `Model_${participants.length + 1}`,
        model_ref: getDefaultModelRefForProvider("openai", "api_key"),
        role_desc: "",
      },
    ];
    const nextOverrides = remapWorkspaceParticipantOverrides(
      workspaceDraft.participantOverrides,
      participants,
      nextParticipants,
    );
    setParticipants(nextParticipants);
    setWorkspaceDraft((current) => ({
      ...current,
      participantOverrides: nextOverrides,
    }));
  }
  function removeParticipant(index: number) {
    const nextParticipants = participants.filter((_, i) => i !== index);
    const nextOverrides = remapWorkspaceParticipantOverrides(
      workspaceDraft.participantOverrides,
      participants,
      nextParticipants,
    );
    setParticipants(nextParticipants);
    setWorkspaceDraft((current) => ({
      ...current,
      participantOverrides: nextOverrides,
    }));
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  const tabs: Array<{ label: string; index: 0 | 1 | 2 | 3; disabled?: boolean }> = [
    { label: "🚀 快速开始", index: 0 },
    { label: "⚙️ Provider 配置", index: 1 },
    { label: "🧩 新建任务", index: 2 },
    { label: "📊 运行详情", index: 3, disabled: !session && sessionList.length === 0 },
  ];
  const providerDraftPreview: ProviderRecord = {
    id: "__draft__",
    name: providerDraft.name || providerDraft.provider_type,
    provider_type: providerDraft.provider_type,
    base_url: providerDraft.base_url || undefined,
    api_format: providerDraft.api_format,
    auth_type: providerDraft.auth_type,
    auth_metadata: safeParseAuthMetadataInput(providerDraft.auth_metadata),
    auth_status: "missing",
    auth_expires_at: null,
    fallback_ids: [],
    is_active: true,
  };
  const providerDraftAuthOptions = getProviderAuthOptions(providerDraftPreview);
  const selectedProviderDraftAuthMethod = providerDraftAuthOptions.some(
    (option) => option.id === providerDraftAuthMethod,
  )
    ? providerDraftAuthMethod
    : defaultProviderAuthMethod(providerDraftPreview);

  return (
    <div className="app-shell">
      {/* Header */}
      <header className="hero">
        <div>
          <p className="eyebrow">AI R&D Workbench</p>
          <h1>多模型研发工作台</h1>
          <p className="subtitle">配置 Provider、定义任务、查看运行结果，一站完成。</p>
        </div>

      </header>

      {/* Tab nav */}
      <nav className="tab-nav">
        {tabs.map((t) => (
          <button
            key={t.index}
            className={`tab-btn${activeTab === t.index ? " tab-btn-active" : ""}${t.disabled ? " tab-btn-disabled" : ""}`}
            onClick={() => !t.disabled && setActiveTab(t.index as 0 | 1 | 2 | 3)}
            disabled={t.disabled}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {/* Tab 0 – Quick Start */}
      {activeTab === 0 && <TabQuickStart onNavigate={setActiveTab} />}

      {/* Tab 1 – Provider Config */}
      {activeTab === 1 && (
        <TabProviders
          providers={providers}
          providerDraft={providerDraft}
          providerDraftAuthMethod={selectedProviderDraftAuthMethod}
          providerDraftAuthOptions={providerDraftAuthOptions}
          setProviderDraft={setProviderDraft}
          onProviderDraftAuthMethodChange={handleProviderDraftAuthMethod}
          providerHealth={providerHealth}
          authFlows={authFlows}
          awsRoleSelection={awsRoleSelection}
          setAwsRoleSelection={setAwsRoleSelection}
          onCreateProvider={handleCreateProvider}
          onHealthCheck={handleProviderHealth}
          onStartAuthFlow={handleStartAuthFlow}
          onCancelAuthFlow={handleCancelProviderAuth}
          onBindAwsRole={handleBindAwsRole}
          onLogoutProvider={handleLogoutProvider}
          onDeleteProvider={handleDeleteProvider}
          onUpdateProvider={handleUpdateProvider}
          onQuickProviderAuthSave={handleQuickProviderAuthSave}
        />
      )}

      {/* Tab 2 – Create Task */}
      {activeTab === 2 && (
        <TabCreateSession
          topic={topic} setTopic={setTopic}
          mode={mode} setMode={setMode}
          selectedTemplateId={selectedTemplateId}
          setSelectedTemplateId={setSelectedTemplateId}
          workspaceDraft={workspaceDraft}
          participants={participants}
          providers={providers}
  providerCatalogs={Object.values(providerCatalogs)}
  loading={loading}
  onWorkspaceDraftChange={(update) =>
    setWorkspaceDraft((current) =>
      typeof update === "function" ? update(current) : { ...current, ...update },
    )
  }
  onUpdateParticipant={updateParticipant}
  onAddParticipant={addParticipant}
  onRemoveParticipant={removeParticipant}
  onApplyWorkspaceTaskPreset={handleApplyWorkspaceTaskPreset}
  onApplyWorkspaceTeamPreset={handleApplyWorkspaceTeamPreset}
  onSubmit={handleCreateSession}
        />
      )}

      {/* Tab 3 – Run Detail */}
      {activeTab === 3 && (session || sessionList.length > 0) && (
        <SessionDetailTabEnhanced
          session={session}
          sessionList={sessionList}
          workspace={workspaceView}
          providers={providers}
          providerCatalogs={Object.values(providerCatalogs)}
          messages={visibleMessages}
          executionEvents={streamView.executionEvents}
          streamState={streamView.streamState}
          onSetStreamState={(nextState) =>
            setStreamView((current) => ({ ...current, streamState: nextState }))
          }
          autoStartToken={streamAutoStartToken}
          snapshot={snapshot}
          setSnapshot={setSnapshot}
          snapshotOpen={snapshotOpen}
          setSnapshotOpen={setSnapshotOpen}
          historyExport={historyExport}
          input={input}
          setInput={setInput}
          onSendMessage={handleSendMessage}
          onSelectSession={handleSelectSession}
          onRenameSession={handleRenameSession}
          onDeleteSession={handleDeleteSession}
          onAppendParticipant={handleAppendParticipantToSession}
          onToggleWorkspaceWriteMode={handleToggleWorkspaceWriteMode}
          onSaveSnapshot={handleSaveSnapshot}
          onExportHistory={handleExportHistory}
          onStreamEvent={handleStreamEvent}
        />
      )}

      {/* Toast container */}
      <div className="toast-container">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`}>{t.message}</div>
        ))}
      </div>
    </div>
  );
}

// ─── Tab 0: Quick Start ───────────────────────────────────────────────────────
function TabQuickStart({ onNavigate }: { onNavigate: (tab: 0 | 1 | 2 | 3) => void }) {
  return (
    <div className="tab-content">
      <div className="quickstart-grid">
        <div className="qs-steps">
          <h2 className="section-title">工作流</h2>
          <div className="step-list">
            <div className="step-card">
              <div className="step-num">1</div>
              <div className="step-body">
                <h3>配置 Provider</h3>
                <p>点击 <strong>⚙️ Provider 配置</strong> Tab，填写名称、类型、Base URL 和 API Key，点击"保存 Provider"。可点击"健康检查"验证连通性。</p>
                <button className="step-link" onClick={() => onNavigate(1)}>前往 Provider 配置 →</button>
              </div>
            </div>
            <div className="step-card">
              <div className="step-num">2</div>
              <div className="step-body">
                <h3>新建任务</h3>
                <p>点击 <strong>🧩 新建任务</strong> Tab，填写任务目标，选择任务模板，配置至少 2 个参与者，并为代码任务附加本地工作区。</p>
                <button className="step-link" onClick={() => onNavigate(2)}>前往新建任务 →</button>
              </div>
            </div>
            <div className="step-card">
              <div className="step-num">3</div>
              <div className="step-body">
                <h3>运行任务</h3>
                <p>任务创建后自动跳转到 <strong>📊 运行详情</strong> Tab。点击"▶ 继续执行"推进当前运行，或在底部输入补充指令，实时查看输出、工具调用和验证结果。</p>
              </div>
            </div>
          </div>
        </div>

        <div className="qs-sidebar">
          <div className="info-card">
            <h3>任务模板</h3>
            <p>当前模板仍复用现有协作模式，但前端已按 Analyze、Fix、Review、Compare 的任务心智组织入口，可先用代码工作区模式承载真实仓库任务。</p>
          </div>

          <div className="info-card">
            <h3>本地 Provider 提示</h3>
            <p>本地 Ollama 无需填写 API Key，Base URL 填：</p>
            <code className="url-code">http://127.0.0.1:11434/v1</code>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── AWS SSO Login Component ──────────────────────────────────────────────────
function AwsSsoLoginButton({ providerId, onStartAuthFlow }: { providerId: string; onStartAuthFlow: (id: string, flowType: "aws_sso_pkce" | "aws_iam", extra: any) => void }) {
  const [isOpen, setIsOpen] = useState(false);
  const [url, setUrl] = useState("");
  const [region, setRegion] = useState("us-east-1");

  if (!isOpen) {
    return <button className="ghost-button small primary" onClick={() => setIsOpen(true)}>AWS 登录</button>;
  }

  return (
    <div style={{ display: "inline-flex", gap: "8px", alignItems: "center", background: "#1e293b", padding: "8px", borderRadius: "8px", marginTop: "8px", width: "100%" }}>
      <input 
        type="text" 
        placeholder="SSO Start URL" 
        value={url} 
        onChange={e => setUrl(e.target.value)} 
        style={{ flex: 2, minWidth: 0, padding: "4px 8px", borderRadius: "4px", border: "1px solid #334155", background: "#0f172a", color: "#f8fafc" }} 
      />
      <input 
        type="text" 
        placeholder="Region" 
        value={region} 
        onChange={e => setRegion(e.target.value)} 
        style={{ flex: 1, minWidth: 0, padding: "4px 8px", borderRadius: "4px", border: "1px solid #334155", background: "#0f172a", color: "#f8fafc" }} 
      />
      <button 
        className="ghost-button small primary"
        disabled={!url}
        onClick={() => {
          onStartAuthFlow(providerId, "aws_sso_pkce", { sso_start_url: url, sso_region: region });
          setIsOpen(false);
        }}
      >
        继续
      </button>
      <button className="ghost-button small" onClick={() => setIsOpen(false)}>取消</button>
    </div>
  );
}

// ─── Tab 1: Provider Config ───────────────────────────────────────────────────
interface TabProvidersProps {
  providers: ProviderRecord[];
  providerDraft: ProviderDraft;
  providerDraftAuthMethod: ProviderAuthMethod;
  providerDraftAuthOptions: ReturnType<typeof getProviderAuthOptions>;
  setProviderDraft: React.Dispatch<React.SetStateAction<ProviderDraft>>;
  onProviderDraftAuthMethodChange: (method: ProviderAuthMethod) => void;
  providerHealth: Record<string, boolean | null>;
  authFlows: Record<string, AuthFlowState>;
  awsRoleSelection: Record<string, { accountId: string; roleName: string }>;
  setAwsRoleSelection: React.Dispatch<React.SetStateAction<Record<string, { accountId: string; roleName: string }>>>;
  onCreateProvider: (e: FormEvent) => void;
  onHealthCheck: (id: string) => void;
  onStartAuthFlow: (id: string, type: "aws_iam" | "aws_sso_pkce" | "openai_codex" | "generic_oauth" | "browser_oauth", extra?: Record<string, string>) => void;
  onCancelAuthFlow: (id: string) => void;
  onBindAwsRole: (id: string) => void;
  onLogoutProvider: (id: string) => void;
  onDeleteProvider: (id: string) => void;
  onUpdateProvider: (id: string, draft: ProviderDraft) => void;
  onQuickProviderAuthSave: (provider: ProviderRecord, method: ProviderAuthMethod, authValue: string) => void;
}

function TabProviders({
  providers, providerDraft, providerDraftAuthMethod, providerDraftAuthOptions, setProviderDraft,
  onProviderDraftAuthMethodChange, providerHealth,
  authFlows, awsRoleSelection, setAwsRoleSelection,
  onCreateProvider, onHealthCheck, onStartAuthFlow, onCancelAuthFlow, onBindAwsRole,
  onLogoutProvider, onDeleteProvider, onUpdateProvider, onQuickProviderAuthSave,
}: TabProvidersProps) {
  const isApiKey = providerDraft.auth_type === "api_key";
  const isIam = providerDraft.auth_type === "iam";
  const isOauth = providerDraft.auth_type === "oauth";

  // 编辑状态：provider_id -> draft
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<TabProvidersProps["providerDraft"] | null>(null);
  const [authMethodDrafts, setAuthMethodDrafts] = useState<Record<string, ProviderAuthMethod>>({});
  const [authValueDrafts, setAuthValueDrafts] = useState<Record<string, string>>({});
  const [draftCatalog, setDraftCatalog] = useState<ProviderModelCatalog>({ provider_id: "__draft__", provider_name: "", provider_type: providerDraft.provider_type, models: [] });
  const [editCatalog, setEditCatalog] = useState<ProviderModelCatalog | null>(null);

  useEffect(() => {
    let active = true;
    void (async () => {
      try {
        const result = await discoverModelCatalog({
          provider: {
            name: providerDraft.name || providerDraft.provider_type,
            provider_type: providerDraft.provider_type,
            base_url: providerDraft.base_url || undefined,
            api_format: providerDraft.api_format,
            auth_type: providerDraft.auth_type,
            auth_value: providerDraft.auth_value || undefined,
            auth_metadata: safeParseAuthMetadataInput(providerDraft.auth_metadata),
            fallback_ids: providerDraft.fallback_ids
              .split(",")
              .map((s) => s.trim())
              .filter(Boolean),
          },
        });
        if (!active) {
          return;
        }
        setDraftCatalog({
          provider_id: result.provider_id || "__draft__",
          provider_name: result.provider_name || providerDraft.name || providerDraft.provider_type,
          provider_type: result.provider_type || providerDraft.provider_type,
          models: Array.isArray(result.models) ? result.models : [],
          detected_at: result.detected_at,
        });
      } catch {
        if (!active) {
          return;
        }
        setDraftCatalog({
          provider_id: "__draft__",
          provider_name: providerDraft.name || providerDraft.provider_type,
          provider_type: providerDraft.provider_type,
          models: [],
        });
      }
    })();

    return () => {
      active = false;
    };
  }, [
    providerDraft.name,
    providerDraft.provider_type,
    providerDraft.base_url,
    providerDraft.api_format,
    providerDraft.auth_type,
    providerDraft.auth_value,
    providerDraft.auth_metadata,
    providerDraft.fallback_ids,
  ]);

  useEffect(() => {
    if (!editingId) {
      setEditCatalog(null);
      return;
    }

    let active = true;

    void (async () => {
      try {
        const result = await discoverModelCatalog({ provider_id: editingId });
        if (!active) {
          return;
        }
        setEditCatalog({
          provider_id: result.provider_id || editingId,
          provider_name: result.provider_name || editingId,
          provider_type: result.provider_type || "",
          models: Array.isArray(result.models) ? result.models : [],
          detected_at: result.detected_at,
        });
      } catch {
        if (!active) {
          return;
        }
        setEditCatalog(null);
      }
    })();

    return () => {
      active = false;
    };
  }, [editingId]);

  function startEdit(p: ProviderRecord) {
    setEditingId(p.id);
    setEditDraft({
      name: p.name,
      provider_type: p.provider_type,
      base_url: p.base_url || "",
      api_format: p.api_format,
      auth_type: p.auth_type,
      auth_value: "",
      fallback_ids: p.fallback_ids.join(","),
      auth_metadata: JSON.stringify(p.auth_metadata || {}, null, 2),
      default_model_ref: getResolvedDefaultModelRef(
        p.provider_type,
        p.auth_type,
        p.name,
        p.auth_metadata,
      ),
    });
  }

  function cancelEdit() { setEditingId(null); setEditDraft(null); }

  function saveEdit(id: string) {
    if (editDraft) { onUpdateProvider(id, editDraft); setEditingId(null); setEditDraft(null); }
  }

  return (
    <div className="tab-content">
      <div className="provider-layout">
        {/* Left: form */}
        <div className="panel">
          <div className="panel-head">
            <h2>添加 Provider</h2>
          </div>
          <form className="stack" onSubmit={onCreateProvider}>
            <div className="form-grid-2">
              <label className="field">
                <span>名称</span>
                <input value={providerDraft.name} onChange={(e) => setProviderDraft((d) => ({ ...d, name: e.target.value }))} placeholder="my-openai" required />
              </label>
              <label className="field">
                <span>Provider 类型</span>
                <select
                  value={providerDraft.provider_type}
                  onChange={(e) => {
                    const nextProviderType = e.target.value;
                    setProviderDraft((d) => ({
                      ...d,
                      provider_type: nextProviderType,
                      default_model_ref: getDefaultModelRefForProvider(
                        nextProviderType,
                        d.auth_type,
                        d.name,
                      ),
                    }));
                  }}
                >
                  {PROVIDER_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </label>
              <label className="field">
                <span>Base URL</span>
                <input value={providerDraft.base_url} onChange={(e) => setProviderDraft((d) => ({ ...d, base_url: e.target.value }))} placeholder="https://api.openai.com/v1" />
              </label>
              <label className="field">
                <span>API Format</span>
                <select value={providerDraft.api_format} onChange={(e) => setProviderDraft((d) => ({ ...d, api_format: e.target.value }))}>
                  {API_FORMATS.map((f) => <option key={f} value={f}>{f}</option>)}
                </select>
              </label>
              <label className="field">
                <span>默认模型</span>
                <ModelRefSelect
                  value={providerDraft.default_model_ref}
                  groups={buildDraftModelGroups(draftCatalog.models, providerDraft.default_model_ref)}
                  onChange={(value) => setProviderDraft((d) => ({ ...d, default_model_ref: value }))}
                  placeholder="自动同步模型"
                />
              </label>
              <label className="field">
                <span>Auth Type</span>
                <select
                  value={providerDraft.auth_type}
                  onChange={(e) => {
                    const nextAuthType = e.target.value;
                    setProviderDraft((d) => ({ ...d, auth_type: nextAuthType }));
                    if (nextAuthType === "api_key" || nextAuthType === "bearer") {
                      onProviderDraftAuthMethodChange(nextAuthType as ProviderAuthMethod);
                    } else if (nextAuthType === "oauth") {
                      onProviderDraftAuthMethodChange("browser");
                    }
                  }}
                >
                  {AUTH_TYPES.map((a) => <option key={a} value={a}>{a}</option>)}
                </select>
              </label>
              {providerDraft.auth_type !== "iam" && (
                <label className="field">
                  <span>认证方式</span>
                  <select
                    value={providerDraftAuthMethod}
                    onChange={(e) => onProviderDraftAuthMethodChange(e.target.value as ProviderAuthMethod)}
                  >
                    {providerDraftAuthOptions.map((option) => (
                      <option key={option.id} value={option.id} disabled={option.disabled}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              <label className="field">
                <span>Auth Value / API Key</span>
                <input
                  value={providerDraft.auth_value}
                  onChange={(e) => setProviderDraft((d) => ({ ...d, auth_value: e.target.value }))}
                  type={isApiKey ? "password" : "text"}
                  placeholder={isApiKey ? "sk-..." : isIam ? "（IAM 登录后自动填充）" : isOauth ? "（OAuth 登录后自动填充）" : "bearer token / helper path"}
                  disabled={isIam || isOauth}
                />
              </label>
            </div>
            <label className="field">
              <span>Fallback IDs（逗号分隔）</span>
              <input value={providerDraft.fallback_ids} onChange={(e) => setProviderDraft((d) => ({ ...d, fallback_ids: e.target.value }))} placeholder="id-1,id-2" />
            </label>
            <label className="field">
              <span>Auth Metadata (JSON)</span>
              <textarea
                value={providerDraft.auth_metadata}
                onChange={(e) => setProviderDraft((d) => ({ ...d, auth_metadata: e.target.value }))}
                placeholder='{"token_endpoint":"https://example.com/token"}'
                rows={5}
              />
            </label>
            <p className="hint-text">💡 本地 Ollama 无需填写 API Key，Base URL 填 http://127.0.0.1:11434/v1</p>
            <div className="row-actions">
              <button type="submit" className="primary-button">保存 Provider</button>
            </div>
          </form>
        </div>

        {/* Right: list */}
        <div className="panel">
          <div className="panel-head">
            <h2>已配置 Provider</h2>
            <span className="badge">{providers.length}</span>
          </div>
          {providers.length === 0
            ? <div className="empty-state">还没有 Provider，请在左侧添加。</div>
            : (
              <div className="provider-cards">
                {providers.map((p) => {
                  const health = providerHealth[p.id];
                  const diagnostic = p.last_diagnostic;
                  const providersById = new Map(providers.map((item) => [item.id, item]));
                  const statusSummary = buildProviderStatusSummary(p, providersById);
                  const flow = authFlows[p.id];
                  const interactiveAuthVisible = statusSummary.showInteractiveAuth;
                  const authOptions = getProviderAuthOptions(p);
                  const selectedAuthMethod = authMethodDrafts[p.id] || defaultProviderAuthMethod(p);
                  const selectedAuthOption = authOptions.find((option) => option.id === selectedAuthMethod) || authOptions[0];
                  const authRequest = selectedAuthOption ? buildInteractiveAuthRequest(p, selectedAuthOption.id) : null;
                  const authValueDraft = authValueDrafts[p.id] || "";
                  return (
                    <div className="provider-card" key={p.id}>
                      <div className="provider-card-head">
                        <div>
                          <strong>{p.name}</strong>
                          <span className="tag">{p.provider_type}</span>
                          <span className="tag">{statusSummary.authLabel}</span>
                          <span className={`tag tag-route-${statusSummary.routeTone}`}>{statusSummary.routeLabel}</span>
                        </div>
                        <div className="provider-card-actions">
                          {health === true && <span className="health-ok">✓ 可用</span>}
                          {health === false && <span className="health-fail">✗ 不可用</span>}
                          {health === null && <span className="health-checking">检查中…</span>}
                          <button className="ghost-button small" onClick={() => onHealthCheck(p.id)}>健康检查</button>
                          {p.auth_status !== "missing" && (
                            <button className="ghost-button small" onClick={() => onLogoutProvider(p.id)}>退出登录</button>
                          )}
                          <button className="ghost-button small" onClick={() => startEdit(p)}>编辑</button>
                          <button className="ghost-button small danger" onClick={() => onDeleteProvider(p.id)}>删除</button>
                          {p.auth_type === "iam" && interactiveAuthVisible && (!flow || flow.status === "failed" || flow.status === "expired") && (
                            <button className="ghost-button small" onClick={() => {
                              const url = prompt("SSO Start URL") || "";
                              const region = prompt("SSO Region", "us-east-1") || "us-east-1";
                              if (url) onStartAuthFlow(p.id, "aws_iam", { sso_start_url: url, sso_region: region });
                            }}>AWS 登录</button>
                          )}
                        </div>
                      </div>
                      <div className={`provider-diagnostic ${diagnostic?.healthy ? "provider-diagnostic-ok" : "provider-diagnostic-neutral"}`}>
                        <strong>当前状态</strong>
                        <p>{statusSummary.diagnosticSummary || "待验证"}</p>
                        <div className="provider-meta-row provider-status-grid">
                          <span>{`认证状态：${statusSummary.authLabel}`}</span>
                          <span>{`路由状态：${statusSummary.routeLabel}`}</span>
                        </div>
                        {statusSummary.diagnosticDetail && <p>{statusSummary.diagnosticDetail}</p>}
                        {statusSummary.currentRouteLabel && (
                          <div className="provider-meta-row provider-status-grid">
                            <span>{`当前路由：${statusSummary.currentRouteLabel}`}</span>
                            {statusSummary.nextRouteLabel ? <span>{`下一跳：${statusSummary.nextRouteLabel}`}</span> : null}
                          </div>
                        )}
                        {diagnostic?.fallback_provider_name && (
                          <p>{`当前已切换到 fallback：${diagnostic.fallback_provider_name}`}</p>
                        )}
                      </div>
                      {statusSummary.fallbackTopologyLabel && (
                        <div className="provider-meta-row">
                          <span>{`Fallback: ${statusSummary.fallbackTopologyLabel}`}</span>
                        </div>
                      )}
                      {(statusSummary.diagnosticTimeLabel || statusSummary.diagnosticReasonLabel) && (
                        <div className="provider-meta-row">
                          {statusSummary.diagnosticTimeLabel && <span>{statusSummary.diagnosticTimeLabel}</span>}
                          {statusSummary.diagnosticReasonLabel && <span>{statusSummary.diagnosticReasonLabel}</span>}
                        </div>
                      )}
                      {statusSummary.recentEvents.length > 0 && (
                        <div className="provider-diagnostic provider-diagnostic-neutral">
                          <strong>最近事件</strong>
                          {statusSummary.recentEvents.map((item) => <p key={`${p.id}-${item}`}>{item}</p>)}
                        </div>
                      )}

                      {/* 编辑模式 */}
                      {editingId === p.id && editDraft ? (
                        <div className="edit-form">
                          <div className="form-grid-2">
                            <label className="field"><span>名称</span><input value={editDraft.name} onChange={(e) => setEditDraft((d) => d && ({ ...d, name: e.target.value }))} /></label>
                            <label className="field">
                              <span>Provider 类型</span>
                              <select
                                value={editDraft.provider_type}
                                onChange={(e) => {
                                  const nextProviderType = e.target.value;
                                  setEditDraft((d) => d && ({
                                    ...d,
                                    provider_type: nextProviderType,
                                    default_model_ref: getDefaultModelRefForProvider(
                                      nextProviderType,
                                      d.auth_type,
                                      d.name,
                                    ),
                                  }));
                                }}
                              >
                                {PROVIDER_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                              </select>
                            </label>
                            <label className="field"><span>Base URL</span><input value={editDraft.base_url} onChange={(e) => setEditDraft((d) => d && ({ ...d, base_url: e.target.value }))} /></label>
                            <label className="field"><span>API Format</span><select value={editDraft.api_format} onChange={(e) => setEditDraft((d) => d && ({ ...d, api_format: e.target.value }))}>{API_FORMATS.map((f) => <option key={f} value={f}>{f}</option>)}</select></label>
                          <label className="field">
                            <span>默认模型</span>
                            <ModelRefSelect
                              value={editDraft.default_model_ref}
                              groups={buildDraftModelGroups(editCatalog?.models || [], editDraft.default_model_ref)}
                              onChange={(value) => setEditDraft((d) => d && ({ ...d, default_model_ref: value }))}
                              placeholder="自动同步模型"
                            />
                          </label>
                            <label className="field">
                              <span>Auth Type</span>
                              <select
                                value={editDraft.auth_type}
                                onChange={(e) => {
                                  const nextAuthType = e.target.value;
                                  setEditDraft((d) => d && ({
                                    ...d,
                                    auth_type: nextAuthType,
                                    default_model_ref: getDefaultModelRefForProvider(
                                      d.provider_type,
                                      nextAuthType,
                                      d.name,
                                    ),
                                  }));
                                }}
                              >
                                {AUTH_TYPES.map((a) => <option key={a} value={a}>{a}</option>)}
                              </select>
                            </label>
                            <label className="field"><span>Auth Value</span><input type={editDraft.auth_type === "api_key" ? "password" : "text"} value={editDraft.auth_value} onChange={(e) => setEditDraft((d) => d && ({ ...d, auth_value: e.target.value }))} placeholder="留空则不修改" /></label>
                          </div>
                          <label className="field"><span>Auth Metadata (JSON)</span><textarea value={editDraft.auth_metadata} onChange={(e) => setEditDraft((d) => d && ({ ...d, auth_metadata: e.target.value }))} rows={5} /></label>
                          <div className="row-actions" style={{ marginTop: 8 }}>
                            <button className="ghost-button small" onClick={cancelEdit}>取消</button>
                            <button className="primary-button small" onClick={() => saveEdit(p.id)}>保存修改</button>
                          </div>
                        </div>
                      ) : (
                        <>
                          <div className="provider-meta-row">
                            <span>{p.api_format}</span>
                            <span>{p.auth_type}</span>
                            <span className="muted-text">{p.base_url || "default endpoint"}</span>
                          </div>
                          {p.auth_type !== "iam" && authOptions.length > 0 && (
                            <div className="auth-flow-box">
                              <label className="field">
                                <span>认证方式</span>
                                <select
                                  value={selectedAuthMethod}
                                  onChange={(e) => setAuthMethodDrafts((current) => ({ ...current, [p.id]: e.target.value as ProviderAuthMethod }))}
                                >
                                  {authOptions.map((option) => (
                                    <option key={option.id} value={option.id} disabled={option.disabled}>
                                      {option.label}
                                    </option>
                                  ))}
                                </select>
                              </label>
                              {selectedAuthOption?.helpText && (
                                <p className="muted-text">{selectedAuthOption.helpText}</p>
                              )}
                              {(selectedAuthMethod === "api_key" || selectedAuthMethod === "bearer") && (
                                <div className="row-actions">
                                  <input
                                    type={selectedAuthMethod === "api_key" ? "password" : "text"}
                                    value={authValueDraft}
                                    placeholder={selectedAuthMethod === "api_key" ? "sk-..." : "Bearer token"}
                                    onChange={(e) => setAuthValueDrafts((current) => ({ ...current, [p.id]: e.target.value }))}
                                  />
                                  <button
                                    className="primary-button small"
                                    onClick={() => onQuickProviderAuthSave(p, selectedAuthMethod, authValueDraft)}
                                  >
                                    保存凭据
                                  </button>
                                </div>
                              )}
                              {(selectedAuthMethod === "browser" || selectedAuthMethod === "device_code") && (
                                <div className="row-actions">
                                  <button
                                    className="ghost-button small"
                                    disabled={!authRequest}
                                    onClick={() => authRequest && onStartAuthFlow(p.id, authRequest.flowType, authRequest.extra)}
                                  >
                                    {selectedAuthMethod === "browser" ? "开始浏览器登录" : "开始 Device Code 登录"}
                                  </button>
                                </div>
                              )}
                            </div>
                          )}
                        </>
                      )}
                      {flow && flow.status === "pending" && (
                        <div className="auth-flow-box">
                          <p>🔐 请在浏览器完成授权：</p>
                          <a href={flow.verificationUri} target="_blank" rel="noreferrer" className="auth-link">{flow.verificationUri}</a>
                          {flow.userCode && <p>设备码：<code>{flow.userCode}</code></p>}
                          <p className="muted-text">轮询中，请稍候…</p>
                          <button className="ghost-button small" onClick={() => onCancelAuthFlow(p.id)}>取消登录</button>
                        </div>
                      )}
                      {flow && flow.status === "awaiting_role" && (
                        <div className="auth-flow-box">
                          <p>✅ 授权完成，请选择 AWS 账号和角色：</p>
                          <div style={{ display: "flex", gap: "8px", width: "100%", marginBottom: "8px" }}>
                            {flow.accounts && flow.accounts.length > 0 ? (
                              <select 
                                style={{ flex: 1 }}
                                value={(awsRoleSelection[p.id] || {}).accountId || ""} 
                                onChange={(e) => setAwsRoleSelection((prev) => ({ ...prev, [p.id]: { ...(prev[p.id] || { roleName: "" }), accountId: e.target.value } }))}
                              >
                                <option value="">选择账号</option>
                                {flow.accounts.map((acc) => <option key={acc.accountId} value={acc.accountId}>{acc.accountName} ({acc.accountId})</option>)}
                              </select>
                            ) : (
                              <input 
                                style={{ flex: 1 }}
                                placeholder="没拉取到列表，请手动输入 12 位 AWS 账号 ID" 
                                value={(awsRoleSelection[p.id] || {}).accountId || ""} 
                                onChange={(e) => setAwsRoleSelection((prev) => ({ ...prev, [p.id]: { ...(prev[p.id] || { roleName: "" }), accountId: e.target.value } }))} 
                              />
                            )}
                            <input 
                              style={{ flex: 1 }}
                              placeholder="角色名称，如 AWSAdministratorAccess" 
                              value={(awsRoleSelection[p.id] || {}).roleName || ""} 
                              onChange={(e) => setAwsRoleSelection((prev) => ({ ...prev, [p.id]: { ...(prev[p.id] || { accountId: "" }), roleName: e.target.value } }))} 
                            />
                          </div>
                          <div className="row-actions">
                            <button className="primary-button small" onClick={() => onBindAwsRole(p.id)}>绑定角色</button>
                            <button className="ghost-button small" onClick={() => onCancelAuthFlow(p.id)}>取消登录</button>
                          </div>
                        </div>
                      )}
                      {flow && flow.status === "completed" && <div className="auth-success">✅ 认证完成</div>}
                      {flow && flow.status === "cancelled" && <div className="muted-text">已取消登录，可重新发起认证。</div>}
                      {flow && flow.status === "failed" && <div className="auth-error">❌ 认证失败：{flow.errorMessage}</div>}
                    </div>
                  );
                })}
              </div>
            )}
        </div>
      </div>
    </div>
  );
}

// ─── Tab 2: Create Session ────────────────────────────────────────────────────
interface TabCreateSessionProps {
  topic: string; setTopic: (v: string) => void;
  mode: CollaborationMode; setMode: (v: CollaborationMode) => void;
  selectedTemplateId: string | null;
  setSelectedTemplateId: (id: string | null) => void;
  workspaceDraft: WorkspaceDraftState;
  participants: ParticipantConfig[];
  providers: ProviderRecord[];
  providerCatalogs: ProviderModelCatalog[];
  loading: boolean;
  onWorkspaceDraftChange: (update: WorkspaceDraftUpdate) => void;
  onUpdateParticipant: (i: number, p: Partial<ParticipantConfig>) => void;
  onAddParticipant: () => void;
  onRemoveParticipant: (i: number) => void;
  onApplyWorkspaceTaskPreset: (preset: WorkspaceTaskPresetRecommendation) => void;
  onApplyWorkspaceTeamPreset: (preset: WorkspaceTeamPresetRecommendation) => void;
  onSubmit: (e: FormEvent) => void;
}

function TabCreateSession({
  topic, setTopic, mode, setMode, selectedTemplateId, setSelectedTemplateId, workspaceDraft, participants, providers, providerCatalogs, loading,
  onWorkspaceDraftChange, onUpdateParticipant, onAddParticipant, onRemoveParticipant,
  onApplyWorkspaceTaskPreset, onApplyWorkspaceTeamPreset, onSubmit,
}: TabCreateSessionProps) {
  const workspaceAliases = participants.map((participant) => participant.custom_id.trim()).filter(Boolean);
  const [labsOpen, setLabsOpen] = useState(false);
  const activeTemplate =
    PRIMARY_TASK_TEMPLATES.find((template) => template.id === selectedTemplateId) || null;
  return (
    <div className="tab-content">
      <form className="stack" onSubmit={onSubmit}>
        {/* Topic */}
        <div className="panel">
          <label className="field">
            <span className="field-label">任务目标</span>
            <textarea
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              rows={3}
              placeholder="例：分析这个仓库的核心模块与风险点，或：修复登录重试失败的问题并给出验证结果"
              required
            />
          </label>
        </div>

        {/* Mode */}
        <div className="panel">
          <h3 className="section-title">任务模板</h3>
          <div className="template-grid">
            {PRIMARY_TASK_TEMPLATES.map((template) => (
              <button
                type="button"
                key={template.id}
                className={`mode-card${template.id === selectedTemplateId ? " mode-card-active" : ""}`}
                onClick={() => {
                  setSelectedTemplateId(template.id);
                  setMode(template.mode);
                  const nextTopic =
                    !topic.trim() || TEMPLATE_DEFAULT_TOPICS.has(topic.trim())
                      ? template.defaultTopic
                      : topic;
                  setTopic(nextTopic);
                }}
              >
                <span className="mode-icon">{template.icon}</span>
                <strong>{template.label}</strong>
                <span>{template.blurb}</span>
              </button>
            ))}
          </div>
          <div className="template-meta">
            <span className="badge">{activeTemplate?.label || "Low-level mode"}</span>
            <span className="muted-text">
              主模板优先覆盖真实仓库分析、修复、评审和方案比较。
            </span>
          </div>
          <div className="labs-panel">
            <div className="panel-head compact">
              <div>
                <h4 className="workspace-card-title">实验模式</h4>
                <p className="hint-text workspace-panel-intro">
                  保留全部底层模式用于高级调试、实验玩法和兼容旧流程。
                </p>
              </div>
              <button
                type="button"
                className="ghost-button small"
                onClick={() => setLabsOpen((current) => !current)}
              >
                {labsOpen ? "收起实验模式" : "展开实验模式"}
              </button>
            </div>
            {labsOpen ? (
              <div className="mode-grid mode-grid-secondary">
                {MODE_OPTIONS.map((opt) => (
                  <button
                    type="button"
                    key={opt.value}
                    className={`mode-card${selectedTemplateId === null && opt.value === mode ? " mode-card-active" : ""}`}
                    onClick={() => {
                      setSelectedTemplateId(null);
                      setMode(opt.value);
                    }}
                  >
                    <span className="mode-icon">{MODE_ICONS[opt.value] || "🤖"}</span>
                    <strong>{opt.label}</strong>
                    <span>{opt.blurb}</span>
                  </button>
                ))}
              </div>
            ) : null}
          </div>
        </div>

        {mode === "code_workspace" && (
          <WorkspaceCreatePanel
            draft={workspaceDraft}
            aliases={workspaceAliases}
            onChange={onWorkspaceDraftChange}
            onApplyTaskPreset={onApplyWorkspaceTaskPreset}
            onApplyTeamPreset={onApplyWorkspaceTeamPreset}
          />
        )}

        {/* Participants */}
        <div className="panel">
          <div className="panel-head">
            <h3 className="section-title" style={{ margin: 0 }}>执行参与者</h3>
            <span className="badge">{participants.length} 个</span>
          </div>
          <div className="participant-list">
            {participants.map((p, i) => {
              const resolvedSelection = resolveParticipantModelSelection(providerCatalogs, {
                provider_id: p.provider_id,
                model_ref: p.model_ref,
              });
              const selectionValue = formatParticipantModelSelection(
                resolvedSelection.provider_id,
                resolvedSelection.model_ref,
              );
              const modelGroups = buildParticipantModelGroups(
                providerCatalogs,
                resolvedSelection.provider_id,
                selectionValue,
              );

              return (
                <div className="participant-card" key={`participant-${i}`}>
                  <div className="participant-card-head">
                    <strong>参与者 {i + 1}</strong>
                    <button type="button" className="ghost-button small danger" onClick={() => onRemoveParticipant(i)} disabled={participants.length <= 2}>删除</button>
                  </div>
                  <div className="form-grid-2">
                    <label className="field">
                      <span>Custom_ID</span>
                      <input value={p.custom_id} onChange={(e) => onUpdateParticipant(i, { custom_id: e.target.value })} placeholder="Model_A" />
                    </label>
                    <label className="field">
                      <span>Provider（可选）</span>
                      <select
                        value={p.provider_id || ""}
                        onChange={(e) =>
                          onUpdateParticipant(i, {
                            provider_id: e.target.value || undefined,
                            model_ref: "",
                          })
                        }
                      >
                        <option value="">自动匹配（按所选模型推断）</option>
                        {providers.map((pv) => <option key={pv.id} value={pv.id}>{pv.name} ({pv.provider_type})</option>)}
                      </select>
                    </label>
                    <label className="field">
                      <span>模型选择</span>
                      <ModelRefSelect
                        value={selectionValue}
                        groups={modelGroups}
                        onChange={(value) => {
                          const parsed = parseParticipantModelSelection(value);
                          const resolved = resolveParticipantModelSelection(providerCatalogs, parsed);
                          onUpdateParticipant(i, {
                            provider_id: resolved.provider_id,
                            model_ref: resolved.model_ref,
                          });
                        }}
                      />
                    </label>
                    <label className="field">
                      <span>Role（可选角色描述）</span>
                      <input value={p.role_desc || ""} onChange={(e) => onUpdateParticipant(i, { role_desc: e.target.value })} placeholder="正方辩手 / 代码审查者 / 侦探…" />
                    </label>
                  </div>
                </div>
              );
            })}
          </div>
          <div className="row-actions" style={{ marginTop: 12 }}>
            <button type="button" className="ghost-button" onClick={onAddParticipant}>＋ 添加参与者</button>
            <button type="submit" className="primary-button" disabled={loading}>
              {loading ? "创建中…" : "🚀 新建任务"}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}

// ─── Tab 3: Session Detail ────────────────────────────────────────────────────
interface TabSessionDetailProps {
  session: SessionDetail | null;
  sessionList: SessionListItem[];
  workspace: SessionWorkspaceView | null;
  providers: ProviderRecord[];
  providerCatalogs: ProviderModelCatalog[];
  messages: ChatMessage[];
  executionEvents: ExecutionEventRecord[];
  streamState: StreamState;
  onSetStreamState: (state: StreamState) => void;
  autoStartToken: number;
  snapshot: SessionSnapshot;
  setSnapshot: React.Dispatch<React.SetStateAction<SessionSnapshot>>;
  snapshotOpen: boolean;
  setSnapshotOpen: (v: boolean) => void;
  historyExport: string;
  input: string;
  setInput: (v: string) => void;
  onSendMessage: (e: FormEvent) => void;
  onSelectSession: (sessionId: string) => void;
  onRenameSession: (sessionId: string) => void;
  onDeleteSession: (sessionId: string) => void;
  onAppendParticipant: (sessionId: string, payloads: ParticipantConfig[]) => Promise<void>;
  onToggleWorkspaceWriteMode: (sessionId: string, canWrite: boolean) => Promise<void>;
  onSaveSnapshot: () => void;
  onExportHistory: () => void;
  onStreamEvent: (eventName: string, payload: StreamPayload) => void;
}

function SessionDetailTabEnhanced(props: TabSessionDetailProps) {
  const { session, providers, providerCatalogs, streamState, onAppendParticipant } = props;
  const [appendOpen, setAppendOpen] = useState(false);
  const [appendDrafts, setAppendDrafts] = useState<ParticipantConfig[]>(() => [createEmptySessionParticipantDraft()]);
  const isStreaming = streamState === "connecting" || streamState === "streaming";

  useEffect(() => {
    setAppendOpen(false);
    setAppendDrafts([createEmptySessionParticipantDraft()]);
  }, [session?.id]);

  async function handleAppendParticipantSubmit(event: FormEvent) {
    event.preventDefault();
    if (!session || isStreaming || appendDrafts.some((item) => !item.model_ref.trim())) {
      return;
    }
    const normalizedDrafts = appendDrafts.map((item) => ({
      ...item,
      ...resolveParticipantModelSelection(providerCatalogs, item),
    }));
    try {
      await onAppendParticipant(session.id, normalizedDrafts);
      setAppendDrafts([createEmptySessionParticipantDraft()]);
      setAppendOpen(false);
    } catch {
      // The child callback already surfaces the error toast. Keep the form open.
    }
  }

  function handleAppendDraftChange(index: number, patch: Partial<ParticipantConfig>) {
    setAppendDrafts((current) =>
      current.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...patch } : item,
      ),
    );
  }

  function handleAppendDraftAddRow() {
    setAppendDrafts((current) => [...current, createEmptySessionParticipantDraft()]);
  }

  function handleAppendDraftRemoveRow(index: number) {
    setAppendDrafts((current) =>
      current.length <= 1
        ? [createEmptySessionParticipantDraft()]
        : current.filter((_, itemIndex) => itemIndex !== index),
    );
  }

  return (
    <div className="stack">
      {session ? (
        <div className="panel">
          <div className="panel-head">
            <h3>任务参与者</h3>
            <button
              type="button"
              className="ghost-button small"
              data-testid="session-participant-add-toggle"
              onClick={() => {
                if (isStreaming) {
                  return;
                }
                setAppendOpen((current) => !current);
              }}
              disabled={isStreaming}
            >
              {appendOpen ? "Cancel" : "+ Add participant"}
            </button>
          </div>
          <div className="workspace-chip-row">
            {session.participants.map((participant) => (
              <span key={participant.id} className="workspace-chip">@{participant.custom_id}</span>
            ))}
          </div>
          <div className="status-label" style={{ marginTop: 10 }}>
            新参与者会从下一次运行开始接入，并复用当前任务上下文。
          </div>

          {appendOpen ? (
            <form className="participant-card" onSubmit={handleAppendParticipantSubmit} style={{ marginTop: 12 }}>
              <div className="participant-card-head">
                <strong>向当前任务追加参与者</strong>
                <span className="status-label">仅限活动任务</span>
              </div>
              <div className="stack">
                {appendDrafts.map((appendDraft, index) => {
                  const resolvedSelection = resolveParticipantModelSelection(providerCatalogs, {
                    provider_id: appendDraft.provider_id,
                    model_ref: appendDraft.model_ref,
                  });
                  const appendSelectionValue = formatParticipantModelSelection(
                    resolvedSelection.provider_id,
                    resolvedSelection.model_ref,
                  );
                  const appendModelGroups = buildParticipantModelGroups(
                    providerCatalogs,
                    resolvedSelection.provider_id,
                    appendSelectionValue,
                  );

                  return (
                    <div className="participant-card" key={`append-draft-${index}`}>
                      <div className="participant-card-head">
                        <strong>草稿 {index + 1}</strong>
                        <button
                          type="button"
                          className="ghost-button small danger"
                          data-testid="session-participant-row-remove"
                          onClick={() => handleAppendDraftRemoveRow(index)}
                          disabled={isStreaming}
                        >
                          删除
                        </button>
                      </div>
                      <div className="form-grid-2">
                        <label className="field">
                          <span>Provider</span>
                          <select
                            data-testid="session-participant-provider"
                            value={appendDraft.provider_id || ""}
                            onChange={(event) =>
                              handleAppendDraftChange(index, {
                                provider_id: event.target.value || undefined,
                                model_ref: "",
                              })
                            }
                            disabled={isStreaming}
                          >
                            <option value="">Auto match by selected model</option>
                            {providers.map((provider) => (
                              <option key={provider.id} value={provider.id}>
                                {provider.name} ({provider.provider_type})
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="field">
                          <span>Model</span>
                          <div data-testid="session-participant-model">
                            <ModelRefSelect
                              value={appendSelectionValue}
                              groups={appendModelGroups}
                              onChange={(value) => {
                                const parsed = parseParticipantModelSelection(value);
                                const resolved = resolveParticipantModelSelection(providerCatalogs, parsed);
                                handleAppendDraftChange(index, {
                                  provider_id: resolved.provider_id,
                                  model_ref: resolved.model_ref,
                                });
                              }}
                              disabled={isStreaming}
                            />
                          </div>
                        </label>
                        <label className="field">
                          <span>Alias</span>
                          <input
                            data-testid="session-participant-custom-id"
                            value={appendDraft.custom_id}
                            onChange={(event) =>
                              handleAppendDraftChange(index, {
                                custom_id: event.target.value,
                              })
                            }
                            placeholder="Reviewer"
                            disabled={isStreaming}
                          />
                        </label>
                        <label className="field">
                          <span>Role</span>
                          <input
                            data-testid="session-participant-role"
                            value={appendDraft.role_desc || ""}
                            onChange={(event) =>
                              handleAppendDraftChange(index, {
                                role_desc: event.target.value,
                              })
                            }
                            placeholder="review code"
                            disabled={isStreaming}
                          />
                        </label>
                      </div>
                    </div>
                  );
                })}
              </div>
              <div className="row-actions" style={{ marginTop: 12 }}>
                    <button
                      type="button"
                      className="ghost-button"
                      data-testid="session-participant-row-add"
                      onClick={handleAppendDraftAddRow}
                      disabled={isStreaming}
                    >
                      + 添加一行
                    </button>
                <button
                  type="submit"
                    className="primary-button"
                    data-testid="session-participant-submit"
                    disabled={isStreaming || appendDrafts.some((item) => !item.model_ref.trim())}
                  >
                    添加到任务
                  </button>
                </div>
              </form>
          ) : null}
        </div>
      ) : null}

      <TabSessionDetail {...props} />
    </div>
  );
}

function TabSessionDetail({
  session, sessionList, workspace, providers, providerCatalogs, messages, executionEvents, streamState, onSetStreamState, autoStartToken, snapshot, setSnapshot, snapshotOpen, setSnapshotOpen,
  historyExport, input, setInput,
  onSendMessage, onSelectSession, onRenameSession, onDeleteSession,
  onAppendParticipant, onToggleWorkspaceWriteMode, onSaveSnapshot, onExportHistory, onStreamEvent,
}: TabSessionDetailProps) {
  const closeStreamRef = useRef<(() => void) | null>(null);
  const streamTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const messageStreamRef = useRef<HTMLDivElement>(null);
  const composerFieldRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const autoScrollPinnedRef = useRef(true);
  const sessionResizeRef = useRef<{ startX: number; startWidth: number } | null>(null);
  const [clockTs, setClockTs] = useState(() => Date.now());
  const [rightPaneWidth, setRightPaneWidth] = useState(420);
  const [appendOpen, setAppendOpen] = useState(false);
  const [appendDraft, setAppendDraft] = useState<ParticipantConfig>(() => createEmptySessionParticipantDraft());
  const [expandedExecutionKeys, setExpandedExecutionKeys] = useState<string[]>([]);
  const [mentionRange, setMentionRange] = useState<MentionRange | null>(null);
  const [mentionIndex, setMentionIndex] = useState(0);
  const isStreaming = streamState === "connecting" || streamState === "streaming";
  const lastAutoStartTokenRef = useRef(0);
  const appendSelectionValue = formatParticipantModelSelection(
    appendDraft.provider_id,
    appendDraft.model_ref,
  );
  const appendModelGroups = buildParticipantModelGroups(
    providerCatalogs,
    appendDraft.provider_id,
    appendSelectionValue,
  );
  const streamStateLabel =
    streamState === "connecting"
      ? "连接中"
      : streamState === "streaming"
        ? "执行中"
        : streamState === "completed"
          ? "已完成"
          : streamState === "failed"
            ? "失败"
        : "空闲";
  const currentClockLabel = new Date(clockTs).toLocaleTimeString("zh-CN", { hour12: false });
  const composerPlaceholder =
    session?.mode === "code_workspace"
      ? "普通消息只记录；用 @alias 或 @all 触发工作区执行，例如：@claude 先做方案"
      : "输入用户消息，发送后由后端接力调度…";
  const mentionCandidates =
    session && mentionRange
      ? buildMentionCandidates(session.participants, mentionRange.query)
      : [];
  const mentionOpen = Boolean(session && mentionRange);
  const activeMentionCandidate =
    mentionCandidates.length > 0
      ? mentionCandidates[Math.min(mentionIndex, mentionCandidates.length - 1)]
      : null;
  const emptyMessage =
    session?.mode === "code_workspace"
      ? "点击\"▶ 继续执行\"或发送带 @alias 的任务，模型会按工作区上下文流式输出。"
      : "点击\"▶ 继续执行\"触发模型发言，或在下方输入用户消息。";

  useEffect(() => {
    return () => {
      closeStreamRef.current?.();
      clearStreamTimeout();
    };
  }, []);

  useEffect(() => {
    const timer = setInterval(() => setClockTs(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    const handleMouseMove = (event: MouseEvent) => {
      if (!sessionResizeRef.current) {
        return;
      }
      const delta = sessionResizeRef.current.startX - event.clientX;
      setRightPaneWidth(clampPaneWidth(sessionResizeRef.current.startWidth + delta));
    };
    const handleMouseUp = () => {
      sessionResizeRef.current = null;
    };

    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, []);

  useEffect(() => {
    autoScrollPinnedRef.current = true;
    setExpandedExecutionKeys([]);
  }, [session?.id]);

  useEffect(() => {
    closeStreamRef.current?.();
    closeStreamRef.current = null;
    clearStreamTimeout();
    onSetStreamState("idle");
    setAppendOpen(false);
    setAppendDraft(createEmptySessionParticipantDraft());
    setMentionRange(null);
    setMentionIndex(0);
  }, [session?.id]);

  useEffect(() => {
    if (!autoScrollPinnedRef.current) {
      return;
    }
    scrollMessageStreamToBottom(isStreaming ? "auto" : "smooth");
  }, [messages, isStreaming]);

  useEffect(() => {
    if (mentionCandidates.length === 0) {
      setMentionIndex(0);
      return;
    }
    setMentionIndex((current) => Math.min(current, mentionCandidates.length - 1));
  }, [mentionCandidates.length]);

  useEffect(() => {
    if (!mentionOpen || typeof document === "undefined") {
      return;
    }

    const handleDocumentPointerStart = (event: MouseEvent | TouchEvent) => {
      const target = event.target;
      if (!(target instanceof Node)) {
        return;
      }
      if (composerFieldRef.current?.contains(target)) {
        return;
      }
      setMentionRange(null);
      setMentionIndex(0);
    };

    document.addEventListener("mousedown", handleDocumentPointerStart, true);
    document.addEventListener("touchstart", handleDocumentPointerStart, true);
    return () => {
      document.removeEventListener("mousedown", handleDocumentPointerStart, true);
      document.removeEventListener("touchstart", handleDocumentPointerStart, true);
    };
  }, [mentionOpen]);

  useEffect(() => {
    if (!session || session.mode !== "code_workspace") {
      lastAutoStartTokenRef.current = autoStartToken;
      return;
    }
    if (autoStartToken > lastAutoStartTokenRef.current && !isStreaming) {
      handleNextRound();
    }
    lastAutoStartTokenRef.current = autoStartToken;
  }, [autoStartToken, session?.id, session?.mode, isStreaming]);

  function clearStreamTimeout() {
    if (streamTimeoutRef.current) {
      clearTimeout(streamTimeoutRef.current);
      streamTimeoutRef.current = null;
    }
  }

  function scheduleStreamTimeout(close: () => void) {
    clearStreamTimeout();
    streamTimeoutRef.current = setTimeout(() => {
      streamTimeoutRef.current = null;
      close();
      onStreamEvent("error", { message: "SSE 请求超时，请重试。" });
    }, 180000);
  }

  function isNearMessageStreamBottom(node: HTMLDivElement | null) {
    if (!node) {
      return true;
    }
    return node.scrollHeight - node.clientHeight - node.scrollTop <= 72;
  }

  function handleMessageStreamScroll(event: React.UIEvent<HTMLDivElement>) {
    autoScrollPinnedRef.current = isNearMessageStreamBottom(event.currentTarget);
  }

  function scrollMessageStreamToBottom(behavior: ScrollBehavior) {
    const node = messageStreamRef.current;
    if (!node) {
      return;
    }
    if (typeof node.scrollTo === "function") {
      node.scrollTo({
        top: node.scrollHeight,
        behavior,
      });
      return;
    }
    node.scrollTop = node.scrollHeight;
  }

  function syncMentionRange(value: string, caretIndex: number) {
    const nextRange = findActiveMentionRange(value, caretIndex);
    setMentionRange(nextRange);
    setMentionIndex(0);
  }

  function insertMention(candidate: MentionCandidate) {
    if (!mentionRange) {
      return;
    }
    const replacement = `@${candidate.customId} `;
    const nextValue =
      input.slice(0, mentionRange.start) +
      replacement +
      input.slice(mentionRange.end);
    const nextCaret = mentionRange.start + replacement.length;
    setInput(nextValue);
    setMentionRange(null);
    setMentionIndex(0);
    window.requestAnimationFrame(() => {
      composerRef.current?.focus();
      composerRef.current?.setSelectionRange(nextCaret, nextCaret);
    });
  }

  function handleComposerChange(event: React.ChangeEvent<HTMLTextAreaElement>) {
    const nextValue = event.target.value;
    setInput(nextValue);
    syncMentionRange(nextValue, event.target.selectionStart ?? nextValue.length);
  }

  function handleComposerSelect(event: React.SyntheticEvent<HTMLTextAreaElement>) {
    const target = event.currentTarget;
    syncMentionRange(target.value, target.selectionStart ?? target.value.length);
  }

  function handleComposerKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (mentionOpen && mentionCandidates.length > 0) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setMentionIndex((current) => (current + 1) % mentionCandidates.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setMentionIndex((current) => (current - 1 + mentionCandidates.length) % mentionCandidates.length);
        return;
      }
      if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault();
        insertMention(activeMentionCandidate || mentionCandidates[0]);
        return;
      }
    }

    if (mentionOpen && event.key === "Escape") {
      event.preventDefault();
      setMentionRange(null);
      setMentionIndex(0);
      return;
    }

    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onSendMessage(event as unknown as FormEvent);
    }
  }

  function startSessionResize(event: React.MouseEvent<HTMLDivElement>) {
    sessionResizeRef.current = {
      startX: event.clientX,
      startWidth: rightPaneWidth,
    };
    event.preventDefault();
  }

  function handleNextRound() {
    if (!session) {
      return;
    }
    if (isStreaming) {
      closeStreamRef.current?.();
      clearStreamTimeout();
      onStreamEvent("error", { message: "生成已手动停止。" });
      return;
    }
    autoScrollPinnedRef.current = true;
    scrollMessageStreamToBottom("smooth");
    onSetStreamState("connecting");
    // 每次调用 GET /stream 都会触发后端 dispatch_round 调度完整一轮
    const close = openSessionStream(session.id, (eventName, payload) => {
      onStreamEvent(eventName, payload);
      // round_end / session_end / error 后主动关闭 EventSource 防止自动重连
      if (eventName === "round_end" || eventName === "session_end" || eventName === "error") {
        clearStreamTimeout();
        close();
        return;
      }
      scheduleStreamTimeout(close);
    });
    closeStreamRef.current = close;
    // 60 秒无任何事件则认为连接或流已卡住；有新事件时续期
    scheduleStreamTimeout(close);
  }

  async function handleAppendParticipantSubmit(event: FormEvent) {
    event.preventDefault();
    if (!session || isStreaming || !appendDraft.model_ref.trim()) {
      return;
    }
    try {
      await onAppendParticipant(session.id, [appendDraft]);
      setAppendDraft(createEmptySessionParticipantDraft());
      setAppendOpen(false);
    } catch {
      // The child callback already surfaces the error toast. Keep the form open.
    }
  }

  return (
    <div className="tab-content">
      <div className="run-detail-header panel">
        <div>
          <p className="eyebrow">Run Detail</p>
          <h2 className="section-title" style={{ marginBottom: 6 }}>运行详情</h2>
          <p className="subtitle" style={{ margin: 0 }}>
            围绕当前任务查看实时输出、工作区上下文、验证结果和下一步动作。
          </p>
        </div>
      </div>
      <div
        className="session-layout"
        style={{
          gridTemplateColumns:
            typeof window !== "undefined" && window.innerWidth <= 980
              ? undefined
              : `minmax(0, 1fr) 12px ${rightPaneWidth}px`,
        }}
      >
        {/* Left: chat */}
        <div className="panel chat-panel-new">
          <nav className="session-history-strip" aria-label="Tasks and runs">
            {sessionList.map((item) => (
              <div
                key={item.id}
                className={`session-history-item${item.id === session?.id ? " session-history-item-active" : ""}`}
                data-active={item.id === session?.id}
              >
                <button
                  type="button"
                  className={`session-history-button${item.id === session?.id ? " session-history-button-active" : ""}`}
                  onClick={() => onSelectSession(item.id)}
                >
                  <strong>{item.title || item.topic}</strong>
                  <span>{item.mode} · Run {item.current_round}</span>
                </button>
                <div className="session-history-actions">
                  <button
                    type="button"
                    className="ghost-button small"
                    aria-label={`重命名 ${item.title || item.topic}`}
                    onClick={() => onRenameSession(item.id)}
                  >
                    重命名
                  </button>
                  <button
                    type="button"
                    className="ghost-button small danger"
                    aria-label={`删除 ${item.title || item.topic}`}
                    onClick={() => onDeleteSession(item.id)}
                  >
                    删除
                  </button>
                </div>
              </div>
            ))}
          </nav>

          {/* Status bar */}
          {session ? (
            <div className="session-status-bar">
              <span className="status-item"><span className="status-label">任务目标</span>{session.title || session.topic}</span>
              <span className="status-item"><span className="status-label">运行 ID</span>{session.id.slice(0, 12)}…</span>
              <span className="status-item"><span className="status-label">模板</span>{session.mode}</span>
              <span className="status-item"><span className="status-label">当前运行</span>{session.current_round}</span>
              <span className="status-item"><span className="status-label">时间</span>{currentClockLabel}</span>
              <span className="status-item"><span className="status-label">执行状态</span>{streamStateLabel}</span>
              <span className={`status-badge status-${session.status}`}>{session.status}</span>
            </div>
          ) : (
            <div className="empty-state">历史任务已加载，点击上方任务标题重新进入。</div>
          )}

          {/* Messages */}
          <div
            ref={messageStreamRef}
            className="message-stream"
            onScroll={handleMessageStreamScroll}
          >
            {!session
              ? <div className="empty-state">请选择一个历史任务查看运行输出和任务侧栏。</div>
              : messages.length === 0
              ? <div className="empty-state">{emptyMessage}</div>
              : messages.map((msg) => (
                msg.type === "execution" ? (
                  (() => {
                    const structuredCard = isStructuredExecutionCard({
                      executionEvent: msg.executionEvent,
                      executionMetadata: msg.executionMetadata,
                    });
                    const commandCard = isCommandExecutionCard({
                      executionEvent: msg.executionEvent,
                      executionMetadata: msg.executionMetadata,
                    });
                    const detailSummary = msg.executionDetail && !structuredCard
                      ? summarizeExecutionBubbleDetail(msg.executionDetail)
                      : null;
                    const expansionKey = msg.executionCorrelationKey || msg.id;
                    const isExpanded = expandedExecutionKeys.includes(expansionKey);
                    const executionSurface = commandCard ? "console" : "default";
                    const visibleDetail = msg.executionDetail
                      ? commandCard || structuredCard || isExpanded || !detailSummary?.collapsible
                        ? msg.executionDetail
                        : detailSummary.preview
                      : null;
                    return (
                  <div
                    key={msg.id}
                    className={`bubble bubble-execution bubble-execution-inline${executionSurface === "console" ? " bubble-execution-console" : ""}${msg.status === "warning" ? " bubble-warning" : ""}${msg.status === "error" ? " bubble-error" : ""}`}
                    data-message-type="execution"
                    data-execution-surface={executionSurface}
                    data-execution-kind={msg.executionKind || "unknown"}
                    data-execution-phase={msg.executionPhase || ""}
                  >
                    <div className="execution-bubble-head">
                      <span className={`execution-bubble-kind execution-bubble-kind-${msg.executionKind || "unknown"}`}>
                        {labelForExecutionBubbleKind(msg.executionKind)}
                      </span>
                      {msg.executionPhase ? <code className="execution-phase">{msg.executionPhase}</code> : null}
                      <span className="workspace-path">Run {msg.round}</span>
                    </div>
                    <div className="execution-bubble-title">{msg.executionTitle || msg.content}</div>
                    {visibleDetail
                      ? renderExecutionBubbleDetail(
                        msg.executionTitle || msg.content,
                        visibleDetail,
                        {
                          executionEvent: msg.executionEvent,
                          executionKind: msg.executionKind,
                          executionMetadata: msg.executionMetadata,
                        },
                        commandCard ? { expandedRawOutput: isExpanded } : undefined,
                      )
                      : null}
                    {commandCard && msg.executionDetail ? (
                      <button
                        type="button"
                        className="execution-bubble-toggle"
                        onClick={() =>
                          setExpandedExecutionKeys((current) =>
                            current.includes(expansionKey)
                              ? current.filter((id) => id !== expansionKey)
                              : [...current, expansionKey],
                          )
                        }
                      >
                        {isExpanded ? "收起原始输出" : "展开原始输出"}
                      </button>
                    ) : detailSummary?.collapsible ? (
                      <button
                        type="button"
                        className="execution-bubble-toggle"
                        onClick={() =>
                          setExpandedExecutionKeys((current) =>
                            current.includes(expansionKey)
                              ? current.filter((id) => id !== expansionKey)
                              : [...current, expansionKey],
                          )
                        }
                      >
                        {isExpanded ? "收起详情" : "展开详情"}
                      </button>
                    ) : null}
                  </div>
                    );
                  })()
                ) : (
                  <div
                    key={msg.id}
                    className={`bubble bubble-${msg.type}${msg.status === "warning" ? " bubble-warning" : ""}${msg.status === "error" ? " bubble-error" : ""}`}
                    data-message-type={msg.type}
                  >
                    {msg.type !== "system" && (
                      <div className="bubble-meta">
                        <strong>{msg.senderId}</strong>
                        <span>Run {msg.round}</span>
                      </div>
                    )}
                    <div className="bubble-content">
                      {renderStructuredText(msg.content)}
                      {msg.status === "streaming" && <span className="cursor-blink">▌</span>}
                    </div>
                    {typeof msg.driftScore === "number" && (
                      <div className="drift-flag">⚠ 偏题告警：{msg.driftScore.toFixed(2)}</div>
                    )}
                  </div>
                )
              ))
            }
          </div>

          {/* Actions */}
          <div className="chat-actions">
            <button
              type="button"
              className={isStreaming ? "ghost-button stop-btn" : "primary-button"}
              onClick={handleNextRound}
              disabled={!session}
            >
              {isStreaming ? "⏹ 停止" : "▶ 继续执行"}
            </button>
            <button type="button" className="ghost-button" onClick={onExportHistory} disabled={!session}>📥 导出运行记录</button>
          </div>

          {/* Composer */}
          <form className="composer" onSubmit={onSendMessage}>
            <div className="composer-field" ref={composerFieldRef}>
              {mentionOpen ? (
                <div className="mention-picker" role="listbox" aria-label="选择要 @ 的参与者">
                  {mentionCandidates.length > 0 ? (
                    mentionCandidates.map((candidate, index) => (
                      <button
                        key={candidate.id || candidate.customId}
                        type="button"
                        className={`mention-option${index === mentionIndex ? " mention-option-active" : ""}`}
                        role="option"
                        aria-selected={index === mentionIndex}
                        onMouseEnter={() => setMentionIndex(index)}
                        onMouseDown={(event) => {
                          event.preventDefault();
                          insertMention(candidate);
                        }}
                      >
                        <span className="mention-option-alias">@{candidate.customId}</span>
                        <span className="mention-option-model">{candidate.modelRef}</span>
                        {candidate.roleDesc ? (
                          <span className="mention-option-role">{candidate.roleDesc}</span>
                        ) : null}
                      </button>
                    ))
                  ) : (
                    <div className="mention-empty">没有匹配的参与者</div>
                  )}
                </div>
              ) : null}
              <textarea
                ref={composerRef}
                value={input}
                onChange={handleComposerChange}
                onSelect={handleComposerSelect}
                onClick={handleComposerSelect}
                rows={2}
                placeholder={composerPlaceholder}
                disabled={!session}
                onKeyDown={handleComposerKeyDown}
              />
            </div>
            <button type="submit" className="primary-button send-btn" disabled={!session || !input.trim()}>发送</button>
          </form>

          {/* Export */}
          {historyExport && (
            <div className="export-block">
              <div className="panel-head compact"><h3>运行记录导出</h3></div>
              <pre>{historyExport}</pre>
            </div>
          )}
        </div>

        <div
          className="session-layout-resizer"
          data-session-layout-resizer="true"
          onMouseDown={startSessionResize}
        />

        {/* Right: workspace / snapshot */}
        <div className="panel snapshot-panel-new">
          <div className="panel-head">
            <h3>任务侧栏</h3>
            <button type="button" className="ghost-button small" onClick={() => setSnapshotOpen(!snapshotOpen)}>
              {snapshotOpen ? "隐藏任务快照" : "展开任务快照"}
            </button>
          </div>

          {session ? (
            <div className="task-sidebar-summary">
              <div className="task-sidebar-card">
                <h4>任务目标</h4>
                <p>{session.title || session.topic}</p>
              </div>
              <div className="task-sidebar-grid">
                <div className="task-sidebar-card">
                  <h4>工作区上下文</h4>
                  <p>{workspace?.display_name || workspace?.root_path || "未绑定工作区"}</p>
                  <span className="status-label">
                    {workspace?.selected_paths?.length ? `${workspace.selected_paths.length} 个选中路径` : "未选择路径"}
                  </span>
                </div>
                <div className="task-sidebar-card">
                  <h4>运行记录</h4>
                  <p>{executionEvents.length > 0 ? `${executionEvents.length} 条执行记录` : "等待本次运行输出"}</p>
                  <span className="status-label">
                    {historyExport ? "已生成导出内容" : "可导出运行记录"}
                  </span>
                </div>
              </div>
            </div>
          ) : null}

          <ExecutionProgressPanel
            entries={executionEvents}
            streamState={streamState}
          />

          {session?.mode === "code_workspace" && (
            <WorkspaceSessionPanel
              sessionId={session.id}
              workspace={workspace}
              participants={session.participants}
              capabilities={session.workspace?.capabilities}
              onToggleWriteMode={(nextCanWrite) => onToggleWorkspaceWriteMode(session.id, nextCanWrite)}
            />
          )}

          {snapshotOpen && session && (
            <div className="stack">
              <label className="field">
                <span>任务目标快照</span>
                <textarea rows={2} value={snapshot.topic} onChange={(e) => setSnapshot((s) => ({ ...s, topic: e.target.value }))} />
              </label>
              <label className="field">
                <span>任务模板</span>
                <select value={snapshot.mode} onChange={(e) => setSnapshot((s) => ({ ...s, mode: e.target.value as CollaborationMode }))}>
                  {MODE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
              <label className="field">
                <span>参与者摘要（每行 id: 摘要）</span>
                <textarea rows={5} value={Object.entries(snapshot.participant_summaries).map(([k, v]) => `${k}: ${v}`).join("\n")} onChange={(e) => setSnapshot((s) => ({ ...s, participant_summaries: e.target.value.split("\n").filter(Boolean).reduce<Record<string, string>>((acc, line) => { const [k, ...rest] = line.split(":"); if (k) acc[k.trim()] = rest.join(":").trim(); return acc; }, {}) }))} />
              </label>
              <label className="field">
                <span>共识列表（每行一条）</span>
                <textarea rows={4} value={snapshot.consensus_list.join("\n")} onChange={(e) => setSnapshot((s) => ({ ...s, consensus_list: e.target.value.split("\n").filter(Boolean) }))} />
              </label>
              <label className="field">
                <span>关键结论（每行一条）</span>
                <textarea rows={4} value={snapshot.key_events.join("\n")} onChange={(e) => setSnapshot((s) => ({ ...s, key_events: e.target.value.split("\n").filter(Boolean) }))} />
              </label>
              <button type="button" className="primary-button" onClick={onSaveSnapshot}>💾 保存任务快照</button>
            </div>
          )}
          {snapshotOpen && !session && (
            <div className="empty-state">先从左侧任务列表中选择一个任务，再查看或编辑任务快照。</div>
          )}
        </div>
      </div>
    </div>
  );
}

function clampPaneWidth(value: number): number {
  return Math.min(760, Math.max(360, value));
}


