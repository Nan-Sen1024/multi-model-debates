jest.mock("react", () => {
  const actual = jest.requireActual("react");
  return {
    ...actual,
    startTransition: (callback: () => void) => {
      setTimeout(callback, 0);
    },
  };
});

import React, { act } from "react";
import { createRoot, Root } from "react-dom/client";

jest.mock("./api", () => {
  const actual = jest.requireActual("./api");
  return {
    ...actual,
    openSessionStream: jest.fn(),
  };
});

import App from "./App";
import { openSessionStream } from "./api";
import { StreamPayload } from "./types";

function mockJsonResponse(data: unknown) {
  return {
    ok: true,
    headers: {
      get(name: string) {
        return name.toLowerCase() === "content-type" ? "application/json" : null;
      },
    },
    json: async () => data,
  } as Response;
}

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("App streaming watchdog", () => {
  let container: HTMLDivElement;
  let root: Root;
  let streamCallback: ((eventName: string, payload: StreamPayload) => void) | null;

  beforeEach(() => {
    jest.restoreAllMocks();
    jest.useFakeTimers();
    localStorage.clear();
    localStorage.setItem("mmdebate.lastSessionId", "session-1");
    Element.prototype.scrollIntoView = jest.fn();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    streamCallback = null;

    jest.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/providers") {
        return mockJsonResponse([]);
      }
      if (path === "/api/sessions") {
        return mockJsonResponse([
          {
            id: "session-1",
            topic: "Long running round",
            mode: "debate",
            status: "active",
            current_round: 1,
            updated_at: 300,
            participant_count: 2,
            last_message_preview: "assistant reply",
          },
        ]);
      }
      if (path === "/api/sessions/session-1") {
        return mockJsonResponse({
          id: "session-1",
          topic: "Long running round",
          mode: "debate",
          status: "active",
          current_round: 1,
          participants: [
            { id: "p1", custom_id: "ModelA", model_ref: "openai/gpt-4o", is_active: true },
            { id: "p2", custom_id: "ModelB", model_ref: "deepseek/deepseek-chat", is_active: true },
          ],
        });
      }
      if (path === "/api/sessions/session-1/snapshot") {
        return mockJsonResponse({
          topic: "Long running round",
          mode: "debate",
          participant_summaries: {},
          consensus_list: [],
          key_events: [],
        });
      }
      if (path === "/api/sessions/session-1/messages") {
        return mockJsonResponse([]);
      }
      throw new Error(`Unexpected fetch: ${path}`);
    });

    (openSessionStream as jest.MockedFunction<typeof openSessionStream>).mockImplementation(
      (_sessionId, callback) => {
        streamCallback = callback;
        return jest.fn();
      },
    );
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
    container.remove();
  });

  test("does not emit a timeout while stream activity is still arriving", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("开始下一轮"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(streamCallback).not.toBeNull();

    await act(async () => {
      jest.advanceTimersByTime(55000);
      streamCallback?.("chunk", {
        participant_id: "ModelA",
        content: "still streaming",
        round: 1,
      });
      await Promise.resolve();
    });

    await act(async () => {
      jest.advanceTimersByTime(10000);
      await Promise.resolve();
    });

    expect(container.textContent).toContain("still streaming");
    expect(container.textContent).not.toContain("SSE 请求超时，请重试。");
  });

  test("renders streamed chunk content before the turn ends", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("开始下一轮"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(streamCallback).not.toBeNull();

    await act(async () => {
      streamCallback?.("chunk", {
        participant_id: "ModelA",
        content: "still streaming",
        round: 1,
      });
      await Promise.resolve();
    });

    expect(container.textContent).toContain("still streaming");
  });

  test("does not force scroll back to bottom while the user is reading older messages", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("开始下一轮"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(streamCallback).not.toBeNull();

    const messageStream = container.querySelector(".message-stream") as HTMLDivElement | null;
    expect(messageStream).not.toBeNull();

    Object.defineProperty(messageStream, "scrollHeight", {
      configurable: true,
      value: 1000,
    });
    Object.defineProperty(messageStream, "clientHeight", {
      configurable: true,
      value: 320,
    });
    Object.defineProperty(messageStream, "scrollTop", {
      configurable: true,
      writable: true,
      value: 0,
    });

    (Element.prototype.scrollIntoView as jest.Mock).mockClear();

    await act(async () => {
      messageStream?.dispatchEvent(new Event("scroll"));
      streamCallback?.("chunk", {
        participant_id: "ModelA",
        content: "new chunk while reading history",
        round: 1,
      });
      await Promise.resolve();
    });

    expect(container.textContent).toContain("new chunk while reading history");
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
  });
});
