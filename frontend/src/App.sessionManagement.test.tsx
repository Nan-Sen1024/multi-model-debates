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
});
