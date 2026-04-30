# Provider Model Invocation Normalization Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an internal normalized invocation plan for provider/model calls without changing existing external APIs, UI contracts, or persisted session/provider data.

**Architecture:** Keep `provider_id + model_ref` as the external contract, add a new internal `ResolvedInvocationPlan` abstraction, and route `LLMGatewayClient.chat_stream()` through plan building first and execution second. Reuse existing provider/auth/runtime behavior rather than rewriting the orchestration stack.

**Tech Stack:** Python 3, FastAPI, httpx, LiteLLM, pytest

---

## File Structure

- Create: `backend/invocation_plan.py`
- Modify: `backend/llm_gateway.py`
- Modify: `tests/test_llm_gateway.py`
- Modify: `tests/test_provider_runtime.py`
- Reference: `docs/superpowers/specs/2026-04-23-provider-model-invocation-normalization-design.md`

## Chunk 1: Internal Invocation Plan Type

### Task 1: Add failing unit tests for invocation plan building

**Files:**
- Modify: `tests/test_llm_gateway.py`
- Create: `backend/invocation_plan.py`

- [ ] **Step 1: Write a failing test for explicit provider binding**

Add a test that:
- builds a `ProviderConfig`
- calls `build_invocation_plan("openai/gpt-5.4", provider_config=provider)`
- asserts the plan keeps the provider instance identity and resolves `model_name == "gpt-5.4"`

- [ ] **Step 2: Run the failing test**

Run: `python -m pytest tests/test_llm_gateway.py -q`
Expected: FAIL because `build_invocation_plan()` and `ResolvedInvocationPlan` do not exist yet.

- [ ] **Step 3: Add failing runtime classification coverage**

Add tests for:
- ChatGPT OAuth runtime
- Anthropic messages runtime
- provider-config + bare model name

- [ ] **Step 4: Run the tests again**

Run: `python -m pytest tests/test_llm_gateway.py -q`
Expected: FAIL for missing invocation-plan implementation.

### Task 2: Implement the normalized invocation plan

**Files:**
- Create: `backend/invocation_plan.py`
- Modify: `backend/llm_gateway.py`
- Test: `tests/test_llm_gateway.py`

- [ ] **Step 1: Create the internal dataclass**

Add a focused module containing:
- `ResolvedInvocationPlan`
- `InvocationRuntimeKind` enum or string constants
- `build_invocation_plan(...)`

- [ ] **Step 2: Implement model-name resolution rules**

Rules:
- if `provider_config` is absent, require `provider/model`
- if `provider_config` is present, allow bare model names but normalize them into the plan
- keep the original requested `model_ref`

- [ ] **Step 3: Implement runtime classification**

Classify at least:
- ChatGPT OAuth responses runtime
- Anthropic messages runtime
- provider-specific httpx OpenAI-compatible runtime
- LiteLLM-compatible runtime

- [ ] **Step 4: Run unit tests**

Run: `python -m pytest tests/test_llm_gateway.py -q`
Expected: PASS for the new invocation-plan coverage.

## Chunk 2: Route Chat Stream Through the Plan

### Task 3: Refactor `LLMGatewayClient.chat_stream()`

**Files:**
- Modify: `backend/llm_gateway.py`
- Test: `tests/test_llm_gateway.py`

- [ ] **Step 1: Write a failing regression test for plan-driven dispatch**

Add a test that:
- patches the low-level runtime methods
- verifies `chat_stream()` uses the plan classification to select the right branch

- [ ] **Step 2: Run the test to confirm failure**

Run: `python -m pytest tests/test_llm_gateway.py -q`
Expected: FAIL because `chat_stream()` still branches directly on scattered conditions.

- [ ] **Step 3: Refactor `chat_stream()` into two phases**

Refactor to:
- build plan first
- derive headers/auth once
- dispatch by `plan.runtime_kind`

Do not change:
- public method signature
- streaming semantics
- existing error classes

- [ ] **Step 4: Keep compatibility wrappers**

Retain `resolve_model_target()` only as a helper or compatibility path if still needed internally, but stop using it as the primary abstraction.

- [ ] **Step 5: Run gateway tests**

Run: `python -m pytest tests/test_llm_gateway.py -q`
Expected: PASS.

## Chunk 3: Provider Runtime Regression Coverage

### Task 4: Verify orchestrator/provider integration still works

**Files:**
- Modify: `tests/test_provider_runtime.py`
- Test only

- [ ] **Step 1: Add a regression test for explicit provider binding**

Assert the orchestrator still passes the correct provider-bound model request into the gateway after normalization changes.

- [ ] **Step 2: Add a regression test for provider auto-match**

Assert provider auto-matching behavior is unchanged for sessions that do not specify `provider_id`.

- [ ] **Step 3: Run provider runtime tests**

Run: `python -m pytest tests/test_provider_runtime.py -q`
Expected: PASS.

## Chunk 4: Verification

### Task 5: Run focused regressions

**Files:**
- Test only

- [ ] **Step 1: Run llm gateway regression set**

Run: `python -m pytest tests/test_llm_gateway.py tests/test_provider_runtime.py -q`
Expected: PASS.

- [ ] **Step 2: Run model catalog discovery regressions**

Run: `python -m pytest tests/test_model_catalog_discovery.py -q`
Expected: PASS because discovery APIs must remain behaviorally compatible.

- [ ] **Step 3: Run workspace/provider safety regressions**

Run: `python -m pytest tests/test_workspace_dispatch_failure.py tests/test_session_participant_append.py -q`
Expected: PASS, confirming the refactor does not bleed into unrelated session/workspace flows.

- [ ] **Step 4: Record residual risks**

Document any remaining risk around:
- runtime classification drift
- provider-specific edge cases
- future catalog metadata expansion
