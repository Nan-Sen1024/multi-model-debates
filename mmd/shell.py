from __future__ import annotations

import importlib
import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from backend.models import ModelParticipant
from backend.workspace_router import extract_workspace_mentions
from backend.workspace_router import resolve_workspace_targets_with_unknowns

from .catalog import ModelCatalogEntry
from .catalog import ModelResolutionError
from .catalog import format_participant_model_selection
from .catalog import resolve_participant_model_selection
from .catalog import resolve_backend_model_ref
from .client import MmdClient
from .commands import ParsedInputKind
from .commands import parse_participant_spec
from .commands import parse_shell_input
from .setup import build_quick_setup_plan
from .setup import build_workspace_capabilities_payload


def _participant_label(participant: Dict[str, Any]) -> str:
    custom_id = str(participant.get("custom_id") or "").strip() or "unknown"
    model_ref = str(participant.get("model_ref") or "").strip() or "unknown"
    provider_id = str(participant.get("provider_id") or "").strip() or None
    selection = format_participant_model_selection(provider_id, model_ref)
    return f"@{custom_id} -> {selection}"


def _configure_terminal_readline(readline_module: Any | None = None) -> bool:
    module = readline_module
    if module is None:
        try:
            module = importlib.import_module("readline")
        except Exception:
            return False

    for command in ("set editing-mode emacs", "tab: complete"):
        try:
            module.parse_and_bind(command)
        except Exception:
            continue

    try:
        module.set_history_length(1000)
    except Exception:
        pass

    return True


@dataclass
class ShellContext:
    current_session: Dict[str, Any]
    catalogs: List[ModelCatalogEntry]


class MmdShell:
    def __init__(
        self,
        client: MmdClient,
        session: Dict[str, Any],
        *,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
    ) -> None:
        self.client = client
        self.session = session
        self.input_fn = input_fn
        self.output_fn = output_fn
        self.catalogs: List[ModelCatalogEntry] = []
        self._focus_participant_ids: tuple[str, ...] = ()
        self._focus_broadcast = False
        self.refresh_catalogs()

    @property
    def session_id(self) -> str:
        return str(self.session.get("id") or "")

    @property
    def prompt(self) -> str:
        return "mmd> "

    def refresh_catalogs(self) -> List[ModelCatalogEntry]:
        providers = self.client.list_providers()
        catalogs: List[ModelCatalogEntry] = []
        for provider in providers:
            provider_id = str(provider.get("id") or "").strip()
            if not provider_id:
                continue
            try:
                discovery = self.client.discover_models(provider_id=provider_id)
            except Exception:
                discovery = {"models": []}
            catalogs.append(
                ModelCatalogEntry(
                    provider_id=provider_id,
                    provider_name=str(provider.get("name") or provider_id).strip(),
                    provider_type=str(provider.get("provider_type") or "").strip(),
                    models=tuple(
                        str(model).strip()
                        for model in discovery.get("models", [])
                        if str(model).strip()
                    ),
                    detected_at=discovery.get("detected_at"),
                )
            )
        self.catalogs = catalogs
        return catalogs

    def run(self) -> None:
        _configure_terminal_readline()
        self._print_session_header()
        while True:
            try:
                self._print_status_bar()
                line = self.input_fn(self.prompt)
            except EOFError:
                self.output_fn("")
                break
            if self.handle_line(line):
                break

    def handle_line(self, line: str) -> bool:
        parsed = parse_shell_input(line)
        if parsed.kind is ParsedInputKind.MESSAGE:
            text = parsed.text.strip()
            if not text:
                return False
            if self._is_focus_directive(text):
                self._handle_focus_directive(text)
                return False
            text = self._apply_focus_to_message(text)
            if not self._validate_mentions(text):
                return False
            self.client.send_user_message(self.session_id, text)
            self._stream_current_round()
            self._refresh_session()
            return False
        return self._handle_command(parsed.command.lower().strip(), parsed.args)

    def _handle_command(self, command: str, args: tuple[str, ...]) -> bool:
        if not command:
            return False
        if command in {"quit", "exit"}:
            return True
        if command == "help":
            self._print(self._help_text())
            return False
        if command in {"who", "participants"}:
            self._print_participants()
            return False
        if command == "status":
            self._print_status()
            return False
        if command == "workspace":
            self._print_workspace()
            return False
        if command == "skills":
            self._print_skill_sources()
            return False
        if command == "mcp":
            self._print_mcp_servers()
            return False
        if command == "agent":
            self._print_agent_profile()
            return False
        if command == "model":
            self._handle_model(args)
            return False
        if command == "add":
            self._handle_add(args)
            return False
        if command == "remove":
            self._handle_remove(args)
            return False
        if command == "rename":
            self._handle_rename(args)
            return False
        if command == "clone":
            self._handle_clone(args)
            return False
        if command in {"to", "focus"}:
            self._handle_focus(args)
            return False
        if command == "setup":
            self._handle_setup(args)
            return False
        if command == "send":
            self.handle_line(" ".join(args))
            return False
        self._print(f"Unknown command: /{command}")
        return False

    def _refresh_session(self) -> Dict[str, Any]:
        self.session = self.client.get_session(self.session_id)
        return self.session

    def _session_participants(self) -> List[Dict[str, Any]]:
        participants = self.session.get("participants", [])
        return [item for item in participants if isinstance(item, dict) and item.get("is_active", True)]

    def _session_as_model_participants(self) -> List[ModelParticipant]:
        result: List[ModelParticipant] = []
        for index, participant in enumerate(self._session_participants(), start=1):
            model_ref = str(participant.get("model_ref") or "").strip()
            if not model_ref:
                continue
            result.append(
                ModelParticipant(
                    id=str(participant.get("id") or f"participant-{index}"),
                    session_id=self.session_id,
                    custom_id=str(participant.get("custom_id") or f"participant-{index}").strip(),
                    model_ref=model_ref,
                    provider_id=participant.get("provider_id"),
                    sequence_order=index,
                    role_desc=participant.get("role_desc"),
                    is_active=bool(participant.get("is_active", True)),
                )
            )
        return result

    def _current_default_model_label(self) -> str:
        model_ref = str(self.session.get("default_model_ref") or "").strip()
        return model_ref

    def _current_focus_labels(self) -> List[str]:
        if self._focus_broadcast:
            return ["@all"]

        participants_by_id = {
            str(participant.get("id") or "").strip(): str(participant.get("custom_id") or "").strip()
            for participant in self._session_participants()
            if str(participant.get("id") or "").strip() and str(participant.get("custom_id") or "").strip()
        }
        labels: List[str] = []
        resolved_ids: List[str] = []
        for participant_id in self._focus_participant_ids:
            custom_id = participants_by_id.get(participant_id)
            if not custom_id:
                continue
            resolved_ids.append(participant_id)
            labels.append(f"@{custom_id}")
        if tuple(resolved_ids) != self._focus_participant_ids:
            self._focus_participant_ids = tuple(resolved_ids)
        return labels

    def _apply_focus_to_message(self, text: str) -> str:
        if self._focus_broadcast:
            return text if text.lower().lstrip().startswith("@all") else f"@all {text}"

        labels = self._current_focus_labels()
        if not labels or extract_workspace_mentions(text):
            return text
        return f"{' '.join(labels)} {text}"

    def _is_focus_directive(self, text: str) -> bool:
        if not text.startswith("@"):
            return False
        residual = re.sub(r"@([A-Za-z0-9_.-]+)", "", text)
        return not residual.strip(" \t\r\n,;:.!?")

    def _normalize_focus_tokens(self, args: tuple[str, ...]) -> List[str]:
        tokens: List[str] = []
        for arg in args:
            for piece in arg.replace(",", " ").split():
                normalized = piece.strip()
                if not normalized:
                    continue
                if normalized.startswith("@"):
                    normalized = normalized[1:]
                normalized = normalized.strip()
                if normalized:
                    tokens.append(normalized)
        return tokens

    def _set_focus_from_aliases(self, aliases: List[str], *, announce: bool = True) -> bool:
        normalized = [alias.strip() for alias in aliases if alias.strip()]
        if not normalized:
            self._focus_participant_ids = ()
            self._focus_broadcast = False
            if announce:
                self._print("Focus cleared.")
            return True

        if any(alias.lower() == "all" for alias in normalized) and len(normalized) > 1:
            if announce:
                self._print("Cannot mix @all with other aliases.")
            return False

        if len(normalized) == 1 and normalized[0].lower() == "all":
            self._focus_participant_ids = ()
            self._focus_broadcast = True
            if announce:
                self._print("Focus: @all")
            return True

        focus_text = " ".join(f"@{alias}" for alias in normalized)
        targets, unknown_mentions = resolve_workspace_targets_with_unknowns(
            focus_text,
            self._session_as_model_participants(),
        )
        if unknown_mentions:
            if announce:
                self._print(f"Unknown alias in focus target: {', '.join(unknown_mentions)}")
            return False
        if not targets:
            if announce:
                self._print("No active participants matched the focus target.")
            return False

        self._focus_participant_ids = tuple(target.id for target in targets)
        self._focus_broadcast = False
        if announce:
            self._print("Focus: " + ", ".join(f"@{target.custom_id}" for target in targets))
        return True

    def _handle_focus_directive(self, text: str) -> None:
        mentions = extract_workspace_mentions(text)
        if not mentions:
            return
        self._set_focus_from_aliases(mentions, announce=False)

    def _handle_focus(self, args: tuple[str, ...]) -> None:
        if not args:
            current = ", ".join(self._current_focus_labels()) or "none"
            self._print(f"Current focus: {current}")
            self._print("Usage: /to <alias...> | /to clear")
            return

        first = args[0].strip().lower()
        if first in {"clear", "none", "reset"} and len(args) == 1:
            self._focus_participant_ids = ()
            self._focus_broadcast = False
            self._print("Focus cleared.")
            return

        tokens = self._normalize_focus_tokens(args)
        self._set_focus_from_aliases(tokens)

    def _validate_mentions(self, text: str) -> bool:
        mentions = extract_workspace_mentions(text)
        if not mentions:
            return True
        targets, unknown_mentions = resolve_workspace_targets_with_unknowns(
            text,
            self._session_as_model_participants(),
        )
        if unknown_mentions:
            self._print(f"Unknown alias in message: {', '.join(unknown_mentions)}")
            return False
        if targets:
            return True
        self._print(f"Unknown alias in message: {', '.join(mentions)}")
        return False

    def _stream_current_round(self) -> None:
        for event in self.client.stream_session(self.session_id):
            payload = event.payload if isinstance(event.payload, dict) else {}
            participant_id = str(payload.get("participant_id") or "").strip()
            round_number = payload.get("round") or payload.get("round_number") or "?"
            if event.event == "turn_start":
                execution_mode = payload.get("execution_mode") or "model"
                self._print(f"[turn_start] {participant_id or 'system'} round={round_number} mode={execution_mode}")
            elif event.event == "model_request":
                self._print(f"[model_request] {participant_id or 'system'} {payload.get('model_ref') or ''}".rstrip())
            elif event.event == "model_response":
                self._print(f"[model_response] {participant_id or 'system'}")
            elif event.event == "agent_plan":
                plan = str(payload.get("plan") or payload.get("summary") or "").strip()
                self._print(f"[agent_plan] {participant_id or 'system'} {plan}".rstrip())
            elif event.event == "tool_call":
                server = str(payload.get("server_name") or payload.get("server") or "").strip()
                tool = str(payload.get("tool_name") or payload.get("tool") or "").strip()
                self._print(f"[tool_call] {participant_id or 'system'} {server}.{tool}".rstrip("."))
            elif event.event == "tool_result":
                server = str(payload.get("server_name") or payload.get("server") or "").strip()
                tool = str(payload.get("tool_name") or payload.get("tool") or "").strip()
                self._print(f"[tool_result] {participant_id or 'system'} {server}.{tool}".rstrip("."))
            elif event.event == "chunk":
                content = str(payload.get("content") or "").rstrip()
                if content:
                    self._print(content)
            elif event.event == "participant_error":
                message = str(payload.get("message") or "participant_error").strip()
                code = str(payload.get("code") or "").strip()
                if code:
                    self._print(f"[participant_error] {participant_id or 'system'} {code}: {message}")
                else:
                    self._print(f"[participant_error] {participant_id or 'system'} {message}")
            elif event.event == "error":
                message = str(payload.get("message") or "error").strip()
                code = str(payload.get("code") or "").strip()
                if code:
                    self._print(f"[error] {code}: {message}")
                else:
                    self._print(f"[error] {message}")

    def _help_text(self) -> str:
        return "\n".join(
            [
                "Commands:",
                "  /help                 Show this help",
                "  /who                  List active participants",
                "  /status               Show session status",
                "  /model                Show current default model",
                "  /model status         Show session model status",
                "  /model set <model>    Set session default model",
                "  /model clear          Clear session default model",
                "  /add <alias> [model]  Append a participant (uses default model if omitted)",
                "  /workspace            Show workspace summary",
                "  /skills               Show session skills",
                "  /mcp                  Show session MCP servers",
                "  /agent                Show agent profile",
                "  /remove <alias>       Remove a participant",
                "  /rename <old> <new>   Rename a participant alias",
                "  /clone [topic]        Clone this session and attach",
                "  /to <alias...>        Focus follow-up text on one or more aliases",
                "  /setup [goal]         Create a new quick-setup session from this one",
                "  /send <text>          Send a message explicitly",
                "  /quit                 Exit the shell",
                "",
                "Plain text is sent as a message.",
                "@alias on a line by itself changes the current focus.",
                "@alias mentions are routed by the backend workspace dispatcher.",
                "@all broadcasts to every active participant.",
            ]
        )

    def _print_session_header(self) -> None:
        title = str(self.session.get("title") or self.session.get("topic") or "session")
        mode = str(self.session.get("mode") or "unknown")
        self._print(f"Attached to {title} [{mode}]")
        self._print("Type /help for commands.")

    def _print(self, message: str = "") -> None:
        self.output_fn(message)

    def _print_status_bar(self) -> None:
        self._print(self._status_bar_text())

    def _status_bar_text(self) -> str:
        title = str(self.session.get("title") or self.session.get("topic") or "session")
        mode = str(self.session.get("mode") or "unknown")
        status = str(self.session.get("status") or "unknown")
        round_number = self.session.get("current_round", 0)
        focus = ",".join(self._current_focus_labels()) or "none"
        default_model = self._current_default_model_label() or "unset"
        workspace_access = self._workspace_access_label()
        return (
            "mmd | "
            f"{title} | {mode} | {status} | r={round_number} | "
            f"focus={focus} | model={default_model} | ws={workspace_access}"
        )

    def _workspace_access_label(self) -> str:
        workspace = self.session.get("workspace")
        if not isinstance(workspace, dict):
            return "none"
        capabilities = workspace.get("capabilities")
        agent_defaults = capabilities.get("agent_defaults") if isinstance(capabilities, dict) else None
        if isinstance(agent_defaults, dict) and bool(agent_defaults.get("can_write", False)):
            return "rw"
        return "ro"

    def _print_status(self) -> None:
        self.refresh_catalogs()
        title = str(self.session.get("title") or self.session.get("topic") or "session")
        mode = str(self.session.get("mode") or "unknown")
        status = str(self.session.get("status") or "unknown")
        round_number = self.session.get("current_round", 0)
        focus = ", ".join(self._current_focus_labels()) or "none"
        default_model = self._current_default_model_label() or "unset"

        self._print(f"Session: {title} [{mode}] status={status} round={round_number}")
        self._print(f"Focus: {focus}")
        self._print(f"Default model: {default_model}")
        self._print("Participants:")
        participants = self._session_participants()
        if participants:
            for participant in participants:
                self._print(f"  - {_participant_label(participant)}")
        else:
            self._print("  none")

        self._print("Providers:")
        providers = self.client.list_providers()
        if providers:
            for provider in providers:
                if not isinstance(provider, dict):
                    continue
                provider_id = str(provider.get("id") or "").strip() or "unknown"
                provider_name = str(provider.get("name") or "").strip() or provider_id
                provider_type = str(provider.get("provider_type") or "").strip() or "unknown"
                auth_status = str(provider.get("auth_status") or "unknown").strip() or "unknown"
                fallback_ids = provider.get("fallback_ids")
                fallback_text = ""
                if isinstance(fallback_ids, list) and fallback_ids:
                    fallback_text = f" fallback={', '.join(str(item) for item in fallback_ids if str(item).strip())}"
                self._print(
                    f"  - {provider_name} [{provider_id}] type={provider_type} auth={auth_status}{fallback_text}"
                )
        else:
            self._print("  none")

        workspace = self.session.get("workspace")
        if isinstance(workspace, dict):
            root_path = str(workspace.get("root_path") or "").strip() or "unknown"
            index_status = str(workspace.get("index_status") or "unknown").strip() or "unknown"
            self._print("Workspace:")
            self._print(f"  root: {root_path}")
            self._print(f"  index_status: {index_status}")
            capabilities = workspace.get("capabilities")
            if isinstance(capabilities, dict):
                agent_defaults = capabilities.get("agent_defaults")
                if isinstance(agent_defaults, dict):
                    self._print(
                        "  agent: "
                        f"mode={agent_defaults.get('mode', 'unknown')}, "
                        f"can_write={agent_defaults.get('can_write', False)}, "
                        f"max_steps={agent_defaults.get('max_steps', 0)}"
                    )
        else:
            self._print("Workspace: none")

    def _print_participants(self) -> None:
        participants = self._session_participants()
        if not participants:
            self._print("No active participants.")
            return
        self._print("Participants:")
        for participant in participants:
            self._print(f"  - {_participant_label(participant)}")

    def _print_workspace(self) -> None:
        workspace = self.session.get("workspace")
        if not isinstance(workspace, dict):
            self._print("No workspace configured.")
            return
        root_path = str(workspace.get("root_path") or "").strip()
        display_name = str(workspace.get("display_name") or "").strip()
        self._print(f"Workspace: {display_name or root_path or 'unknown'}")
        self._print(f"  root: {root_path or 'unknown'}")
        self._print(f"  index_status: {workspace.get('index_status') or 'unknown'}")
        summary = str(workspace.get("summary") or "").strip()
        if summary:
            self._print(f"  summary: {summary}")
        capabilities = workspace.get("capabilities")
        if not isinstance(capabilities, dict):
            self._print("  No capability manifest.")
            return
        agent_defaults = capabilities.get("agent_defaults")
        if isinstance(agent_defaults, dict):
            self._print(
                "  agent: "
                f"mode={agent_defaults.get('mode', 'unknown')}, "
                f"can_write={agent_defaults.get('can_write', False)}, "
                f"max_steps={agent_defaults.get('max_steps', 0)}"
            )

    def _print_skill_sources(self) -> None:
        workspace = self.session.get("workspace")
        capabilities = workspace.get("capabilities") if isinstance(workspace, dict) else None
        skill_sources = capabilities.get("skill_sources") if isinstance(capabilities, dict) else []
        if not skill_sources:
            self._print("No skill sources configured.")
            return
        self._print("Skill sources:")
        for source in skill_sources:
            if not isinstance(source, dict):
                continue
            path = str(source.get("path") or "").strip()
            label = str(source.get("label") or "").strip()
            self._print(f"  - {path}" + (f" ({label})" if label else ""))

    def _print_mcp_servers(self) -> None:
        workspace = self.session.get("workspace")
        capabilities = workspace.get("capabilities") if isinstance(workspace, dict) else None
        mcp_servers = capabilities.get("mcp_servers") if isinstance(capabilities, dict) else []
        if not mcp_servers:
            self._print("No MCP servers configured.")
            return
        self._print("MCP servers:")
        for server in mcp_servers:
            if not isinstance(server, dict):
                continue
            name = str(server.get("name") or "").strip()
            transport = str(server.get("transport") or "").strip()
            self._print(f"  - {name} [{transport}]")

    def _print_agent_profile(self) -> None:
        workspace = self.session.get("workspace")
        capabilities = workspace.get("capabilities") if isinstance(workspace, dict) else None
        agent_defaults = capabilities.get("agent_defaults") if isinstance(capabilities, dict) else None
        if not isinstance(agent_defaults, dict):
            self._print("No agent profile configured.")
            return
        self._print("Agent profile:")
        self._print(f"  mode: {agent_defaults.get('mode')}")
        self._print(f"  can_write: {agent_defaults.get('can_write')}")
        self._print(f"  max_steps: {agent_defaults.get('max_steps')}")
        self._print(f"  memory_scope: {agent_defaults.get('memory_scope')}")

    def _handle_model(self, args: tuple[str, ...]) -> None:
        if not args:
            default_model = self._current_default_model_label() or "unset"
            self._print(f"Default model: {default_model}")
            self._print("Usage: /model [status|set <model>|clear|list]")
            return

        action = args[0].strip().lower()
        if action in {"status", "show"}:
            self._print_status()
            return
        if action == "list":
            self._print_model_catalogs()
            return
        if action == "clear":
            self._set_session_default_model(None)
            return
        if action == "set":
            if len(args) < 2:
                self._print("Usage: /model set <provider/model>")
                return
            self._set_session_default_model(" ".join(args[1:]))
            return
        if len(args) == 1:
            self._set_session_default_model(args[0])
            return
        self._print("Usage: /model [status|set <model>|clear|list]")

    def _set_session_default_model(self, model_ref: Optional[str]) -> None:
        self.refresh_catalogs()
        normalized = str(model_ref or "").strip()
        if normalized:
            try:
                resolved = resolve_backend_model_ref(self.catalogs, {"model_ref": normalized})
            except ModelResolutionError as exc:
                self._print(str(exc))
                if exc.candidate_provider_ids:
                    self._print(f"Candidates: {', '.join(exc.candidate_provider_ids)}")
                return
        else:
            resolved = ""

        updated = self.client.update_session_config(
            self.session_id,
            {"default_model_ref": resolved or None},
        )
        self.session = updated
        if resolved:
            self._print(f"Default model set to {resolved}")
        else:
            self._print("Default model cleared")

    def _print_model_catalogs(self) -> None:
        self.refresh_catalogs()
        if not self.catalogs:
            self._print("No discovered models.")
            return
        self._print("Models:")
        for catalog in self.catalogs:
            provider_name = str(catalog.provider_name or catalog.provider_id or "unknown").strip()
            provider_type = str(catalog.provider_type or "unknown").strip()
            self._print(f"  - {provider_name} [{provider_type}]")
            for model in catalog.models:
                self._print(f"      {model}")

    def _handle_add(self, args: tuple[str, ...]) -> None:
        if not args:
            self._print("Usage: /add <alias> [model] [role description]")
            return
        self.refresh_catalogs()
        alias = args[0].strip()
        if not alias:
            self._print("Usage: /add <alias> [model] [role description]")
            return

        if len(args) >= 2:
            model_ref = args[1].strip()
            role_desc = " ".join(args[2:]).strip() or None
        else:
            model_ref = self._current_default_model_label()
            role_desc = None
            if not model_ref:
                self._print(
                    "No default model is set. Run /model set <provider/model> first, "
                    "or pass a model to /add."
                )
                return

        try:
            selection = resolve_participant_model_selection(self.catalogs, {"model_ref": model_ref})
        except ModelResolutionError as exc:
            self._print(str(exc))
            if exc.candidate_provider_ids:
                self._print(f"Candidates: {', '.join(exc.candidate_provider_ids)}")
            return

        payload: Dict[str, Any] = {
            "custom_id": alias,
            "model_ref": selection["model_ref"],
        }
        if selection.get("provider_id"):
            payload["provider_id"] = selection["provider_id"]
        if role_desc:
            payload["role_desc"] = role_desc

        updated = self.client.append_session_participant(self.session_id, payload)
        self.session = updated
        self._print(f"Added participant {alias}")

    def _handle_remove(self, args: tuple[str, ...]) -> None:
        if len(args) != 1:
            self._print("Usage: /remove <alias>")
            return

        alias = args[0].strip()
        if not alias:
            self._print("Usage: /remove <alias>")
            return

        updated = self.client.remove_session_participant(self.session_id, alias)
        self.session = updated
        self._print(f"Removed participant {alias}")

    def _handle_rename(self, args: tuple[str, ...]) -> None:
        if len(args) != 2:
            self._print("Usage: /rename <old> <new>")
            return

        old_alias = args[0].strip()
        new_alias = args[1].strip()
        if not old_alias or not new_alias:
            self._print("Usage: /rename <old> <new>")
            return

        updated = self.client.rename_session_participant(self.session_id, old_alias, new_alias)
        self.session = updated
        self._print(f"Renamed participant {old_alias} -> {new_alias}")

    def _handle_clone(self, args: tuple[str, ...]) -> None:
        topic = " ".join(args).strip()
        if not topic:
            topic = self.input_fn("Clone topic [same as current]: ").strip()

        cloned = self.client.clone_session(self.session_id, topic=topic or None, mode=self.session.get("mode"))
        new_session_id = str(cloned.get("id") or "").strip()
        if not new_session_id:
            self._print("Clone failed: backend returned no session id.")
            return
        self.session = self.client.get_session(new_session_id)
        self._print(f"Cloned session to {new_session_id}")

    def _handle_setup(self, args: tuple[str, ...]) -> None:
        goal = " ".join(args).strip()
        if not goal:
            goal = self.input_fn("What do you want this session to do? ").strip()
        if not goal:
            self._print("Setup cancelled.")
            return

        plan = build_quick_setup_plan(goal)
        workspace = self.session.get("workspace")
        root_path = str(workspace.get("root_path") or "").strip() if isinstance(workspace, dict) else ""
        if not root_path:
            root_path = self.input_fn("Workspace root path: ").strip()
        if not root_path:
            self._print("Setup cancelled: workspace root is required for code workspace sessions.")
            return

        participants = [
            {
                "custom_id": participant.get("custom_id"),
                "model_ref": participant.get("model_ref"),
                "provider_id": participant.get("provider_id"),
                "role_desc": participant.get("role_desc"),
            }
            for participant in self._session_participants()
        ]
        payload = {
            "topic": self.session.get("topic") or goal,
            "mode": "code_workspace",
            "participants": participants,
            "workspace": {
                "root_path": root_path,
                "display_name": workspace.get("display_name") if isinstance(workspace, dict) else None,
                "scan_excludes": workspace.get("scan_excludes", []) if isinstance(workspace, dict) else [],
                "selected_paths": workspace.get("selected_paths", []) if isinstance(workspace, dict) else [],
                "index_status": "pending",
                "capabilities": build_workspace_capabilities_payload(goal=goal, preset=plan.preset),
            },
        }
        created = self.client.create_session(payload)
        self.session = self.client.get_session(str(created.get("id") or ""))
        self._print(f"Created quick-setup session {created.get('id')} ({plan.preset})")
