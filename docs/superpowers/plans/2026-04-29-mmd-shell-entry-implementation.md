# `mmd shell` Entry Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an explicit `mmd shell` entry that launches the same repo-local terminal flow as `mmd` with no arguments.

**Architecture:** Keep the existing terminal launcher as the single source of truth. Add a thin CLI alias so `mmd shell` calls the same session resolution and shell startup path used by `mmd` without arguments. Update help text and docs so the entry is visible and discoverable.

**Tech Stack:** Python 3.12, argparse, pytest, existing `mmd` terminal client.

---

### Task 1: Add a regression test for the explicit shell entry

**Files:**
- Modify: `tests/test_mmd_launcher.py`

- [ ] **Step 1: Write the failing test**

```python
def test_cmd_shell_uses_default_terminal_flow(monkeypatch):
    ...
```

Run: `pytest tests/test_mmd_launcher.py -q`
Expected: FAIL because `cmd_shell` does not exist yet.

- [ ] **Step 2: Implement the test expectation**

Assert that `cmd_shell()` resolves the current workspace session and launches the shell runner with the same behavior as the default terminal path.

- [ ] **Step 3: Verify the test fails for the missing feature**

Run: `pytest tests/test_mmd_launcher.py -q`
Expected: FAIL.

### Task 2: Implement the `mmd shell` CLI subcommand

**Files:**
- Modify: `mmd/__main__.py`
- Modify: `mmd/launcher.py`

- [ ] **Step 1: Add a thin `cmd_shell()` wrapper**

Make the wrapper call the existing terminal launcher and accept the same workspace/session lookup inputs used by the default path.

- [ ] **Step 2: Register a `shell` subcommand**

Wire `mmd shell` into argparse and dispatch it in `main()` with the same runtime behavior as `mmd` with no arguments.

- [ ] **Step 3: Run the focused test**

Run: `pytest tests/test_mmd_launcher.py -q`
Expected: PASS.

### Task 3: Update help text and docs

**Files:**
- Modify: `mmd/__main__.py`
- Modify: `README.md`

- [ ] **Step 1: Add help text for `mmd shell`**

Make the quick-start epilog mention the explicit shell entry and keep the existing no-argument flow intact.

- [ ] **Step 2: Update README usage examples**

Document `mmd shell` next to `mmd` as the explicit terminal entry point.

- [ ] **Step 3: Run CLI verification**

Run:
`python -m mmd --help`
`pytest tests/test_mmd_launcher.py tests/test_mmd_shell.py tests/test_mmd_commands.py -q`

Expected: help shows `shell`, and the focused tests pass.

