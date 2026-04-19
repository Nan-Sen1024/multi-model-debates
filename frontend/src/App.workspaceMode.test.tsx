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

  test("shows code workspace creation controls and alias hints", async () => {
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

    const createSessionTab = Array.from(container.querySelectorAll("button")).find((node) =>
      node.textContent?.includes("创建会话"),
    ) as HTMLButtonElement | undefined;
    expect(createSessionTab).toBeDefined();

    await act(async () => {
      createSessionTab?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    const workspaceCard = Array.from(container.querySelectorAll(".mode-card")).find((node) =>
      node.textContent?.includes("代码工作区"),
    ) as HTMLButtonElement | undefined;
    expect(workspaceCard).toBeDefined();

    await act(async () => {
      workspaceCard?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(container.textContent).toContain("工作区路径");
    expect(container.textContent).toContain("@alias");
    expect(container.textContent).toContain("本地代码工作区");
  });

  test("renders workspace tree and auto-starts streaming after send", async () => {
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

    expect(container.textContent).toContain("demo-repo");
    expect(container.textContent).toContain("README.md");
    expect(container.textContent).toContain("src/app.py");
    expect(container.textContent).toContain("@alias");

    const textarea = Array.from(container.querySelectorAll("textarea")).find((node) =>
      (node as HTMLTextAreaElement).placeholder.includes("@alias"),
    ) as HTMLTextAreaElement | undefined;
    expect(textarea).toBeDefined();

    await act(async () => {
      textarea!.value = "@claude 先审查 README";
      Simulate.change(textarea!);
      await Promise.resolve();
    });

    const sendButton = Array.from(container.querySelectorAll("button")).find((node) =>
      node.textContent?.includes("发送"),
    ) as HTMLButtonElement | undefined;
    expect(sendButton).toBeDefined();
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
  });

  test("scans workspace preview and submits selected tree paths", async () => {
    let createSessionBody: Record<string, unknown> | null = null;

    jest.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/providers") {
        return mockJsonResponse([]);
      }
      if (path === "/api/sessions" && !init?.method) {
        return mockJsonResponse([]);
      }
      if (path === "/api/workspace/preview" && init?.method === "POST") {
        return mockJsonResponse({
          root_path: "D:/repo/demo",
          display_name: "demo-repo",
          repo_fingerprint: "preview-fingerprint",
          scan_excludes: [".git"],
          selected_paths: [],
          index_status: "ready",
          last_scanned_at: 1710000000,
          summary: "3 个文件，2 个顶层目录/文件",
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

    const createSessionTab = Array.from(container.querySelectorAll("button")).find((node) =>
      node.textContent?.includes("创建会话"),
    ) as HTMLButtonElement | undefined;

    await act(async () => {
      createSessionTab?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    const workspaceCard = Array.from(container.querySelectorAll(".mode-card")).find((node) =>
      node.textContent?.includes("代码工作区"),
    ) as HTMLButtonElement | undefined;

    await act(async () => {
      workspaceCard?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    const rootPathInput = container.querySelector(
      'input[placeholder*="multi-model-debates"]',
    ) as HTMLInputElement | null;
    expect(rootPathInput).not.toBeNull();

    await act(async () => {
      rootPathInput!.value = "D:/repo/demo";
      Simulate.change(rootPathInput!);
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

    expect(container.textContent).toContain("backend/api.py");

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
      node.textContent?.includes("🚀 创建会话"),
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
