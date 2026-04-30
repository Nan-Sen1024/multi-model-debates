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

describe("App code workspace passive session sync", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    jest.restoreAllMocks();
    jest.useFakeTimers();
    localStorage.clear();
    localStorage.setItem("mmdebate.lastSessionId", "workspace-session");
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

  test("polls persisted messages for active code workspace sessions even without SSE activity", async () => {
    let messageFetchCount = 0;

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
            topic: "Passive sync",
            mode: "code_workspace",
            status: "active",
            current_round: 52,
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
          topic: "Passive sync",
          mode: "code_workspace",
          status: "active",
          current_round: 52,
          participants: [
            { id: "p1", custom_id: "g54", model_ref: "gpt-5.4", is_active: true },
          ],
          workspace: {
            root_path: "D:/repo/demo",
            display_name: "demo-repo",
            repo_fingerprint: "fingerprint-123",
            scan_excludes: [],
            selected_paths: [],
            index_status: "ready",
            last_scanned_at: 1710000000,
            summary: "workspace ready",
          },
        });
      }
      if (path === "/api/sessions/workspace-session/snapshot") {
        return mockJsonResponse({
          topic: "Passive sync",
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
          scan_excludes: [],
          selected_paths: [],
          index_status: "ready",
          last_scanned_at: 1710000000,
          summary: "workspace ready",
          files: [],
          tree: [],
        });
      }
      if (path === "/api/sessions/workspace-session/messages") {
        messageFetchCount += 1;
        if (messageFetchCount === 1) {
          return mockJsonResponse([]);
        }
        return mockJsonResponse([
          {
            id: "m1",
            sender_id: "[用户]",
            message_type: "user_intervention",
            content: "@g54 修复它",
            is_masked: false,
            is_compressed: false,
            drift_score: null,
            round_number: 52,
            created_at: 100,
          },
          {
            id: "m2",
            sender_id: "g54",
            message_type: "dialogue",
            content: "这是被被动同步拉回来的回复",
            is_masked: false,
            is_compressed: false,
            drift_score: null,
            round_number: 52,
            created_at: 101,
          },
        ]);
      }
      throw new Error(`Unexpected fetch: ${path}`);
    });

    await act(async () => {
      root.render(React.createElement(App));
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(container.textContent).not.toContain("这是被被动同步拉回来的回复");
    expect(messageFetchCount).toBe(1);

    await act(async () => {
      jest.advanceTimersByTime(5000);
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(messageFetchCount).toBeGreaterThanOrEqual(2);
    expect(container.textContent).toContain("这是被被动同步拉回来的回复");
  });
});
