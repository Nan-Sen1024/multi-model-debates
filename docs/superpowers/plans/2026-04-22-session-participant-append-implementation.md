# Existing Session Participant Append Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow users to append a newly authenticated model participant to an existing active session from the session detail page.

**Architecture:** Add an append-only backend endpoint that inserts one validated participant into the existing session, guarded against mid-stream mutation. On the frontend, add a single-participant form in session detail that reuses the existing provider/model catalog dropdowns and refreshes the current session after success.

**Tech Stack:** Python 3, FastAPI, aiosqlite, pytest, React 18, TypeScript, Jest

---

## File Structure

- Modify: `backend/api.py`
- Modify: `backend/orchestrator.py`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Test: `tests/test_session_participant_append.py`
- Test: `frontend/src/App.sessionManagement.test.tsx`

## Chunk 1: Backend Append API

### Task 1: Add the failing backend tests

**Files:**
- Create: `tests/test_session_participant_append.py`

- [ ] **Step 1: Write the failing test**

```python
def test_append_participant_to_active_session(...):
    updated = run(orchestrator.append_participant(session.id, ParticipantInput(...)))
    assert [p.custom_id for p in updated.participants] == ["A", "B", "C"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_session_participant_append.py -q`
Expected: FAIL because append support does not exist yet.

- [ ] **Step 3: Add more failing coverage**

Cover:
- duplicate `custom_id`
- append while session runtime is generating
- append in `code_workspace`

- [ ] **Step 4: Run test to verify it still fails for the expected reasons**

Run: `python -m pytest tests/test_session_participant_append.py -q`
Expected: FAIL with missing method / missing API behavior.

### Task 2: Implement backend session append support

**Files:**
- Modify: `backend/orchestrator.py`
- Modify: `backend/api.py`
- Test: `tests/test_session_participant_append.py`

- [ ] **Step 1: Add API payload model and route**

Add a participant append payload in `backend/api.py` and expose:
- `POST /api/sessions/{session_id}/participants`

- [ ] **Step 2: Implement `SessionOrchestrator.append_participant()`**

Behavior:
- acquire session lock
- load current session
- reject non-active session
- reject if runtime `is_generating`
- validate provider binding and custom id uniqueness
- assign `sequence_order = max + 1`
- insert one row into `model_participants`
- return reloaded session

- [ ] **Step 3: Run backend tests**

Run: `python -m pytest tests/test_session_participant_append.py -q`
Expected: PASS.

## Chunk 2: Frontend Session Detail Append UI

### Task 3: Add the failing frontend test

**Files:**
- Modify: `frontend/src/App.sessionManagement.test.tsx`

- [ ] **Step 1: Write the failing UI test**

Test:
- load an existing session
- open “add participant” UI
- choose provider/model
- submit
- assert the refreshed session includes the new participant alias

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- --runInBand App.sessionManagement.test.tsx`
Expected: FAIL because no append UI or API exists yet.

### Task 4: Implement frontend API and UI

**Files:**
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`
- Test: `frontend/src/App.sessionManagement.test.tsx`

- [ ] **Step 1: Add frontend API helper**

Add:
- `appendSessionParticipant(sessionId, payload)`

- [ ] **Step 2: Add session detail draft state**

Reuse existing provider/model selection helpers:
- `buildParticipantModelGroups`
- `ModelRefSelect`
- `formatParticipantModelSelection`
- `parseParticipantModelSelection`

- [ ] **Step 3: Render append form in session detail**

Requirements:
- hide when no session
- disable when stream is active
- submit one participant only
- refresh current session from API response

- [ ] **Step 4: Run frontend test**

Run: `npm test -- --runInBand App.sessionManagement.test.tsx`
Expected: PASS.

## Chunk 3: Verification

### Task 5: Run focused regressions

**Files:**
- Test only

- [ ] **Step 1: Run backend regression set**

Run: `python -m pytest tests/test_session_participant_append.py tests/test_provider_runtime.py tests/test_workspace_dispatch_failure.py -q`
Expected: PASS.

- [ ] **Step 2: Run frontend regression set**

Run: `npm test -- --runInBand App.sessionManagement.test.tsx App.streaming.test.tsx api.test.ts`
Expected: PASS.

- [ ] **Step 3: Run frontend build**

Run: `npm run build`
Expected: Compiled successfully.
