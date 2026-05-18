# AI R&D Workbench V1 Run Result Sidebar Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the run sidebar from a generic status area into a structured result surface that highlights files changed, commands run, verification state, and blockers.

**Architecture:** Reuse the existing `executionEvents` stream data already accumulated in the frontend. Add a lightweight result summarizer in the frontend that derives structured sidebar sections from tool calls, tool outputs, warnings, and errors, without requiring backend API changes.

**Tech Stack:** React 18, TypeScript, Jest DOM tests, existing session stream reducer.

---

## Chunk 1: Result summary tests

**Files:**
- Modify: `frontend/src/App.workspaceMode.test.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Write the failing test**

Add a test that streams representative tool events and expects the sidebar to show:
- files touched or inspected
- commands executed
- validation state
- blockers or warnings

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -- App.workspaceMode.test.tsx --runInBand`
Expected: FAIL because the sidebar currently only shows coarse cards and does not summarize execution artifacts.

- [ ] **Step 3: Write minimal implementation**

Add a frontend-only summarizer that:
- extracts file paths from tool metadata and payloads
- extracts command lines and exit info from tool results
- classifies validation state from command content and error status
- exposes warnings or participant errors as blockers

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm test -- App.workspaceMode.test.tsx --runInBand`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/App.workspaceMode.test.tsx
git commit -m "feat: summarize run artifacts in task sidebar"
```

## Chunk 2: Layout polish and verification

**Files:**
- Modify: `frontend/src/styles.css`

- [ ] **Step 1: Polish sidebar layout**

Ensure summary lists remain readable on desktop and mobile and do not collapse the workspace panel.

- [ ] **Step 2: Run focused tests**

Run: `npm test -- App.workspaceMode.test.tsx --runInBand`
Expected: PASS

- [ ] **Step 3: Run full frontend suite**

Run: `npm test -- --runInBand`
Expected: PASS

- [ ] **Step 4: Manual smoke check**

Verify the run detail sidebar renders result sections in the browser and remains readable.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/styles.css docs/superpowers/plans/2026-05-08-ai-rd-workbench-v1-run-result-sidebar-implementation.md
git commit -m "docs: record run result sidebar plan"
```
