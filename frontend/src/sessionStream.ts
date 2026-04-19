import { ChatMessage, StreamPayload, StreamState } from "./types";

export interface SessionStreamViewState {
  messages: ChatMessage[];
  liveMessage: ChatMessage | null;
  streamState: StreamState;
}

function finalizeLiveMessage(liveMessage: ChatMessage | null): ChatMessage[] {
  if (!liveMessage) {
    return [];
  }
  return [{ ...liveMessage, status: "done" }];
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

export function applyStreamEvent(
  state: SessionStreamViewState,
  eventName: string,
  payload: StreamPayload,
): SessionStreamViewState {
  if (eventName === "ping") {
    return state;
  }

  if (eventName === "chunk") {
    const senderId = payload.participant_id || "Unknown";
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

  if (eventName === "turn_end") {
    return {
      messages: [...state.messages, ...finalizeLiveMessage(state.liveMessage)],
      liveMessage: null,
      streamState: "streaming",
    };
  }

  if (eventName === "round_end") {
    return {
      ...state,
      streamState: "completed",
    };
  }

  if (eventName === "session_end") {
    return {
      messages: [
        ...state.messages,
        ...finalizeLiveMessage(state.liveMessage),
        {
          id: `end-${Date.now()}`,
          senderId: "system",
          type: "system",
          content: `会话结束：${payload.reason || "unknown"}\n${payload.summary || ""}`,
          round: payload.round || 0,
          status: "done",
        },
      ],
      liveMessage: null,
      streamState: "completed",
    };
  }

  if (eventName === "error") {
    return {
      messages: [
        ...state.messages,
        {
          id: `err-${Date.now()}`,
          senderId: payload.participant_id || "system",
          type: "system",
          content: payload.message || "调度异常",
          round: payload.round || 0,
          status: "error",
        },
      ],
      liveMessage: null,
      streamState: "failed",
    };
  }

  if (eventName === "agent_plan") {
    return {
      ...state,
      messages: [
        ...state.messages,
        systemStreamMessage(
          `agent-plan-${Date.now()}`,
          `[Agent 计划] ${payload.participant_id || "Unknown"}\n${payload.content || ""}`,
          payload.round || 0,
        ),
      ],
      streamState: "streaming",
    };
  }

  if (eventName === "tool_call") {
    const toolTarget = [payload.server_name, payload.tool_name].filter(Boolean).join(".");
    const argumentsText =
      payload.arguments && Object.keys(payload.arguments).length > 0
        ? `\n${JSON.stringify(payload.arguments, null, 2)}`
        : "";
    return {
      ...state,
      messages: [
        ...state.messages,
        systemStreamMessage(
          `tool-call-${Date.now()}`,
          `[工具调用] ${payload.participant_id || "Unknown"} -> ${toolTarget || "unknown"}${argumentsText}`,
          payload.round || 0,
        ),
      ],
      streamState: "streaming",
    };
  }

  if (eventName === "tool_result") {
    const toolTarget = [payload.server_name, payload.tool_name].filter(Boolean).join(".");
    return {
      ...state,
      messages: [
        ...state.messages,
        systemStreamMessage(
          `tool-result-${Date.now()}`,
          `[工具结果] ${toolTarget || "unknown"}\n${payload.text || payload.content || ""}`,
          payload.round || 0,
        ),
      ],
      streamState: "streaming",
    };
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
