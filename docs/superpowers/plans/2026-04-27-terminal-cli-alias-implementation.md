# Terminal CLI And Alias Rules Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the terminal client enforce explicit alias resolution, reject unknown `@alias` mentions, and present the command surface in a way that matches the design approved for `mmd`.

**Architecture:** Keep the existing `mmd` REPL and backend workspace routing, but add a mention-resolution helper that returns both matched participants and unknown aliases. The shell should use that helper to hard-fail on unknown mentions, while keeping `@all` broadcast behavior and session-local aliases intact. Update the help text and tests to reflect the terminal-first command model.

**Tech Stack:** Python 3.12, pytest, existing `backend` models/router, existing `mmd` terminal client

---

## File Structure

- Modify: `backend/workspace_router.py`
- Modify: `mmd/shell.py`
- Modify: `tests/test_workspace_router.py`
- Modify: `tests/test_mmd_shell.py`
- Modify: `README.md`

### Task 1: Add explicit mention validation

**Files:**
- Modify: `backend/workspace_router.py`
- Modify: `tests/test_workspace_router.py`

- [ ] **Step 1: Add a failing test for mixed valid and unknown aliases**

Add a test that sends a message like `@coder @missing fix this` and asserts:
- `coder` is still resolved
- `missing` is reported as unknown
- `@all` still broadcasts to all active participants

- [ ] **Step 2: Run the new test and confirm it fails**

Run: `python -m pytest tests/test_workspace_router.py -q`
Expected: FAIL because the router currently only returns matched participants and drops unknown alias information.

- [ ] **Step 3: Implement a helper that returns matches and unknown aliases**

Add a helper that preserves the current `resolve_workspace_targets()` behavior but also returns unknown mentions for the shell to reject.

- [ ] **Step 4: Re-run the router tests**

Run: `python -m pytest tests/test_workspace_router.py -q`
Expected: PASS.

### Task 2: Make the shell reject unknown aliases

**Files:**
- Modify: `mmd/shell.py`
- Modify: `tests/test_mmd_shell.py`

- [ ] **Step 1: Add a failing shell regression test**

Add a test that sends `@coder @missing please fix` and asserts:
- the shell prints an unknown-alias error
- the message is not sent to the backend

- [ ] **Step 2: Run the new shell test and confirm it fails**

Run: `python -m pytest tests/test_mmd_shell.py -q`
Expected: FAIL because the shell currently accepts any message that resolves at least one alias.

- [ ] **Step 3: Wire the shell to the new mention-validation helper**

Reject the message if any mention is unknown, even when some mentions are valid.

- [ ] **Step 4: Re-run the shell tests**

Run: `python -m pytest tests/test_mmd_shell.py -q`
Expected: PASS.

### Task 3: Tighten command surface documentation

**Files:**
- Modify: `mmd/shell.py`
- Modify: `README.md`

- [ ] **Step 1: Update `/help` output**

Document the command set in the shell so users can see:
- `/who`
- `/add`
- `/setup`
- `/workspace`
- `/skills`
- `/mcp`
- `/agent`
- `/quit`
- `@all`

- [ ] **Step 2: Add a README terminal section**

Summarize:
- `mmd` as the terminal entry point
- bare model auto-binding when unique
- `@alias` routing rules
- the `@all` broadcast rule

- [ ] **Step 3: Run the focused test set**

Run: `python -m pytest tests/test_workspace_router.py tests/test_mmd_shell.py tests/test_mmd_commands.py tests/test_mmd_catalog.py -q`
Expected: PASS.

