# Existing Session Batch Participant Append Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow users to append multiple authenticated model participants to an existing active session in one submission.

**Architecture:** Keep the existing single-append API for compatibility, add a batch append API that validates and inserts all participants atomically, and switch the session detail UI to a multi-row draft editor that submits through the batch API.

**Tech Stack:** Python 3, FastAPI, aiosqlite, pytest, React 18, TypeScript, Jest

---

## File Structure

- Modify: `backend/api.py`
- Modify: `backend/orchestrator.py`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.tsx`
- Test: `tests/test_session_participant_append.py`
- Test: `frontend/src/App.sessionManagement.test.tsx`

## Chunk 1: Backend Batch Append API

### Task 1: Add failing backend tests for batch append

**Files:**
- Modify: `tests/test_session_participant_append.py`

- [ ] **Step 1: Write a failing success test**

Add a test for:
- `POST /api/sessions/{session_id}/participants/batch`
- request body with two participants
- response contains both newly appended aliases in order

- [ ] **Step 2: Run backend test to verify it fails**

Run: `python -m pytest tests/test_session_participant_append.py -q`
Expected: FAIL because the batch route does not exist yet.

- [ ] **Step 3: Add failing atomicity coverage**

Add a test where:
- first participant is valid
- second participant duplicates an existing alias
- response is 400
- session participant list remains unchanged

- [ ] **Step 4: Run backend test to verify failure reason is correct**

Run: `python -m pytest tests/test_session_participant_append.py -q`
Expected: FAIL due to missing batch API / handler.

### Task 2: Implement backend batch append support

**Files:**
- Modify: `backend/api.py`
- Modify: `backend/orchestrator.py`
- Test: `tests/test_session_participant_append.py`

- [ ] **Step 1: Add batch payload model and route**

Add:
- request model with `participants: list[ParticipantPayload]`
- `POST /api/sessions/{session_id}/participants/batch`

- [ ] **Step 2: Implement orchestrator batch append**

Add an internal batch path that:
- acquires the same session lock
- validates session status/runtime once
- validates the whole batch against existing participants and itself
- enforces max participant count
- inserts all rows inside one transaction
- returns reloaded session

- [ ] **Step 3: Reuse shared validation/write helpers**

Refactor single-append and batch-append to share:
- custom id resolution
- provider binding validation
- sequence order assignment

- [ ] **Step 4: Run backend tests**

Run: `python -m pytest tests/test_session_participant_append.py -q`
Expected: PASS.

## Chunk 2: Frontend Multi-Row Append UI

### Task 3: Add failing frontend test for batch append

**Files:**
- Modify: `frontend/src/App.sessionManagement.test.tsx`

- [ ] **Step 1: Extend the session detail test**

Write a failing test that:
- opens the add participant panel
- adds a second draft row
- fills two rows
- submits once
- asserts both aliases appear in the participant chips

- [ ] **Step 2: Run frontend test to verify it fails**

Run: `powershell -Command "$env:CI='true'; npm test -- --runInBand App.sessionManagement.test.tsx"`
Expected: FAIL because the current UI only supports one draft and calls the single-append API.

### Task 4: Implement frontend batch append

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/App.sessionManagement.test.tsx`

- [ ] **Step 1: Add frontend batch API helper**

Add:
- `appendSessionParticipants(sessionId, participants)`

- [ ] **Step 2: Replace single draft state with draft list state**

In session detail enhancement:
- keep `ParticipantConfig[]`
- support add/remove draft rows
- reuse provider/model dropdown helpers per row

- [ ] **Step 3: Submit drafts through the batch endpoint**

Requirements:
- single submit for all draft rows
- disable while streaming
- require each row to have `model_ref`
- clear drafts on success and keep one empty row ready for next use

- [ ] **Step 4: Run frontend test**

Run: `powershell -Command "$env:CI='true'; npm test -- --runInBand App.sessionManagement.test.tsx"`
Expected: PASS.

## Chunk 3: Verification

### Task 5: Run focused regressions

**Files:**
- Test only

- [ ] **Step 1: Run backend regression set**

Run: `python -m pytest tests/test_session_participant_append.py tests/test_provider_runtime.py tests/test_workspace_dispatch_failure.py -q`
Expected: PASS.

- [ ] **Step 2: Run frontend regression set**

Run: `powershell -Command "$env:CI='true'; npm test -- --runInBand App.sessionManagement.test.tsx App.streaming.test.tsx api.test.ts"`
Expected: PASS.

- [ ] **Step 3: Run frontend build**

Run: `npm run build`
Expected: Compiled successfully.
