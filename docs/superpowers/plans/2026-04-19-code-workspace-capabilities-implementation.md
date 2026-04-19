# Code Workspace Capabilities Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add configurable `skills`, `MCP`, and `agent` runtime support to `code_workspace` while preserving the existing session/SSE/provider architecture.

**Architecture:** Extend the existing workspace session config with a capability manifest, then add three focused runtime layers: skill discovery/loading, MCP server/tool access, and an agent loop that can plan, call tools, and continue generating. Keep normal chat/debate modes unchanged; only `code_workspace` uses the new capability stack.

**Tech Stack:** Python 3, FastAPI, aiosqlite, pytest, React, TypeScript, `litellm`, `httpx`, `mcp`

---

## File Structure

- Create: `backend/workspace_capabilities.py`
- Create: `backend/workspace_skills.py`
- Create: `backend/workspace_mcp.py`
- Create: `backend/workspace_agent.py`
- Modify: `backend/models.py`
- Modify: `backend/orchestrator.py`
- Modify: `backend/api.py`
- Modify: `backend/llm_gateway.py`
- Modify: `backend/message_store.py`
- Modify: `backend/strategies.py`
- Modify: `backend/workspace_context.py`
- Modify: `requirements.txt`
- Modify: `frontend/src/WorkspaceMode.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Test: `tests/test_workspace_capabilities.py`
- Test: `tests/test_workspace_skills.py`
- Test: `tests/test_workspace_mcp.py`
- Test: `tests/test_workspace_agent.py`
- Test: `frontend/src/App.workspaceCapabilities.test.tsx`

## Chunk 1: Capability Config and Persistence

### Task 1: Add workspace capability manifest to session config

**Files:**
- Create: `backend/workspace_capabilities.py`
- Modify: `backend/models.py`
- Modify: `backend/orchestrator.py`
- Modify: `backend/api.py`
- Test: `tests/test_workspace_capabilities.py`

- [ ] **Step 1: Write the failing test**

```python
def test_workspace_capability_manifest_round_trip(tmp_path):
    ...
    session = await orchestrator.create_session(...)
    loaded = await orchestrator.load_session(session.id)
    assert loaded.config.workspace.capabilities is not None
```

- [ ] **Step 2: Run the targeted test to verify it fails**

Run: `pytest tests/test_workspace_capabilities.py::test_workspace_capability_manifest_round_trip -q`

Expected: FAIL because capability manifest fields do not exist yet.

- [ ] **Step 3: Implement the minimal config and persistence changes**

Add:
- `SkillSourceConfig`
- `MCPServerConfig`
- `AgentProfileConfig`
- `ParticipantCapabilityConfig`
- `WorkspaceCapabilityManifest`
- JSON serialization/deserialization for workspace capability manifest
- API payload round-trip for workspace capability config

- [ ] **Step 4: Run the targeted test again**

Run: `pytest tests/test_workspace_capabilities.py::test_workspace_capability_manifest_round_trip -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/workspace_capabilities.py backend/models.py backend/orchestrator.py backend/api.py tests/test_workspace_capabilities.py
git commit -m "feat: persist workspace capability manifest"
```

## Chunk 2: Skills Discovery and Prompt Injection

### Task 2: Load local and public skills from markdown manifests

**Files:**
- Create: `backend/workspace_skills.py`
- Modify: `backend/workspace_context.py`
- Modify: `backend/orchestrator.py`
- Test: `tests/test_workspace_skills.py`

- [ ] **Step 1: Write the failing test**

```python
def test_skill_registry_loads_local_and_public_skills(tmp_path):
    ...
    skills = registry.discover(...)
    assert "code-review" in skills
    assert "shared-test" in skills
```

- [ ] **Step 2: Run the targeted test to verify it fails**

Run: `pytest tests/test_workspace_skills.py -q`

Expected: FAIL because the skills registry does not exist yet.

- [ ] **Step 3: Implement the minimal skill registry**

Add support for:
- scanning configured skill roots
- parsing `SKILL.md` frontmatter and body
- deduplicating by skill name
- emitting a prompt-ready skill summary for `code_workspace`

- [ ] **Step 4: Run the targeted test again**

Run: `pytest tests/test_workspace_skills.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/workspace_skills.py backend/workspace_context.py backend/orchestrator.py tests/test_workspace_skills.py
git commit -m "feat: load workspace skills"
```

## Chunk 3: MCP Runtime

### Task 3: Add MCP server configuration and tool calling

**Files:**
- Create: `backend/workspace_mcp.py`
- Modify: `requirements.txt`
- Modify: `backend/llm_gateway.py`
- Modify: `backend/orchestrator.py`
- Test: `tests/test_workspace_mcp.py`

- [ ] **Step 1: Write the failing test**

```python
def test_mcp_runtime_lists_tools_and_calls_tool(tmp_path):
    ...
    tools = await runtime.list_tools(...)
    result = await runtime.call_tool(...)
    assert result.text == "ok"
```

- [ ] **Step 2: Run the targeted test to verify it fails**

Run: `pytest tests/test_workspace_mcp.py -q`

Expected: FAIL because MCP runtime support does not exist yet.

- [ ] **Step 3: Implement the minimal MCP client/runtime**

Add:
- `mcp` dependency
- stdio transport support
- streamable HTTP transport support
- tool allowlist filtering
- conversion of MCP tools into model-visible descriptors
- tool result normalization for prompt/history use

- [ ] **Step 4: Run the targeted test again**

Run: `pytest tests/test_workspace_mcp.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/workspace_mcp.py backend/llm_gateway.py backend/orchestrator.py requirements.txt tests/test_workspace_mcp.py
git commit -m "feat: add workspace mcp runtime"
```

## Chunk 4: Agent Loop and SSE Events

### Task 4: Add an agent runtime for plan/tool/result loops

**Files:**
- Create: `backend/workspace_agent.py`
- Modify: `backend/orchestrator.py`
- Modify: `backend/message_store.py`
- Modify: `backend/strategies.py`
- Modify: `backend/api.py`
- Test: `tests/test_workspace_agent.py`

- [ ] **Step 1: Write the failing test**

```python
def test_workspace_agent_executes_tool_then_continues(tmp_path):
    ...
    events = [chunk async for chunk in orchestrator.dispatch_round(session.id)]
    assert any(e.event == "tool_call" for e in events)
    assert any(e.event == "chunk" for e in events)
```

- [ ] **Step 2: Run the targeted test to verify it fails**

Run: `pytest tests/test_workspace_agent.py -q`

Expected: FAIL because the agent loop does not exist yet.

- [ ] **Step 3: Implement the minimal agent loop**

Add:
- participant-level agent profiles
- `plan_only` / `tool_loop` / `full_agent` behavior
- structured plan/tool/result events
- tool output persistence for downstream participants
- hard `max_steps` guard

- [ ] **Step 4: Run the targeted test again**

Run: `pytest tests/test_workspace_agent.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/workspace_agent.py backend/orchestrator.py backend/message_store.py backend/strategies.py backend/api.py tests/test_workspace_agent.py
git commit -m "feat: add workspace agent loop"
```

## Chunk 5: Frontend Capability Editor

### Task 5: Expose skills, MCP, and agent settings in the workspace UI

**Files:**
- Modify: `frontend/src/WorkspaceMode.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/api.ts`
- Test: `frontend/src/App.workspaceCapabilities.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
test("shows capability editors in code workspace mode", async () => {
  ...
  expect(screen.getByText("Skills")).toBeInTheDocument();
  expect(screen.getByText("MCP")).toBeInTheDocument();
  expect(screen.getByText("Agent")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the targeted test to verify it fails**

Run: `cd frontend && npm test -- --watchAll=false --runInBand App.workspaceCapabilities.test.tsx`

Expected: FAIL because capability editors do not exist yet.

- [ ] **Step 3: Implement the minimal UI**

Add:
- workspace capability form fields
- participant-level capability overrides
- payload round-trip to backend
- capability summary display in the session detail view

- [ ] **Step 4: Run the targeted test again**

Run: `cd frontend && npm test -- --watchAll=false --runInBand App.workspaceCapabilities.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/WorkspaceMode.tsx frontend/src/App.tsx frontend/src/types.ts frontend/src/api.ts frontend/src/App.workspaceCapabilities.test.tsx
git commit -m "feat: add workspace capability ui"
```

## Chunk 6: Full Regression

### Task 6: Run backend and frontend regression

**Files:**
- None

- [ ] **Step 1: Run backend tests**

Run: `pytest tests/test_workspace_capabilities.py tests/test_workspace_skills.py tests/test_workspace_mcp.py tests/test_workspace_agent.py tests/test_workspace_mode.py tests/test_workspace_context.py tests/test_workspace_dispatch.py -q`

Expected: all pass.

- [ ] **Step 2: Run frontend tests**

Run: `cd frontend && npm test -- --watchAll=false --runInBand`

Expected: all pass.

- [ ] **Step 3: Commit**

```bash
git add backend frontend tests docs/superpowers/plans/2026-04-19-code-workspace-capabilities-implementation.md
git commit -m "feat: implement workspace capabilities"
```
