import React, { act } from "react";
import { createRoot, Root } from "react-dom/client";

jest.mock("./api", () => ({
  getWorkspaceFileContent: jest.fn(),
}));

import { getWorkspaceFileContent } from "./api";
import { WorkspaceSessionPanel } from "./WorkspaceMode";
import type { SessionWorkspaceView } from "./types";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

describe("WorkspaceSessionPanel file viewer", () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    jest.restoreAllMocks();
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

  test("loads and displays file content when a workspace file is clicked", async () => {
    const workspace: SessionWorkspaceView = {
      root_path: "D:/repo/demo",
      display_name: "demo-repo",
      repo_fingerprint: "fingerprint-123",
      scan_excludes: [],
      selected_paths: ["backend/api.py"],
      index_status: "ready",
      last_scanned_at: 1710000000,
      summary: "2 files",
      capabilities: null,
      files: ["backend/api.py", "README.md"],
      tree: [
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
          ],
        },
        {
          name: "README.md",
          path: "README.md",
          kind: "file",
          children: [],
        },
      ],
    };

    (getWorkspaceFileContent as jest.MockedFunction<typeof getWorkspaceFileContent>).mockResolvedValue({
      path: "backend/api.py",
      content: "print('workspace file viewer')",
      truncated: false,
    });

    await act(async () => {
      root.render(
        <WorkspaceSessionPanel
          sessionId="workspace-session"
          workspace={workspace}
          participants={[
            { custom_id: "claude", model_ref: "anthropic/claude-4.6" },
            { custom_id: "codex", model_ref: "openai/gpt-5.4" },
          ]}
          capabilities={null}
        />,
      );
      await Promise.resolve();
    });

    const fileButton = container.querySelector(
      'button[data-workspace-file-path="backend/api.py"]',
    ) as HTMLButtonElement | null;

    expect(fileButton).not.toBeNull();

    await act(async () => {
      fileButton?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(getWorkspaceFileContent).toHaveBeenCalledWith("workspace-session", "backend/api.py");
    expect(container.textContent).toContain("print('workspace file viewer')");
  });

  test("allows resizing the workspace tree and file panes", async () => {
    const workspace: SessionWorkspaceView = {
      root_path: "D:/repo/demo",
      display_name: "demo-repo",
      repo_fingerprint: "fingerprint-123",
      scan_excludes: [],
      selected_paths: [],
      index_status: "ready",
      last_scanned_at: 1710000000,
      summary: "2 files",
      capabilities: null,
      files: ["backend/api.py", "README.md"],
      tree: [
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
          ],
        },
      ],
    };

    await act(async () => {
      root.render(
        <WorkspaceSessionPanel
          sessionId="workspace-session"
          workspace={workspace}
          participants={[{ custom_id: "claude", model_ref: "anthropic/claude-4.6" }]}
          capabilities={null}
        />,
      );
      await Promise.resolve();
    });

    const browser = container.querySelector(".workspace-file-browser") as HTMLDivElement | null;
    const resizer = container.querySelector(
      '[data-workspace-browser-resizer="true"]',
    ) as HTMLDivElement | null;

    expect(browser).not.toBeNull();
    expect(resizer).not.toBeNull();
    expect(browser?.style.gridTemplateColumns).toContain("280px");

    await act(async () => {
      resizer?.dispatchEvent(new MouseEvent("mousedown", { bubbles: true, clientX: 300 }));
      window.dispatchEvent(new MouseEvent("mousemove", { bubbles: true, clientX: 420 }));
      window.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
      await Promise.resolve();
    });

    expect(browser?.style.gridTemplateColumns).toContain("400px");
  });
});
