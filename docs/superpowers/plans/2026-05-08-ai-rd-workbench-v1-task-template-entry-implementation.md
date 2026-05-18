# AI R&D Workbench V1 Task Template Entry Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the raw multi-mode create screen with a task-template entry that highlights repo analysis, fixing, review, and comparison while keeping backend `mode` compatibility.

**Architecture:** Keep the existing `mode` enum and session creation API. Add a frontend-only task template layer that maps a smaller set of primary templates onto existing modes, uses `code_workspace` as the default workbench path, and moves the raw mode list into an explicitly secondary experimental area.

**Tech Stack:** React 18, TypeScript, Jest DOM tests, existing FastAPI session API.

---

## Scope Notes

- Current worktree is already dirty, including overlapping frontend files:
  - `frontend/src/App.tsx`
  - `frontend/src/styles.css`
  - `frontend/src/App.workspaceMode.test.tsx`
- Do not remove the old mode enum or backend behavior.
- This slice only covers the create-task experience and its tests.

## Chunk 1: Task template model and create-page defaults

**Files:**
- Modify: `frontend/src/modeOptions.ts`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/App.workspaceMode.test.tsx`

- [ ] **Step 1: Write the failing test**

Add or update a test that expects:
- the create page to foreground a small set of primary task templates
- the default create path to start in a repo-oriented template
- the workspace configuration panel to appear for the default repo-oriented path

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -- App.workspaceMode.test.tsx --runInBand`
Expected: FAIL because the UI still renders the full raw mode grid as the primary control.

- [ ] **Step 3: Write minimal implementation**

Add a task-template layer with primary templates such as:
- analyze repo
- fix or implement
- review changes
- compare approaches

Map templates to existing modes, with the default template pointing at `code_workspace`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm test -- App.workspaceMode.test.tsx --runInBand`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/modeOptions.ts frontend/src/App.tsx frontend/src/App.workspaceMode.test.tsx
git commit -m "feat: add primary task templates for task creation"
```

## Chunk 2: Experimental mode downranking

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Modify: `frontend/src/App.workspaceCapabilities.test.tsx`
- Modify: `frontend/src/App.modelDropdown.test.tsx`

- [ ] **Step 1: Write the failing test**

Update tests so they expect:
- raw modes to live behind an experimental or advanced section
- advanced users to still be able to choose low-level modes
- existing code-workspace flows to stay reachable through the new primary templates

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -- App.workspaceCapabilities.test.tsx App.modelDropdown.test.tsx --runInBand`
Expected: FAIL because the current UI still exposes raw modes directly and without hierarchy.

- [ ] **Step 3: Write minimal implementation**

Update the create page so:
- primary task templates render first
- raw modes render in a secondary expandable area labeled as experimental or low-level
- selected state remains visually clear across both layers

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm test -- App.workspaceCapabilities.test.tsx App.modelDropdown.test.tsx --runInBand`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/styles.css frontend/src/App.workspaceCapabilities.test.tsx frontend/src/App.modelDropdown.test.tsx
git commit -m "feat: downrank raw modes behind experimental templates"
```

## Chunk 3: Verification

**Files:**
- Modify: tests only if assertions need narrowing

- [ ] **Step 1: Run focused create-flow regression tests**

Run: `npm test -- App.workspaceMode.test.tsx App.workspaceCapabilities.test.tsx App.modelDropdown.test.tsx --runInBand`
Expected: PASS

- [ ] **Step 2: Run full frontend suite**

Run: `npm test -- --runInBand`
Expected: PASS

- [ ] **Step 3: Manual smoke check**

Verify in the browser:
- create page opens on a primary repo task template
- workspace panel is immediately visible for the default repo path
- experimental modes are collapsed or visually secondary

- [ ] **Step 4: Record verification evidence**

Capture exact commands, exit codes, and any residual UI gaps.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-05-08-ai-rd-workbench-v1-task-template-entry-implementation.md
git commit -m "docs: record task template entry implementation plan"
```
