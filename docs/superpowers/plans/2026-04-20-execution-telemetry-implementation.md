# Execution Telemetry and Inline Progress UI Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make execution progress visible and trustworthy by upgrading SSE events into atomic runtime telemetry and rendering the same events both in the chat transcript and the right-side execution panel.

**Architecture:** Extend the backend stream model with real runtime phase events for session loading, workspace scanning, prompt building, model invocation, tool handling, and persistence. On the frontend, normalize those events into a single execution event store, project them into inline execution cards inside the main transcript, and reuse the same event objects in the right-side execution timeline.

**Tech Stack:** Python 3, FastAPI, aiosqlite, pytest, React 18, TypeScript, EventSource/SSE

---

## File Structure

- Modify: `backend/orchestrator.py`
- Modify: `backend/workspace_agent.py`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/sessionStream.ts`
- Modify: `frontend/src/ExecutionProgress.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Test: `tests/test_workspace_agent.py`
- Test: `tests/test_provider_runtime.py`
- Test: `frontend/src/sessionStream.test.ts`
- Test: `frontend/src/App.workspaceMode.test.tsx`
- Test: `frontend/src/App.streaming.test.tsx`
- Test: `frontend/src/api.test.ts`

## Chunk 1: Backend Atomic Runtime Events

### Task 1: Add phase and state events to normal session turns

**Files:**
- Modify: `backend/orchestrator.py`
- Test: `tests/test_provider_runtime.py`

- [ ] **Step 1: Write the failing test**

```python
def test_dispatch_round_emits_phase_events_for_normal_turn(...):
    events = [chunk async for chunk in orchestrator.dispatch_round(session.id)]
    assert any(chunk.event == "phase_start" for chunk in events)
    assert any(chunk.event == "model_request" for chunk in events)
    assert any(chunk.event == "state_write" for chunk in events)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_provider_runtime.py -q`

Expected: FAIL because `phase_start`, `model_request`, and `state_write` are not emitted yet.

- [ ] **Step 3: Implement minimal backend event emission**

Add real events around:
- session/runtime loading
- prompt construction
- model request start
- first response/response completion marker
- message persistence
- turn finalization

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_provider_runtime.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/orchestrator.py tests/test_provider_runtime.py
git commit -m "feat: emit atomic execution events for normal turns"
```

### Task 2: Add phase events to `code_workspace` and agent loop

**Files:**
- Modify: `backend/orchestrator.py`
- Modify: `backend/workspace_agent.py`
- Test: `tests/test_workspace_agent.py`

- [ ] **Step 1: Write the failing test**

```python
def test_workspace_agent_emits_phase_events(...):
    events = [chunk async for chunk in orchestrator.dispatch_round(session.id)]
    assert any(chunk.event == "phase_start" and chunk.metadata.get("phase") == "scan_workspace" for chunk in events)
    assert any(chunk.event == "phase_start" and chunk.metadata.get("phase") == "call_tool" for chunk in events)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_workspace_agent.py -q`

Expected: FAIL because these phase events are not emitted yet.

- [ ] **Step 3: Implement minimal workspace and agent telemetry**

Add phase events around:
- workspace scan
- target resolution
- prompt build
- model invocation
- directive parse
- tool call
- tool result persistence
- turn finalize

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_workspace_agent.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/orchestrator.py backend/workspace_agent.py tests/test_workspace_agent.py
git commit -m "feat: emit workspace and agent phase events"
```

## Chunk 2: Frontend Event Model

### Task 3: Extend stream/event typings and transport handling

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Test: `frontend/src/api.test.ts`

- [ ] **Step 1: Write the failing test**

```tsx
test("forwards phase and state events", () => {
  source.emit("phase_start", { phase: "build_prompt" });
  source.emit("state_write", { summary: "saved" });
  expect(events.map((e) => e.event)).toContain("phase_start");
  expect(events.map((e) => e.event)).toContain("state_write");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- --watchAll=false --runInBand src/api.test.ts`

Expected: FAIL because the new events are not bound.

- [ ] **Step 3: Implement minimal event typing and binding**

Add:
- `phase_start`
- `phase_end`
- `reasoning_note`
- `model_request`
- `model_response`
- `state_write`

to stream types and EventSource listeners.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- --watchAll=false --runInBand src/api.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/api.ts frontend/src/api.test.ts
git commit -m "feat: add execution telemetry stream types"
```

### Task 4: Normalize new events into a single execution event store

**Files:**
- Modify: `frontend/src/sessionStream.ts`
- Test: `frontend/src/sessionStream.test.ts`

- [ ] **Step 1: Write the failing test**

```tsx
test("maps phase and state events into execution events", () => {
  state = applyStreamEvent(state, "phase_start", { phase: "build_prompt", round: 1 });
  state = applyStreamEvent(state, "state_write", { summary: "message stored", round: 1 });
  expect(state.executionEvents).toHaveLength(2);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- --watchAll=false --runInBand src/sessionStream.test.ts`

Expected: FAIL because those events are ignored today.

- [ ] **Step 3: Implement minimal reducer support**

Add:
- event-to-summary mapping
- `kind`
- `phase`
- `metadata`
- consistent status mapping

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- --watchAll=false --runInBand src/sessionStream.test.ts`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/sessionStream.ts frontend/src/sessionStream.test.ts
git commit -m "feat: normalize execution telemetry events"
```

## Chunk 3: Inline Execution Cards in Chat

### Task 5: Render execution cards inside the main transcript

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/App.streaming.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
test("shows execution cards inline in the chat transcript", async () => {
  streamCallback?.("phase_start", { phase: "scan_workspace", round: 1, summary: "正在扫描工作区" });
  expect(screen.getByText("正在扫描工作区")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- --watchAll=false --runInBand src/App.streaming.test.tsx`

Expected: FAIL because the chat transcript only renders user/model/system bubbles.

- [ ] **Step 3: Implement minimal inline execution message rendering**

Add:
- `execution` message type
- execution card component styling
- folding of high-value execution events into transcript order
- no duplication for plain `chunk` text

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- --watchAll=false --runInBand src/App.streaming.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/styles.css frontend/src/App.streaming.test.tsx
git commit -m "feat: render inline execution cards in chat"
```

## Chunk 4: Better Execution Timeline

### Task 6: Upgrade the right-side execution panel to show richer event details

**Files:**
- Modify: `frontend/src/ExecutionProgress.tsx`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/App.workspaceMode.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
test("execution panel shows phase labels and detail payloads", async () => {
  streamCallback?.("phase_start", { phase: "build_prompt", round: 1, summary: "构建上下文" });
  expect(screen.getByText("构建上下文")).toBeInTheDocument();
  expect(screen.getByText(/build_prompt/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd frontend && npm test -- --watchAll=false --runInBand src/App.workspaceMode.test.tsx`

Expected: FAIL because the timeline only shows summary and round.

- [ ] **Step 3: Implement minimal richer timeline**

Add:
- phase badge
- event kind badge
- improved detail rendering
- status icon refinement

- [ ] **Step 4: Run test to verify it passes**

Run: `cd frontend && npm test -- --watchAll=false --runInBand src/App.workspaceMode.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/ExecutionProgress.tsx frontend/src/styles.css frontend/src/App.workspaceMode.test.tsx
git commit -m "feat: enrich execution timeline details"
```

## Chunk 5: Regression

### Task 7: Run backend and frontend regression

**Files:**
- None

- [ ] **Step 1: Run backend tests**

Run: `python -m pytest tests/test_provider_runtime.py tests/test_workspace_agent.py -q`

Expected: PASS.

- [ ] **Step 2: Run frontend targeted tests**

Run: `cd frontend && npm test -- --watchAll=false --runInBand src/api.test.ts src/sessionStream.test.ts src/App.streaming.test.tsx src/App.workspaceMode.test.tsx`

Expected: PASS.

- [ ] **Step 3: Run frontend build**

Run: `cd frontend && npm run build`

Expected: Compiled successfully.

- [ ] **Step 4: Commit**

```bash
git add backend frontend tests docs/superpowers/specs/2026-04-20-execution-telemetry-design.md docs/superpowers/plans/2026-04-20-execution-telemetry-implementation.md
git commit -m "feat: improve execution telemetry visibility"
```
