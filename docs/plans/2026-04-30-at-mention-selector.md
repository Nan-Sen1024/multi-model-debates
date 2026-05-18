# @ Mention Selector Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add WeChat-style `@` participant autocomplete to the session composer.

**Architecture:** Implement as a local React interaction inside `TabSessionDetail`, backed by `session.participants`. The textarea remains the source of truth; selecting a mention rewrites the active token and keeps backend-compatible plain text routing.

**Tech Stack:** React 18, TypeScript, Jest/react-dom test-utils, existing CSS.

---

### Task 1: Add Mention Parsing And Composer State

**Files:**
- Modify: `frontend/src/App.tsx`

**Steps:**
1. Add a `MentionCandidate` type and helper to find the active `@query` range before the caret.
2. Add refs/state in `TabSessionDetail` for textarea node, active mention range, and highlighted index.
3. Build filtered candidates from `session.participants`, matching `custom_id`, `model_ref`, and `role_desc`.

### Task 2: Add Keyboard And Mouse Selection

**Files:**
- Modify: `frontend/src/App.tsx`

**Steps:**
1. Replace the composer textarea handlers with `handleComposerChange`, `handleComposerKeyDown`, and `insertMention`.
2. On ArrowUp/ArrowDown, move highlight without moving the caret.
3. On Enter/Tab, insert the highlighted candidate if the picker is open.
4. On Escape, close the picker.

### Task 3: Render And Style Picker

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`

**Steps:**
1. Render a positioned picker above the textarea inside a `.composer-field`.
2. Show alias, model ref, and role description.
3. Add CSS for `.mention-picker`, active option, and empty state.

### Task 4: Add Regression Test

**Files:**
- Modify: `frontend/src/App.workspaceMode.test.tsx`

**Steps:**
1. Load the existing workspace session fixture.
2. Type `@co` into the composer.
3. Assert the picker shows `@codex` and not unrelated filtered-only entries.
4. Press Enter and assert the textarea value becomes `@codex `.
5. Run `npm test -- --watchAll=false src/App.workspaceMode.test.tsx`.

### Task 5: Verify

Run:

```bash
npm test -- --watchAll=false src/App.workspaceMode.test.tsx
npm test -- --watchAll=false src/App.workspaceMode.test.tsx src/App.streaming.test.tsx
```
