import React, { act } from "react";
import { createRoot, Root } from "react-dom/client";
import { Simulate } from "react-dom/test-utils";

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

describe("App code workspace mode", () => {
  let container: HTMLDivElement;
  let root: Root;
  let streamCallback: ((eventName: string, payload: StreamPayload) => void) | null;

  beforeEach(() => {
    jest.restoreAllMocks();
    localStorage.clear();
    Element.prototype.scrollIntoView = jest.fn();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    streamCallback = null;
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  test("shows primary task templates and downranks raw modes behind labs", async () => {
    jest.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/providers") {
        return mockJsonResponse([
          {
            id: "provider-openai",
            name: "openai-primary",
            provider_type: "openai",
            base_url: "https://api.openai.com/v1",
            api_format: "openai-completions",
            auth_type: "api_key",
            auth_metadata: {},
            auth_status: "ready",
            auth_expires_at: null,
            fallback_ids: [],
            is_active: true,
          },
        ]);
      }
      if (path === "/api/sessions") {
        return mockJsonResponse([]);
      }
      if (path === "/api/model-catalog/discover") {
        return mockJsonResponse({
          provider_id: "provider-openai",
          provider_name: "openai-primary",
          provider_type: "openai",
          models: ["gpt-5.4"],
          detected_at: 123,
        });
      }
      throw new Error(`Unexpected fetch: ${path}`);
    });

    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.textContent).toContain("多模型研发工作台");
    expect(container.textContent).toContain("配置 Provider");
    expect(container.textContent).toContain("新建任务");
    expect(container.textContent).toContain("运行任务");

    const createTaskTab = Array.from(container.querySelectorAll("button")).find((node) =>
      node.textContent?.includes("新建任务"),
    ) as HTMLButtonElement | undefined;
    expect(createTaskTab).toBeDefined();

    await act(async () => {
      createTaskTab?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.textContent).toContain("Analyze Repo");
    expect(container.textContent).toContain("Fix or Implement");
    expect(container.textContent).toContain("Review Changes");
    expect(container.textContent).toContain("Compare Approaches");
    expect(container.textContent).toContain("实验模式");
    expect(container.textContent).toContain("任务目标");
    expect(container.textContent).toContain("工作区路径");
    expect(container.textContent).toContain("别名提示");
    expect(container.textContent).toContain("本地代码工作区");
    expect(container.textContent).not.toContain("狼人杀");
    expect(container.textContent).not.toContain("剧本杀");
  });

  test("renders run detail framing and auto-starts streaming after send", async () => {
    localStorage.setItem("mmdebate.lastSessionId", "workspace-session");

    jest.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/providers") {
        return mockJsonResponse([]);
      }
      if (path === "/api/sessions") {
        return mockJsonResponse([
          {
            id: "workspace-session",
            title: "workspace",
            topic: "评审本地仓库",
            mode: "code_workspace",
            status: "active",
            current_round: 1,
            updated_at: 300,
            participant_count: 2,
            last_message_preview: "assistant reply",
          },
        ]);
      }
      if (path === "/api/sessions/workspace-session") {
        return mockJsonResponse({
          id: "workspace-session",
          title: "workspace",
          topic: "评审本地仓库",
          mode: "code_workspace",
          status: "active",
          current_round: 1,
          participants: [
            { id: "p1", custom_id: "claude", model_ref: "anthropic/claude-4.6", is_active: true },
            { id: "p2", custom_id: "codex", model_ref: "openai/gpt-5.4", is_active: true },
          ],
          workspace: {
            root_path: "D:/repo/demo",
            display_name: "demo-repo",
            repo_fingerprint: "fingerprint-123",
            scan_excludes: [],
            selected_paths: ["README.md"],
            index_status: "ready",
            last_scanned_at: 1710000000,
            summary: "2 个文件，1 个顶层目录/文件",
          },
        });
      }
      if (path === "/api/sessions/workspace-session/snapshot") {
        return mockJsonResponse({
          topic: "评审本地仓库",
          mode: "code_workspace",
          participant_summaries: {},
          consensus_list: [],
          key_events: [],
        });
      }
      if (path === "/api/sessions/workspace-session/messages") {
        return mockJsonResponse([]);
      }
      if (path === "/api/sessions/workspace-session/workspace") {
        return mockJsonResponse({
          root_path: "D:/repo/demo",
          display_name: "demo-repo",
          repo_fingerprint: "fingerprint-123",
          scan_excludes: [],
          selected_paths: ["README.md"],
          index_status: "ready",
          last_scanned_at: 1710000000,
          summary: "2 个文件，1 个顶层目录/文件",
          files: ["README.md", "src/app.py"],
          tree: [
            {
              name: "README.md",
              path: "README.md",
              kind: "file",
              children: [],
            },
            {
              name: "src",
              path: "src",
              kind: "dir",
              children: [
                {
                  name: "app.py",
                  path: "src/app.py",
                  kind: "file",
                  children: [],
                },
              ],
            },
          ],
        });
      }
      if (path === "/api/sessions/workspace-session/messages" && init?.method === "POST") {
        return mockJsonResponse({ status: "queued" });
      }
      throw new Error(`Unexpected fetch: ${path}`);
    });

    (openSessionStream as jest.MockedFunction<typeof openSessionStream>).mockImplementation(
      (_sessionId, callback) => {
        streamCallback = callback;
        return jest.fn();
      },
    );

    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.textContent).toContain("运行详情");
    expect(container.textContent).toContain("任务侧栏");
    expect(container.textContent).toContain("任务目标");
    expect(container.textContent).toContain("当前运行");
    expect(container.textContent).toContain("工作区上下文");
    expect(container.textContent).toContain("运行记录");
    expect(container.textContent).toContain("继续执行");

    expect(container.textContent).toContain("demo-repo");
    expect(container.textContent).toContain("README.md");
    expect(container.textContent).toContain("src/app.py");
    expect(container.textContent).toContain("@alias");

    const textarea = Array.from(container.querySelectorAll("textarea")).find((node) =>
      (node as HTMLTextAreaElement).placeholder.includes("@alias"),
    ) as HTMLTextAreaElement | undefined;
    expect(textarea).toBeDefined();
    const sendButton = Array.from(container.querySelectorAll("button")).find((node) =>
      node.textContent?.includes("发送"),
    ) as HTMLButtonElement | undefined;
    expect(sendButton).toBeDefined();

    await act(async () => {
      textarea!.value = "你好啊";
      textarea!.setSelectionRange(textarea!.value.length, textarea!.value.length);
      Simulate.change(textarea!);
      sendButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      for (let i = 0; i < 6; i += 1) {
        await Promise.resolve();
      }
    });

    expect(openSessionStream).not.toHaveBeenCalled();

    await act(async () => {
      textarea!.value = "@co";
      textarea!.setSelectionRange(3, 3);
      Simulate.change(textarea!);
      await Promise.resolve();
    });

    const mentionPicker = container.querySelector(".mention-picker") as HTMLElement | null;
    expect(mentionPicker).not.toBeNull();
    expect(mentionPicker?.textContent).toContain("@codex");
    expect(mentionPicker?.textContent).not.toContain("@claude");

    await act(async () => {
      Simulate.keyDown(textarea!, { key: "Enter" });
      await Promise.resolve();
    });

    expect(textarea?.value).toBe("@codex ");

    await act(async () => {
      textarea!.value = "＠cl";
      textarea!.setSelectionRange(3, 3);
      Simulate.change(textarea!);
      await Promise.resolve();
    });

    expect(container.querySelector(".mention-picker")?.textContent).toContain("@claude");

    await act(async () => {
      textarea!.value = "让@co";
      textarea!.setSelectionRange(4, 4);
      Simulate.change(textarea!);
      await Promise.resolve();
    });

    expect(container.querySelector(".mention-picker")?.textContent).toContain("@codex");

    await act(async () => {
      document.body.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
      await Promise.resolve();
    });

    expect(container.querySelector(".mention-picker")).toBeNull();

    await act(async () => {
      textarea!.value = "@claude 先审查 README";
      textarea!.setSelectionRange(textarea!.value.length, textarea!.value.length);
      Simulate.change(textarea!);
      await Promise.resolve();
    });

    expect(container.querySelector(".mention-picker")).toBeNull();

    await act(async () => {
      textarea!.value = "@claude";
      textarea!.setSelectionRange(textarea!.value.length, textarea!.value.length);
      Simulate.change(textarea!);
      await Promise.resolve();
    });

    expect(container.querySelector(".mention-picker")).not.toBeNull();

    await act(async () => {
      textarea!.value = "@claude 先审查 README";
      textarea!.setSelectionRange(textarea!.value.length, textarea!.value.length);
      Simulate.change(textarea!);
      await Promise.resolve();
    });

    expect(container.querySelector(".mention-picker")).toBeNull();

    expect(sendButton?.disabled).toBe(false);

    await act(async () => {
      sendButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      for (let i = 0; i < 6; i += 1) {
        await Promise.resolve();
      }
    });

    expect(openSessionStream).toHaveBeenCalled();
    expect(streamCallback).not.toBeNull();

    await act(async () => {
      streamCallback?.("turn_start", {
        participant_id: "claude",
        round: 1,
        execution_mode: "agent",
      });
      streamCallback?.("phase_start", {
        participant_id: "claude",
        round: 1,
        phase: "scan_workspace",
        summary: "扫描工作区",
        file_count: 2,
      });
      streamCallback?.("tool_call", {
        participant_id: "claude",
        round: 1,
        server_name: "filesystem",
        tool_name: "read_file",
        arguments: { path: "README.md" },
      });
      streamCallback?.("tool_result", {
        participant_id: "claude",
        round: 1,
        server_name: "filesystem",
        tool_name: "read_file",
        text: "README 内容",
      });
      streamCallback?.("chunk", {
        participant_id: "claude",
        content: "正在查看 README",
        round: 1,
      });
      streamCallback?.("turn_end", {
        participant_id: "claude",
        round: 1,
      });
      streamCallback?.("round_end", {
        round: 1,
      });
      await Promise.resolve();
    });

    expect(container.textContent).toContain("正在查看 README");
    expect(container.textContent).toContain("实时执行日志");
    expect(container.textContent).toContain("claude 执行完成");
    expect(container.textContent).toContain("扫描工作区");
    expect(container.textContent).toContain("已读取文件");
    expect(container.textContent).toContain("README 内容");
    expect(container.textContent).toContain("涉及文件");
    expect(container.textContent).toContain("README.md");
    expect(container.textContent).toContain("执行命令");
    expect(container.textContent).toContain("验证状态");
    expect(container.querySelector('[data-execution-kind="phase"]')).not.toBeNull();
    expect(container.querySelectorAll('[data-execution-kind="turn"]')).toHaveLength(1);
  });

  test("summarizes run artifacts, commands, validation state, and blockers in the task sidebar", async () => {
    localStorage.setItem("mmdebate.lastSessionId", "workspace-session");

    jest.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/providers") {
        return mockJsonResponse([]);
      }
      if (path === "/api/sessions") {
        return mockJsonResponse([
          {
            id: "workspace-session",
            title: "workspace",
            topic: "修复测试失败",
            mode: "code_workspace",
            status: "active",
            current_round: 2,
            updated_at: 300,
            participant_count: 2,
            last_message_preview: "assistant reply",
          },
        ]);
      }
      if (path === "/api/sessions/workspace-session") {
        return mockJsonResponse({
          id: "workspace-session",
          title: "workspace",
          topic: "修复测试失败",
          mode: "code_workspace",
          status: "active",
          current_round: 2,
          participants: [
            { id: "p1", custom_id: "claude", model_ref: "anthropic/claude-4.6", is_active: true },
            { id: "p2", custom_id: "codex", model_ref: "openai/gpt-5.4", is_active: true },
          ],
          workspace: {
            root_path: "D:/repo/demo",
            display_name: "demo-repo",
            repo_fingerprint: "fingerprint-123",
            scan_excludes: [],
            selected_paths: ["README.md", "frontend/src/App.tsx"],
            index_status: "ready",
            last_scanned_at: 1710000000,
            summary: "2 个文件，1 个顶层目录/文件",
          },
        });
      }
      if (path === "/api/sessions/workspace-session/snapshot") {
        return mockJsonResponse({
          topic: "修复测试失败",
          mode: "code_workspace",
          participant_summaries: {},
          consensus_list: [],
          key_events: [],
        });
      }
      if (path === "/api/sessions/workspace-session/messages") {
        return mockJsonResponse([]);
      }
      if (path === "/api/sessions/workspace-session/workspace") {
        return mockJsonResponse({
          root_path: "D:/repo/demo",
          display_name: "demo-repo",
          repo_fingerprint: "fingerprint-123",
          scan_excludes: [],
          selected_paths: ["README.md", "frontend/src/App.tsx"],
          index_status: "ready",
          last_scanned_at: 1710000000,
          summary: "2 个文件，1 个顶层目录/文件",
          files: ["README.md", "frontend/src/App.tsx"],
          tree: [],
        });
      }
      throw new Error(`Unexpected fetch: ${path}`);
    });

    (openSessionStream as jest.MockedFunction<typeof openSessionStream>).mockImplementation(
      (_sessionId, callback) => {
        streamCallback = callback;
        return jest.fn();
      },
    );

    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find((node) =>
      node.textContent?.includes("继续执行"),
    ) as HTMLButtonElement | undefined;
    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      streamCallback?.("tool_call", {
        participant_id: "claude",
        round: 2,
        server_name: "filesystem",
        tool_name: "read_file",
        arguments: { path: "README.md" },
      });
      streamCallback?.("tool_result", {
        participant_id: "claude",
        round: 2,
        server_name: "filesystem",
        tool_name: "read_file",
        text: "README 内容",
      });
      streamCallback?.("tool_call", {
        participant_id: "claude",
        round: 2,
        server_name: "workspace",
        tool_name: "run_command",
        arguments: { command: "npm test -- --runInBand", cwd: "D:/repo/demo" },
      });
      streamCallback?.("tool_result", {
        participant_id: "claude",
        round: 2,
        server_name: "workspace",
        tool_name: "run_command",
        text: "command=npm test -- --runInBand\ncwd=D:/repo/demo\nexit_code=1",
      });
      streamCallback?.("participant_error", {
        participant_id: "claude",
        round: 2,
        code: "TEST_FAILURE",
        message: "1 failing test remains",
        summary: "验证未通过",
      });
      await Promise.resolve();
    });

    expect(container.textContent).toContain("涉及文件");
    expect(container.textContent).toContain("README.md");
    expect(container.textContent).toContain("执行命令");
    expect(container.textContent).toContain("npm test -- --runInBand");
    expect(container.textContent).toContain("验证状态");
    expect(container.textContent).toContain("验证未通过");
    expect(container.textContent).toContain("阻塞与告警");
    expect(container.textContent).toContain("1 failing test remains");
  });

  test("keeps long commands out of the task sidebar summary while preserving them in execution surfaces", async () => {
    localStorage.setItem("mmdebate.lastSessionId", "workspace-session");

    jest.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/providers") {
        return mockJsonResponse([]);
      }
      if (path === "/api/sessions") {
        return mockJsonResponse([
          {
            id: "workspace-session",
            title: "workspace",
            topic: "修复测试失败",
            mode: "code_workspace",
            status: "active",
            current_round: 10,
            updated_at: 300,
            participant_count: 1,
            last_message_preview: "assistant reply",
          },
        ]);
      }
      if (path === "/api/sessions/workspace-session") {
        return mockJsonResponse({
          id: "workspace-session",
          title: "workspace",
          topic: "修复测试失败",
          mode: "code_workspace",
          status: "active",
          current_round: 10,
          participants: [
            { id: "p1", custom_id: "deepseek", model_ref: "deepseek/deepseek-chat", is_active: true },
          ],
          workspace: {
            root_path: "D:/repo/demo",
            display_name: "demo-repo",
            repo_fingerprint: "fingerprint-123",
            scan_excludes: [],
            selected_paths: ["frontend/src/App.tsx"],
            index_status: "ready",
            last_scanned_at: 1710000000,
            summary: "1 个文件",
          },
        });
      }
      if (path === "/api/sessions/workspace-session/snapshot") {
        return mockJsonResponse({
          topic: "修复测试失败",
          mode: "code_workspace",
          participant_summaries: {},
          consensus_list: [],
          key_events: [],
        });
      }
      if (path === "/api/sessions/workspace-session/messages") {
        return mockJsonResponse([]);
      }
      if (path === "/api/sessions/workspace-session/workspace") {
        return mockJsonResponse({
          root_path: "D:/repo/demo",
          display_name: "demo-repo",
          repo_fingerprint: "fingerprint-123",
          scan_excludes: [],
          selected_paths: ["frontend/src/App.tsx"],
          index_status: "ready",
          last_scanned_at: 1710000000,
          summary: "1 个文件",
          files: ["frontend/src/App.tsx"],
          tree: [],
        });
      }
      throw new Error(`Unexpected fetch: ${path}`);
    });

    (openSessionStream as jest.MockedFunction<typeof openSessionStream>).mockImplementation(
      (_sessionId, callback) => {
        streamCallback = callback;
        return jest.fn();
      },
    );

    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll("button")).find((node) =>
      node.textContent?.includes("继续执行"),
    ) as HTMLButtonElement | undefined;
    expect(startButton).toBeDefined();

    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    await act(async () => {
      streamCallback?.("tool_call", {
        participant_id: "deepseek",
        round: 10,
        server_name: "workspace",
        tool_name: "run_command",
        arguments: {
          command:
            "bash -lc grep -n \"workspacePresets\\|applyWorkspaceTeamPreset\\|WorkspaceTaskPresetRecommendation\\|onApplyTaskPreset\\|onApplyTeamPreset\\|selectedTemplateId\\|PRIMARY_TASK_TEMPLATES\" frontend/src/App.tsx frontend/src/WorkspaceMode.tsx frontend/src/modeOptions.ts || true",
          cwd: "D:/repo/demo",
        },
      });
      await Promise.resolve();
    });

    const taskSidebar = container.querySelector(".task-sidebar-summary") as HTMLElement | null;

    expect(taskSidebar).not.toBeNull();
    expect(taskSidebar?.textContent).not.toContain("bash -lc grep -n");
    expect(taskSidebar?.querySelector(".task-sidebar-command-item")).toBeNull();
    expect(container.textContent).toContain("bash -lc grep -n");
  });

  test("hydrates persisted assistant replies after round end when SSE emits no chunk", async () => {
    localStorage.setItem("mmdebate.lastSessionId", "workspace-session");
    let messageFetchCount = 0;

    jest.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/providers") {
        return mockJsonResponse([]);
      }
      if (path === "/api/sessions") {
        return mockJsonResponse([
          {
            id: "workspace-session",
            title: "workspace",
            topic: "评审本地仓库",
            mode: "code_workspace",
            status: "active",
            current_round: 1,
            updated_at: 300,
            participant_count: 1,
            last_message_preview: "assistant reply",
          },
        ]);
      }
      if (path === "/api/sessions/workspace-session") {
        return mockJsonResponse({
          id: "workspace-session",
          title: "workspace",
          topic: "评审本地仓库",
          mode: "code_workspace",
          status: "active",
          current_round: 1,
          participants: [
            { id: "p1", custom_id: "g54", model_ref: "openai/gpt-5.4", is_active: true },
          ],
          workspace: {
            root_path: "D:/repo/demo",
            display_name: "demo-repo",
            repo_fingerprint: "fingerprint-123",
            scan_excludes: [],
            selected_paths: ["README.md"],
            index_status: "ready",
            last_scanned_at: 1710000000,
            summary: "2 个文件，1 个顶层目录/文件",
          },
        });
      }
      if (path === "/api/sessions/workspace-session/snapshot") {
        return mockJsonResponse({
          topic: "评审本地仓库",
          mode: "code_workspace",
          participant_summaries: {},
          consensus_list: [],
          key_events: [],
        });
      }
      if (path === "/api/sessions/workspace-session/messages" && init?.method === "POST") {
        return mockJsonResponse({ status: "queued" });
      }
      if (path === "/api/sessions/workspace-session/messages") {
        messageFetchCount += 1;
        if (messageFetchCount === 1) {
          return mockJsonResponse([]);
        }
        return mockJsonResponse([
          {
            id: "user-msg-1",
            sender_id: "[用户]",
            message_type: "user_intervention",
            content: "@g54 直接修复",
            is_masked: false,
            is_compressed: false,
            drift_score: null,
            round_number: 1,
            created_at: 100,
          },
          {
            id: "assistant-msg-1",
            sender_id: "g54",
            message_type: "assistant",
            content: "已从持久化消息回填最终回复",
            is_masked: false,
            is_compressed: false,
            drift_score: null,
            round_number: 1,
            created_at: 101,
          },
        ]);
      }
      if (path === "/api/sessions/workspace-session/workspace") {
        return mockJsonResponse({
          root_path: "D:/repo/demo",
          display_name: "demo-repo",
          repo_fingerprint: "fingerprint-123",
          scan_excludes: [],
          selected_paths: ["README.md"],
          index_status: "ready",
          last_scanned_at: 1710000000,
          summary: "2 个文件，1 个顶层目录/文件",
          files: ["README.md"],
          tree: [
            {
              name: "README.md",
              path: "README.md",
              kind: "file",
              children: [],
            },
          ],
        });
      }
      throw new Error(`Unexpected fetch: ${path}`);
    });

    (openSessionStream as jest.MockedFunction<typeof openSessionStream>).mockImplementation(
      (_sessionId, callback) => {
        streamCallback = callback;
        return jest.fn();
      },
    );

    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    const textarea = Array.from(container.querySelectorAll("textarea")).find((node) =>
      (node as HTMLTextAreaElement).placeholder.includes("@alias"),
    ) as HTMLTextAreaElement | undefined;
    expect(textarea).toBeDefined();

    await act(async () => {
      textarea!.value = "@g54 直接修复";
      Simulate.change(textarea!);
      await Promise.resolve();
    });

    const sendButton = Array.from(container.querySelectorAll("button")).find((node) =>
      node.textContent?.includes("发送"),
    ) as HTMLButtonElement | undefined;
    expect(sendButton).toBeDefined();

    await act(async () => {
      sendButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      for (let i = 0; i < 6; i += 1) {
        await Promise.resolve();
      }
    });

    expect(openSessionStream).toHaveBeenCalled();
    expect(streamCallback).not.toBeNull();

    await act(async () => {
      streamCallback?.("round_end", {
        round: 1,
      });
      for (let i = 0; i < 6; i += 1) {
        await Promise.resolve();
      }
    });

    expect(messageFetchCount).toBe(2);
    expect(container.textContent).toContain("已从持久化消息回填最终回复");
  });

  test("allows resizing the session chat and workspace panes", async () => {
    localStorage.setItem("mmdebate.lastSessionId", "workspace-session");

    jest.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/providers") {
        return mockJsonResponse([]);
      }
      if (path === "/api/sessions") {
        return mockJsonResponse([
          {
            id: "workspace-session",
            title: "workspace",
            topic: "评审本地仓库",
            mode: "code_workspace",
            status: "active",
            current_round: 1,
            updated_at: 300,
            participant_count: 2,
            last_message_preview: "assistant reply",
          },
        ]);
      }
      if (path === "/api/sessions/workspace-session") {
        return mockJsonResponse({
          id: "workspace-session",
          title: "workspace",
          topic: "评审本地仓库",
          mode: "code_workspace",
          status: "active",
          current_round: 1,
          participants: [
            { id: "p1", custom_id: "claude", model_ref: "anthropic/claude-4.6", is_active: true },
            { id: "p2", custom_id: "codex", model_ref: "openai/gpt-5.4", is_active: true },
          ],
          workspace: {
            root_path: "D:/repo/demo",
            display_name: "demo-repo",
            repo_fingerprint: "fingerprint-123",
            scan_excludes: [],
            selected_paths: ["README.md"],
            index_status: "ready",
            last_scanned_at: 1710000000,
            summary: "2 个文件，1 个顶层目录/文件",
          },
        });
      }
      if (path === "/api/sessions/workspace-session/snapshot") {
        return mockJsonResponse({
          topic: "评审本地仓库",
          mode: "code_workspace",
          participant_summaries: {},
          consensus_list: [],
          key_events: [],
        });
      }
      if (path === "/api/sessions/workspace-session/messages") {
        return mockJsonResponse([]);
      }
      if (path === "/api/sessions/workspace-session/workspace") {
        return mockJsonResponse({
          root_path: "D:/repo/demo",
          display_name: "demo-repo",
          repo_fingerprint: "fingerprint-123",
          scan_excludes: [],
          selected_paths: ["README.md"],
          index_status: "ready",
          last_scanned_at: 1710000000,
          summary: "2 个文件，1 个顶层目录/文件",
          files: ["README.md", "src/app.py"],
          tree: [
            {
              name: "README.md",
              path: "README.md",
              kind: "file",
              children: [],
            },
          ],
        });
      }
      throw new Error(`Unexpected fetch: ${path}`);
    });

    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    const sessionLayout = container.querySelector(".session-layout") as HTMLDivElement | null;
    const resizer = container.querySelector(
      '[data-session-layout-resizer="true"]',
    ) as HTMLDivElement | null;

    expect(sessionLayout).not.toBeNull();
    expect(resizer).not.toBeNull();
    expect(sessionLayout?.style.gridTemplateColumns).toContain("420px");

    await act(async () => {
      resizer?.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, clientX: 700 }));
      window.dispatchEvent(new MouseEvent("mousemove", { bubbles: true, clientX: 580 }));
      window.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      await Promise.resolve();
    });

    expect(sessionLayout?.style.gridTemplateColumns).toContain("540px");
  });

  test("scans workspace preview and submits selected tree paths", async () => {
    let createSessionBody: Record<string, unknown> | null = null;
    let previewRequestBody: Record<string, unknown> | null = null;

    jest.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/providers") {
        return mockJsonResponse([]);
      }
      if (path === "/api/sessions" && !init?.method) {
        return mockJsonResponse([]);
      }
      if (path === "/api/workspace/preview" && init?.method === "POST") {
        previewRequestBody = JSON.parse(String(init.body)) as Record<string, unknown>;
        return mockJsonResponse({
          root_path: "D:/repo/demo",
          display_name: "demo-repo",
          repo_fingerprint: "preview-fingerprint",
          scan_excludes: [".git"],
          selected_paths: [],
          index_status: "ready",
          last_scanned_at: 1710000000,
          summary: "3 个文件，2 个顶层目录/文件",
          capabilities: {
            skill_sources: [
              {
                path: "D:/repo/skills",
                source_type: "local",
                label: null,
                recursive: true,
                enabled: true,
              },
            ],
            mcp_servers: [],
            agent_defaults: {
              mode: "tool_loop",
              max_steps: 6,
              can_write: false,
              allowed_skills: [],
              allowed_mcp_servers: [],
              memory_scope: "workspace_shared",
            },
            participant_overrides: {},
          },
          discovered_skills: [
            {
              name: "product-owner",
              description: "产品化评审技能",
              summary: "把问题收敛到 Workspace、Task、Run、Review、Provider。",
              path: "D:/repo/skills/product-owner/SKILL.md",
              source_type: "local",
              source_label: null,
            },
          ],
          files: ["README.md", "backend/api.py", "backend/orchestrator.py"],
          tree: [
            {
              name: "README.md",
              path: "README.md",
              kind: "file",
              children: [],
            },
            {
              name: "backend",
              path: "backend",
              kind: "dir",
              children: [
                {
                  name: "api.py",
                  path: "backend/api.py",
                  kind: "file",
                  children: [],
                },
                {
                  name: "orchestrator.py",
                  path: "backend/orchestrator.py",
                  kind: "file",
                  children: [],
                },
              ],
            },
          ],
        });
      }
      if (path === "/api/sessions" && init?.method === "POST") {
        createSessionBody = JSON.parse(String(init.body)) as Record<string, unknown>;
        return mockJsonResponse({
          id: "workspace-session",
          status: "active",
          mode: "code_workspace",
        });
      }
      if (path === "/api/sessions/workspace-session") {
        return mockJsonResponse({
          id: "workspace-session",
          title: "workspace",
          topic: "评审本地仓库",
          mode: "code_workspace",
          status: "active",
          current_round: 0,
          participants: [
            { id: "p1", custom_id: "Model_A", model_ref: "anthropic/claude-4.6", is_active: true },
            { id: "p2", custom_id: "Model_B", model_ref: "openai/gpt-5.4", is_active: true },
          ],
          workspace: {
            root_path: "D:/repo/demo",
            display_name: "demo-repo",
            repo_fingerprint: "preview-fingerprint",
            scan_excludes: [".git"],
            selected_paths: ["backend"],
            index_status: "ready",
            last_scanned_at: 1710000000,
            summary: "3 个文件，2 个顶层目录/文件",
          },
        });
      }
      if (path === "/api/sessions/workspace-session/snapshot") {
        return mockJsonResponse({
          topic: "评审本地仓库",
          mode: "code_workspace",
          participant_summaries: {},
          consensus_list: [],
          key_events: [],
        });
      }
      if (path === "/api/sessions/workspace-session/workspace") {
        return mockJsonResponse({
          root_path: "D:/repo/demo",
          display_name: "demo-repo",
          repo_fingerprint: "preview-fingerprint",
          scan_excludes: [".git"],
          selected_paths: ["backend"],
          index_status: "ready",
          last_scanned_at: 1710000000,
          summary: "3 个文件，2 个顶层目录/文件",
          files: ["README.md", "backend/api.py", "backend/orchestrator.py"],
          tree: [],
        });
      }
      throw new Error(`Unexpected fetch: ${path}`);
    });

    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const createTaskTab = Array.from(container.querySelectorAll("button")).find((node) =>
      node.textContent?.includes("新建任务"),
    ) as HTMLButtonElement | undefined;

    await act(async () => {
      createTaskTab?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    const workspaceCard = Array.from(container.querySelectorAll(".mode-card")).find((node) =>
      node.textContent?.includes("代码工作区"),
    ) as HTMLButtonElement | undefined;

    await act(async () => {
      workspaceCard?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    const advancedToggle = Array.from(container.querySelectorAll("button")).find((node) =>
      node.textContent?.includes("高级配置"),
    ) as HTMLButtonElement | undefined;
    expect(advancedToggle).toBeDefined();

    await act(async () => {
      advancedToggle?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    const rootPathInput = Array.from(container.querySelectorAll("input")).find((node) =>
      (node as HTMLInputElement).placeholder.includes("multi-model-debates"),
    ) as HTMLInputElement | null;
    const skillSourcesInput = container.querySelector(
      'textarea[name="workspace-skill-sources"]',
    ) as HTMLTextAreaElement | null;
    expect(rootPathInput).not.toBeNull();
    expect(skillSourcesInput).not.toBeNull();

    await act(async () => {
      rootPathInput!.value = "D:/repo/demo";
      Simulate.change(rootPathInput!);
      Simulate.change(skillSourcesInput!, { target: { value: "D:/repo/skills" } });
      await Promise.resolve();
    });

    const scanButton = Array.from(container.querySelectorAll("button")).find((node) =>
      node.textContent?.includes("扫描工作区"),
    ) as HTMLButtonElement | undefined;
    expect(scanButton).toBeDefined();

    await act(async () => {
      scanButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(previewRequestBody).toMatchObject({
      root_path: "D:/repo/demo",
      scan_excludes: [],
      capabilities: {
        skill_sources: [
          {
            path: "D:/repo/skills",
            source_type: "local",
            recursive: true,
            enabled: true,
          },
        ],
      },
    });
    expect(container.textContent).toContain("Agency Starter Pack");
    expect(container.textContent).toContain("已发现技能");
    expect(container.textContent).toContain("product-owner");
    expect(container.textContent).toContain("产品化评审技能");
    expect(container.textContent).toContain("建议 Task Presets");
    expect(container.textContent).toContain("Analyze Repo with Product Lens");
    expect(container.textContent).toContain("建议 Team Presets");
    expect(container.textContent).toContain("PO + Implementer");
    expect(container.textContent).toContain("backend/api.py");

    const taskPresetButton = container.querySelector(
      'button[data-workspace-task-preset="analyze_repo_product_lens"]',
    ) as HTMLButtonElement | null;
    const teamPresetButton = container.querySelector(
      'button[data-workspace-team-preset="product_owner_implementer_pair"]',
    ) as HTMLButtonElement | null;
    const topicInput = container.querySelector(".panel textarea") as HTMLTextAreaElement | null;
    const firstAliasInput = Array.from(container.querySelectorAll("input")).find((node) =>
      (node as HTMLInputElement).placeholder === "Model_A",
    ) as HTMLInputElement | undefined;

    expect(taskPresetButton).not.toBeNull();
    expect(teamPresetButton).not.toBeNull();
    expect(topicInput).not.toBeNull();
    expect(firstAliasInput).toBeDefined();

    await act(async () => {
      taskPresetButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      teamPresetButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(topicInput?.value).toContain("产品定位");
    expect(firstAliasInput?.value).toBe("Product_Owner");

    const backendCheckbox = container.querySelector(
      'input[data-workspace-select-path="backend"]',
    ) as HTMLInputElement | null;
    expect(backendCheckbox).not.toBeNull();

    await act(async () => {
      backendCheckbox!.checked = true;
      Simulate.change(backendCheckbox!);
      await Promise.resolve();
    });

    const createButton = Array.from(container.querySelectorAll("button")).find((node) =>
      node.textContent?.includes("🚀 新建任务"),
    ) as HTMLButtonElement | undefined;
    expect(createButton).toBeDefined();

    await act(async () => {
      createButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      for (let i = 0; i < 4; i += 1) {
        await Promise.resolve();
      }
    });

    expect(createSessionBody).not.toBeNull();
    expect(createSessionBody?.workspace).toMatchObject({
      root_path: "D:/repo/demo",
      selected_paths: ["backend"],
    });
  });
});
