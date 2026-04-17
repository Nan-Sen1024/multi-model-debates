# Provider/Auth Runtime Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stabilize provider/auth integration, fix existing P0/P1 defects, and introduce a minimal provider-profile/runtime-resolver architecture that supports the requested model families without rewriting the orchestrator.

**Architecture:** Keep the current FastAPI + SQLite + React structure, but insert a registry-based provider/auth/runtime layer between `provider_configs` and `LLMGatewayClient`. Auth flows remain in `backend/auth_flow.py`, request execution remains in `backend/llm_gateway.py`, and new focused modules own static provider metadata and runtime resolution. Compression and frontend SSE issues are fixed in-place instead of re-architecting the session flow.

**Tech Stack:** Python 3, FastAPI, aiosqlite, httpx, LiteLLM fallback path, React 19, TypeScript, SQLite, pytest

---

## File Structure

- Create: `docs/superpowers/specs/2026-04-17-provider-auth-runtime-design.md`
- Create: `docs/superpowers/plans/2026-04-17-provider-auth-runtime-implementation.md`
- Create: `backend/provider_profiles.py`
- Create: `backend/runtime_resolver.py`
- Create: `backend/transport_adapters.py`
- Modify: `backend/models.py:29-37`
- Modify: `backend/database.py`
- Modify: `backend/llm_gateway.py:59-498`
- Modify: `backend/auth_flow.py:107-820`
- Modify: `backend/api.py`
- Modify: `backend/message_store.py:18-160`
- Modify: `backend/context_compressor.py:293-460`
- Modify: `backend/orchestrator.py:203-454`
- Modify: `frontend/src/App.tsx:99-900`
- Modify: `frontend/src/api.ts:134-190`
- Modify: `requirements.txt`
- Test: `tests/test_llm_gateway.py`
- Test: `tests/test_auth_flow.py`
- Test: `tests/test_message_store.py`

## Chunk 1: Backend Runtime and Auth

### Task 1: Fix auth config persistence and model ref validation

**Files:**
- Modify: `backend/llm_gateway.py:59-176`
- Test: `tests/test_llm_gateway.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_deserialize_auth_config_preserves_metadata():
    cfg = deserialize_auth_config(
        "iam",
        {"metadata": {"region": "us-east-1", "cert_path": "/tmp/cert.pem"}},
    )
    assert cfg.metadata["region"] == "us-east-1"
    assert cfg.metadata["cert_path"] == "/tmp/cert.pem"


def test_invalid_no_slash_raises():
    with pytest.raises(ValidationError):
        validate_model_ref("openai-gpt4")


def test_invalid_multiple_slashes_raises():
    with pytest.raises(ValidationError):
        validate_model_ref("openai/gpt/4o")
```

- [ ] **Step 2: Run the targeted tests to verify they fail**

Run: `pytest tests/test_llm_gateway.py::TestValidateModelRef -q`

Expected: existing invalid cases fail because `validate_model_ref()` currently accepts malformed values.

- [ ] **Step 3: Implement the minimal fix**

```python
def validate_model_ref(model_ref: str) -> Tuple[str, str]:
    parts = [part.strip() for part in model_ref.split("/")]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValidationError(...)
    return parts[0], parts[1]


def deserialize_auth_config(...):
    ...
    return AuthConfig(..., metadata=raw_payload.get("metadata") or {})
```

- [ ] **Step 4: Run the targeted tests again**

Run: `pytest tests/test_llm_gateway.py -q`

Expected: all gateway unit tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/llm_gateway.py tests/test_llm_gateway.py
git commit -m "fix: restore auth metadata and validate model refs"
```

### Task 2: Introduce provider profiles and runtime resolver

**Files:**
- Create: `backend/provider_profiles.py`
- Create: `backend/runtime_resolver.py`
- Create: `backend/transport_adapters.py`
- Modify: `backend/models.py:29-37`
- Modify: `backend/llm_gateway.py:365-498`
- Test: `tests/test_llm_gateway.py`

- [ ] **Step 1: Write failing tests for runtime resolution**

```python
def test_runtime_resolver_maps_deepseek_to_openai_compatible():
    provider = make_provider(
        name="deepseek",
        provider_type=ProviderType.CUSTOM,
        base_url="https://api.deepseek.com/v1",
        auth_type=AuthType.API_KEY,
    )
    resolved = RuntimeResolver().resolve(provider, "deepseek/deepseek-chat")
    assert resolved.transport == "openai_compatible"
    assert resolved.base_url == "https://api.deepseek.com/v1"


def test_runtime_resolver_maps_claude_direct_to_anthropic():
    provider = make_provider(
        name="anthropic",
        provider_type=ProviderType.ANTHROPIC,
        base_url="https://api.anthropic.com",
        auth_type=AuthType.API_KEY,
    )
    resolved = RuntimeResolver().resolve(provider, "anthropic/claude-sonnet-4.5")
    assert resolved.transport == "anthropic"
```

- [ ] **Step 2: Run the targeted tests to verify they fail**

Run: `pytest tests/test_llm_gateway.py -q`

Expected: failure because `RuntimeResolver` and profile registry do not exist yet.

- [ ] **Step 3: Implement the minimal registry and resolver**

```python
@dataclass
class ProviderProfile:
    provider_id: str
    default_transport: str
    supported_auth: list[str]
    default_base_url: str | None = None


class RuntimeResolver:
    def resolve(self, provider_config: ProviderConfig, model_ref: str) -> ResolvedRuntimeConfig:
        profile = get_provider_profile(provider_config, model_ref)
        return ResolvedRuntimeConfig(
            transport=profile.default_transport,
            base_url=provider_config.base_url or profile.default_base_url,
            auth=provider_config.auth_config,
            provider=provider,
            model=model,
        )
```

- [ ] **Step 4: Rewire gateway code to use the resolver**

Run: `pytest tests/test_llm_gateway.py -q`

Expected: resolver tests and existing gateway tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/provider_profiles.py backend/runtime_resolver.py backend/transport_adapters.py backend/models.py backend/llm_gateway.py tests/test_llm_gateway.py
git commit -m "feat: add provider profiles and runtime resolver"
```

### Task 3: Normalize auth session persistence for browser/device flows

**Files:**
- Modify: `backend/database.py`
- Modify: `backend/auth_flow.py:107-820`
- Modify: `backend/api.py`
- Test: `tests/test_auth_flow.py`

- [ ] **Step 1: Write failing auth flow tests**

```python
def test_pkce_context_round_trip_persists_in_auth_session():
    ...
    row = run(load_auth_session(...))
    assert json.loads(row["context_json"])["code_verifier"]


def test_handle_pkce_callback_updates_provider_auth_config():
    ...
    cfg = load_provider_auth_config(...)
    assert cfg["oauth_token"]["access_token"] == "token-123"
```

- [ ] **Step 2: Run the targeted tests to verify they fail**

Run: `pytest tests/test_auth_flow.py -q`

Expected: failure because the current flow stores context inconsistently and does not normalize provider write-back.

- [ ] **Step 3: Implement the schema-compatible persistence fix**

```python
CREATE_AUTH_SESSIONS_TABLE = """
...
context_json    TEXT,
...
"""

await db.execute(
    "UPDATE auth_sessions SET context_json = ?, updated_at = ? WHERE id = ?",
    (json.dumps(ctx), now, auth_session_id),
)
```

- [ ] **Step 4: Refactor flow handlers to consume `context_json` instead of overloaded fields**

Run: `pytest tests/test_auth_flow.py -q`

Expected: auth flow tests pass and provider config receives normalized auth payloads.

- [ ] **Step 5: Commit**

```bash
git add backend/database.py backend/auth_flow.py backend/api.py tests/test_auth_flow.py
git commit -m "fix: persist interactive auth session context"
```

### Task 4: Make enterprise and provider-specific auth consumable at runtime

**Files:**
- Modify: `backend/llm_gateway.py:365-498`
- Modify: `backend/transport_adapters.py`
- Test: `tests/test_llm_gateway.py`

- [ ] **Step 1: Write failing tests for enterprise auth consumption**

```python
def test_auth_manager_preserves_iam_metadata_for_bedrock():
    cfg = AuthConfig(
        auth_type=AuthType.IAM,
        metadata={"region": "us-east-1", "access_key_id": "AKIA...", "secret_access_key": "secret"},
    )
    runtime = RuntimeResolver().resolve(make_provider(auth_type=AuthType.IAM), "anthropic/claude-sonnet-4")
    assert runtime.auth.metadata["region"] == "us-east-1"


def test_xai_mtls_metadata_is_exposed_to_transport():
    cfg = AuthConfig(
        auth_type=AuthType.API_KEY,
        api_key="xai-key",
        metadata={"cert_path": "client.pem", "key_path": "client.key"},
    )
    ...
```

- [ ] **Step 2: Run the targeted tests to verify they fail**

Run: `pytest tests/test_llm_gateway.py -q`

Expected: failure because current runtime path drops or ignores enterprise metadata.

- [ ] **Step 3: Implement minimal transport-facing runtime payloads**

```python
class ResolvedRuntimeConfig:
    transport: str
    base_url: str | None
    headers: dict[str, str]
    auth: AuthConfig
    request_options: dict[str, object]
```

- [ ] **Step 4: Re-run the targeted tests**

Run: `pytest tests/test_llm_gateway.py -q`

Expected: metadata-dependent paths now pass.

- [ ] **Step 5: Commit**

```bash
git add backend/llm_gateway.py backend/transport_adapters.py tests/test_llm_gateway.py
git commit -m "feat: expose enterprise auth data to transport adapters"
```

## Chunk 2: Compression, Frontend State, and Bootstrap

### Task 5: Make compression reduce prompt history

**Files:**
- Modify: `backend/message_store.py:66-160`
- Modify: `backend/context_compressor.py:293-460`
- Modify: `backend/orchestrator.py:203-454`
- Test: `tests/test_message_store.py`

- [ ] **Step 1: Write the failing regression test**

```python
def test_build_history_uses_summary_for_compressed_range(store, session_id):
    ...
    history = run(store.load_renderable_history(session_id))
    assert "[历史摘要]" in history
    assert "原始长消息正文" not in history
```

- [ ] **Step 2: Run the targeted tests to verify they fail**

Run: `pytest tests/test_message_store.py -q`

Expected: failure because compressed messages still render original content.

- [ ] **Step 3: Implement the minimal history-compaction path**

```python
async def load_renderable_messages(...):
    # merge compressed_summaries with uncompressed messages
    ...
```

- [ ] **Step 4: Re-run the targeted tests**

Run: `pytest tests/test_message_store.py -q`

Expected: compressed history tests pass and existing message-store behavior remains green.

- [ ] **Step 5: Commit**

```bash
git add backend/message_store.py backend/context_compressor.py backend/orchestrator.py tests/test_message_store.py
git commit -m "fix: render compressed history summaries in prompts"
```

### Task 6: Refresh session state after SSE and clean timers

**Files:**
- Modify: `frontend/src/App.tsx:99-900`
- Modify: `frontend/src/api.ts:134-190`

- [ ] **Step 1: Add the failing frontend regression coverage or minimal harness notes**

```tsx
it("refreshes session detail after turn_end", async () => {
  ...
  expect(loadSessionDetail).toHaveBeenCalled()
})
```

- [ ] **Step 2: Run the frontend verification command**

Run: `npm test -- --watch=false`

Expected: either targeted failure in the new test, or a documented blocker if the repository lacks a runnable frontend test harness.

- [ ] **Step 3: Implement the minimal state fix**

```tsx
if (eventName === "turn_end" || eventName === "session_end") {
  void reloadSessionState(session.id)
}

useEffect(() => () => {
  Object.values(pollTimers.current).forEach(clearInterval)
  if (sessionTimeoutRef.current) clearTimeout(sessionTimeoutRef.current)
}, [])
```

- [ ] **Step 4: Re-run frontend verification**

Run: `npm test -- --watch=false` or `npm run build`

Expected: no timer leaks from the modified paths and the app still builds.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/api.ts
git commit -m "fix: refresh session state and clear frontend timers"
```

### Task 7: Restore bootstrap dependencies and run end-to-end verification

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Write the minimal dependency regression check**

```text
The repository must install core runtime/test dependencies from requirements.txt without manual package guessing.
```

- [ ] **Step 2: Restore the commented dependencies**

```text
fastapi>=0.111.0
uvicorn[standard]>=0.29.0
litellm>=1.40.0
hypothesis>=6.100.0
pytest>=8.0.0
pytest-asyncio>=0.23.0
aiosqlite>=0.20.0
```

- [ ] **Step 3: Run backend verification**

Run: `pytest -q`

Expected: test suite passes or reports only known environment-dependent skips.

- [ ] **Step 4: Run frontend verification**

Run: `npm run build`

Expected: production build succeeds.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt
git commit -m "fix: restore required project dependencies"
```

## Execution Notes

- Use `superpowers:using-git-worktrees` before implementation because the current worktree is dirty.
- Use `superpowers:test-driven-development` for every task that changes behavior.
- Use `superpowers:systematic-debugging` before changing any failing path discovered during execution.
- Use `superpowers:verification-before-completion` before claiming the implementation is done.

Plan complete and saved to `docs/superpowers/plans/2026-04-17-provider-auth-runtime-implementation.md`. Ready to execute.
