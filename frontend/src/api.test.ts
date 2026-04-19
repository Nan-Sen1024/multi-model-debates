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
  beforeEach(() => {
    MockEventSource.instances = [];
    (globalThis as { EventSource?: unknown }).EventSource = MockEventSource;
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
        payload: { message: "SSE 连接中断，请检查后端服务状态。" },
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
});
