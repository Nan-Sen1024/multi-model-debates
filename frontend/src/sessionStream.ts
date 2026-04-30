import { ChatMessage, ExecutionEventRecord, StreamPayload, StreamState } from "./types";

export interface SessionStreamViewState {
  messages: ChatMessage[];
  liveMessage: ChatMessage | null;
  streamState: StreamState;
  executionEvents: ExecutionEventRecord[];
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
  return {
    id: executionMessageId(eventRecord),
    senderId: eventRecord.participantId || "system",
    type: "execution",
    content: [eventRecord.summary, eventRecord.detail].filter(Boolean).join("\n"),
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
    executionPhase: eventRecord.phase,
    executionTitle: eventRecord.summary,
    executionDetail: eventRecord.detail,
  };
}

function shouldMirrorExecutionToChat(eventRecord: ExecutionEventRecord): boolean {
  return eventRecord.status === "warning" || eventRecord.status === "error";
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
  const appendStreamingDetail =
    existing.event === incoming.event &&
    (incoming.event === "tool_output" || incoming.event === "model_output");
  return {
    ...existing,
    ...incoming,
    id: existing.id,
    correlationKey: existing.correlationKey || incoming.correlationKey,
    summary: incoming.summary || existing.summary,
    detail: appendStreamingDetail
      ? appendDetail(existing.detail, incoming.detail)
      : mergeDetail(existing.detail, incoming.detail),
    kind: incoming.kind || existing.kind,
    phase: incoming.phase || existing.phase,
    metadata: {
      ...(existing.metadata || {}),
      ...(incoming.metadata || {}),
    },
  };
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
  if (payload.code === "AUTHENTICATION_REQUIRED") {
    return `${participantName(payload)} 认证过期`;
  }
  return `${participantName(payload)} 调用失败`;
}

function participantErrorDetail(payload: StreamPayload): string | undefined {
  if (payload.code === "AUTHENTICATION_REQUIRED") {
    const provider = [payload.provider_name, payload.provider_id ? `(${payload.provider_id})` : undefined]
      .filter(Boolean)
      .join(" ");
    return formatDetailLines([
      payload.model_ref ? `模型：${payload.model_ref}` : undefined,
      provider ? `Provider：${provider}` : undefined,
      payload.auth_type ? `认证方式：${payload.auth_type}` : undefined,
      payload.code ? `code=${payload.code}` : undefined,
      payload.summary || payload.message,
      payload.remediation,
    ]);
  }
  return formatDetailLines([
    payload.code ? `code=${payload.code}` : undefined,
    payload.summary || payload.message,
  ]);
}

function compactToolResultDetail(payload: StreamPayload): string {
  const text = payload.text || payload.content || "";
  if (!text) {
    return "";
  }
  const commandSummary = text
    .split(/\r?\n/)
    .filter((line) => /^(command|cwd|exit_code)=/.test(line));
  if (commandSummary.length > 0) {
    return commandSummary.join("\n");
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
    return `turn:${participantId}:${round}`;
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

export function applyStreamEvent(
  state: SessionStreamViewState,
  eventName: string,
  payload: StreamPayload,
): SessionStreamViewState {
  if (eventName === "ping") {
    return state;
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
    return upsertExecution(
      state,
      executionEvent(
        "model_output",
        payload.summary || "模型输出中",
        payload.round || 0,
        "info",
        payload.participant_id,
        payload.content || payload.text || "",
        "model",
        payload.phase,
        { ...payload },
        eventCorrelationKey("model_output", payload),
      ),
      { streamState: "streaming" },
    );
  }

  if (eventName === "chunk") {
    const senderId = participantName(payload);
    if (
      state.liveMessage &&
      state.liveMessage.senderId === senderId &&
      state.liveMessage.type === "model"
    ) {
      return {
        ...state,
        liveMessage: {
          ...state.liveMessage,
          content: `${state.liveMessage.content}${payload.content || ""}`,
          status: "streaming",
        },
        streamState: "streaming",
      };
    }
    return {
      ...state,
      liveMessage: {
        id: `${senderId}-${Date.now()}`,
        senderId,
        type: "model",
        content: payload.content || "",
        round: payload.round || 0,
        status: "streaming",
      },
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
    const target = toolTarget(payload);
    const argumentsText =
      payload.arguments && Object.keys(payload.arguments).length > 0
        ? JSON.stringify(payload.arguments, null, 2)
        : "";
    return upsertExecution(
      state,
      executionEvent(
        "tool_call",
        `${participantName(payload)} 调用 ${target || "unknown"}`,
        payload.round || 0,
        "running",
        payload.participant_id,
        formatDetailLines([argumentsText]),
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
    const target = toolTarget(payload);
    return upsertExecution(
      state,
      executionEvent(
        "tool_result",
        `${target || "unknown"} 返回结果`,
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
        payload.summary || "已写入状态",
        payload.round || 0,
        "done",
        payload.participant_id,
        formatDetailLines([
          payload.target ? `target=${payload.target}` : undefined,
          payload.server_name ? `server=${payload.server_name}` : undefined,
          payload.tool_name ? `tool=${payload.tool_name}` : undefined,
        ]),
        "state",
        payload.phase,
        { ...payload },
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
