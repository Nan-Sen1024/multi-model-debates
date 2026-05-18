# AI R&D Workbench V1 UI Semantic Shift Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reframe the existing frontend from a session/chat console into a task/run-oriented R&D workbench without rewriting the backend session protocol.

**Architecture:** Keep the current `session` APIs, stream handling, and workspace execution mechanics intact. Shift the user-facing information architecture in the frontend so the primary path reads as `Workspace -> Task -> Run -> Result`, and reinterpret the existing session detail view as a run detail view with a task sidebar.

**Tech Stack:** React 18, TypeScript, Jest DOM tests, existing FastAPI session/workspace API.

---

## Scope Notes

- Current worktree is dirty and already contains overlapping edits in:
  - `frontend/src/App.tsx`
  - `frontend/src/App.workspaceMode.test.tsx`
  - `frontend/src/styles.css`
- Do not revert or replace the existing `@mention` workflow, workspace auto-start behavior, or current backend session semantics.
- This plan only covers the first vertical slice:
  - task-driven entry labels and copy
  - run-oriented detail framing
  - task sidebar replacing snapshot-first framing
  - focused frontend tests

## Chunk 1: Task-oriented entry and navigation

**Files:**
- Modify: `frontend/src/App.workspaceMode.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Write the failing test**

Add or update a frontend test that expects:
- the create tab label to use task language instead of session language
- the detail tab label to use run/task language instead of session detail language
- the quick-start section to describe the product as a workspace/task/run flow

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -- App.workspaceMode.test.tsx --runInBand`
Expected: FAIL because the current UI still uses `创建会话`, `会话详情`, and chat-console copy.

- [ ] **Step 3: Write minimal implementation**

Update `frontend/src/App.tsx` so:
- hero title/subtitle describe an AI R&D workbench instead of a debate console
- quick-start cards use `配置 Provider -> 新建任务 -> 运行任务`
- tab labels use task/run terminology
- create form headings, placeholders, and primary CTA read as task creation

Keep all backend payload names and handlers unchanged in this step.

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm test -- App.workspaceMode.test.tsx --runInBand`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/App.workspaceMode.test.tsx frontend/src/styles.css
git commit -m "feat: shift entry flow to task and run language"
```

## Chunk 2: Run detail framing and task sidebar

**Files:**
- Modify: `frontend/src/App.workspaceMode.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Write the failing test**

Add or update a frontend test that expects the detail view to show:
- run/task-oriented history and status labels
- a primary action phrased as running or continuing a task
- a right-side task sidebar with goal, execution state, workspace context, and validation framing

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -- App.workspaceMode.test.tsx --runInBand`
Expected: FAIL because the current detail view still exposes `会话`, `轮次`, `开始下一轮`, and `开发面板/快照面板`.

- [ ] **Step 3: Write minimal implementation**

Update `frontend/src/App.tsx` and `frontend/src/styles.css` so the existing detail screen is reinterpreted as:
- left: task/run history list
- center: run timeline / live output
- right: task sidebar

Specific UI changes:
- history strip labels use task/run wording
- status bar foregrounds task goal, run id, run state, and clock
- primary action becomes `运行任务` or `继续执行`
- right panel title becomes task-oriented
- snapshot editor becomes secondary under the sidebar instead of being the headline panel

Preserve:
- current stream wiring
- message rendering
- workspace session panel
- existing snapshot save mechanics

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm test -- App.workspaceMode.test.tsx --runInBand`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/App.workspaceMode.test.tsx frontend/src/styles.css
git commit -m "feat: reframe session detail as run detail"
```

## Chunk 3: Focused verification

**Files:**
- Modify: `frontend/src/App.workspaceMode.test.tsx` if assertions need tightening

- [ ] **Step 1: Run focused frontend suite**

Run: `npm test -- App.workspaceMode.test.tsx --runInBand`
Expected: PASS with all updated workspace-mode tests green.

- [ ] **Step 2: Run broader frontend confidence check**

Run: `npm test -- --runInBand`
Expected: PASS, or document any unrelated pre-existing failures with exact evidence.

- [ ] **Step 3: Manual smoke check with local dev server**

Run frontend and backend, then verify the main path in the browser:
- tab copy matches task/run framing
- workspace mode still scans and selects paths
- detail page still streams and shows the task sidebar

- [ ] **Step 4: Record verification evidence**

Capture:
- exact test commands
- exit codes
- any remaining gaps caused by environment or unrelated dirty changes

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/App.workspaceMode.test.tsx frontend/src/styles.css docs/superpowers/plans/2026-05-08-ai-rd-workbench-v1-ui-semantic-shift-implementation.md
git commit -m "docs: record ai rd workbench ui semantic shift plan"
```
