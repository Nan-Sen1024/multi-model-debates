# Code Workspace Mode Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `code_workspace` session mode that can attach to one local repository path, route messages by `@alias`, and stream shared code-review / coding outputs to the UI.

**Architecture:** Keep the existing FastAPI + SQLite + React app. Persist workspace settings inside the existing session `config` JSON, add focused backend helpers for scanning, context assembly, and `@alias` parsing, and branch the orchestrator only for `code_workspace` dispatch. The frontend gets a workspace-specific layout that still reuses the current session and SSE primitives.

**Tech Stack:** Python 3, FastAPI, aiosqlite, pytest, React, TypeScript, react-scripts/Jest

---

## File Structure

- Create: `backend/workspace_scanner.py`
- Create: `backend/workspace_context.py`
- Create: `backend/workspace_router.py`
- Modify: `backend/enums.py`
- Modify: `backend/models.py`
- Modify: `backend/database.py`
- Modify: `backend/api.py`
- Modify: `backend/strategies.py`
- Modify: `backend/orchestrator.py`
- Modify: `frontend/src/modeOptions.ts`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/WorkspaceMode.tsx`
- Test: `tests/test_workspace_mode.py`
- Test: `tests/test_workspace_scanner.py`
- Test: `tests/test_workspace_router.py`
- Test: `frontend/src/App.workspaceMode.test.tsx`

## Chunk 1: Workspace State and Session Persistence

### Task 1: Add workspace config to the session model

**Files:**
- Modify: `backend/enums.py`
- Modify: `backend/models.py`
- Modify: `backend/orchestrator.py`
- Test: `tests/test_workspace_mode.py`

- [ ] **Step 1: Write the failing test**

```python
def test_code_workspace_session_persists_root_path(tmp_path):
    ...
    session = await orchestrator.create_session(...)
    loaded = await orchestrator.load_session(session.id)
    assert loaded.config.workspace.root_path == str(tmp_path)
```

- [ ] **Step 2: Run the targeted test to verify it fails**

Run: `pytest tests/test_workspace_mode.py::test_code_workspace_session_persists_root_path -q`

Expected: fail because `code_workspace` and `WorkspaceConfig` do not exist yet.

- [ ] **Step 3: Implement the minimal model and persistence changes**

Add:
- `CollaborationMode.CODE_WORKSPACE = "code_workspace"`
- `WorkspaceConfig` dataclass
- `SessionConfig.workspace: Optional[WorkspaceConfig]`
- session config JSON serialization/deserialization for workspace fields

- [ ] **Step 4: Run the targeted test again**

Run: `pytest tests/test_workspace_mode.py::test_code_workspace_session_persists_root_path -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/enums.py backend/models.py backend/orchestrator.py tests/test_workspace_mode.py
git commit -m "feat: persist code workspace session config"
```

### Task 2: Add workspace API payloads

**Files:**
- Modify: `backend/api.py`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/types.ts`
- Test: `tests/test_workspace_mode.py`

- [ ] **Step 1: Write the failing test**

```python
def test_workspace_payload_round_trip_accepts_selected_paths(tmp_path):
    ...
    response = client.post("/api/sessions", json={...})
    assert response.status_code == 200
```

- [ ] **Step 2: Run the targeted test to verify it fails**

Run: `pytest tests/test_workspace_mode.py -q`

Expected: fail because `workspace` is not accepted or returned yet.

- [ ] **Step 3: Implement the minimal API payload support**

Add a `workspace` object to session create/update payloads and return workspace metadata in session responses.

- [ ] **Step 4: Run the targeted test again**

Run: `pytest tests/test_workspace_mode.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/api.py frontend/src/api.ts frontend/src/types.ts tests/test_workspace_mode.py
git commit -m "feat: expose workspace payloads"
```

## Chunk 2: Workspace Scanning and `@alias` Routing

### Task 3: Build repository scanning helper

**Files:**
- Create: `backend/workspace_scanner.py`
- Test: `tests/test_workspace_scanner.py`

- [ ] **Step 1: Write the failing test**

```python
def test_scan_workspace_skips_noise_dirs(tmp_path):
    ...
    tree = scan_workspace(str(tmp_path))
    assert ".git" not in tree
    assert "node_modules" not in tree
```

- [ ] **Step 2: Run the targeted test to verify it fails**

Run: `pytest tests/test_workspace_scanner.py -q`

Expected: fail because scanner does not exist yet.

- [ ] **Step 3: Implement the minimal scanner**

Traverse the local repository, skip common noise directories, and return a compact tree/summary structure.

- [ ] **Step 4: Run the targeted test again**

Run: `pytest tests/test_workspace_scanner.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/workspace_scanner.py tests/test_workspace_scanner.py
git commit -m "feat: scan local workspace repositories"
```

### Task 4: Parse `@alias` and resolve workspace targets

**Files:**
- Create: `backend/workspace_router.py`
- Test: `tests/test_workspace_router.py`

- [ ] **Step 1: Write the failing test**

```python
def test_extract_mentions_matches_aliases():
    assert extract_workspace_mentions("@claude 先看方案，再让 @codex 写代码") == ["claude", "codex"]
```

- [ ] **Step 2: Run the targeted test to verify it fails**

Run: `pytest tests/test_workspace_router.py -q`

Expected: fail because router helper does not exist yet.

- [ ] **Step 3: Implement the minimal parser**

Support `@alias`, multiple mentions, and `@all`. Default to all active workspace participants when no mentions are present.

- [ ] **Step 4: Run the targeted test again**

Run: `pytest tests/test_workspace_router.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/workspace_router.py tests/test_workspace_router.py
git commit -m "feat: route workspace tasks by alias"
```

### Task 5: Branch orchestrator dispatch for `code_workspace`

**Files:**
- Modify: `backend/strategies.py`
- Modify: `backend/orchestrator.py`
- Modify: `backend/api.py`
- Test: `tests/test_workspace_mode.py`

- [ ] **Step 1: Write the failing test**

```python
def test_workspace_dispatch_streams_all_targeted_participants(monkeypatch):
    ...
    events = collect_stream_events(...)
    assert [e.event for e in events if e.event == "chunk"]
```

- [ ] **Step 2: Run the targeted test to verify it fails**

Run: `pytest tests/test_workspace_mode.py::test_workspace_dispatch_streams_all_targeted_participants -q`

Expected: fail because `code_workspace` dispatch path is missing.

- [ ] **Step 3: Implement the minimal dispatch branch**

Use the scanner/router/context helpers to build shared context and stream one or more targeted participants in order.

- [ ] **Step 4: Run the targeted test again**

Run: `pytest tests/test_workspace_mode.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/strategies.py backend/orchestrator.py backend/api.py tests/test_workspace_mode.py
git commit -m "feat: dispatch code workspace sessions"
```

## Chunk 3: Frontend Workspace UI

### Task 6: Add the workspace mode to the UI and payloads

**Files:**
- Modify: `frontend/src/modeOptions.ts`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.tsx`
- Create: `frontend/src/WorkspaceMode.tsx`
- Test: `frontend/src/App.workspaceMode.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
test("shows code workspace layout and alias-aware composer", async () => {
  ...
  expect(screen.getByText("代码工作区")).toBeInTheDocument();
  expect(screen.getByPlaceholderText(/@alias/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the targeted test to verify it fails**

Run: `cd frontend && npm test -- --watchAll=false --runInBand App.workspaceMode.test.tsx`

Expected: fail because the mode and UI do not exist yet.

- [ ] **Step 3: Implement the minimal UI**

Add `code_workspace` to mode options, render a workspace-specific layout, and make the composer hint or autocomplete `@alias` targets.

- [ ] **Step 4: Run the targeted test again**

Run: `cd frontend && npm test -- --watchAll=false --runInBand App.workspaceMode.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/modeOptions.ts frontend/src/types.ts frontend/src/api.ts frontend/src/App.tsx frontend/src/WorkspaceMode.tsx frontend/src/App.workspaceMode.test.tsx
git commit -m "feat: add code workspace ui"
```

## Chunk 4: Verification

### Task 7: Run full regression

**Files:**
- None

- [ ] **Step 1: Run backend tests**

Run: `pytest tests/test_workspace_mode.py tests/test_workspace_scanner.py tests/test_workspace_router.py -q`

Expected: all pass.

- [ ] **Step 2: Run frontend tests**

Run: `cd frontend && npm test -- --watchAll=false --runInBand`

Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add backend frontend tests docs/superpowers/plans/2026-04-19-code-workspace-implementation.md
git commit -m "feat: implement code workspace mode"
```
