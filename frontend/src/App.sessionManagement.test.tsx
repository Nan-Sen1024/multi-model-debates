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

function mockJsonErrorResponse(data: unknown, status = 503) {
  return {
    ok: false,
    status,
    headers: {
      get(name: string) {
        return name.toLowerCase() === "content-type" ? "application/json" : null;
      },
    },
    json: async () => data,
  } as Response;
}

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("App session management", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    jest.restoreAllMocks();
    jest.useFakeTimers();
    jest.setSystemTime(new Date("2026-04-18T14:23:00Z"));
    localStorage.clear();
    localStorage.setItem("mmdebate.lastSessionId", "session-1");
    Element.prototype.scrollIntoView = jest.fn();
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

  test("shows a current time label and supports renaming and deleting sessions", async () => {
    let sessions = [
      {
        id: "session-1",
        title: "Alpha",
        topic: "Original topic",
        mode: "debate",
        status: "active",
        current_round: 2,
        updated_at: 300,
        participant_count: 2,
        last_message_preview: "assistant reply",
      },
      {
        id: "session-2",
        title: "Beta",
        topic: "Other topic",
        mode: "chat",
        status: "active",
        current_round: 1,
        updated_at: 200,
        participant_count: 2,
        last_message_preview: "older reply",
      },
    ];

    jest.spyOn(window, "prompt").mockReturnValue("Renamed session");
    jest.spyOn(window, "confirm").mockReturnValue(true);

    jest.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/providers") {
        return mockJsonResponse([]);
      }
      if (path === "/api/sessions") {
        return mockJsonResponse(sessions);
      }
      if (path === "/api/sessions/session-1" && (!init || init.method === undefined)) {
        return mockJsonResponse({
          id: "session-1",
          title: sessions[0].title,
          topic: "Original topic",
          mode: "debate",
          status: "active",
          current_round: 2,
          participants: [
            { id: "p1", custom_id: "ModelA", model_ref: "openai/gpt-4o", is_active: true },
            { id: "p2", custom_id: "ModelB", model_ref: "deepseek/deepseek-chat", is_active: true },
          ],
        });
      }
      if (path === "/api/sessions/session-1/snapshot") {
        return mockJsonResponse({
          topic: "Original topic",
          mode: "debate",
          participant_summaries: {},
          consensus_list: [],
          key_events: [],
        });
      }
      if (path === "/api/sessions/session-1/messages") {
        return mockJsonResponse([]);
      }
      if (path === "/api/sessions/session-2" && (!init || init.method === undefined)) {
        return mockJsonResponse({
          id: "session-2",
          title: sessions[1].title,
          topic: "Other topic",
          mode: "chat",
          status: "active",
          current_round: 1,
          participants: [
            { id: "p1", custom_id: "ModelA", model_ref: "openai/gpt-4o", is_active: true },
            { id: "p2", custom_id: "ModelB", model_ref: "deepseek/deepseek-chat", is_active: true },
          ],
        });
      }
      if (path === "/api/sessions/session-2/snapshot") {
        return mockJsonResponse({
          topic: "Other topic",
          mode: "chat",
          participant_summaries: {},
          consensus_list: [],
          key_events: [],
        });
      }
      if (path === "/api/sessions/session-2/messages") {
        return mockJsonResponse([]);
      }
      if (path === "/api/sessions/session-1" && init?.method === "PATCH") {
        sessions = sessions.map((item) =>
          item.id === "session-1" ? { ...item, title: "Renamed session" } : item,
        );
        return mockJsonResponse({
          id: "session-1",
          title: "Renamed session",
          topic: "Original topic",
          mode: "debate",
          status: "active",
          current_round: 2,
          participants: [
            { id: "p1", custom_id: "ModelA", model_ref: "openai/gpt-4o", is_active: true },
            { id: "p2", custom_id: "ModelB", model_ref: "deepseek/deepseek-chat", is_active: true },
          ],
        });
      }
      if (path === "/api/sessions/session-2" && init?.method === "DELETE") {
        sessions = sessions.filter((item) => item.id !== "session-2");
        return mockJsonResponse({ reason: "user_terminated", summary: "" });
      }
      throw new Error(`Unexpected fetch: ${path}`);
    });

    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.textContent).toContain("时间");
    expect(container.textContent).toContain("Alpha");
    expect(container.textContent).toContain("Beta");

    const renameButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.getAttribute("aria-label") === "重命名 Alpha",
    ) as HTMLButtonElement | undefined;
    expect(renameButton).toBeDefined();

    await act(async () => {
      renameButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.textContent).toContain("Renamed session");

    const deleteButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.getAttribute("aria-label") === "删除 Beta",
    ) as HTMLButtonElement | undefined;
    expect(deleteButton).toBeDefined();

    await act(async () => {
      deleteButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.textContent).not.toContain("Beta");
  });

  test("appends multiple participants to the current session from session detail", async () => {
    let sessionDetail = {
      id: "session-1",
      title: "Alpha",
      topic: "Original topic",
      mode: "debate",
      status: "active",
      current_round: 2,
      participants: [
        { id: "p1", custom_id: "ModelA", model_ref: "openai/gpt-4o", provider_id: "provider-openai", is_active: true },
        { id: "p2", custom_id: "ModelB", model_ref: "deepseek/deepseek-chat", is_active: true },
      ],
    };

    jest.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/providers") {
        return mockJsonResponse([
          {
            id: "provider-openai",
            name: "OpenAI Browser",
            provider_type: "openai",
            base_url: "",
            api_format: "responses",
            auth_type: "oauth",
            auth_metadata: {},
            fallback_ids: [],
            is_active: true,
            auth_status: "ready",
          },
        ]);
      }
      if (path === "/api/model-catalog/discover" && init?.method === "POST") {
        return mockJsonResponse({
          provider_id: "provider-openai",
          provider_name: "OpenAI Browser",
          provider_type: "openai",
          models: ["gpt-4o", "gpt-5.4"],
          detected_at: 123,
        });
      }
      if (path === "/api/sessions") {
        return mockJsonResponse([
          {
            id: "session-1",
            title: "Alpha",
            topic: "Original topic",
            mode: "debate",
            status: "active",
            current_round: 2,
            updated_at: 300,
            participant_count: sessionDetail.participants.length,
            last_message_preview: "assistant reply",
          },
        ]);
      }
      if (path === "/api/sessions/session-1" && (!init || init.method === undefined)) {
        return mockJsonResponse(sessionDetail);
      }
      if (path === "/api/sessions/session-1/snapshot") {
        return mockJsonResponse({
          topic: "Original topic",
          mode: "debate",
          participant_summaries: {},
          consensus_list: [],
          key_events: [],
        });
      }
      if (path === "/api/sessions/session-1/messages") {
        return mockJsonResponse([]);
      }
      if (path === "/api/sessions/session-1/participants/batch" && init?.method === "POST") {
        sessionDetail = {
          ...sessionDetail,
          participants: [
            ...sessionDetail.participants,
            {
              id: "p3",
              custom_id: "Reviewer",
              model_ref: "gpt-5.4",
              provider_id: "provider-openai",
              role_desc: "review code",
              is_active: true,
            },
            {
              id: "p4",
              custom_id: "Tester",
              model_ref: "gpt-4o",
              provider_id: "provider-openai",
              role_desc: "write tests",
              is_active: true,
            },
          ],
        };
        return mockJsonResponse(sessionDetail);
      }
      throw new Error(`Unexpected fetch: ${path}`);
    });

    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    const toggleButton = container.querySelector(
      '[data-testid="session-participant-add-toggle"]',
    ) as HTMLButtonElement | null;
    expect(toggleButton).not.toBeNull();

    await act(async () => {
      toggleButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    const addRowButton = container.querySelector(
      '[data-testid="session-participant-row-add"]',
    ) as HTMLButtonElement | null;
    expect(addRowButton).not.toBeNull();

    await act(async () => {
      addRowButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    const providerSelects = Array.from(
      container.querySelectorAll('[data-testid="session-participant-provider"]'),
    ) as HTMLSelectElement[];
    const modelSelects = Array.from(
      container.querySelectorAll('[data-testid="session-participant-model"] select'),
    ) as HTMLSelectElement[];
    const aliasInputs = Array.from(
      container.querySelectorAll('[data-testid="session-participant-custom-id"]'),
    ) as HTMLInputElement[];
    const roleInputs = Array.from(
      container.querySelectorAll('[data-testid="session-participant-role"]'),
    ) as HTMLInputElement[];
    const submitButton = container.querySelector(
      '[data-testid="session-participant-submit"]',
    ) as HTMLButtonElement | null;

    expect(providerSelects).toHaveLength(2);
    expect(modelSelects).toHaveLength(2);
    expect(aliasInputs).toHaveLength(2);
    expect(roleInputs).toHaveLength(2);
    expect(submitButton).not.toBeNull();

    await act(async () => {
      providerSelects[0].value = "provider-openai";
      providerSelects[0].dispatchEvent(new Event("change", { bubbles: true }));
      modelSelects[0].value = "provider-openai::gpt-5.4";
      modelSelects[0].dispatchEvent(new Event("change", { bubbles: true }));
      aliasInputs[0].value = "Reviewer";
      aliasInputs[0].dispatchEvent(new Event("input", { bubbles: true }));
      roleInputs[0].value = "review code";
      roleInputs[0].dispatchEvent(new Event("input", { bubbles: true }));

      providerSelects[1].value = "provider-openai";
      providerSelects[1].dispatchEvent(new Event("change", { bubbles: true }));
      modelSelects[1].value = "provider-openai::gpt-4o";
      modelSelects[1].dispatchEvent(new Event("change", { bubbles: true }));
      aliasInputs[1].value = "Tester";
      aliasInputs[1].dispatchEvent(new Event("input", { bubbles: true }));
      roleInputs[1].value = "write tests";
      roleInputs[1].dispatchEvent(new Event("input", { bubbles: true }));

      submitButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.textContent).toContain("@Reviewer");
    expect(container.textContent).toContain("@Tester");
  });

  test("auto-binds provider_id when appending a participant from a bare model selection", async () => {
    let sessionDetail = {
      id: "session-1",
      title: "Alpha",
      topic: "Original topic",
      mode: "debate",
      status: "active",
      current_round: 2,
      participants: [
        { id: "p1", custom_id: "ModelA", model_ref: "openai/gpt-4o", provider_id: "provider-openai", is_active: true },
        { id: "p2", custom_id: "ModelB", model_ref: "deepseek/deepseek-chat", is_active: true },
      ],
    };

    let submittedBody: Record<string, unknown> | null = null;

    jest.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/providers") {
        return mockJsonResponse([
          {
            id: "provider-openai",
            name: "OpenAI Browser",
            provider_type: "openai",
            base_url: "",
            api_format: "responses",
            auth_type: "oauth",
            auth_metadata: {},
            fallback_ids: [],
            is_active: true,
            auth_status: "ready",
          },
        ]);
      }
      if (path === "/api/model-catalog/discover" && init?.method === "POST") {
        return mockJsonResponse({
          provider_id: "provider-openai",
          provider_name: "OpenAI Browser",
          provider_type: "openai",
          models: ["gpt-4o", "gpt-5.4"],
          detected_at: 123,
        });
      }
      if (path === "/api/sessions") {
        return mockJsonResponse([
          {
            id: "session-1",
            title: "Alpha",
            topic: "Original topic",
            mode: "debate",
            status: "active",
            current_round: 2,
            updated_at: 300,
            participant_count: sessionDetail.participants.length,
            last_message_preview: "assistant reply",
          },
        ]);
      }
      if (path === "/api/sessions/session-1" && (!init || init.method === undefined)) {
        return mockJsonResponse(sessionDetail);
      }
      if (path === "/api/sessions/session-1/snapshot") {
        return mockJsonResponse({
          topic: "Original topic",
          mode: "debate",
          participant_summaries: {},
          consensus_list: [],
          key_events: [],
        });
      }
      if (path === "/api/sessions/session-1/messages") {
        return mockJsonResponse([]);
      }
      if (path === "/api/sessions/session-1/participants/batch" && init?.method === "POST") {
        submittedBody = JSON.parse(String(init.body || "{}")) as Record<string, unknown>;
        sessionDetail = {
          ...sessionDetail,
          participants: [
            ...sessionDetail.participants,
            {
              id: "p3",
              custom_id: "Reviewer",
              model_ref: "gpt-5.4",
              provider_id: "provider-openai",
              role_desc: "review code",
              is_active: true,
            },
          ],
        };
        return mockJsonResponse(sessionDetail);
      }
      throw new Error(`Unexpected fetch: ${path}`);
    });

    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    const toggleButton = container.querySelector(
      '[data-testid="session-participant-add-toggle"]',
    ) as HTMLButtonElement | null;
    expect(toggleButton).not.toBeNull();

    await act(async () => {
      toggleButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    const providerSelect = container.querySelector(
      '[data-testid="session-participant-provider"]',
    ) as HTMLSelectElement | null;
    const modelSelect = container.querySelector(
      '[data-testid="session-participant-model"] select',
    ) as HTMLSelectElement | null;
    const aliasInput = container.querySelector(
      '[data-testid="session-participant-custom-id"]',
    ) as HTMLInputElement | null;
    const roleInput = container.querySelector(
      '[data-testid="session-participant-role"]',
    ) as HTMLInputElement | null;

    expect(providerSelect).not.toBeNull();
    expect(modelSelect).not.toBeNull();
    expect(aliasInput).not.toBeNull();
    expect(roleInput).not.toBeNull();

    await act(async () => {
      providerSelect!.value = "";
      providerSelect!.dispatchEvent(new Event("change", { bubbles: true }));
      Simulate.change(modelSelect!, { target: { value: "gpt-5.4" } });
      Simulate.change(aliasInput!, { target: { value: "Reviewer" } });
      Simulate.change(roleInput!, { target: { value: "review code" } });
      await Promise.resolve();
    });

    expect(providerSelect!.value).toBe("provider-openai");

    const submitButton = container.querySelector(
      '[data-testid="session-participant-submit"]',
    ) as HTMLButtonElement | null;
    expect(submitButton).not.toBeNull();

    await act(async () => {
      submitButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(submittedBody).toEqual({
      participants: [
        {
          custom_id: "Reviewer",
          model_ref: "gpt-5.4",
          provider_id: "provider-openai",
          role_desc: "review code",
        },
      ],
    });
  });

  test("keeps the append participant panel open when the backend rejects the request", async () => {
    let sessionDetail = {
      id: "session-1",
      title: "Alpha",
      topic: "Original topic",
      mode: "debate",
      status: "active",
      current_round: 2,
      participants: [
        { id: "p1", custom_id: "ModelA", model_ref: "openai/gpt-4o", provider_id: "provider-openai", is_active: true },
      ],
    };

    jest.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const path = String(input);
      if (path === "/api/providers") {
        return mockJsonResponse([
          {
            id: "provider-openai",
            name: "OpenAI Browser",
            provider_type: "openai",
            base_url: "",
            api_format: "responses",
            auth_type: "oauth",
            auth_metadata: {},
            fallback_ids: [],
            is_active: true,
            auth_status: "ready",
          },
        ]);
      }
      if (path === "/api/model-catalog/discover" && init?.method === "POST") {
        return mockJsonResponse({
          provider_id: "provider-openai",
          provider_name: "OpenAI Browser",
          provider_type: "openai",
          models: ["gpt-4o", "gpt-5.4"],
          detected_at: 123,
        });
      }
      if (path === "/api/sessions") {
        return mockJsonResponse([
          {
            id: "session-1",
            title: "Alpha",
            topic: "Original topic",
            mode: "debate",
            status: "active",
            current_round: 2,
            updated_at: 300,
            participant_count: sessionDetail.participants.length,
            last_message_preview: "assistant reply",
          },
        ]);
      }
      if (path === "/api/sessions/session-1" && (!init || init.method === undefined)) {
        return mockJsonResponse(sessionDetail);
      }
      if (path === "/api/sessions/session-1/snapshot") {
        return mockJsonResponse({
          topic: "Original topic",
          mode: "debate",
          participant_summaries: {},
          consensus_list: [],
          key_events: [],
        });
      }
      if (path === "/api/sessions/session-1/messages") {
        return mockJsonResponse([]);
      }
      if (path === "/api/sessions/session-1/participants/batch" && init?.method === "POST") {
        return mockJsonErrorResponse({
          error: {
            code: "PROVIDER_UNAVAILABLE",
            message: "LiteLLM ChatGPT responses invocation failed: Cannot connect to host 127.0.0.1:7897",
          },
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

    const toggleButton = container.querySelector(
      '[data-testid="session-participant-add-toggle"]',
    ) as HTMLButtonElement | null;
    expect(toggleButton).not.toBeNull();

    await act(async () => {
      toggleButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
    });

    const providerSelect = container.querySelector(
      '[data-testid="session-participant-provider"]',
    ) as HTMLSelectElement | null;
    const modelSelect = container.querySelector(
      '[data-testid="session-participant-model"] select',
    ) as HTMLSelectElement | null;
    const aliasInput = container.querySelector(
      '[data-testid="session-participant-custom-id"]',
    ) as HTMLInputElement | null;
    const roleInput = container.querySelector(
      '[data-testid="session-participant-role"]',
    ) as HTMLInputElement | null;
    const submitButton = container.querySelector(
      '[data-testid="session-participant-submit"]',
    ) as HTMLButtonElement | null;

    expect(providerSelect).not.toBeNull();
    expect(modelSelect).not.toBeNull();
    expect(aliasInput).not.toBeNull();
    expect(roleInput).not.toBeNull();
    expect(submitButton).not.toBeNull();

    await act(async () => {
      providerSelect!.value = "provider-openai";
      providerSelect!.dispatchEvent(new Event("change", { bubbles: true }));
      modelSelect!.value = "provider-openai::gpt-5.4";
      modelSelect!.dispatchEvent(new Event("change", { bubbles: true }));
      aliasInput!.value = "Reviewer";
      aliasInput!.dispatchEvent(new Event("input", { bubbles: true }));
      roleInput!.value = "review code";
      roleInput!.dispatchEvent(new Event("input", { bubbles: true }));
      submitButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(
      container.querySelector('[data-testid="session-participant-submit"]'),
    ).not.toBeNull();
  });
});
