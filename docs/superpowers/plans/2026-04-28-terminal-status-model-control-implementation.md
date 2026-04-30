# Terminal Status And Model Control Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a terminal-visible status surface and explicit model-control commands so `mmd` feels closer to Codex / Hermes / OpenClaw without changing the existing multi-participant session model.

**Architecture:** Keep `provider/model` as the canonical backend model reference. Add session-level default model persistence on the backend, then expose it through a compact `status` view and `model` commands in the terminal shell. Preserve `@alias` and `/to` for routing focus, and do not introduce participant model mutation in v1.

**Tech Stack:** Python 3.12, FastAPI, SQLite, httpx, pytest, existing `mmd` terminal client.

---

## Chunk 1: Backend Session Default Model And Status Data

### Task 1: Persist a session default model in the backend

**Files:**
- Modify: `backend/models.py`
- Modify: `backend/orchestrator.py`
- Modify: `backend/api.py`
- Test: `tests/test_session_model_control.py`

- [ ] **Step 1: Write the failing test**

```python
def test_session_config_can_persist_default_model_ref():
    ...
```

Run: `pytest tests/test_session_model_control.py -q`
Expected: FAIL because `default_model_ref` is not yet stored or returned.

- [ ] **Step 2: Write minimal implementation**

Add `default_model_ref: Optional[str] = None` to `SessionConfig`, serialize it in `_serialize_session_config`, deserialize it in session loading, and expose it in the session payload returned by the API.

- [ ] **Step 3: Add a session config update endpoint**

Add `PATCH /api/sessions/{session_id}/config` that accepts `default_model_ref` and updates only session-level config.

- [ ] **Step 4: Run the focused test**

Run: `pytest tests/test_session_model_control.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/models.py backend/orchestrator.py backend/api.py tests/test_session_model_control.py
git commit -m "feat: persist session default model"
```

### Task 2: Expose compact status data through existing session and provider endpoints

**Files:**
- Modify: `backend/api.py`
- Modify: `backend/orchestrator.py`
- Test: `tests/test_session_model_control.py`

- [ ] **Step 1: Write the failing test**

```python
def test_session_status_payload_includes_default_model_and_workspace_summary():
    ...
```

Run: `pytest tests/test_session_model_control.py -q`
Expected: FAIL because the payload does not yet include everything needed for the CLI status view.

- [ ] **Step 2: Write minimal implementation**

Extend the existing session payload to include the session default model and enough workspace/provider metadata for the CLI to render a compact status block. Keep the response shape flat and readable.

- [ ] **Step 3: Run the focused test**

Run: `pytest tests/test_session_model_control.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add backend/api.py backend/orchestrator.py tests/test_session_model_control.py
git commit -m "feat: expose session status data"
```

## Chunk 2: CLI Status And Model Commands

### Task 3: Add `mmd status` and `mmd models status/list/set`

**Files:**
- Modify: `mmd/__main__.py`
- Modify: `mmd/client.py`
- Modify: `mmd/catalog.py` if helper logic is needed
- Test: `tests/test_mmd_status_model_control.py`

- [ ] **Step 1: Write the failing test**

```python
def test_models_set_updates_default_model():
    ...

def test_status_renders_session_model_provider_and_focus():
    ...
```

Run: `pytest tests/test_mmd_status_model_control.py -q`
Expected: FAIL because the CLI does not yet implement these commands.

- [ ] **Step 2: Write minimal implementation**

Add client methods for reading/writing session config, then implement:

- `mmd status`
- `mmd models status`
- `mmd models list`
- `mmd models set <provider/model>`

Keep the output compact and terminal-friendly.

- [ ] **Step 3: Run the focused test**

Run: `pytest tests/test_mmd_status_model_control.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add mmd/__main__.py mmd/client.py mmd/catalog.py tests/test_mmd_status_model_control.py
git commit -m "feat: add terminal status and model commands"
```

### Task 4: Add shell `/status`, `/model`, and prompt status rendering

**Files:**
- Modify: `mmd/shell.py`
- Modify: `mmd/__main__.py`
- Test: `tests/test_mmd_shell.py`

- [ ] **Step 1: Write the failing test**

```python
def test_shell_status_and_model_commands_render_current_state():
    ...
```

Run: `pytest tests/test_mmd_shell.py -q`
Expected: FAIL because `/status` and `/model` are not yet implemented.

- [ ] **Step 2: Write minimal implementation**

Add shell commands for:

- `/status`
- `/model`
- `/model set <provider/model>`
- `/model clear`

Also update the prompt so it can show focus and current default model together, similar to a compact Codex/Hermes status line.

- [ ] **Step 3: Run the focused test**

Run: `pytest tests/test_mmd_shell.py -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add mmd/shell.py mmd/__main__.py tests/test_mmd_shell.py
git commit -m "feat: expose terminal status and model controls"
```

## Chunk 3: Documentation And Verification

### Task 5: Update terminal docs and verify the full surface

**Files:**
- Modify: `README.md`
- Modify: `mmd/__main__.py`
- Test: `tests/test_mmd_commands.py`
- Test: `tests/test_mmd_shell.py`
- Test: `tests/test_session_model_control.py`
- Test: `tests/test_mmd_status_model_control.py`

- [ ] **Step 1: Write the failing check**

Update docs and test help text so the new commands are visible in the terminal help and README.

- [ ] **Step 2: Run the focused regression set**

Run:
`pytest tests/test_session_model_control.py tests/test_mmd_status_model_control.py tests/test_mmd_commands.py tests/test_mmd_shell.py -q`

Expected: all green.

- [ ] **Step 3: Update README**

Document:

- `mmd status`
- `mmd models status/list/set`
- `/status`
- `/model`
- default model vs participant model

- [ ] **Step 4: Final verification**

Run:
`python -m mmd --help`
`pytest tests/test_session_model_control.py tests/test_mmd_status_model_control.py tests/test_mmd_commands.py tests/test_mmd_shell.py -q`

Expected: help shows the new commands and all tests pass.

- [ ] **Step 5: Commit**

```bash
git add README.md mmd/__main__.py
git commit -m "docs: describe terminal status and model controls"
```

