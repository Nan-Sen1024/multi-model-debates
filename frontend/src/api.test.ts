import { openSessionStream } from "./api";

class MockEventSource {
  static instances: MockEventSource[] = [];
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;

  readonly url: string;
  readonly listeners = new Map<string, Array<(event: MessageEvent | Event) => void>>();
  onerror: ((event: Event) => void) | null = null;
  readyState = MockEventSource.OPEN;

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(eventName: string, listener: (event: MessageEvent | Event) => void) {
    const current = this.listeners.get(eventName) || [];
    current.push(listener);
    this.listeners.set(eventName, current);
  }

  close() {
    this.readyState = MockEventSource.CLOSED;
  }

  emit(eventName: string, payload?: unknown) {
    const data = payload === undefined ? undefined : JSON.stringify(payload);
    for (const listener of this.listeners.get(eventName) || []) {
      listener({ data } as MessageEvent);
    }
  }

  emitError() {
    this.onerror?.(new Event("error"));
  }
}

describe("openSessionStream", () => {
  const originalLocation = window.location;

  beforeEach(() => {
    MockEventSource.instances = [];
    (globalThis as { EventSource?: unknown }).EventSource = MockEventSource;
    Object.defineProperty(window, "location", {
      configurable: true,
      value: {
        ...originalLocation,
        protocol: "http:",
        hostname: "localhost",
        port: "3000",
        origin: "http://localhost:3000",
      },
    });
  });

  afterEach(() => {
    Object.defineProperty(window, "location", {
      configurable: true,
      value: originalLocation,
    });
  });

  test("emits a transport error before any terminal stream event", () => {
    const received: Array<{ event: string; payload: Record<string, unknown> }> = [];

    openSessionStream("session-1", (event, payload) => {
      received.push({ event, payload: payload as Record<string, unknown> });
    });

    MockEventSource.instances[0].emitError();

    expect(received).toEqual([
      {
        event: "error",
        payload: {
          code: "SSE_CONNECTION_FAILED",
          message: "SSE 连接中断，请检查后端服务状态。",
        },
      },
    ]);
  });

  test("emits a reload-oriented transport error after stream progress", () => {
    const received: Array<{ event: string; payload: Record<string, unknown> }> = [];

    openSessionStream("session-progress", (event, payload) => {
      received.push({ event, payload: payload as Record<string, unknown> });
    });

    MockEventSource.instances[0].emit("tool_result", {
      participant_id: "claude",
      server_name: "workspace",
      tool_name: "write_file",
      text: "Wrote 123 characters to backend/api.py",
      round: 61,
    });
    MockEventSource.instances[0].emitError();

    expect(received).toEqual([
      {
        event: "tool_result",
        payload: {
          participant_id: "claude",
          server_name: "workspace",
          tool_name: "write_file",
          text: "Wrote 123 characters to backend/api.py",
          round: 61,
        },
      },
      {
        event: "error",
        payload: {
          code: "SSE_INTERRUPTED_AFTER_PROGRESS",
          message: "SSE 连接在执行过程中中断，后端可能被热重载或重启。已刷新会话历史，请检查后端日志和最新工具输出。",
        },
      },
    ]);
  });

  test("ignores transport errors after a round_end terminal stream event", () => {
    const received: Array<{ event: string; payload: Record<string, unknown> }> = [];

    openSessionStream("session-2", (event, payload) => {
      received.push({ event, payload: payload as Record<string, unknown> });
    });

    MockEventSource.instances[0].emit("round_end", { round: 1 });
    MockEventSource.instances[0].emitError();

    expect(received).toEqual([
      {
        event: "round_end",
        payload: { round: 1 },
      },
    ]);
  });

  test("forwards ping events so the app can reset idle timers", () => {
    const received: Array<{ event: string; payload: Record<string, unknown> }> = [];

    openSessionStream("session-3", (event, payload) => {
      received.push({ event, payload: payload as Record<string, unknown> });
    });

    MockEventSource.instances[0].emit("ping", { ts: 123 });

    expect(received).toEqual([
      {
        event: "ping",
        payload: { ts: 123 },
      },
    ]);
  });

  test("forwards workspace agent events", () => {
    const received: Array<{ event: string; payload: Record<string, unknown> }> = [];

    openSessionStream("session-4", (event, payload) => {
      received.push({ event, payload: payload as Record<string, unknown> });
    });

    MockEventSource.instances[0].emit("agent_plan", {
      participant_id: "claude",
      content: "先列目录",
      round: 1,
    });
    MockEventSource.instances[0].emit("tool_call", {
      participant_id: "claude",
      server_name: "filesystem",
      tool_name: "list_directory",
      round: 1,
    });
    MockEventSource.instances[0].emit("model_output", {
      participant_id: "claude",
      content: "{\"action\":\"tool_call\"",
      round: 1,
    });
    MockEventSource.instances[0].emit("tool_result", {
      participant_id: "claude",
      server_name: "filesystem",
      tool_name: "list_directory",
      text: "README.md",
      round: 1,
    });

    expect(received).toEqual([
      {
        event: "agent_plan",
        payload: {
          participant_id: "claude",
          content: "先列目录",
          round: 1,
        },
      },
      {
        event: "tool_call",
        payload: {
          participant_id: "claude",
          server_name: "filesystem",
          tool_name: "list_directory",
          round: 1,
        },
      },
      {
        event: "model_output",
        payload: {
          participant_id: "claude",
          content: "{\"action\":\"tool_call\"",
          round: 1,
        },
      },
      {
        event: "tool_result",
        payload: {
          participant_id: "claude",
          server_name: "filesystem",
          tool_name: "list_directory",
          text: "README.md",
          round: 1,
        },
      },
    ]);
  });

  test("forwards turn_start events", () => {
    const received: Array<{ event: string; payload: Record<string, unknown> }> = [];

    openSessionStream("session-5", (event, payload) => {
      received.push({ event, payload: payload as Record<string, unknown> });
    });

    MockEventSource.instances[0].emit("turn_start", {
      participant_id: "claude",
      round: 2,
      execution_mode: "agent",
    });

    expect(received).toEqual([
      {
        event: "turn_start",
        payload: {
          participant_id: "claude",
          round: 2,
          execution_mode: "agent",
        },
      },
    ]);
  });

  test("forwards execution telemetry phase and state events", () => {
    const received: Array<{ event: string; payload: Record<string, unknown> }> = [];

    openSessionStream("session-6", (event, payload) => {
      received.push({ event, payload: payload as Record<string, unknown> });
    });

    MockEventSource.instances[0].emit("phase_start", {
      participant_id: "claude",
      round: 2,
      phase: "build_prompt",
      summary: "构建工作区上下文",
    });
    MockEventSource.instances[0].emit("state_write", {
      participant_id: "claude",
      round: 2,
      target: "message",
      summary: "已写入参与者消息",
    });

    expect(received).toEqual([
      {
        event: "phase_start",
        payload: {
          participant_id: "claude",
          round: 2,
          phase: "build_prompt",
          summary: "构建工作区上下文",
        },
      },
      {
        event: "state_write",
        payload: {
          participant_id: "claude",
          round: 2,
          target: "message",
          summary: "已写入参与者消息",
        },
      },
    ]);
  });

  test("forwards protocol-level research events", () => {
    const received: Array<{ event: string; payload: Record<string, unknown> }> = [];

    openSessionStream("session-7", (event, payload) => {
      received.push({ event, payload: payload as Record<string, unknown> });
    });

    MockEventSource.instances[0].emit("research_search", {
      participant_id: "deepseek",
      round: 3,
      query: "中美之间的大事件",
      result_count: 43,
      summary: "搜索到 43 个网页",
    });
    MockEventSource.instances[0].emit("research_open_pages", {
      participant_id: "deepseek",
      round: 3,
      page_count: 13,
      items: ["中美如何做大人工智能合作的蛋糕", "查看全部"],
      summary: "浏览 13 个页面",
    });
    MockEventSource.instances[0].emit("research_note", {
      participant_id: "deepseek",
      round: 3,
      content: "这些结果涵盖贸易、外交、军事、科技等多个方面。",
      summary: "这些结果涵盖了多个方面",
    });

    expect(received).toEqual([
      {
        event: "research_search",
        payload: {
          participant_id: "deepseek",
          round: 3,
          query: "中美之间的大事件",
          result_count: 43,
          summary: "搜索到 43 个网页",
        },
      },
      {
        event: "research_open_pages",
        payload: {
          participant_id: "deepseek",
          round: 3,
          page_count: 13,
          items: ["中美如何做大人工智能合作的蛋糕", "查看全部"],
          summary: "浏览 13 个页面",
        },
      },
      {
        event: "research_note",
        payload: {
          participant_id: "deepseek",
          round: 3,
          content: "这些结果涵盖贸易、外交、军事、科技等多个方面。",
          summary: "这些结果涵盖了多个方面",
        },
      },
    ]);
  });

  test("bypasses the CRA dev proxy for SSE in local development", () => {
    openSessionStream("session-dev", () => {});

    expect(MockEventSource.instances[0].url).toBe(
      "http://127.0.0.1:8000/api/sessions/session-dev/stream",
    );
  });

  test("forwards participant_error events", () => {
    const received: Array<{ event: string; payload: Record<string, unknown> }> = [];

    openSessionStream("session-7", (event, payload) => {
      received.push({ event, payload: payload as Record<string, unknown> });
    });

    MockEventSource.instances[0].emit("participant_error", {
      participant_id: "claude",
      round: 3,
      code: "PROVIDER_UNAVAILABLE",
      message: "provider down",
    });

    expect(received).toEqual([
      {
        event: "participant_error",
        payload: {
          participant_id: "claude",
          round: 3,
          code: "PROVIDER_UNAVAILABLE",
          message: "provider down",
        },
      },
    ]);
  });
});
