import React, { act } from "react";
import { createRoot, Root } from "react-dom/client";
import { Simulate } from "react-dom/test-utils";

import App from "./App";

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

describe("App workspace capability editor", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    jest.restoreAllMocks();
    localStorage.clear();
    Element.prototype.scrollIntoView = jest.fn();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    container.remove();
  });

  test("shows capability editors in code workspace mode", async () => {
    jest.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/providers") {
        return mockJsonResponse([]);
      }
      if (path === "/api/sessions") {
        return mockJsonResponse([]);
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
    });

    const workspaceCard = Array.from(container.querySelectorAll(".mode-card")).find((node) =>
      node.textContent?.includes("代码工作区"),
    ) as HTMLButtonElement | undefined;
    expect(workspaceCard).toBeDefined();

    await act(async () => {
      workspaceCard?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(container.textContent).toContain("Skills");
    expect(container.textContent).toContain("MCP");
    expect(container.textContent).toContain("Agent");
    expect(container.textContent).toContain("参与者覆盖");
  });

  test("submits workspace capabilities when creating a code workspace session", async () => {
    let createSessionBody: Record<string, unknown> | null = null;

    jest.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/providers") {
        return mockJsonResponse([]);
      }
      if (path === "/api/sessions" && !init?.method) {
        return mockJsonResponse([]);
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
          topic: "评审仓库",
          mode: "code_workspace",
          status: "active",
          current_round: 0,
          workspace: {
            root_path: "D:/repo/demo",
            display_name: "demo-repo",
            repo_fingerprint: null,
            scan_excludes: [".git"],
            selected_paths: ["README.md"],
            index_status: "pending",
            last_scanned_at: null,
            summary: null,
            capabilities: {
              skill_sources: [
                { path: ".codex/skills", source_type: "local", recursive: true, enabled: true },
              ],
              mcp_servers: [
                {
                  name: "filesystem",
                  transport: "stdio",
                  command: "npx",
                  args: ["-y", "@modelcontextprotocol/server-filesystem"],
                  env: { ROOT_PATH: "D:/repo/demo" },
                  tools_allowlist: ["read_file"],
                  enabled: true,
                },
              ],
              agent_defaults: {
                mode: "tool_loop",
                max_steps: 4,
                can_write: false,
                allowed_skills: ["repo-review"],
                allowed_mcp_servers: ["filesystem"],
                memory_scope: "workspace_shared",
              },
              participant_overrides: {
                Model_A: {
                  skills: ["focused-review"],
                  mcp_servers: ["filesystem"],
                  agent: {
                    mode: "full_agent",
                    max_steps: 8,
                    can_write: false,
                    allowed_skills: [],
                    allowed_mcp_servers: [],
                    memory_scope: "workspace_shared",
                  },
                },
              },
            },
          },
          participants: [
            { id: "p1", custom_id: "Model_A", model_ref: "anthropic/claude-4.6", is_active: true },
            { id: "p2", custom_id: "Model_B", model_ref: "openai/gpt-5.4", is_active: true },
          ],
        });
      }
      if (path === "/api/sessions/workspace-session/snapshot") {
        return mockJsonResponse({
          topic: "评审仓库",
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
          repo_fingerprint: "fingerprint-123",
          scan_excludes: [".git"],
          selected_paths: ["README.md"],
          index_status: "ready",
          last_scanned_at: 1710000000,
          summary: "workspace ready",
          files: ["README.md"],
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
    const skillSourcesInput = container.querySelector(
      'textarea[name="workspace-skill-sources"]',
    ) as HTMLTextAreaElement | null;
    const mcpNameInput = container.querySelector(
      'input[name="workspace-mcp-name-0"]',
    ) as HTMLInputElement | null;
    const mcpCommandInput = container.querySelector(
      'input[name="workspace-mcp-command-0"]',
    ) as HTMLInputElement | null;
    const mcpArgsInput = container.querySelector(
      'textarea[name="workspace-mcp-args-0"]',
    ) as HTMLTextAreaElement | null;
    const mcpEnvInput = container.querySelector(
      'textarea[name="workspace-mcp-env-0"]',
    ) as HTMLTextAreaElement | null;
    const mcpToolsInput = container.querySelector(
      'textarea[name="workspace-mcp-tools-0"]',
    ) as HTMLTextAreaElement | null;
    const agentModeSelect = container.querySelector(
      'select[name="workspace-agent-mode"]',
    ) as HTMLSelectElement | null;
    const agentMaxStepsInput = container.querySelector(
      'input[name="workspace-agent-max-steps"]',
    ) as HTMLInputElement | null;
    const agentSkillsInput = container.querySelector(
      'textarea[name="workspace-agent-skills"]',
    ) as HTMLTextAreaElement | null;
    const agentServersInput = container.querySelector(
      'textarea[name="workspace-agent-mcp-servers"]',
    ) as HTMLTextAreaElement | null;
    const overrideSkillsInput = container.querySelector(
      'textarea[name="workspace-override-skills-Model_A"]',
    ) as HTMLTextAreaElement | null;
    const overrideServersInput = container.querySelector(
      'textarea[name="workspace-override-mcp-servers-Model_A"]',
    ) as HTMLTextAreaElement | null;
    const overrideAgentModeSelect = container.querySelector(
      'select[name="workspace-override-agent-mode-Model_A"]',
    ) as HTMLSelectElement | null;
    const overrideAgentMaxStepsInput = container.querySelector(
      'input[name="workspace-override-agent-max-steps-Model_A"]',
    ) as HTMLInputElement | null;
    const overrideAgentCanWriteInput = container.querySelector(
      'input[name="workspace-override-agent-can-write-Model_A"]',
    ) as HTMLInputElement | null;
    const overrideAgentSkillsInput = container.querySelector(
      'textarea[name="workspace-override-agent-skills-Model_A"]',
    ) as HTMLTextAreaElement | null;
    const overrideAgentServersInput = container.querySelector(
      'textarea[name="workspace-override-agent-mcp-servers-Model_A"]',
    ) as HTMLTextAreaElement | null;
    const overrideAgentMemoryScopeInput = container.querySelector(
      'input[name="workspace-override-agent-memory-scope-Model_A"]',
    ) as HTMLInputElement | null;

    expect(rootPathInput).not.toBeNull();
    expect(skillSourcesInput).not.toBeNull();
    expect(mcpNameInput).not.toBeNull();
    expect(agentModeSelect).not.toBeNull();
    expect(overrideSkillsInput).not.toBeNull();
    expect(overrideAgentModeSelect).not.toBeNull();
    expect(overrideAgentCanWriteInput).not.toBeNull();
    expect(overrideAgentSkillsInput).not.toBeNull();

    await act(async () => {
      Simulate.change(rootPathInput!, { target: { value: "D:/repo/demo" } });
      Simulate.change(skillSourcesInput!, { target: { value: ".codex/skills" } });
      Simulate.change(mcpNameInput!, { target: { value: "filesystem" } });
      Simulate.change(mcpCommandInput!, { target: { value: "npx" } });
      Simulate.change(mcpArgsInput!, {
        target: { value: "-y\n@modelcontextprotocol/server-filesystem" },
      });
      Simulate.change(mcpEnvInput!, { target: { value: "ROOT_PATH=D:/repo/demo" } });
      Simulate.change(mcpToolsInput!, { target: { value: "read_file" } });
      Simulate.change(agentModeSelect!, { target: { value: "tool_loop" } });
      Simulate.change(agentMaxStepsInput!, { target: { value: "4" } });
      Simulate.change(agentSkillsInput!, { target: { value: "repo-review" } });
      Simulate.change(agentServersInput!, { target: { value: "filesystem" } });
      Simulate.change(overrideSkillsInput!, { target: { value: "focused-review" } });
      Simulate.change(overrideServersInput!, { target: { value: "filesystem" } });
      Simulate.change(overrideAgentModeSelect!, { target: { value: "full_agent" } });
      Simulate.change(overrideAgentMaxStepsInput!, { target: { value: "8" } });
      Simulate.change(overrideAgentSkillsInput!, { target: { value: "fix-bugs" } });
      Simulate.change(overrideAgentServersInput!, { target: { value: "filesystem\nshell" } });
      Simulate.change(overrideAgentMemoryScopeInput!, { target: { value: "participant_private" } });
      Simulate.change(overrideAgentCanWriteInput!, { target: { checked: true } });
      await Promise.resolve();
    });

    const submitButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.trim() === "🚀 创建会话",
    ) as HTMLButtonElement | undefined;
    expect(submitButton).toBeDefined();

    await act(async () => {
      submitButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(createSessionBody).not.toBeNull();
    expect(createSessionBody).toMatchObject({
      mode: "code_workspace",
      workspace: {
        root_path: "D:/repo/demo",
        capabilities: {
          skill_sources: [
            { path: ".codex/skills", source_type: "local", recursive: true, enabled: true },
          ],
          mcp_servers: [
            {
              name: "filesystem",
              transport: "stdio",
              command: "npx",
              args: ["-y", "@modelcontextprotocol/server-filesystem"],
              env: { ROOT_PATH: "D:/repo/demo" },
              tools_allowlist: ["read_file"],
              enabled: true,
            },
          ],
          agent_defaults: {
            mode: "tool_loop",
            max_steps: 4,
            allowed_skills: ["repo-review"],
            allowed_mcp_servers: ["filesystem"],
          },
          participant_overrides: {
            Model_A: {
              skills: ["focused-review"],
              mcp_servers: ["filesystem"],
              agent: {
                mode: "full_agent",
                max_steps: 8,
                can_write: true,
                allowed_skills: ["fix-bugs"],
                allowed_mcp_servers: ["filesystem", "shell"],
                memory_scope: "participant_private",
              },
            },
          },
        },
      },
    });
    expect(container.textContent).toContain("参与者覆盖");
    expect(container.textContent).toContain("@Model_A");
    expect(container.textContent).toContain("full_agent");
  });

  test("copies default agent settings into a participant override", async () => {
    jest.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/providers") {
        return mockJsonResponse([]);
      }
      if (path === "/api/sessions") {
        return mockJsonResponse([]);
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

    const agentModeSelect = container.querySelector(
      'select[name="workspace-agent-mode"]',
    ) as HTMLSelectElement | null;
    const agentMaxStepsInput = container.querySelector(
      'input[name="workspace-agent-max-steps"]',
    ) as HTMLInputElement | null;
    const agentCanWriteInput = container.querySelector(
      'input[name="workspace-agent-can-write"]',
    ) as HTMLInputElement | null;
    const agentSkillsInput = container.querySelector(
      'textarea[name="workspace-agent-skills"]',
    ) as HTMLTextAreaElement | null;
    const agentServersInput = container.querySelector(
      'textarea[name="workspace-agent-mcp-servers"]',
    ) as HTMLTextAreaElement | null;
    const agentMemoryScopeInput = container.querySelector(
      'input[name="workspace-agent-memory-scope"]',
    ) as HTMLInputElement | null;
    const copyDefaultButton = Array.from(container.querySelectorAll("button")).find((node) =>
      node.textContent?.includes("复制默认 Agent 到 @Model_A"),
    ) as HTMLButtonElement | undefined;

    expect(copyDefaultButton).toBeDefined();

    await act(async () => {
      Simulate.change(agentModeSelect!, { target: { value: "full_agent" } });
      Simulate.change(agentMaxStepsInput!, { target: { value: "9" } });
      Simulate.change(agentCanWriteInput!, { target: { checked: true } });
      Simulate.change(agentSkillsInput!, { target: { value: "repo-review\nfix-bugs" } });
      Simulate.change(agentServersInput!, { target: { value: "filesystem\nshell" } });
      Simulate.change(agentMemoryScopeInput!, { target: { value: "participant_private" } });
      copyDefaultButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    const overrideAgentModeSelect = container.querySelector(
      'select[name="workspace-override-agent-mode-Model_A"]',
    ) as HTMLSelectElement | null;
    const overrideAgentMaxStepsInput = container.querySelector(
      'input[name="workspace-override-agent-max-steps-Model_A"]',
    ) as HTMLInputElement | null;
    const overrideAgentCanWriteInput = container.querySelector(
      'input[name="workspace-override-agent-can-write-Model_A"]',
    ) as HTMLInputElement | null;
    const overrideAgentSkillsInput = container.querySelector(
      'textarea[name="workspace-override-agent-skills-Model_A"]',
    ) as HTMLTextAreaElement | null;
    const overrideAgentServersInput = container.querySelector(
      'textarea[name="workspace-override-agent-mcp-servers-Model_A"]',
    ) as HTMLTextAreaElement | null;
    const overrideAgentMemoryScopeInput = container.querySelector(
      'input[name="workspace-override-agent-memory-scope-Model_A"]',
    ) as HTMLInputElement | null;

    expect(overrideAgentModeSelect?.value).toBe("full_agent");
    expect(overrideAgentMaxStepsInput?.value).toBe("9");
    expect(overrideAgentCanWriteInput?.checked).toBe(true);
    expect(overrideAgentSkillsInput?.value).toBe("repo-review\nfix-bugs");
    expect(overrideAgentServersInput?.value).toBe("filesystem\nshell");
    expect(overrideAgentMemoryScopeInput?.value).toBe("participant_private");
  });
});
