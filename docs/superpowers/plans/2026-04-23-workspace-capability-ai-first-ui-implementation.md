# Workspace Capability AI-First UI Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make workspace `skill` / `MCP` / `agent` setup understandable at a glance by adding an AI-generated draft flow and hiding the advanced schema editor behind an opt-in panel.

**Architecture:** Keep the existing capability manifest data model. Add one backend endpoint that asks a configured model to generate a workspace capability draft from natural language, then let the frontend apply that draft into the existing form state. The main UI becomes a simple assistant-first flow with a short preview and a collapsed advanced editor for manual tuning.

**Tech Stack:** FastAPI, Pydantic, `LLMGatewayClient`, React 18, TypeScript, Jest, React Testing Library style DOM tests.

---

## Chunk 1: Backend capability draft generation

**Files:**
- Modify: `backend/api.py`
- Modify: `backend/llm_gateway.py` if a small helper is needed for non-stream collection
- Test: `tests/test_workspace_capability_generation.py` or extend an existing backend test file

- [ ] **Step 1: Write the failing test**

Create a test that posts a natural-language workspace intent plus a provider/model selection and expects a structured capability draft response.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_workspace_capability_generation.py -q`
Expected: FAIL because the endpoint does not exist yet.

- [ ] **Step 3: Write minimal implementation**

Add a `POST /api/workspace/capabilities/generate` endpoint that:
- accepts intent text and optional provider/model selection
- calls the existing LLM gateway
- parses JSON into the existing workspace capability schema
- returns the normalized manifest

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_workspace_capability_generation.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/api.py backend/llm_gateway.py tests/test_workspace_capability_generation.py
git commit -m "feat: add ai workspace capability draft generation"
```

## Chunk 2: Frontend AI-first workspace editor

**Files:**
- Modify: `frontend/src/WorkspaceMode.tsx`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/types.ts`
- Test: `frontend/src/App.workspaceCapabilities.test.tsx`

- [ ] **Step 1: Write the failing test**

Add a test that renders the workspace create panel and verifies:
- it shows a plain-language AI prompt area
- advanced schema editing is collapsed by default
- clicking generate applies a returned manifest into the visible preview and form state

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test -- App.workspaceCapabilities.test.tsx --runInBand`
Expected: FAIL because the assistant UI and generation flow are missing.

- [ ] **Step 3: Write minimal implementation**

Add:
- an AI assistant card with prompt, model selector, and `Generate` button
- a readable summary preview derived from the manifest
- a collapsed advanced editor for raw `skill_sources` / `mcp_servers` / `agent_defaults` / overrides
- API wiring to the new backend endpoint

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm test -- App.workspaceCapabilities.test.tsx --runInBand`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/WorkspaceMode.tsx frontend/src/api.ts frontend/src/types.ts frontend/src/App.workspaceCapabilities.test.tsx
git commit -m "feat: make workspace capabilities AI-first"
```

