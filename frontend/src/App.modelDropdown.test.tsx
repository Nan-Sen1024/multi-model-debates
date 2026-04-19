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

describe("App dynamic model dropdown", () => {
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

  test("loads discovered models into the provider edit dropdown", async () => {
    const fetchMock = jest.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/providers") {
        return mockJsonResponse([
          {
            id: "provider-xai",
            name: "xai-primary",
            provider_type: "xai",
            base_url: "https://api.x.ai/v1",
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
      if (path === "/api/model-catalog/discover" && init?.method === "POST") {
        const body = JSON.parse(String(init.body || "{}")) as Record<string, unknown>;
        if (body.provider_id === "provider-xai") {
          return mockJsonResponse({
            provider_id: "provider-xai",
            provider_name: "xai-primary",
            provider_type: "xai",
            models: ["grok-4.3", "grok-4.2"],
            detected_at: 123,
          });
        }
        if (body.provider) {
          return mockJsonResponse({
            provider_id: "__draft__",
            provider_name: String((body.provider as Record<string, unknown>).name || "draft"),
            provider_type: String((body.provider as Record<string, unknown>).provider_type || "openai"),
            models: [],
            detected_at: 123,
          });
        }
      }
      throw new Error(`Unexpected fetch: ${path}`);
    });

    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    const providerTab = Array.from(container.querySelectorAll("button")).find((node) =>
      node.textContent?.includes("Provider 配置"),
    ) as HTMLButtonElement | undefined;
    expect(providerTab).toBeDefined();

    await act(async () => {
      providerTab?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    const editButton = Array.from(container.querySelectorAll(".provider-card button")).find((node) =>
      node.textContent?.includes("编辑"),
    ) as HTMLButtonElement | undefined;
    expect(editButton).toBeDefined();

    await act(async () => {
      editButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    const discoverCall = fetchMock.mock.calls.find(([input, init]) =>
      String(input) === "/api/model-catalog/discover" &&
      JSON.parse(String(init?.body || "{}")).provider_id === "provider-xai",
    );
    expect(discoverCall).toBeDefined();
    expect(JSON.parse(String(discoverCall?.[1]?.body || "{}"))).toEqual({
      provider_id: "provider-xai",
    });

    expect(container.textContent).toContain("grok-4.3");
    expect(
      Array.from(container.querySelectorAll(".edit-form option")).some((option) => option.textContent === "grok-4.3"),
    ).toBe(true);
  });

  test("auto-binds provider_id from the selected model in create session", async () => {
    const fetchMock = jest.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
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
          {
            id: "provider-xai",
            name: "xai-primary",
            provider_type: "xai",
            base_url: "https://api.x.ai/v1",
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
      if (path === "/api/model-catalog/discover" && init?.method === "POST") {
        const body = JSON.parse(String(init.body || "{}")) as Record<string, unknown>;
        if (body.provider_id === "provider-openai") {
          return mockJsonResponse({
            provider_id: "provider-openai",
            provider_name: "openai-primary",
            provider_type: "openai",
            models: ["gpt-5.4"],
            detected_at: 123,
          });
        }
        if (body.provider_id === "provider-xai") {
          return mockJsonResponse({
            provider_id: "provider-xai",
            provider_name: "xai-primary",
            provider_type: "xai",
            models: ["grok-4.3"],
            detected_at: 123,
          });
        }
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
      await Promise.resolve();
    });

    const participantCard = Array.from(container.querySelectorAll(".participant-card")).find(
      (node) => node.textContent?.includes("参与者 1"),
    ) as HTMLElement | undefined;
    expect(participantCard).toBeDefined();

    const modelField = Array.from(participantCard?.querySelectorAll(".field") || []).find((node) =>
      node.textContent?.includes("模型选择"),
    ) as HTMLElement | undefined;
    const modelSelect = modelField?.querySelector("select") as HTMLSelectElement | undefined;
    const providerSelect = Array.from(participantCard?.querySelectorAll("select") || [])[0] as HTMLSelectElement | undefined;
    expect(modelSelect).toBeDefined();
    expect(providerSelect).toBeDefined();
    expect(Array.from(modelSelect!.options).some((option) => option.textContent === "grok-4.3")).toBe(true);

    await act(async () => {
      modelSelect!.value = "provider-xai::grok-4.3";
      modelSelect!.dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
    });

    expect(providerSelect!.value).toBe("provider-xai");
    expect(fetchMock.mock.calls.filter(([input]) => String(input) === "/api/model-catalog/discover").length).toBeGreaterThan(0);
  });
});
