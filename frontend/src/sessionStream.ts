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
