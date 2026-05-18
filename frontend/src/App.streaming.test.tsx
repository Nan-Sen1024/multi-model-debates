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
      (node) => node.textContent?.includes("继续执行"),
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

  test("shows structured idle execution surfaces before any run starts", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const executionPanel = container.querySelector(".execution-panel-live") as HTMLElement | null;
    expect(executionPanel).not.toBeNull();
    expect(executionPanel?.getAttribute("data-stream-state")).toBe("idle");
    expect(executionPanel?.getAttribute("data-has-entries")).toBe("false");
    expect(executionPanel?.getAttribute("data-surface-tone")).toBe("idle");
    expect(executionPanel?.className).toContain("execution-panel-live");
    expect(container.textContent).toContain("本轮摘要");
    expect(container.textContent).toContain("等待下一轮开始");
    expect(container.textContent).toContain("运行状态已就绪，开始任务后会持续显示模型、工具和验证进展。");
    expect(container.textContent).toContain("等待执行");
    expect(container.textContent).toContain("关键进展");
    expect(container.textContent).toContain("等待开始下一轮后，这里会显示关键进展。");
    expect(container.textContent).toContain("原始事件尚未产生");
  });

  test("keeps the task sidebar focused on task and workspace context instead of duplicating run summary cards", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const taskSidebar = container.querySelector(".task-sidebar-summary") as HTMLElement | null;

    expect(taskSidebar).not.toBeNull();
    expect(taskSidebar?.textContent).toContain("任务目标");
    expect(taskSidebar?.textContent).toContain("工作区上下文");
    expect(taskSidebar?.textContent).toContain("运行记录");
    expect(taskSidebar?.textContent).not.toContain("涉及文件");
    expect(taskSidebar?.textContent).not.toContain("执行命令");
    expect(taskSidebar?.textContent).not.toContain("验证状态");
    expect(taskSidebar?.textContent).not.toContain("阻塞与告警");
  });

  test("elevates the whole execution surface when idle instead of only the small badge", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const executionPanel = container.querySelector(".execution-panel-live") as HTMLElement | null;
    expect(executionPanel).not.toBeNull();
    expect(executionPanel?.getAttribute("data-surface-tone")).toBe("idle");

    const stateBanner = executionPanel?.querySelector(".execution-state-banner") as HTMLElement | null;
    expect(stateBanner?.getAttribute("data-surface-tone")).toBe("idle");
  });

  test("renders streamed chunk content before the turn ends", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("继续执行"),
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

  test("renders structured model text with readable paragraphs and code blocks", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("继续执行"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      streamCallback?.("chunk", {
        participant_id: "ModelA",
        round: 1,
        content: "结论如下：\n\n- 第一项\n- 第二项\n\n```ts\nconst ok = true;\n```",
      });
      await Promise.resolve();
    });

    const modelBubble = container.querySelector(
      '[data-message-type="model"] .bubble-content',
    ) as HTMLElement | null;

    expect(modelBubble).not.toBeNull();
    expect(modelBubble?.querySelectorAll("li")).toHaveLength(2);
    expect(modelBubble?.querySelector("pre code")?.textContent).toContain("const ok = true;");
  });

  test("renders research progress lines as structured research cards instead of plain text paragraphs", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("继续执行"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      streamCallback?.("chunk", {
        participant_id: "ModelB",
        round: 1,
        content:
          "搜索到 43 个网页\n这些结果显示了一些相关条目。为了全面了解中美之间的重大事件，我们需要同时打开这些可能包含重要信息的页面。\n\n浏览 13 个页面\n中美通话不到48小时，美方重量级代表落地北京，有两件大事不能再拖\n中美如何做大人工智能合作的蛋糕\n查看全部\n\n这些结果涵盖了多个方面，包括贸易、外交、军事、科技等。为了更全面地了解中美关系，我们还需要进一步搜索更早的事件。",
      });
      await Promise.resolve();
    });

    const modelBubble = container.querySelector(
      '[data-message-type="model"] .bubble-content',
    ) as HTMLElement | null;

    expect(modelBubble).not.toBeNull();
    expect(modelBubble?.querySelectorAll(".research-step-card").length).toBeGreaterThanOrEqual(2);
    expect(modelBubble?.textContent).toContain("搜索到 43 个网页");
    expect(modelBubble?.textContent).toContain("浏览 13 个页面");
    expect(modelBubble?.textContent).toContain("查看全部");
  });

  test("renders protocol-level research events as execution cards in the main transcript", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("继续执行"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      streamCallback?.("research_search", {
        participant_id: "ModelB",
        round: 2,
        query: "中美之间的大事件",
        result_count: 43,
        summary: "搜索到 43 个网页",
      });
      streamCallback?.("research_open_pages", {
        participant_id: "ModelB",
        round: 2,
        page_count: 13,
        items: ["中美如何做大人工智能合作的蛋糕", "查看全部"],
        summary: "浏览 13 个页面",
      });
      await Promise.resolve();
    });

    const executionMessages = Array.from(
      container.querySelectorAll('[data-message-type="execution"]'),
    ) as HTMLElement[];

    expect(executionMessages.some((node) => node.textContent?.includes("搜索到 43 个网页"))).toBe(true);
    expect(executionMessages.some((node) => node.textContent?.includes("浏览 13 个页面"))).toBe(true);
  });

  test("keeps file read activity in the execution panel instead of mirroring it into the main transcript", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("继续执行"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      streamCallback?.("tool_call", {
        participant_id: "ModelA",
        round: 1,
        server_name: "workspace",
        tool_name: "read_file",
        arguments: { path: "src/App.tsx" },
      });
      streamCallback?.("tool_result", {
        participant_id: "ModelA",
        round: 1,
        server_name: "workspace",
        tool_name: "read_file",
        text: "path: src/App.tsx\nline 1\nline 2",
      });
      await Promise.resolve();
    });

    const executionMessages = Array.from(
      container.querySelectorAll('[data-message-type="execution"]'),
    ) as HTMLElement[];

    expect(executionMessages.some((node) => node.textContent?.includes("已读取文件"))).toBe(false);
    expect(container.textContent).toContain("已读取文件");
    expect(container.textContent).toContain("src/App.tsx");
  });

  test("keeps file write activity in the execution panel instead of mirroring it into the main transcript", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("继续执行"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      streamCallback?.("tool_call", {
        participant_id: "ModelA",
        round: 1,
        server_name: "workspace",
        tool_name: "write_file",
        arguments: { path: "notes/new-spec.md" },
      });
      streamCallback?.("tool_result", {
        participant_id: "ModelA",
        round: 1,
        server_name: "workspace",
        tool_name: "write_file",
        text: "path: notes/new-spec.md\nnew file created\nwritten to notes/new-spec.md\ncontent preview",
      });
      await Promise.resolve();
    });

    let executionMessages = Array.from(
      container.querySelectorAll('[data-message-type="execution"]'),
    ) as HTMLElement[];

    expect(executionMessages.some((node) => node.textContent?.includes("已写入文件"))).toBe(false);
    expect(container.textContent).toContain("新建文件");
    expect(container.textContent).toContain("notes/new-spec.md");

    await act(async () => {
      streamCallback?.("tool_result", {
        participant_id: "ModelA",
        round: 1,
        server_name: "workspace",
        tool_name: "write_file",
        text: "path: notes/new-spec.md\noverwrite existing file\nupdated notes/new-spec.md\ncontent preview",
      });
      await Promise.resolve();
    });

    executionMessages = Array.from(
      container.querySelectorAll('[data-message-type="execution"]'),
    ) as HTMLElement[];

    expect(executionMessages.some((node) => node.textContent?.includes("覆盖写入"))).toBe(false);
    expect(container.textContent).toContain("覆盖写入");
    expect(container.textContent).toContain("已覆盖");
  });

  test("keeps directory browsing activity in the execution panel instead of mirroring it into the main transcript", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("继续执行"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      streamCallback?.("tool_call", {
        participant_id: "ModelB",
        round: 1,
        server_name: "workspace",
        tool_name: "list_files",
        arguments: { path: "src" },
      });
      streamCallback?.("tool_result", {
        participant_id: "ModelB",
        round: 1,
        server_name: "workspace",
        tool_name: "list_files",
        text: "path: src\ncomponents\nstyles.css",
      });
      await Promise.resolve();
    });

    const executionMessages = Array.from(
      container.querySelectorAll('[data-message-type="execution"]'),
    ) as HTMLElement[];

    expect(executionMessages.some((node) => node.textContent?.includes("已列出目录"))).toBe(false);
    expect(container.textContent).toContain("已列出目录");
    expect(container.textContent).toContain("路径：src");
  });

  test("renders research progress as a structured execution card with progress blocks", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("继续执行"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      streamCallback?.("research_search", {
        participant_id: "ModelB",
        round: 1,
        query: "Codex style execution bubble",
        result_count: 7,
        summary: "搜索到 7 个网页",
      });
      streamCallback?.("research_open_pages", {
        participant_id: "ModelB",
        round: 1,
        page_count: 3,
        items: ["A structured card", "B structured card"],
        summary: "浏览 3 个页面",
      });
      await Promise.resolve();
    });

    const researchBubble = Array.from(
      container.querySelectorAll('[data-message-type="execution"]'),
    ).find((node) => node.textContent?.includes("搜索到 7 个网页")) as HTMLElement | undefined;

    expect(researchBubble).toBeDefined();
    expect(researchBubble?.querySelector(".execution-bubble-research-card")).not.toBeNull();
    expect(researchBubble?.querySelector(".execution-bubble-section-head")?.textContent).toContain("Research 进展");
    expect(researchBubble?.textContent).toContain("Codex style execution bubble");
    expect(researchBubble?.textContent).toContain("浏览 3 个页面");
  });

  test("renders provider fallback and participant errors as alert-style execution cards", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("继续执行"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      streamCallback?.("provider_fallback", {
        participant_id: "ModelA",
        round: 2,
        provider_name: "openai-primary",
        provider_id: "provider-openai-primary",
        fallback_provider_name: "openai-backup",
        fallback_provider_id: "provider-openai-backup",
        code: "AUTHENTICATION_REQUIRED",
        message: "API key invalid",
      });
      streamCallback?.("participant_error", {
        participant_id: "ModelA",
        round: 2,
        code: "TEST_FAILURE",
        summary: "验证未通过",
        message: "1 failing test remains",
      });
      await Promise.resolve();
    });

    const alertBubble = Array.from(
      container.querySelectorAll('[data-message-type="execution"]'),
    ).find((node) => node.textContent?.includes("已切换到备用路由")) as HTMLElement | undefined;

    expect(alertBubble).toBeDefined();
    expect(alertBubble?.className).toContain("bubble-warning");
    expect(alertBubble?.querySelector(".execution-bubble-alert-card")).not.toBeNull();
    expect(alertBubble?.querySelector(".execution-bubble-section-head")?.textContent).toContain("路由 / 告警");
    expect(alertBubble?.textContent).toContain("openai-primary");
    expect(alertBubble?.textContent).toContain("openai-backup");
    expect(alertBubble?.textContent).toContain("验证未通过");
  });

  test("renders execution telemetry in the live execution log", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("继续执行"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(streamCallback).not.toBeNull();

    await act(async () => {
      streamCallback?.("phase_start", {
        participant_id: "ModelA",
        phase: "build_prompt",
        summary: "构建任务上下文",
        round: 1,
      });
      await Promise.resolve();
    });

    const executionRow = container.querySelector(
      '[data-execution-kind="phase"]',
    ) as HTMLElement | null;
    expect(executionRow).not.toBeNull();
    expect(executionRow?.textContent).toContain("构建任务上下文");
    expect(executionRow?.textContent).toContain("build_prompt");
    expect(container.querySelector('[data-message-type="execution"]')).toBeNull();
  });

  test("mirrors only decision-relevant execution signals into the main transcript", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("继续执行"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      streamCallback?.("phase_start", {
        participant_id: "ModelA",
        phase: "build_prompt",
        summary: "构建任务上下文",
        round: 1,
      });
      streamCallback?.("tool_result", {
        participant_id: "ModelA",
        round: 1,
        server_name: "workspace",
        tool_name: "read_file",
        text: "README 内容",
        arguments: { path: "README.md" },
      });
      streamCallback?.("provider_fallback", {
        participant_id: "ModelA",
        round: 1,
        provider_name: "openai-primary",
        provider_id: "provider-openai-primary",
        fallback_provider_name: "openai-backup",
        fallback_provider_id: "provider-openai-backup",
        code: "AUTHENTICATION_REQUIRED",
        message: "API key invalid",
      });
      streamCallback?.("tool_result", {
        participant_id: "ModelA",
        round: 1,
        server_name: "workspace",
        tool_name: "run_command",
        text: "command=pytest -q\ncwd=.\nexit_code=1\nstdout:\n1 failed",
      });
      await Promise.resolve();
    });

    const executionMessages = Array.from(
      container.querySelectorAll('[data-message-type="execution"]'),
    ) as HTMLElement[];

    expect(executionMessages.length).toBeGreaterThan(0);
    expect(executionMessages.some((node) => node.textContent?.includes("构建任务上下文"))).toBe(false);
    expect(executionMessages.some((node) => node.textContent?.includes("已读取文件"))).toBe(false);
    expect(executionMessages.some((node) => node.textContent?.includes("已切换到备用路由"))).toBe(true);
    expect(executionMessages.some((node) => node.textContent?.includes("命令执行失败"))).toBe(true);

    const fallbackBubble = executionMessages.find((node) => node.textContent?.includes("已切换到备用路由"));
    expect(fallbackBubble?.className).toContain("bubble-execution-inline");
    expect(fallbackBubble?.className).toContain("bubble-warning");
    expect(fallbackBubble?.querySelector(".execution-bubble-head")).not.toBeNull();
    expect(fallbackBubble?.querySelector(".execution-bubble-title")).not.toBeNull();
    expect(fallbackBubble?.querySelector(".execution-bubble-alert-card")).not.toBeNull();
    expect(fallbackBubble?.querySelector(".execution-bubble-kind")?.textContent).toBe("本轮");
  });

  test("does not mirror low-signal state writes into the main transcript", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("继续执行"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      streamCallback?.("state_write", {
        participant_id: "ModelA",
        round: 1,
        target: "message",
        summary: "step",
      });
      await Promise.resolve();
    });

    expect(container.querySelector('[data-message-type="execution"]')).toBeNull();
    expect(container.textContent).not.toContain("已写入消息");
  });

  test("shows successful command cards with command, exit code, and collapsed raw output by default", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("继续执行"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(streamCallback).not.toBeNull();

    await act(async () => {
      streamCallback?.("tool_result", {
        participant_id: "ModelA",
        round: 1,
        server_name: "workspace",
        tool_name: "run_command",
        text: "command=npm test -- --runInBand\ncwd=D:/repo/demo\nexit_code=0\nstdout:\nline1\nline2\nline3\nline4\nline5",
      });
      await Promise.resolve();
    });

    const executionBubble = Array.from(
      container.querySelectorAll('[data-message-type="execution"]'),
    ).find((node) => node.textContent?.includes("npm test -- --runInBand")) as HTMLElement | undefined;

    expect(executionBubble).toBeDefined();
    expect(executionBubble?.className).toContain("bubble-execution-console");
    expect(executionBubble?.getAttribute("data-execution-surface")).toBe("console");
    expect(executionBubble?.textContent).toContain("命令");
    expect(executionBubble?.textContent).toContain("npm test -- --runInBand");
    expect(executionBubble?.textContent).toContain("退出码");
    expect(executionBubble?.textContent).toContain("0");
    expect(executionBubble?.textContent).toContain("摘要");
    expect(executionBubble?.textContent).toContain("原始输出");
    expect(executionBubble?.textContent).toContain("标准输出");
    expect(executionBubble?.textContent).toContain("共 5 行");
    expect(executionBubble?.textContent).toContain("line1");
    expect(executionBubble?.textContent).toContain("line3");
    expect(executionBubble?.textContent).not.toContain("line5");
    expect(executionBubble?.textContent).not.toContain("stdout:");
    expect(executionBubble?.querySelector(".execution-bubble-code-shell")).not.toBeNull();
    expect(executionBubble?.querySelector(".execution-bubble-code-preview")).not.toBeNull();
    expect(executionBubble?.querySelector(".execution-bubble-code-raw")).toBeNull();

    const expandButton = Array.from(executionBubble?.querySelectorAll("button") || []).find(
      (node) => node.textContent?.includes("展开原始输出"),
    ) as HTMLButtonElement | undefined;
    expect(expandButton).toBeDefined();

    await act(async () => {
      expandButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(executionBubble?.textContent).toContain("line5");
    expect(executionBubble?.textContent).toContain("收起原始输出");
    expect(executionBubble?.querySelector(".execution-bubble-code-raw")).not.toBeNull();
  });

  test("keeps command card raw output expanded across re-renders for the same execution card", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("继续执行"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      streamCallback?.("tool_result", {
        participant_id: "ModelA",
        round: 1,
        server_name: "workspace",
        tool_name: "run_command",
        text: "command=pytest -q\ncwd=.\nexit_code=0\nstdout:\nline1\nline2\nline3\nline4",
      });
      await Promise.resolve();
    });

    let executionBubble = Array.from(
      container.querySelectorAll('[data-message-type="execution"]'),
    ).find((node) => node.textContent?.includes("pytest -q")) as HTMLElement | undefined;

    expect(executionBubble).toBeDefined();

    const expandButton = Array.from(executionBubble?.querySelectorAll("button") || []).find(
      (node) => node.textContent?.includes("展开原始输出"),
    ) as HTMLButtonElement | undefined;

    expect(expandButton).toBeDefined();

    await act(async () => {
      expandButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(executionBubble?.textContent).toContain("line4");
    expect(executionBubble?.textContent).toContain("收起原始输出");

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    executionBubble = Array.from(
      container.querySelectorAll('[data-message-type="execution"]'),
    ).find((node) => node.textContent?.includes("pytest -q")) as HTMLElement | undefined;

    expect(executionBubble?.textContent).toContain("line4");
    expect(executionBubble?.textContent).toContain("收起原始输出");
  });

  test("shows failed command cards with stderr summary and collapsed raw output by default", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("继续执行"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      streamCallback?.("tool_result", {
        participant_id: "ModelA",
        round: 1,
        server_name: "workspace",
        tool_name: "run_command",
        text: "command=npm test -- --runInBand\ncwd=D:/repo/demo\nexit_code=1\nstdout:\nline1\nline2\nline3\nline4\nline5\nstderr:\nwarning detail",
      });
      await Promise.resolve();
    });

    const executionBubble = Array.from(
      container.querySelectorAll('[data-message-type="execution"]'),
    ).find((node) => node.querySelector(".execution-bubble-command")) as HTMLElement | undefined;

    expect(executionBubble).toBeDefined();
    expect(executionBubble?.className).toContain("bubble-execution-console");
    expect(executionBubble?.getAttribute("data-execution-surface")).toBe("console");
    expect(executionBubble?.textContent).toContain("命令");
    expect(executionBubble?.textContent).toContain("npm test -- --runInBand");
    expect(executionBubble?.textContent).toContain("退出码");
    expect(executionBubble?.textContent).toContain("1");
    expect(executionBubble?.textContent).toContain("stderr 摘要");
    expect(executionBubble?.textContent).toContain("D:/repo/demo");
    expect(executionBubble?.textContent).not.toContain("line5");
    expect(executionBubble?.querySelector(".execution-bubble-code-shell")).not.toBeNull();
    expect(executionBubble?.querySelector(".execution-bubble-stream-stderr .execution-bubble-code-preview")).not.toBeNull();
    expect(executionBubble?.querySelector(".execution-bubble-code-raw")).toBeNull();

    const expandButton = Array.from(executionBubble?.querySelectorAll("button") || []).find(
      (node) => node.textContent?.includes("展开原始输出"),
    ) as HTMLButtonElement | undefined;
    expect(expandButton).toBeDefined();

    await act(async () => {
      expandButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(executionBubble?.textContent).toContain("line5");
    expect(executionBubble?.textContent).toContain("warning detail");
    expect(executionBubble?.textContent).toContain("收起原始输出");
    expect(executionBubble?.querySelector(".execution-bubble-code-raw")).not.toBeNull();
  });

  test("sanitizes noisy bash command failures before mirroring them into the chat transcript", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("继续执行"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      streamCallback?.("tool_call", {
        participant_id: "ModelA",
        round: 10,
        server_name: "workspace",
        tool_name: "run_command",
        arguments: {
          command: "bash",
          args: ["-lc", "python - <<'PY'"],
          cwd: ".",
          shell: "bash",
          timeout_seconds: 120,
        },
      });
      streamCallback?.("tool_result", {
        participant_id: "ModelA",
        round: 10,
        server_name: "workspace",
        tool_name: "run_command",
        text: "command=bash -lc python - <<'PY'\ncwd=.\nexit_code=127\nstderr:\nwsl: \uFFFDhKm0R localhost \uFFFDN\u0006tM\uFFFDn\u007F\f\uFFFDFO*g\\\uFFFD\uFFFDP0R WSL\u00020NAT !j\u000F_\u000BN\uFFFDv WSL \rN/e\u0001c localhost \uFFFDN\u0006t\u00020\n/bin/bash: line 1: python: command not found",
      });
      await Promise.resolve();
    });

    const executionBubble = Array.from(
      container.querySelectorAll('[data-message-type="execution"]'),
    ).find((node) => node.querySelector(".execution-bubble-command")) as HTMLElement | undefined;

    expect(executionBubble).toBeDefined();
    expect(executionBubble?.textContent).toContain("bash 环境中未找到 python 命令");
    expect(executionBubble?.textContent).not.toContain("wsl:");
    expect(executionBubble?.textContent).not.toContain("/bin/bash: line 1: python: command not found");
    expect(executionBubble?.textContent).not.toContain("�");
  });

  test("filters execution log by command and file activity", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("继续执行"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      streamCallback?.("tool_call", {
        participant_id: "ModelA",
        round: 1,
        server_name: "workspace",
        tool_name: "read_file",
        arguments: { path: "README.md" },
      });
      streamCallback?.("tool_result", {
        participant_id: "ModelA",
        round: 1,
        server_name: "workspace",
        tool_name: "read_file",
        text: "README content",
      });
      streamCallback?.("tool_call", {
        participant_id: "ModelA",
        round: 1,
        server_name: "workspace",
        tool_name: "run_command",
        arguments: { command: "pytest", args: ["-q"] },
      });
      streamCallback?.("tool_result", {
        participant_id: "ModelA",
        round: 1,
        server_name: "workspace",
        tool_name: "run_command",
        text: "command=pytest -q\ncwd=.\nexit_code=0\nstdout:\nall good",
      });
      await Promise.resolve();
    });

    const commandFilter = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("命令"),
    ) as HTMLButtonElement | undefined;
    const fileFilter = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("文件"),
    ) as HTMLButtonElement | undefined;

    expect(commandFilter).toBeDefined();
    expect(fileFilter).toBeDefined();

    await act(async () => {
      commandFilter?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(container.textContent).toContain("命令执行完成");
    expect(container.textContent).not.toContain("已读取文件 README.md");

    await act(async () => {
      fileFilter?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(container.textContent).toContain("已读取文件 README.md");
    expect(container.textContent).not.toContain("命令执行完成 · pytest -q");
  });

  test("shows a curated execution timeline first and keeps raw events collapsed by default", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("继续执行"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      streamCallback?.("tool_call", {
        participant_id: "ModelA",
        round: 1,
        server_name: "workspace",
        tool_name: "read_file",
        arguments: { path: "README.md" },
      });
      streamCallback?.("tool_result", {
        participant_id: "ModelA",
        round: 1,
        server_name: "workspace",
        tool_name: "read_file",
        text: "README content",
      });
      streamCallback?.("state_write", {
        participant_id: "ModelA",
        round: 1,
        target: "message",
        summary: "step",
      });
      streamCallback?.("tool_call", {
        participant_id: "ModelA",
        round: 1,
        server_name: "workspace",
        tool_name: "run_command",
        arguments: { command: "pytest", args: ["-q"] },
      });
      streamCallback?.("tool_result", {
        participant_id: "ModelA",
        round: 1,
        server_name: "workspace",
        tool_name: "run_command",
        text: "command=pytest -q\ncwd=.\nexit_code=1\nstdout:\n1 failed",
      });
      await Promise.resolve();
    });

    expect(container.textContent).toContain("本轮摘要");
    expect(container.textContent).toContain("验证未通过");
    expect(container.textContent).toContain("README.md");
    expect(container.textContent).toContain("pytest");
    expect(container.textContent).toContain("关键进展");
    expect(container.textContent).toContain("已读取文件");
    expect(container.textContent).toContain("命令执行失败");
    expect(container.textContent).not.toContain("已写入消息");
    expect(container.querySelector('[data-summary-field="validation"]')?.getAttribute("data-summary-tone")).toBe("danger");
    expect(container.querySelector('[data-summary-field="commands"]')?.getAttribute("data-summary-tone")).toBe("active");
    expect(container.querySelector('[data-summary-field="files"]')?.getAttribute("data-summary-tone")).toBe("active");
    expect(container.querySelector('[data-summary-field="blockers"]')?.getAttribute("data-summary-tone")).toBe("clear");

    const rawToggle = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("查看原始事件"),
    ) as HTMLButtonElement | undefined;
    expect(rawToggle).toBeDefined();

    const executionLog = container.querySelector(".execution-log");
    expect(executionLog?.textContent).toContain("原始事件已收起");

    await act(async () => {
      rawToggle?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(executionLog?.textContent).toContain("已写入消息");
    const rawKinds = Array.from(executionLog?.querySelectorAll(".execution-kind") || []).map((node) => node.textContent);
    expect(rawKinds).toEqual(expect.arrayContaining(["更新", "工具"]));
    expect(rawKinds).not.toEqual(expect.arrayContaining(["STATE", "TOOL"]));
  });

  test("adds explicit visual tone hooks for in-progress execution summaries", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("继续执行"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      streamCallback?.("phase_start", {
        participant_id: "ModelA",
        phase: "scan_workspace",
        summary: "扫描工作区",
        round: 10,
        step: "step",
        file_count: 345,
      });
      await Promise.resolve();
    });

    const executionPanel = container.querySelector(".execution-panel-live") as HTMLElement | null;
    expect(executionPanel?.getAttribute("data-stream-state")).toBe("streaming");

    const activeRoundChip = container.querySelector(
      '[data-execution-round-chip="10"]',
    ) as HTMLButtonElement | null;
    expect(activeRoundChip).not.toBeNull();
    expect(activeRoundChip?.className).toContain("execution-round-chip-active");
    expect(activeRoundChip?.getAttribute("data-round-status")).toBe("running");

    expect(container.querySelector('[data-summary-field="validation"]')?.getAttribute("data-summary-tone")).toBe("working");
    expect(container.querySelector('[data-summary-field="files"]')?.getAttribute("data-summary-tone")).toBe("empty");
    expect(container.querySelector('[data-summary-field="commands"]')?.getAttribute("data-summary-tone")).toBe("empty");
    expect(container.querySelector('[data-summary-field="blockers"]')?.getAttribute("data-summary-tone")).toBe("clear");
  });

  test("promotes failed stream state into a distinct execution badge style", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("继续执行"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      streamCallback?.("error", {
        code: "SSE_CONNECTION_FAILED",
        message: "SSE 连接中断，请检查后端服务状态。",
        round: 10,
      });
      await Promise.resolve();
    });

    const statusBadge = container.querySelector(".execution-panel-live .status-badge") as HTMLElement | null;
    expect(statusBadge).not.toBeNull();
    expect(statusBadge?.textContent).toContain("失败");
    expect(statusBadge?.className).toContain("status-failed");
    expect(statusBadge?.className).not.toContain("status-idle");
  });

  test("lets the operator switch execution diagnostics by round", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("继续执行"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      streamCallback?.("tool_call", {
        participant_id: "ModelA",
        round: 1,
        server_name: "workspace",
        tool_name: "read_file",
        arguments: { path: "README.md" },
      });
      streamCallback?.("tool_result", {
        participant_id: "ModelA",
        round: 1,
        server_name: "workspace",
        tool_name: "read_file",
        text: "README content",
      });
      streamCallback?.("round_end", {
        round: 1,
      });
      streamCallback?.("tool_call", {
        participant_id: "ModelA",
        round: 2,
        server_name: "workspace",
        tool_name: "run_command",
        arguments: { command: "pytest", args: ["-q"] },
      });
      streamCallback?.("tool_result", {
        participant_id: "ModelA",
        round: 2,
        server_name: "workspace",
        tool_name: "run_command",
        text: "command=pytest -q\ncwd=.\nexit_code=1\nstdout:\n1 failed",
      });
      await Promise.resolve();
    });

    expect(container.textContent).toContain("当前轮次");
    expect(container.textContent).toContain("pytest");

    const run1Button = container.querySelector(
      '[data-execution-round-chip="1"]',
    ) as HTMLButtonElement | null;
    expect(run1Button).toBeDefined();
    const initialRoundChips = Array.from(
      container.querySelectorAll("[data-execution-round-chip]"),
    ) as HTMLButtonElement[];
    expect(initialRoundChips[0].textContent).toContain("第 2 轮");

    await act(async () => {
      run1Button?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    const roundChips = Array.from(
      container.querySelectorAll("[data-execution-round-chip]"),
    ) as HTMLButtonElement[];
    expect(roundChips.some((chip) => chip.textContent?.includes("第 1 轮"))).toBe(true);
    const sessionTimelineLabel = container.querySelector(
      '[data-execution-timeline-kind="session"] .execution-kind',
    ) as HTMLElement | null;
    expect(sessionTimelineLabel?.textContent).toBe("运行");
    expect(container.textContent).toContain("已读取文件 README.md");
    expect(container.textContent).not.toContain("命令执行失败 · pytest -q");
  });

  test("shows round chips with status-aware labels for fast triage", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("继续执行"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      streamCallback?.("tool_call", {
        participant_id: "ModelA",
        round: 1,
        server_name: "workspace",
        tool_name: "run_command",
        arguments: { command: "pytest", args: ["-q"] },
      });
      streamCallback?.("tool_result", {
        participant_id: "ModelA",
        round: 1,
        server_name: "workspace",
        tool_name: "run_command",
        text: "command=pytest -q\ncwd=.\nexit_code=0\nstdout:\nall good",
      });
      streamCallback?.("round_end", {
        round: 1,
      });
      streamCallback?.("participant_error", {
        participant_id: "ModelA",
        round: 2,
        code: "TEST_FAILURE",
        summary: "验证未通过",
        message: "1 failing test remains",
      });
      await Promise.resolve();
    });

    const roundChips = Array.from(
      container.querySelectorAll("[data-execution-round-chip]"),
    ) as HTMLButtonElement[];

    expect(roundChips).toHaveLength(2);
    expect(roundChips[0].textContent).toContain("第 2 轮");
    expect(roundChips[0].textContent).toContain("阻塞");
    expect(roundChips[0].getAttribute("data-round-status")).toBe("blocked");
    expect(roundChips[1].textContent).toContain("第 1 轮");
    expect(roundChips[1].textContent).toContain("通过");
    expect(roundChips[1].getAttribute("data-round-status")).toBe("passed");
  });

  test("does not force scroll back to bottom while the user is reading older messages", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("继续执行"),
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

  test("keeps auto-scroll inside the message list instead of scrolling the whole page", async () => {
    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("继续执行"),
    ) as HTMLButtonElement | undefined;

    expect(startButton).toBeDefined();

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
      value: 680,
    });
    Object.defineProperty(messageStream, "scrollTo", {
      configurable: true,
      value: jest.fn(({ top }: { top: number }) => {
        messageStream.scrollTop = top;
      }),
    });

    (Element.prototype.scrollIntoView as jest.Mock).mockClear();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(streamCallback).not.toBeNull();

    await act(async () => {
      streamCallback?.("chunk", {
        participant_id: "ModelA",
        content: "stream keeps going",
        round: 1,
      });
      await Promise.resolve();
    });

    expect(messageStream.scrollTop).toBe(1000);
    expect(messageStream.scrollTo).toHaveBeenCalled();
    expect(Element.prototype.scrollIntoView).not.toHaveBeenCalled();
  });
});
