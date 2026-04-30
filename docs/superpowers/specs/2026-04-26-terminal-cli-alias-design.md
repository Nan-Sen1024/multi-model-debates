# Terminal CLI And Alias Design For multi-model-debates
Date: 2026-04-26

## 1. Background

The current web UI already supports sessions, participants, providers, and code-workspace execution, but it is still too form-heavy for the workflow the user wants:

- Start a session from the terminal.
- Add multiple models quickly.
- Give each model a short alias.
- Use `@alias` to target one or more participants.
- Let the AI help create skills, MCP servers, and agent profiles instead of hand-editing raw JSON.
- Keep file writes and command execution inside the existing safe workspace executor.

This project already has the important backend pieces:

- `backend/orchestrator.py` owns sessions and participant dispatch.
- `backend/workspace_executor.py` exposes safe `read_file`, `list_files`, `write_file`, and `run_command`.
- `backend/workspace_agent.py` loops tool results back into the model.
- `backend/workspace_router.py` already parses `@alias` mentions.

So the right move is not to build a second runtime. The right move is a terminal client that reuses the existing backend APIs.

## 2. Decision

### Chosen approach: terminal client over the existing backend

Build a terminal-first client, tentatively `mmd`, that talks to the current HTTP/SSE backend.

Why this is the best fit:

- It matches the interaction model of Claude Code, Codex, OpenClaw, and Hermes.
- It keeps the backend as the single execution boundary.
- It avoids duplicating provider routing, session state, and workspace safety.
- It can be added incrementally without breaking the web UI.

### Rejected approaches

- Web-only command palette: easier to ship, but it does not solve the terminal-first use case.
- Full local runtime rewrite: too much duplication and too much safety risk.

## 3. User Model

The CLI should think in four layers:

1. Provider config
2. Model ref
3. Session participant alias
4. Optional session profile

Rules:

- `provider/model` stays the canonical model ref that the backend understands.
- A participant alias is the session-local `custom_id`.
- `@alias` always targets a participant in the current session.
- A bare model name is only a convenience input. The CLI resolves it to a concrete provider/model target before sending it to the backend.

## 4. Terminal Shape

The terminal should be session-first and stream-first.

Recommended layout:

- Header: session title, mode, round, workspace state.
- Participant row: active aliases and their models.
- Message stream: user messages and model output.
- Execution stream: tool calls, file edits, command runs, and verification results.
- Input line: plain text sends a message; commands start with `/`.

The input experience should support:

- Command history
- Tab completion for aliases and commands
- Inline `@alias` completion
- Clear error messages when a model or alias is ambiguous

## 5. Command Model

### Top-level commands

These are the entry points from a fresh shell:

- `mmd` open the default interactive terminal shell immediately.
- `mmd sessions` list sessions
- `mmd attach <session_id>` attach to an existing session
- `mmd new` create a new session
- `mmd providers` list providers and discovered models
- `mmd models` show the discovered model catalog
- `mmd help` show command help

### In-session commands

Once attached, the prompt is interactive:

- Plain text sends a message to the current session.
- `/help` shows commands and examples.
- `/add <alias> <model> [role description]` appends a new participant.
- `/remove <alias>` removes a participant from the current session.
- `/rename <old> <new>` renames a participant alias in place.
- `/clone [topic]` clones the current session and reattaches the shell to the copy.
- `/setup` opens a guided AI-assisted setup flow.
- `/workspace` shows workspace status and capability summary.
- `/skills` shows the skills available to this session.
- `/mcp` shows MCP servers available to this session.
- `/agent` shows the current agent profile for the session or selected participant.
- `/repair <task>` switches the current session into a repair-oriented request, with explicit workspace execution visibility.
- `/quit` exits the interactive shell.

### Examples

```text
mmd attach 2a9efb15-b492-42f9-8711-aaa835a8f690
/add reviewer gpt-5.4
/add coder anthropic/claude-sonnet
@reviewer 请先审查 backend/llm_gateway.py 的 proxy 处理
@coder 修复之后运行 pytest tests/test_llm_gateway.py -q
/setup
```

## 6. Alias Rules

This is the core of the feature.

### Alias syntax

- Aliases are session-local.
- Aliases must be short and shell-friendly.
- Use the same character class the backend already accepts for mentions: letters, digits, `_`, `.`, and `-`.
- Recommended length: 1 to 32 characters.
- Matching is case-insensitive.
- `@all` is reserved for broadcasting to every active participant and cannot be used as a participant alias.
- Unknown mentions are rejected with a hard error instead of being silently ignored.
- Rename and remove operations update the session-local alias map immediately.

### Resolution order

When the CLI needs to resolve `@alias` or a participant alias:

1. Exact alias match in the current session.
2. Unique prefix match in the current session, if the user typed a partial alias and completion is enabled.
3. If there are multiple matches, prompt the user to choose.
4. Never silently guess when the match is ambiguous.

Important:

- Prefix matching is a UI convenience for completion, not a runtime routing rule.
- Runtime mention routing should only accept exact session aliases or `@all`.
- If a shell command targets an unknown alias, it should fail loudly so the user can correct it immediately.

### Model selection for `add`

When the user adds a participant, the CLI should accept these inputs:

- `provider/model`
- a bare model name, if it can be bound uniquely to one provider
- a provider name or provider alias, if that resolves uniquely

Resolution rules:

1. If the user types `provider/model`, use it as-is.
2. If the user types a bare model name and exactly one provider exposes it, auto-bind that provider.
3. If more than one provider exposes the same model, show a picker.
4. If no provider exposes it, require an explicit `provider/model`.

This keeps the user experience simple without weakening the backend validation.

### Why this is better than raw JSON

- The user never has to remember the full provider payload shape.
- `@alias` stays readable in the prompt.
- The CLI can autocomplete from the current session instead of exposing raw config objects.

## 7. AI-Assisted Setup For Skills, MCP, And Agent Profiles

The current UI is hard to use because it asks the user to fill in nested structures by hand. The terminal should solve that with presets and a guided setup flow.

### Recommended model

Treat skills, MCP servers, and agent settings as a session profile:

- `skills`: what the model is allowed to use
- `mcp_servers`: what external tool servers are attached
- `agent`: write policy, max steps, and allowed tool scope

The CLI should not ask the user to edit nested JSON directly unless they choose an advanced path.

### Recommended flow

1. User types `/setup`.
2. The CLI asks for a goal in plain language.
3. The AI drafts a session profile or a `code_workspace` profile.
4. The CLI shows the diff or summary.
5. The user confirms.
6. The CLI creates a new session or clones the current one with that profile.

### Suggested presets

- `review`: read-only, focused on analysis and critique.
- `repair`: write enabled, safe workspace loop, verify with command execution.
- `planner`: no write access, long-context planning.
- `coder`: write enabled, filesystem and command access inside workspace limits.

### Important boundary

If the user wants to change skills/MCP/agent after the session is already running, the first version should prefer cloning into a new session with the updated profile instead of inventing a large edit surface for live sessions.

## 8. Workspace Repair UX

This is the part the user explicitly cares about.

The terminal must make it obvious when the model is really doing the repair loop:

- Read files
- Inspect directories
- Write changes
- Run verification commands
- Feed the results back into the agent loop

The execution stream should render those events separately from plain text responses.

Recommended event labels:

- `turn_start`
- `model_request`
- `model_response`
- `tool_call`
- `tool_result`
- `chunk`
- `participant_error`

That makes the terminal feel closer to Hermes and Codex, where the user can see the model actually using tools instead of only seeing the final answer.

## 9. Backend Impact

The CLI should reuse what already exists first.

### Existing endpoints it can use

- `GET /api/sessions`
- `GET /api/sessions/{id}`
- `POST /api/sessions`
- `POST /api/sessions/{id}/messages`
- `POST /api/sessions/{id}/participants`
- `POST /api/sessions/{id}/participants/batch`
- `GET /api/sessions/{id}/stream`
- `GET /api/providers`
- `POST /api/model-catalog/discover`
- `PATCH /api/sessions/{id}/workspace/can-write`

### Small backend extension that is worth adding

Expose the already-existing `orchestrator.clone_session(...)` as an API endpoint so the CLI can materialize profile changes cleanly.

That keeps the first version simple:

- create a new session from a profile
- clone an existing session with a new profile
- append participants to a live session

### Future backend extension

If live editing of skills/MCP/agent settings becomes necessary later, add a dedicated workspace patch endpoint. That is a follow-up, not a prerequisite for the CLI design.

## 10. Safety Boundaries

These are non-negotiable:

- The CLI does not get direct local filesystem access to the workspace.
- The CLI does not run arbitrary shell commands locally on behalf of the model.
- All file edits and command execution go through the backend workspace executor.
- Commands exposed to the model remain allowlisted on the backend.
- Ambiguous alias and model resolution must ask the user instead of guessing.

## 11. Rollout Plan

### Phase 1

- Add the terminal client shell.
- Support `sessions`, `attach`, `new`, `providers`, and `models`.
- Support `@alias` routing and participant append.
- Render SSE events in a readable terminal stream.

### Phase 2

- Add the guided `/setup` flow.
- Add session profile presets.
- Add clone-based profile application.

### Phase 3

- Polish workspace repair output.
- Improve alias completion and provider/model auto-binding.
- Add tests for alias resolution and terminal command parsing.

## 12. Reference Patterns

The design borrows the best parts of these tools without copying their runtime:

- Claude Code: slash commands, terminal-first flow, MCP and agent-style control surfaces.
- OpenClaw: session-first command vocabulary and model selection that can accept shorthand.
- Hermes: structured tool-call streaming and execution visibility.
- Codex CLI: a simple command shell with a clear confirm/apply style for risky actions.

## 13. Outcome

If we build this way, the user can stay in the terminal, type natural language, add multiple models by alias, and watch the repair loop in real time without manually editing nested config forms.
