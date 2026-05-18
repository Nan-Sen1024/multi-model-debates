import { ChatMessage, ExecutionEventRecord, StreamPayload, StreamState } from "./types";

export interface SessionStreamViewState {
  messages: ChatMessage[];
  liveMessage: ChatMessage | null;
  streamState: StreamState;
  executionEvents: ExecutionEventRecord[];
  pendingChunkBuffer?: {
    senderId: string;
    round: number;
    content: string;
  } | null;
}

function finalizeLiveMessage(
  liveMessage: ChatMessage | null,
  status: ChatMessage["status"] = "done",
): ChatMessage[] {
  if (!liveMessage) {
    return [];
  }
  return [{ ...liveMessage, status }];
}

function systemStreamMessage(
  id: string,
  content: string,
  round: number,
): ChatMessage {
  return {
    id,
    senderId: "system",
    type: "system",
    content,
    round,
    status: "done",
  };
}

function executionEvent(
  event: ExecutionEventRecord["event"],
  summary: string,
  round: number,
  status: ExecutionEventRecord["status"],
  participantId?: string,
  detail?: string,
  kind?: ExecutionEventRecord["kind"],
  phase?: string,
  metadata?: Record<string, unknown>,
  correlationKey?: string,
): ExecutionEventRecord {
  return {
    id: `${event}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    event,
    correlationKey,
    participantId,
    round,
    summary,
    detail,
    status,
    kind,
    phase,
    metadata,
  };
}

function executionMessageId(eventRecord: ExecutionEventRecord): string {
  return `execution-${eventRecord.id}`;
}

function executionChatMessage(eventRecord: ExecutionEventRecord): ChatMessage {
  const metadata = (eventRecord.metadata || {}) as Record<string, unknown>;
  const toolName = typeof metadata.tool_name === "string" ? metadata.tool_name : "";
  const compactContent =
    eventRecord.event === "tool_result" && (toolName === "read_file" || toolName === "list_files" || toolName === "write_file")
      ? eventRecord.summary
      : [eventRecord.summary, eventRecord.detail].filter(Boolean).join("\n");
  return {
    id: executionMessageId(eventRecord),
    senderId: eventRecord.participantId || "system",
    type: "execution",
    content: compactContent,
    round: eventRecord.round,
    status:
      eventRecord.status === "error"
        ? "error"
        : eventRecord.status === "warning"
          ? "warning"
        : eventRecord.status === "running"
          ? "streaming"
          : "done",
    executionKind: eventRecord.kind,
    executionEvent: eventRecord.event,
    executionPhase: eventRecord.phase,
    executionTitle: eventRecord.summary,
    executionDetail: eventRecord.detail,
    executionMetadata: eventRecord.metadata,
    executionCorrelationKey: eventRecord.correlationKey,
  };
}

function liveModelMessage(
  senderId: string,
  round: number,
  content: string,
): ChatMessage {
  return {
    id: `${senderId}-${Date.now()}`,
    senderId,
    type: "model",
    content,
    round,
    status: "streaming",
  };
}

function isMirroredToolResult(eventRecord: ExecutionEventRecord): boolean {
  if (eventRecord.event !== "tool_result") {
    return false;
  }
  const metadata = (eventRecord.metadata || {}) as Record<string, unknown>;
  const toolName = typeof metadata.tool_name === "string" ? metadata.tool_name : "";
  return toolName === "run_command";
}

function isMirroredToolCall(eventRecord: ExecutionEventRecord): boolean {
  if (eventRecord.event !== "tool_call") {
    return false;
  }
  const metadata = (eventRecord.metadata || {}) as Record<string, unknown>;
  const toolName = typeof metadata.tool_name === "string" ? metadata.tool_name : "";
  return toolName === "run_command";
}

function isMirroredResearchEvent(eventRecord: ExecutionEventRecord): boolean {
  return (
    eventRecord.event === "research_search" ||
    eventRecord.event === "research_open_pages" ||
    eventRecord.event === "research_note"
  );
}

function shouldMirrorExecutionToChat(eventRecord: ExecutionEventRecord): boolean {
  if (isMirroredToolCall(eventRecord)) {
    return true;
  }
  if (isMirroredResearchEvent(eventRecord)) {
    return true;
  }
  if (eventRecord.event === "provider_fallback") {
    return true;
  }
  if (isMirroredToolResult(eventRecord)) {
    return true;
  }
  return (
    eventRecord.event === "participant_error" ||
    eventRecord.event === "error" ||
    eventRecord.event === "session_end"
  );
}

function shouldAppendStreamingDetail(existing: ExecutionEventRecord, incoming: ExecutionEventRecord): boolean {
  if (existing.event === incoming.event && (incoming.event === "tool_output" || incoming.event === "model_output")) {
    return true;
  }
  return false;
}

function shouldRetainExistingEnvelope(existing: ExecutionEventRecord, incoming: ExecutionEventRecord): boolean {
  if (existing.event === "research_search" && incoming.event === "research_open_pages") {
    return true;
  }
  if (
    (existing.event === "research_search" || existing.event === "research_open_pages")
    && incoming.event === "research_note"
  ) {
    return true;
  }
  if (existing.event === "provider_fallback" && incoming.event === "participant_error") {
    return true;
  }
  return false;
}

function isProtocolNoiseChunk(content: string): boolean {
  const trimmed = content.trim();
  if (!trimmed) {
    return false;
  }
  if (!trimmed.startsWith("{")) {
    return false;
  }
  if (/"action"\s*:\s*"tool_call"/.test(trimmed)) {
    return true;
  }
  try {
    const payload = JSON.parse(trimmed) as Record<string, unknown>;
    return payload.action === "tool_call";
  } catch {
    return false;
  }
}

function looksLikeToolCallProtocolCandidate(content: string): boolean {
  const trimmed = content.trim();
  if (!trimmed.startsWith("{")) {
    return false;
  }
  if (isProtocolNoiseChunk(trimmed)) {
    return true;
  }
  return /"action"|"tool"|"server"|"arguments"/.test(trimmed);
}

function resolvePendingChunkBuffer(
  state: SessionStreamViewState,
  options?: { dropProtocolCandidates?: boolean },
): SessionStreamViewState {
  const pending = state.pendingChunkBuffer;
  if (!pending) {
    return state;
  }
  if (
    options?.dropProtocolCandidates
    && looksLikeToolCallProtocolCandidate(pending.content)
  ) {
    return {
      ...state,
      pendingChunkBuffer: null,
    };
  }
  if (
    state.liveMessage
    && state.liveMessage.senderId === pending.senderId
    && state.liveMessage.type === "model"
  ) {
    return {
      ...state,
      liveMessage: {
        ...state.liveMessage,
        content: `${state.liveMessage.content}${pending.content}`,
        status: "streaming",
      },
      pendingChunkBuffer: null,
    };
  }
  return {
    ...state,
    liveMessage: liveModelMessage(pending.senderId, pending.round, pending.content),
    pendingChunkBuffer: null,
  };
}

function mergeDetail(existing?: string, incoming?: string): string | undefined {
  if (!existing) {
    return incoming;
  }
  if (!incoming || existing === incoming) {
    return existing;
  }
  if (existing.includes(incoming)) {
    return existing;
  }
  if (incoming.includes(existing)) {
    return incoming;
  }
  return `${existing}\n\n${incoming}`;
}

function appendDetail(existing?: string, incoming?: string): string | undefined {
  if (!existing) {
    return incoming;
  }
  if (!incoming) {
    return existing;
  }
  return `${existing}${incoming}`;
}

function mergeExecutionRecord(
  existing: ExecutionEventRecord,
  incoming: ExecutionEventRecord,
): ExecutionEventRecord {
  const appendStreamingDetail = shouldAppendStreamingDetail(existing, incoming);
  const retainExistingEnvelope = shouldRetainExistingEnvelope(existing, incoming);
  const incomingDetail =
    retainExistingEnvelope && incoming.summary && incoming.summary !== existing.summary
      ? mergeDetail(incoming.summary, incoming.detail) || incoming.summary
      : incoming.detail;
  const mergedDetail = appendStreamingDetail
    ? appendDetail(existing.detail, incomingDetail)
    : mergeDetail(existing.detail, incomingDetail);
  const mergedRecord: ExecutionEventRecord = {
    ...existing,
    ...incoming,
    id: existing.id,
    correlationKey: existing.correlationKey || incoming.correlationKey,
    event: appendStreamingDetail || retainExistingEnvelope ? existing.event : incoming.event,
    summary:
      retainExistingEnvelope
        ? existing.summary
        : incoming.summary && incoming.summary !== existing.summary
        ? incoming.summary
        : existing.summary || incoming.summary,
    detail: mergedDetail,
    kind: incoming.kind || existing.kind,
    phase: incoming.phase || existing.phase,
    metadata: {
      ...(existing.metadata || {}),
      ...(incoming.metadata || {}),
    },
  };
  if (
    mergedRecord.event === "model_output" &&
    typeof mergedRecord.detail === "string" &&
    parseToolCallJsonPreview(mergedRecord.detail)
  ) {
    return {
      ...mergedRecord,
      summary: "模型准备调用工具",
      detail: undefined,
    };
  }
  return mergedRecord;
}

function findRunningExecutionIndex(
  events: ExecutionEventRecord[],
  correlationKey?: string,
): number {
  if (!correlationKey) {
    return -1;
  }
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (
      event.correlationKey === correlationKey &&
      (event.status === "running" ||
        event.status === "warning" ||
        event.status === "info" ||
        event.event === "tool_output" ||
        event.event === "model_output")
    ) {
      return index;
    }
  }
  return -1;
}

function upsertExecution(
  state: SessionStreamViewState,
  eventRecord: ExecutionEventRecord,
  options?: {
    finalizeLive?: boolean;
    finalizeLiveStatus?: ChatMessage["status"];
    streamState?: StreamState;
  },
): SessionStreamViewState {
  const finalizedMessages = options?.finalizeLive
    ? [...state.messages, ...finalizeLiveMessage(state.liveMessage, options.finalizeLiveStatus)]
    : state.messages;
  const existingIndex = findRunningExecutionIndex(
    state.executionEvents,
    eventRecord.correlationKey,
  );
  if (existingIndex >= 0) {
    const mergedRecord = mergeExecutionRecord(
      state.executionEvents[existingIndex],
      eventRecord,
    );
    const nextMessages = [...finalizedMessages];
    const messageIndex = nextMessages.findIndex(
      (message) => message.id === executionMessageId(state.executionEvents[existingIndex]),
    );
    if (messageIndex >= 0) {
      nextMessages[messageIndex] = executionChatMessage(mergedRecord);
    } else if (shouldMirrorExecutionToChat(mergedRecord)) {
      nextMessages.push(executionChatMessage(mergedRecord));
    }
    const nextExecutionEvents = [...state.executionEvents];
    nextExecutionEvents[existingIndex] = mergedRecord;
    return {
      messages: nextMessages,
      liveMessage: options?.finalizeLive ? null : state.liveMessage,
      streamState: options?.streamState ?? state.streamState,
      executionEvents: nextExecutionEvents,
    };
  }
  const nextMessages = [...finalizedMessages];
  if (shouldMirrorExecutionToChat(eventRecord)) {
    nextMessages.push(executionChatMessage(eventRecord));
  }
  return {
    messages: nextMessages,
    liveMessage: options?.finalizeLive ? null : state.liveMessage,
    streamState: options?.streamState ?? state.streamState,
    executionEvents: [...state.executionEvents, eventRecord],
  };
}

function participantName(payload: StreamPayload): string {
  return payload.participant_id || "Unknown";
}

function toolTarget(payload: StreamPayload): string {
  return [payload.server_name, payload.tool_name].filter(Boolean).join(".");
}

function participantErrorSummary(payload: StreamPayload): string {
  if (payload.code === "AGENT_MAX_STEPS") {
    return `${participantName(payload)} 本轮已暂停`;
  }
  if (payload.code === "WORKSPACE_AGENT_ERROR") {
    return `${participantName(payload)} 执行受阻`;
  }
  if (payload.code === "AUTHENTICATION_REQUIRED") {
    return `${participantName(payload)} 认证不可用`;
  }
  return `${participantName(payload)} 调用失败`;
}

function participantErrorDetail(payload: StreamPayload): string | undefined {
  const extendedPayload = payload as StreamPayload & { configured_max_steps?: number };
  if (payload.code === "AGENT_MAX_STEPS") {
    return formatDetailLines([
      payload.message || "达到当前步数预算",
      typeof extendedPayload.configured_max_steps === "number"
        ? `当前预算：${extendedPayload.configured_max_steps} 步`
        : undefined,
      "可继续执行，或提高该参与者的 Agent 步数配置。",
    ]);
  }
  if (payload.code === "AUTHENTICATION_REQUIRED") {
    const provider = [payload.provider_name, payload.provider_id ? `(${payload.provider_id})` : undefined]
      .filter(Boolean)
      .join(" ");
    const summary =
      payload.summary
      || (payload.auth_type === "oauth"
        ? "认证已过期或失效"
        : payload.auth_type === "api_key"
          ? "API Key 无效或缺失"
          : "认证失败");
    return formatDetailLines([
      payload.model_ref ? `模型：${payload.model_ref}` : undefined,
      provider ? `Provider：${provider}` : undefined,
      payload.auth_type ? `认证方式：${payload.auth_type}` : undefined,
      payload.code ? `code=${payload.code}` : undefined,
      summary,
      payload.summary && payload.message && payload.message !== payload.summary
        ? payload.message
        : !payload.summary
          ? payload.message
          : undefined,
      payload.remediation,
    ]);
  }
  return formatDetailLines([
    payload.code ? `code=${payload.code}` : undefined,
    payload.summary || payload.message,
    payload.summary && payload.message && payload.message !== payload.summary
      ? payload.message
      : undefined,
  ]);
}

function compactToolResultDetail(payload: StreamPayload): string {
  const text = payload.text || payload.content || "";
  if (!text) {
    return "";
  }
  if (payload.tool_name === "run_command") {
    return formatCommandResultDetail(text);
  }
  if (text.length <= 800) {
    return text;
  }
  return `${text.slice(0, 800)}\n... [detail truncated ${text.length - 800} chars]`;
}

function parseToolCallJsonPreview(detail: string): boolean {
  const trimmed = detail.trim();
  if (!trimmed.startsWith("{")) {
    return false;
  }
  if (/"action"\s*:\s*"tool_call"/.test(trimmed)) {
    return true;
  }
  if (!trimmed.endsWith("}")) {
    return false;
  }
  try {
    const payload = JSON.parse(trimmed) as Record<string, unknown>;
    return payload.action === "tool_call";
  } catch {
    return false;
  }
}

function isRunCommandTool(payload: StreamPayload): boolean {
  return payload.server_name === "workspace" && payload.tool_name === "run_command";
}

function pathFromToolArguments(payload: StreamPayload): string | null {
  const path = payload.arguments?.path;
  return typeof path === "string" && path.trim() ? path.trim() : null;
}

function humanizeToolTarget(payload: StreamPayload): string {
  if (payload.tool_name === "read_file") {
    return "读取文件";
  }
  if (payload.tool_name === "list_files") {
    return "浏览目录";
  }
  if (payload.tool_name === "write_file") {
    return "写入文件";
  }
  return toolTarget(payload) || "unknown";
}

function summarizeToolCall(payload: StreamPayload): string {
  if (isRunCommandTool(payload)) {
    const command = formatCommandFromArguments(payload.arguments);
    return command ? `执行命令 ${command}` : "执行命令";
  }
  const path = pathFromToolArguments(payload);
  const target = humanizeToolTarget(payload);
  return path ? `${target} ${path}` : target;
}

function describeToolCall(payload: StreamPayload): string | undefined {
  if (isRunCommandTool(payload)) {
    const lines = [
      payload.arguments?.cwd ? `目录：${String(payload.arguments.cwd)}` : undefined,
      payload.arguments?.shell ? `Shell：${String(payload.arguments.shell)}` : undefined,
      payload.arguments?.timeout_seconds ? `超时：${String(payload.arguments.timeout_seconds)}s` : undefined,
    ];
    return formatDetailLines(lines);
  }
  const path = pathFromToolArguments(payload);
  const argumentsText =
    payload.arguments && Object.keys(payload.arguments).length > 0
      ? JSON.stringify(payload.arguments, null, 2)
      : "";
  return formatDetailLines([
    path ? `路径：${path}` : undefined,
    argumentsText && !path ? argumentsText : undefined,
  ]);
}

function summarizeToolResult(payload: StreamPayload): string {
  if (isRunCommandTool(payload)) {
    const commandState = parseCommandResult(payload.text || payload.content || "");
    if (typeof commandState.exitCode === "number") {
      return commandState.exitCode === 0 ? "命令执行完成" : "命令执行失败";
    }
    return "命令返回结果";
  }
  if (payload.tool_name === "read_file") {
    return "已读取文件";
  }
  if (payload.tool_name === "list_files") {
    return "已列出目录";
  }
  if (payload.tool_name === "write_file") {
    return "已写入文件";
  }
  return `${toolTarget(payload) || "unknown"} 返回结果`;
}

function formatCommandFromArguments(argumentsValue?: Record<string, unknown>): string {
  if (!argumentsValue) {
    return "";
  }
  const command = typeof argumentsValue.command === "string" ? argumentsValue.command : "";
  const args = Array.isArray(argumentsValue.args)
    ? argumentsValue.args.filter((item): item is string => typeof item === "string")
    : [];
  const commandLine = typeof argumentsValue.command_line === "string" ? argumentsValue.command_line : "";
  if (command) {
    return [command, ...args].join(" ").trim();
  }
  return commandLine.trim();
}

function parseCommandResult(text: string): {
  command?: string;
  cwd?: string;
  exitCode?: number;
  stdout?: string;
  stderr?: string;
} {
  const lines = text.split(/\r?\n/);
  const result: {
    command?: string;
    cwd?: string;
    exitCode?: number;
    stdout?: string;
    stderr?: string;
  } = {};
  let section: "stdout" | "stderr" | null = null;
  const stdout: string[] = [];
  const stderr: string[] = [];

  for (const line of lines) {
    if (line.startsWith("command=")) {
      result.command = line.slice("command=".length).trim();
      section = null;
      continue;
    }
    if (line.startsWith("cwd=")) {
      result.cwd = line.slice("cwd=".length).trim();
      section = null;
      continue;
    }
    if (line.startsWith("exit_code=")) {
      const raw = Number.parseInt(line.slice("exit_code=".length).trim(), 10);
      if (Number.isFinite(raw)) {
        result.exitCode = raw;
      }
      section = null;
      continue;
    }
    if (line === "stdout:") {
      section = "stdout";
      continue;
    }
    if (line === "stderr:") {
      section = "stderr";
      continue;
    }
    if (section === "stdout") {
      stdout.push(line);
      continue;
    }
    if (section === "stderr") {
      stderr.push(line);
    }
  }

  if (stdout.length > 0) {
    result.stdout = stdout.join("\n").trim();
  }
  if (stderr.length > 0) {
    result.stderr = stderr.join("\n").trim();
  }
  return result;
}

function stripKnownNoiseFromStderr(stderr: string): string {
  return stderr
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => {
      if (!line) {
        return false;
      }
      if (line.startsWith("wsl:")) {
        return false;
      }
      return true;
    })
    .join("\n")
    .trim();
}

function inferShellLabel(command?: string, stderr?: string): string | null {
  const source = `${command || ""}\n${stderr || ""}`;
  if (/\bbash\b|\/bin\/bash/.test(source)) {
    return "bash";
  }
  if (/\bpwsh\b/.test(source)) {
    return "pwsh";
  }
  if (/\bpowershell\b/i.test(source)) {
    return "PowerShell";
  }
  if (/\bcmd(?:\.exe)?\b/i.test(source)) {
    return "cmd";
  }
  return null;
}

function summarizeKnownCommandFailure(result: {
  command?: string;
  stderr?: string;
}): string[] {
  const cleanedStderr = stripKnownNoiseFromStderr(result.stderr || "");
  if (!cleanedStderr) {
    return [];
  }

  const missingCommandMatch =
    cleanedStderr.match(/line \d+: ([^:\s]+): command not found/) ||
    cleanedStderr.match(/(^|[\n\r])([^:\s]+): command not found/) ||
    cleanedStderr.match(/(^|[\n\r])([^:\s]+): not found/);

  if (missingCommandMatch) {
    const missingCommand = (missingCommandMatch[1] || missingCommandMatch[2] || "").trim();
    const shellLabel = inferShellLabel(result.command, cleanedStderr);
    const summary = shellLabel
      ? `问题摘要：${shellLabel} 环境中未找到 ${missingCommand} 命令`
      : `问题摘要：未找到 ${missingCommand} 命令`;
    const suggestion =
      missingCommand === "python"
        ? "建议：改用 python3，或切换到已安装 Python 的 shell。"
        : undefined;
    return [summary, suggestion].filter(Boolean) as string[];
  }

  if (/[�]/.test(result.stderr || "")) {
    return ["问题摘要：终端返回了无法正确解码的错误输出。"];
  }

  return [];
}

function formatCommandResultDetail(text: string): string {
  const result = parseCommandResult(text);
  const cleanedStderr = result.stderr ? stripKnownNoiseFromStderr(result.stderr) : "";
  const knownFailureLines = summarizeKnownCommandFailure(result);
  const lines = [
    ...knownFailureLines,
    result.command ? `命令：${result.command}` : undefined,
    result.cwd ? `目录：${result.cwd}` : undefined,
    typeof result.exitCode === "number" ? `退出码：${result.exitCode}` : undefined,
    result.stdout ? `标准输出：\n${result.stdout}` : undefined,
    knownFailureLines.length === 0 && cleanedStderr ? `标准错误：\n${cleanedStderr}` : undefined,
  ];
  const formatted = formatDetailLines(lines);
  if (formatted) {
    return formatted;
  }
  if (text.length <= 800) {
    return text;
  }
  return `${text.slice(0, 800)}\n... [detail truncated ${text.length - 800} chars]`;
}

function eventCorrelationKey(eventName: string, payload: StreamPayload): string | undefined {
  const participantId = payload.participant_id || "system";
  const round = payload.round || 0;
  if (eventName === "turn_start" || eventName === "turn_end") {
    return `turn:${participantId}:${round}`;
  }
  if (eventName === "participant_error") {
    return `alert:${participantId}:${round}`;
  }
  if (eventName === "provider_fallback") {
    return `alert:${participantId}:${round}`;
  }
  if (
    eventName === "research_search"
    || eventName === "research_open_pages"
    || eventName === "research_note"
  ) {
    return `research:${participantId}:${round}`;
  }
  if (eventName === "phase_start" || eventName === "phase_end") {
    return `phase:${participantId}:${round}:${payload.phase || "unknown"}:${
      typeof payload.step === "number" ? payload.step : ""
    }`;
  }
  if (eventName === "model_request" || eventName === "model_response") {
    return `model:${participantId}:${round}:${payload.model_ref || ""}`;
  }
  if (eventName === "model_output") {
    return `model-output:${participantId}:${round}:${payload.model_ref || ""}:${
      typeof payload.step === "number" ? payload.step : "current"
    }`;
  }
  if (eventName === "tool_output") {
    return `tool-output:${participantId}:${round}:${toolTarget(payload)}:${
      typeof payload.step === "number" ? payload.step : "current"
    }`;
  }
  if (eventName === "tool_call" || eventName === "tool_result") {
    return `tool:${participantId}:${round}:${toolTarget(payload)}`;
  }
  return undefined;
}

function formatDetailLines(lines: Array<string | null | undefined>): string | undefined {
  const filtered = lines.map((line) => line?.trim()).filter(Boolean) as string[];
  return filtered.length > 0 ? filtered.join("\n") : undefined;
}

function summarizeStateWrite(payload: StreamPayload): string {
  if (payload.target === "message") {
    return "已写入消息";
  }
  if (payload.target === "session") {
    return "已更新会话状态";
  }
  return "已写入状态";
}

function formatResearchDetail(lines: Array<string | null | undefined>): string | undefined {
  return formatDetailLines(lines);
}

function summarizeResearchSearch(payload: StreamPayload): string {
  return payload.summary || `搜索到 ${payload.result_count || 0} 个网页`;
}

function describeResearchSearch(payload: StreamPayload): string | undefined {
  return formatResearchDetail([
    payload.query ? `查询：${payload.query}` : undefined,
    typeof payload.result_count === "number" ? `结果数：${payload.result_count}` : undefined,
    payload.content || payload.text,
  ]);
}

function summarizeResearchOpenPages(payload: StreamPayload): string {
  return payload.summary || `浏览 ${payload.page_count || 0} 个页面`;
}

function describeResearchOpenPages(payload: StreamPayload): string | undefined {
  const items = Array.isArray(payload.items)
    ? payload.items.filter((item): item is string => typeof item === "string" && item.trim().length > 0)
    : [];
  return formatResearchDetail([
    typeof payload.page_count === "number" ? `页面数：${payload.page_count}` : undefined,
    ...items,
  ]);
}

function summarizeResearchNote(payload: StreamPayload): string {
  return payload.summary || "研究补充说明";
}

function describeResearchNote(payload: StreamPayload): string | undefined {
  return formatResearchDetail([payload.content || payload.text]);
}

function describeStateWrite(payload: StreamPayload): string | undefined {
  return formatDetailLines([
    payload.server_name ? `server=${payload.server_name}` : undefined,
    payload.tool_name ? `tool=${payload.tool_name}` : undefined,
  ]);
}

export function applyStreamEvent(
  state: SessionStreamViewState,
  eventName: string,
  payload: StreamPayload,
): SessionStreamViewState {
  if (eventName === "ping") {
    return state;
  }

  if (eventName !== "chunk") {
    state = resolvePendingChunkBuffer(state, { dropProtocolCandidates: true });
  }

  if (eventName === "turn_start") {
    const executionMode =
      payload.execution_mode === "agent" ? "Agent" : "Model";
    return upsertExecution(
      state,
      executionEvent(
        "turn_start",
        `${participantName(payload)} 开始执行`,
        payload.round || 0,
        "running",
        payload.participant_id,
        `${executionMode} · Round ${payload.round || 0}`,
        "turn",
        undefined,
        { ...payload },
        eventCorrelationKey("turn_start", payload),
      ),
      { streamState: "streaming" },
    );
  }

  if (eventName === "phase_start") {
    return upsertExecution(
      state,
      executionEvent(
        "phase_start",
        payload.summary || `进入阶段 ${payload.phase || "unknown"}`,
        payload.round || 0,
        "running",
        payload.participant_id,
        formatDetailLines([
          payload.phase ? `phase=${payload.phase}` : undefined,
          typeof payload.step === "number" ? `step=${payload.step}` : undefined,
          typeof payload.file_count === "number" ? `file_count=${payload.file_count}` : undefined,
          typeof payload.target_count === "number" ? `target_count=${payload.target_count}` : undefined,
        ]),
        "phase",
        payload.phase,
        { ...payload },
        eventCorrelationKey("phase_start", payload),
      ),
      { streamState: "streaming" },
    );
  }

  if (eventName === "phase_end") {
    return upsertExecution(
      state,
      executionEvent(
        "phase_end",
        payload.summary || `阶段完成 ${payload.phase || "unknown"}`,
        payload.round || 0,
        "done",
        payload.participant_id,
        formatDetailLines([
          payload.phase ? `phase=${payload.phase}` : undefined,
          typeof payload.file_count === "number" ? `file_count=${payload.file_count}` : undefined,
          typeof payload.target_count === "number" ? `target_count=${payload.target_count}` : undefined,
          typeof payload.input_message_count === "number"
            ? `input_message_count=${payload.input_message_count}`
            : undefined,
        ]),
        "phase",
        payload.phase,
        { ...payload },
        eventCorrelationKey("phase_end", payload),
      ),
      { streamState: "streaming" },
    );
  }

  if (eventName === "reasoning_note") {
    return upsertExecution(
      state,
      executionEvent(
        "reasoning_note",
        payload.summary || "执行说明",
        payload.round || 0,
        "info",
        payload.participant_id,
        payload.content || payload.message || "",
        "note",
        payload.phase,
        { ...payload },
      ),
      { streamState: "streaming" },
    );
  }

  if (eventName === "model_request") {
    return upsertExecution(
      state,
      executionEvent(
        "model_request",
        payload.summary || `发起模型请求`,
        payload.round || 0,
        "running",
        payload.participant_id,
        formatDetailLines([
          payload.content || undefined,
          (payload as StreamPayload & { model_ref?: string }).model_ref
            ? `model=${(payload as StreamPayload & { model_ref?: string }).model_ref}`
            : undefined,
        ]),
        "model",
        payload.phase,
        { ...payload },
        eventCorrelationKey("model_request", payload),
      ),
      { streamState: "streaming" },
    );
  }

  if (eventName === "model_response") {
    return upsertExecution(
      state,
      executionEvent(
        "model_response",
        payload.summary || "模型开始返回输出",
        payload.round || 0,
        "done",
        payload.participant_id,
        formatDetailLines([
          (payload as StreamPayload & { model_ref?: string }).model_ref
            ? `model=${(payload as StreamPayload & { model_ref?: string }).model_ref}`
            : undefined,
        ]),
        "model",
        payload.phase,
        { ...payload },
        eventCorrelationKey("model_response", payload),
      ),
      { streamState: "streaming" },
    );
  }

  if (eventName === "model_output") {
    const detail = payload.content || payload.text || "";
    const isToolCallPreview = parseToolCallJsonPreview(detail);
    return upsertExecution(
      state,
      executionEvent(
        "model_output",
        isToolCallPreview ? "模型准备调用工具" : payload.summary || "模型输出中",
        payload.round || 0,
        "info",
        payload.participant_id,
        isToolCallPreview ? undefined : detail,
        "model",
        payload.phase,
        { ...payload },
        eventCorrelationKey("model_output", payload),
      ),
      { streamState: "streaming" },
    );
  }

  if (eventName === "chunk") {
    const content = payload.content || "";
    const senderId = participantName(payload);

    if (state.pendingChunkBuffer) {
      const samePendingSender =
        state.pendingChunkBuffer.senderId === senderId
        && state.pendingChunkBuffer.round === (payload.round || 0);
      if (samePendingSender) {
        const bufferedContent = `${state.pendingChunkBuffer.content}${content}`;
        if (isProtocolNoiseChunk(bufferedContent)) {
          return {
            ...state,
            pendingChunkBuffer: null,
            streamState: "streaming",
          };
        }
        if (looksLikeToolCallProtocolCandidate(bufferedContent)) {
          return {
            ...state,
            pendingChunkBuffer: {
              ...state.pendingChunkBuffer,
              content: bufferedContent,
            },
            streamState: "streaming",
          };
        }
        state = resolvePendingChunkBuffer({
          ...state,
          pendingChunkBuffer: {
            ...state.pendingChunkBuffer,
            content: bufferedContent,
          },
        });
      } else {
        state = resolvePendingChunkBuffer(state);
      }
    }

    if (isProtocolNoiseChunk(content)) {
      return {
        ...state,
        streamState: "streaming",
      };
    }
    if (
      !state.liveMessage
      && looksLikeToolCallProtocolCandidate(content)
    ) {
      return {
        ...state,
        pendingChunkBuffer: {
          senderId,
          round: payload.round || 0,
          content,
        },
        streamState: "streaming",
      };
    }
    if (
      state.liveMessage &&
      state.liveMessage.senderId === senderId &&
      state.liveMessage.type === "model"
    ) {
      return {
        ...state,
        liveMessage: {
          ...state.liveMessage,
          content: `${state.liveMessage.content}${content}`,
          status: "streaming",
        },
        pendingChunkBuffer: null,
        streamState: "streaming",
      };
    }
    return {
      ...state,
      liveMessage: liveModelMessage(senderId, payload.round || 0, content),
      pendingChunkBuffer: null,
      streamState: "streaming",
    };
  }

  if (eventName === "agent_plan") {
    return upsertExecution(
      state,
      executionEvent(
        "agent_plan",
        `${participantName(payload)} 产出执行计划`,
        payload.round || 0,
        "info",
        payload.participant_id,
        payload.content || "",
        "note",
        payload.phase,
        { ...payload },
      ),
      { streamState: "streaming" },
    );
  }

  if (eventName === "tool_call") {
    return upsertExecution(
      state,
      executionEvent(
        "tool_call",
        summarizeToolCall(payload),
        payload.round || 0,
        "running",
        payload.participant_id,
        describeToolCall(payload),
        "tool",
        payload.phase,
        { ...payload },
        eventCorrelationKey("tool_call", payload),
      ),
      { streamState: "streaming" },
    );
  }

  if (eventName === "tool_output") {
    const target = toolTarget(payload);
    const streamLabel = payload.stream ? `[${payload.stream}] ` : "";
    return upsertExecution(
      state,
      executionEvent(
        "tool_output",
        `${target || "unknown"} 输出中`,
        payload.round || 0,
        "info",
        payload.participant_id,
        `${streamLabel}${payload.text || payload.content || ""}`,
        "output",
        payload.phase,
        { ...payload },
        eventCorrelationKey("tool_output", payload),
      ),
      { streamState: "streaming" },
    );
  }

  if (eventName === "tool_result") {
    return upsertExecution(
      state,
      executionEvent(
        "tool_result",
        summarizeToolResult(payload),
        payload.round || 0,
        "done",
        payload.participant_id,
        compactToolResultDetail(payload),
        "tool",
        payload.phase,
        { ...payload },
        eventCorrelationKey("tool_result", payload),
      ),
      { streamState: "streaming" },
    );
  }

  if (eventName === "state_write") {
    return upsertExecution(
      state,
      executionEvent(
        "state_write",
        summarizeStateWrite(payload),
        payload.round || 0,
        "done",
        payload.participant_id,
        describeStateWrite(payload),
        "state",
        payload.phase,
        { ...payload },
      ),
      { streamState: "streaming" },
    );
  }

  if (eventName === "research_search") {
    return upsertExecution(
      state,
      executionEvent(
        "research_search",
        summarizeResearchSearch(payload),
        payload.round || 0,
        "info",
        payload.participant_id,
        describeResearchSearch(payload),
        "note",
        payload.phase,
        { ...payload },
        eventCorrelationKey("research_search", payload),
      ),
      { streamState: "streaming" },
    );
  }

  if (eventName === "research_open_pages") {
    return upsertExecution(
      state,
      executionEvent(
        "research_open_pages",
        summarizeResearchOpenPages(payload),
        payload.round || 0,
        "info",
        payload.participant_id,
        describeResearchOpenPages(payload),
        "note",
        payload.phase,
        { ...payload },
        eventCorrelationKey("research_open_pages", payload),
      ),
      { streamState: "streaming" },
    );
  }

  if (eventName === "research_note") {
    return upsertExecution(
      state,
      executionEvent(
        "research_note",
        summarizeResearchNote(payload),
        payload.round || 0,
        "info",
        payload.participant_id,
        describeResearchNote(payload),
        "note",
        payload.phase,
        { ...payload },
        eventCorrelationKey("research_note", payload),
      ),
      { streamState: "streaming" },
    );
  }

  if (eventName === "turn_end") {
    return upsertExecution(
      {
        ...state,
      messages: [...state.messages, ...finalizeLiveMessage(state.liveMessage)],
      liveMessage: null,
      pendingChunkBuffer: null,
    },
      executionEvent(
        "turn_end",
        `${participantName(payload)} 执行完成`,
        payload.round || 0,
        "done",
        payload.participant_id,
        undefined,
        "turn",
        payload.phase,
        { ...payload },
        eventCorrelationKey("turn_end", payload),
      ),
      { streamState: "streaming" },
    );
  }

  if (eventName === "participant_error") {
    return upsertExecution(
      {
        ...state,
        messages: [
          ...state.messages,
          ...finalizeLiveMessage(state.liveMessage, "warning"),
        ],
        liveMessage: null,
        pendingChunkBuffer: null,
      },
      executionEvent(
        "participant_error",
        participantErrorSummary(payload),
        payload.round || 0,
        "warning",
        payload.participant_id,
        participantErrorDetail(payload),
        "turn",
        payload.phase,
        { ...payload },
        eventCorrelationKey("participant_error", payload),
      ),
      { streamState: "streaming" },
    );
  }

  if (eventName === "provider_fallback") {
    const primary = [payload.provider_name, payload.provider_id ? `(${payload.provider_id})` : undefined]
      .filter(Boolean)
      .join(" ");
    const fallback = [payload.fallback_provider_name, payload.fallback_provider_id ? `(${payload.fallback_provider_id})` : undefined]
      .filter(Boolean)
      .join(" ");
    return upsertExecution(
      state,
      executionEvent(
        "provider_fallback",
        `${participantName(payload)} 已切换到备用路由`,
        payload.round || 0,
        "warning",
        payload.participant_id,
        formatDetailLines([
          primary ? `主 Provider：${primary}` : undefined,
          fallback ? `Fallback Provider：${fallback}` : undefined,
          payload.code ? `code=${payload.code}` : undefined,
          payload.message || undefined,
        ]),
        "turn",
        payload.phase,
        { ...payload },
        eventCorrelationKey("provider_fallback", payload),
      ),
      { streamState: "streaming" },
    );
  }

  if (eventName === "round_end") {
    return upsertExecution(
      state,
      executionEvent(
        "round_end",
        `第 ${payload.round || 0} 轮完成`,
        payload.round || 0,
        "done",
        payload.participant_id,
        undefined,
        "session",
        payload.phase,
        { ...payload },
      ),
      { streamState: "completed" },
    );
  }

  if (eventName === "session_end") {
    const executionState = upsertExecution(
      {
        ...state,
        messages: [...state.messages, ...finalizeLiveMessage(state.liveMessage)],
        liveMessage: null,
        pendingChunkBuffer: null,
      },
      executionEvent(
        "session_end",
        "会话结束",
        payload.round || 0,
        "done",
        payload.participant_id,
        payload.summary || payload.reason || "",
        "session",
        payload.phase,
        { ...payload },
      ),
      { streamState: "completed" },
    );
    return {
      ...executionState,
      messages: [
        ...executionState.messages,
        systemStreamMessage(
          `end-${Date.now()}`,
          `会话结束：${payload.reason || "unknown"}\n${payload.summary || ""}`,
          payload.round || 0,
        ),
      ],
    };
  }

  if (eventName === "error") {
    return upsertExecution(
      {
        ...state,
        messages: [...state.messages, ...finalizeLiveMessage(state.liveMessage, "error")],
        liveMessage: null,
      },
      executionEvent(
        "error",
        payload.message || "调度异常",
        payload.round || 0,
        "error",
        payload.participant_id,
        payload.code,
        "session",
        payload.phase,
        { ...payload },
      ),
      { streamState: "failed" },
    );
  }

  if (eventName === "drift_alert") {
    if (
      state.liveMessage &&
      state.liveMessage.senderId === payload.participant_id
    ) {
      return {
        ...state,
        liveMessage: {
          ...state.liveMessage,
          driftScore: payload.score,
          status: "warning",
        },
      };
    }
    return {
      ...state,
      messages: state.messages.map((message) =>
        message.senderId === payload.participant_id
          ? { ...message, driftScore: payload.score, status: "warning" }
          : message,
      ),
    };
  }

  return state;
}
