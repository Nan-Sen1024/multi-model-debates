from __future__ import annotations

import argparse
import os
import sys
from typing import Any, Dict, Iterable, List

from .catalog import ModelResolutionError
from .catalog import load_model_catalogs
from .catalog import resolve_participant_model_selection
from .client import MmdClient
from .client import MmdClientError
from .commands import parse_participant_spec
from .launcher import run_default_terminal
from .launcher import resolve_default_session
from .launcher import _select_bootstrap_participants
from .setup import build_quick_setup_plan
from .setup import build_workspace_capabilities_payload
from .setup import infer_preset
from .shell import MmdShell


CLI_HELP_EPILOG = """Quick start:
  mmd
  mmd shell
  mmd new --goal "repair backend llm gateway" --workspace-root .
  mmd attach <session_id>

In-session:
  /add <alias> <provider/model or unique bare model>
  /to <alias...>
  /status
  /model [status|set|clear]
  /rename <old> <new>
  /remove <alias>
  /clone [topic]
  /setup [goal]

Alias rules:
  @alias targets a session participant.
  @alias on its own changes the current focus.
  @all broadcasts to every active participant.
  Aliases are case-insensitive.
  Unknown aliases fail loudly.

Model rules:
  provider/model is canonical.
  Bare model names auto-bind only when they match one provider uniquely.
  /add <alias> uses the current session default model when you omit the model.
"""


def _build_participant_payloads(
    client: MmdClient,
    participant_specs: Iterable[str],
) -> List[Dict[str, Any]]:
    catalogs = load_model_catalogs(client)
    payloads: List[Dict[str, Any]] = []
    for spec in participant_specs:
        parsed = parse_participant_spec(spec)
        try:
            resolved = resolve_participant_model_selection(
                catalogs,
                {"model_ref": parsed.model_ref},
            )
        except ModelResolutionError as exc:
            raise SystemExit(
                f"Cannot resolve model '{parsed.model_ref}': {exc}. Use provider/model explicitly."
            ) from exc
        payload: Dict[str, Any] = {
            "custom_id": parsed.alias,
            "model_ref": resolved["model_ref"],
        }
        if resolved.get("provider_id"):
            payload["provider_id"] = resolved["provider_id"]
        if parsed.role_desc:
            payload["role_desc"] = parsed.role_desc
        payloads.append(payload)
    return payloads


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mmd",
        description="multi-model-debates terminal client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=CLI_HELP_EPILOG,
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="Backend API base URL")
    parser.add_argument("--timeout", type=float, default=30.0, help="HTTP timeout in seconds")
    subparsers = parser.add_subparsers(dest="command", required=False)

    subparsers.add_parser("sessions", help="List sessions")
    subparsers.add_parser("providers", help="List providers")
    subparsers.add_parser("status", help="Show current session status")
    models_parser = subparsers.add_parser("models", help="List or manage the current session default model")
    models_parser.add_argument("action", nargs="?", default="list", help="list / status / set / clear")
    models_parser.add_argument("model_ref", nargs="?", default="", help="provider/model or bare model")

    attach_parser = subparsers.add_parser("attach", help="Attach to a session")
    attach_parser.add_argument("session_id")

    shell_parser = subparsers.add_parser("shell", help="Enter the terminal shell")
    shell_parser.add_argument("session_id", nargs="?", default="", help="Optional session id")

    new_parser = subparsers.add_parser("new", help="Create a quick-setup session and attach")
    new_parser.add_argument("--topic", default="", help="Session topic")
    new_parser.add_argument("--goal", default="", help="Natural language goal")
    new_parser.add_argument("--mode", default="", help="Session mode override")
    new_parser.add_argument("--preset", default="", help="review / planner / repair / coder")
    new_parser.add_argument("--workspace-root", default="", help="Workspace root path")
    new_parser.add_argument("--participant", action="append", default=[], help="alias=model or alias provider/model")
    new_parser.add_argument("--skill-source", action="append", default=[], help="Skill source path")

    return parser


def cmd_sessions(client: MmdClient) -> int:
    sessions = client.list_sessions()
    if not sessions:
        print("No sessions.")
        return 0
    for session in sessions:
        title = session.get("title") or session.get("topic") or "session"
        print(
            f"{session.get('id')} | {title} | mode={session.get('mode')} | "
            f"round={session.get('current_round')} | participants={session.get('participant_count')}"
        )
    return 0


def cmd_providers(client: MmdClient) -> int:
    providers = client.list_providers()
    if not providers:
        print("No providers.")
        return 0
    for provider in providers:
        print(
            f"{provider.get('id')} | {provider.get('name')} | "
            f"{provider.get('provider_type')} | auth={provider.get('auth_status')}"
        )
    return 0


def _cmd_models_list(client: MmdClient, *, output_fn=print) -> int:
    catalogs = load_model_catalogs(client)
    if not catalogs:
        output_fn("No discovered models.")
        return 0
    for catalog in catalogs:
        output_fn(f"{catalog.provider_id} | {catalog.provider_name}")
        for model in catalog.models:
            output_fn(f"  - {model}")
    return 0


def _resolve_terminal_session(
    client: MmdClient,
    *,
    cwd: str | None = None,
    session_id: str | None = None,
    output_fn=print,
):
    session = resolve_default_session(
        client,
        cwd=cwd,
        session_id=session_id,
        fallback_to_recent=False,
    )
    if session is None:
        output_fn("No session matched this workspace.")
    return session


def _run_session_shell_command(
    client: MmdClient,
    command: str,
    *,
    cwd: str | None = None,
    session_id: str | None = None,
    output_fn=print,
) -> int:
    session = _resolve_terminal_session(
        client,
        cwd=cwd,
        session_id=session_id,
        output_fn=output_fn,
    )
    if session is None:
        return 1
    shell = MmdShell(client, session, input_fn=lambda prompt: "", output_fn=output_fn)
    shell.handle_line(command)
    return 0


def cmd_status(
    client: MmdClient,
    cwd: str | None = None,
    session_id: str | None = None,
    output_fn=print,
) -> int:
    return _run_session_shell_command(
        client,
        "/status",
        cwd=cwd,
        session_id=session_id,
        output_fn=output_fn,
    )


def cmd_shell(
    client: MmdClient,
    cwd: str | None = None,
    session_id: str | None = None,
    input_fn=input,
    output_fn=print,
) -> int:
    return run_default_terminal(
        client,
        cwd=cwd or os.getcwd(),
        input_fn=input_fn,
        output_fn=output_fn,
        session_id=session_id,
    )


def cmd_models(
    client: MmdClient,
    action: str = "list",
    model_ref: str = "",
    cwd: str | None = None,
    session_id: str | None = None,
    output_fn=print,
) -> int:
    normalized_action = str(action or "list").strip().lower()
    if normalized_action in {"", "list"}:
        return _cmd_models_list(client, output_fn=output_fn)
    if normalized_action == "status":
        return _run_session_shell_command(
            client,
            "/model status",
            cwd=cwd,
            session_id=session_id,
            output_fn=output_fn,
        )
    if normalized_action == "clear":
        return _run_session_shell_command(
            client,
            "/model clear",
            cwd=cwd,
            session_id=session_id,
            output_fn=output_fn,
        )
    if normalized_action == "set":
        normalized_model_ref = str(model_ref or "").strip()
        if not normalized_model_ref:
            raise SystemExit("mmd models set requires a model reference")
        return _run_session_shell_command(
            client,
            f"/model set {normalized_model_ref}",
            cwd=cwd,
            session_id=session_id,
            output_fn=output_fn,
        )
    raise SystemExit(f"Unknown models action: {action}")


def _create_quick_session_payload(
    *,
    topic: str,
    goal: str,
    mode: str,
    preset: str,
    workspace_root: str,
    participant_specs: Iterable[str],
    participants: Iterable[Dict[str, Any]] | None,
    skill_sources: Iterable[str],
    client: MmdClient,
) -> Dict[str, Any]:
    resolved_participants = (
        [dict(item) for item in participants]
        if participants is not None
        else _build_participant_payloads(client, participant_specs)
    )
    resolved_preset = (preset or infer_preset(goal)).strip().lower()
    resolved_mode = (mode or "").strip() or ("code_workspace" if workspace_root else "chat")
    workspace = None
    if workspace_root:
        workspace = {
            "root_path": workspace_root,
            "display_name": None,
            "scan_excludes": [],
            "selected_paths": [],
            "index_status": "pending",
            "capabilities": build_workspace_capabilities_payload(
                goal=goal or topic,
                preset=resolved_preset,
                skill_sources=skill_sources,
            ),
        }
    return {
        "topic": topic or goal or "Untitled session",
        "mode": resolved_mode,
        "participants": resolved_participants,
        "workspace": workspace,
    }


def cmd_new(client: MmdClient, args: argparse.Namespace) -> int:
    participant_specs = list(args.participant)
    if participant_specs:
        participants = _build_participant_payloads(client, participant_specs)
    else:
        catalogs = load_model_catalogs(client)
        resolved_preset = (args.preset or infer_preset(args.goal)).strip().lower()
        participants = _select_bootstrap_participants(catalogs, preset=resolved_preset)
        if not participants:
            raise SystemExit(
                "No participants were provided and no discovered models were available. "
                "Configure at least one provider, or pass --participant alias=model."
            )

    payload = _create_quick_session_payload(
        topic=args.topic,
        goal=args.goal,
        mode=args.mode,
        preset=args.preset,
        workspace_root=args.workspace_root,
        participant_specs=participant_specs,
        participants=participants,
        skill_sources=args.skill_source,
        client=client,
    )
    created = client.create_session(payload)
    session_id = str(created.get("id") or "")
    shell = MmdShell(client, client.get_session(session_id))
    shell.run()
    return 0


def cmd_attach(client: MmdClient, session_id: str) -> int:
    shell = MmdShell(client, client.get_session(session_id))
    shell.run()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        with MmdClient(base_url=args.base_url, timeout=args.timeout, trust_env=False) as client:
            if not args.command:
                return run_default_terminal(
                    client,
                    cwd=os.getcwd(),
                    session_id=os.environ.get("MMD_SESSION_ID"),
                )
            if args.command == "sessions":
                return cmd_sessions(client)
            if args.command == "status":
                return cmd_status(
                    client,
                    cwd=os.getcwd(),
                    session_id=os.environ.get("MMD_SESSION_ID"),
                )
            if args.command == "providers":
                return cmd_providers(client)
            if args.command == "models":
                return cmd_models(
                    client,
                    action=args.action,
                    model_ref=args.model_ref,
                    cwd=os.getcwd(),
                    session_id=os.environ.get("MMD_SESSION_ID"),
                )
            if args.command == "attach":
                return cmd_attach(client, args.session_id)
            if args.command == "shell":
                return cmd_shell(
                    client,
                    cwd=os.getcwd(),
                    session_id=(args.session_id or os.environ.get("MMD_SESSION_ID")),
                )
            if args.command == "new":
                return cmd_new(client, args)
    except MmdClientError as exc:
        print(f"mmd: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
