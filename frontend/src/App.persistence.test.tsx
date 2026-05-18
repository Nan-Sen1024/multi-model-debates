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

describe("App session persistence", () => {
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

  test("restores the latest valid session, history, and draft on app boot", async () => {
    localStorage.setItem("mmdebate.lastSessionId", "session-2");
    localStorage.setItem(
      "mmdebate.composerDrafts",
      JSON.stringify({
        "session-1": "draft for first",
        "session-2": "draft for restored",
      }),
    );

    jest.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/providers") {
        return mockJsonResponse([]);
      }
      if (path === "/api/sessions") {
        return mockJsonResponse([
          {
            id: "session-1",
            topic: "Other topic",
            mode: "debate",
            status: "active",
            current_round: 1,
            updated_at: 100,
            participant_count: 2,
            last_message_preview: "older reply",
          },
          {
            id: "session-2",
            topic: "Restored topic",
            mode: "chat",
            status: "active",
            current_round: 3,
            updated_at: 300,
            participant_count: 2,
            last_message_preview: "assistant reply",
          },
        ]);
      }
      if (path === "/api/sessions/session-2") {
        return mockJsonResponse({
          id: "session-2",
          topic: "Restored topic",
          mode: "chat",
          status: "active",
          current_round: 3,
          participants: [
            { id: "p1", custom_id: "ModelA", model_ref: "openai/gpt-4o", is_active: true },
            { id: "p2", custom_id: "ModelB", model_ref: "anthropic/claude-3", is_active: true },
          ],
        });
      }
      if (path === "/api/sessions/session-2/snapshot") {
        return mockJsonResponse({
          topic: "Restored topic",
          mode: "chat",
          participant_summaries: {},
          consensus_list: [],
          key_events: [],
        });
      }
      if (path === "/api/sessions/session-2/messages") {
        return mockJsonResponse([
          {
            id: "m1",
            sender_id: "[用户]",
            message_type: "user_intervention",
            content: "history user",
            is_masked: false,
            is_compressed: false,
            drift_score: null,
            round_number: 2,
            created_at: 200,
          },
          {
            id: "m2",
            sender_id: "ModelA",
            message_type: "dialogue",
            content: "assistant reply",
            is_masked: false,
            is_compressed: false,
            drift_score: null,
            round_number: 3,
            created_at: 201,
          },
        ]);
      }
      if (path === "/api/sessions/session-1") {
        return mockJsonResponse({
          id: "session-1",
          topic: "Other topic",
          mode: "debate",
          status: "active",
          current_round: 1,
          participants: [
            { id: "p1", custom_id: "ModelA", model_ref: "openai/gpt-4o", is_active: true },
            { id: "p2", custom_id: "ModelB", model_ref: "anthropic/claude-3", is_active: true },
          ],
        });
      }
      if (path === "/api/sessions/session-1/snapshot") {
        return mockJsonResponse({
          topic: "Other topic",
          mode: "debate",
          participant_summaries: {},
          consensus_list: [],
          key_events: [],
        });
      }
      if (path === "/api/sessions/session-1/messages") {
        return mockJsonResponse([
          {
            id: "m3",
            sender_id: "[用户]",
            message_type: "user_intervention",
            content: "older user",
            is_masked: false,
            is_compressed: false,
            drift_score: null,
            round_number: 1,
            created_at: 100,
          },
        ]);
      }
      throw new Error(`Unexpected fetch: ${path}`);
    });

    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.textContent).toContain("assistant reply");
    expect(container.textContent).toContain("history user");
    expect(container.querySelector('[aria-label="Tasks and runs"]')?.textContent).toContain("Restored topic");

    const draftBox = Array.from(container.querySelectorAll("textarea")).find(
      (node) => (node as HTMLTextAreaElement).value === "draft for restored",
    ) as HTMLTextAreaElement | undefined;
    expect(draftBox?.value).toBe("draft for restored");

    const otherSessionButton = Array.from(container.querySelectorAll("button")).find(
      (node) => node.textContent?.includes("Other topic"),
    ) as HTMLButtonElement | undefined;
    expect(otherSessionButton).toBeDefined();

    await act(async () => {
      otherSessionButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.textContent).toContain("older user");
  });

  test("still restores session history when providers fail to load", async () => {
    localStorage.setItem("mmdebate.lastSessionId", "session-2");

    jest.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const path = String(input);
      if (path === "/api/providers") {
        return {
          ok: false,
          status: 500,
          headers: {
            get(name: string) {
              return name.toLowerCase() === "content-type" ? "application/json" : null;
            },
          },
          json: async () => ({ detail: "provider list failed" }),
        } as Response;
      }
      if (path === "/api/sessions") {
        return mockJsonResponse([
          {
            id: "session-2",
            topic: "Restored despite provider error",
            mode: "chat",
            status: "active",
            current_round: 3,
            updated_at: 300,
            participant_count: 2,
            last_message_preview: "assistant reply",
          },
        ]);
      }
      if (path === "/api/sessions/session-2") {
        return mockJsonResponse({
          id: "session-2",
          topic: "Restored despite provider error",
          mode: "chat",
          status: "active",
          current_round: 3,
          participants: [
            { id: "p1", custom_id: "ModelA", model_ref: "openai/gpt-4o", is_active: true },
            { id: "p2", custom_id: "ModelB", model_ref: "anthropic/claude-3", is_active: true },
          ],
        });
      }
      if (path === "/api/sessions/session-2/snapshot") {
        return mockJsonResponse({
          topic: "Restored despite provider error",
          mode: "chat",
          participant_summaries: {},
          consensus_list: [],
          key_events: [],
        });
      }
      if (path === "/api/sessions/session-2/messages") {
        return mockJsonResponse([
          {
            id: "m2",
            sender_id: "ModelA",
            message_type: "dialogue",
            content: "assistant reply",
            is_masked: false,
            is_compressed: false,
            drift_score: null,
            round_number: 3,
            created_at: 201,
          },
        ]);
      }
      throw new Error(`Unexpected fetch: ${path}`);
    });

    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.textContent).toContain("Restored despite provider error");
    expect(container.textContent).toContain("assistant reply");
  });
});
