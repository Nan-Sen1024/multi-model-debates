import React, { act } from "react";
import { createRoot, Root } from "react-dom/client";

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

describe("App provider auth options", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    jest.restoreAllMocks();
    jest.useFakeTimers();
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => {
      root.unmount();
    });
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
    container.remove();
  });

  test("shows auth method choices for codex and claude providers", async () => {
    jest.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/providers") {
        return mockJsonResponse([
          {
            id: "p-codex",
            name: "codex",
            provider_type: "openai",
            base_url: "https://api.openai.com/v1",
            api_format: "openai-completions",
            auth_type: "oauth",
            auth_metadata: {},
            auth_status: "missing",
            auth_expires_at: null,
            fallback_ids: [],
            is_active: true,
          },
          {
            id: "p-claude",
            name: "claude",
            provider_type: "anthropic",
            base_url: "https://api.anthropic.com/v1",
            api_format: "anthropic-messages",
            auth_type: "oauth",
            auth_metadata: {
              authorization_endpoint: "https://example.com/authorize",
              token_endpoint: "https://example.com/token",
              device_authorization_endpoint: "https://example.com/device",
              client_id: "client-123",
              scope: "openid profile",
            },
            auth_status: "missing",
            auth_expires_at: null,
            fallback_ids: [],
            is_active: true,
          },
        ]);
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

    const providerTab = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("Provider 配置"),
    ) as HTMLButtonElement | undefined;
    expect(providerTab).toBeDefined();

    await act(async () => {
      providerTab?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    const cards = Array.from(container.querySelectorAll(".provider-card"));
    const codexCard = cards.find((node) => node.textContent?.includes("codex"));
    const claudeCard = cards.find((node) => node.textContent?.includes("claude"));

    expect(codexCard?.textContent).toContain("认证方式");
    expect(codexCard?.textContent).toContain("浏览器登录");
    expect(codexCard?.textContent).toContain("Device Code");
    expect(codexCard?.textContent).toContain("API Key");

    expect(claudeCard?.textContent).toContain("认证方式");
    expect(claudeCard?.textContent).toContain("浏览器登录");
    expect(claudeCard?.textContent).toContain("Device Code");
    expect(claudeCard?.textContent).toContain("API Key");
  });

  test("shows auth method choices in the provider creation form", async () => {
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

    const providerTab = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("Provider 配置"),
    ) as HTMLButtonElement | undefined;
    expect(providerTab).toBeDefined();

    await act(async () => {
      providerTab?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    const createPanel = Array.from(container.querySelectorAll(".panel")).find(
      (node) => node.textContent?.includes("添加 Provider"),
    );

    expect(createPanel?.textContent).toContain("认证方式");
    expect(createPanel?.textContent).toContain("浏览器登录");
    expect(createPanel?.textContent).toContain("Device Code");
    expect(createPanel?.textContent).toContain("API Key");
  });

  test("shows logout action for a ready provider", async () => {
    jest.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/providers") {
        return mockJsonResponse([
          {
            id: "p-codex",
            name: "codex",
            provider_type: "openai",
            base_url: "https://api.openai.com/v1",
            api_format: "openai-completions",
            auth_type: "oauth",
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
      if (path === "/api/providers/p-codex/auth/logout") {
        return mockJsonResponse({ provider_id: "p-codex", status: "logged_out" });
      }
      throw new Error(`Unexpected fetch: ${path}`);
    });

    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const providerTab = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("Provider 配置"),
    ) as HTMLButtonElement | undefined;
    await act(async () => {
      providerTab?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(container.textContent).toContain("退出登录");
  });

  test("shows cancel action for a pending auth flow", async () => {
    const fetchMock = jest.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/providers") {
        return mockJsonResponse([
          {
            id: "p-codex",
            name: "codex",
            provider_type: "openai",
            base_url: "https://api.openai.com/v1",
            api_format: "openai-completions",
            auth_type: "oauth",
            auth_metadata: {},
            auth_status: "missing",
            auth_expires_at: null,
            fallback_ids: [],
            is_active: true,
          },
        ]);
      }
      if (path === "/api/sessions") {
        return mockJsonResponse([]);
      }
      if (path === "/api/providers/p-codex/auth/start" && init?.method === "POST") {
        return mockJsonResponse({
          auth_session_id: "auth-codex",
          verification_uri: "https://auth.example.com/device",
          user_code: "ABCD-1234",
          expires_in: 300,
          interval: 5,
          flow_type: "openai_codex",
        });
      }
      if (path === "/api/providers/p-codex/auth/cancel/auth-codex" && init?.method === "POST") {
        return mockJsonResponse({
          auth_session_id: "auth-codex",
          status: "cancelled",
          flow_type: "openai_codex",
          error_message: "用户已取消登录",
        });
      }
      if (path === "/api/providers/p-codex/auth/status/auth-codex") {
        return mockJsonResponse({
          auth_session_id: "auth-codex",
          status: "pending",
          flow_type: "openai_codex",
        });
      }
      throw new Error(`Unexpected fetch: ${path}`);
    });

    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const providerTab = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("Provider 配置"),
    ) as HTMLButtonElement | undefined;
    await act(async () => {
      providerTab?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    const authSelect = Array.from(container.querySelectorAll(".provider-card select")).find(
      (node) => (node as HTMLSelectElement).value === "browser",
    ) as HTMLSelectElement | undefined;
    await act(async () => {
      authSelect!.value = "device_code";
      authSelect?.dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
    });

    const startButton = Array.from(container.querySelectorAll(".provider-card button")).find(
      (node) => node.textContent?.includes("开始 Device Code 登录"),
    ) as HTMLButtonElement | undefined;
    await act(async () => {
      startButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(container.textContent).toContain("取消登录");

    const cancelButton = Array.from(container.querySelectorAll(".provider-card button")).find(
      (node) => node.textContent?.includes("取消登录"),
    ) as HTMLButtonElement | undefined;
    await act(async () => {
      cancelButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/providers/p-codex/auth/cancel/auth-codex",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
