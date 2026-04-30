from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Sequence

from .client import MmdClient
from .catalog import ModelCatalogEntry
from .catalog import load_model_catalogs
from .catalog import format_participant_model_selection
from .shell import MmdShell
from .setup import build_quick_setup_plan
from .setup import build_workspace_capabilities_payload


def _normalize_path(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""

    if text.startswith("/mnt/") and len(text) >= 7 and text[5].isalpha():
        text = f"{text[5].upper()}:/{text[7:]}"
    elif len(text) >= 3 and text[1] == ":" and text[2] == "/":
        text = f"{text[0].upper()}{text[1:]}"

    return os.path.normcase(text)


def _session_workspace_root(session: Dict[str, Any]) -> str:
    workspace = session.get("workspace")
    if isinstance(workspace, dict):
        root_path = str(workspace.get("root_path") or "").strip()
        if root_path:
            return root_path
    return ""


def _default_aliases(preset: str) -> tuple[str, str]:
    normalized = str(preset or "").strip().lower()
    if normalized == "review":
        return ("reviewer", "critic")
    if normalized == "planner":
        return ("planner", "critic")
    if normalized in {"repair", "coder"}:
        return ("coder", "reviewer")
    return ("assistant_a", "assistant_b")


def _bootstrap_candidate_models(catalogs: Sequence[ModelCatalogEntry]) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for catalog in catalogs:
        provider_id = str(catalog.provider_id or "").strip()
        if not provider_id:
            continue
        for model in catalog.models:
            normalized = str(model or "").strip()
            if not normalized:
                continue
            candidates.append((provider_id, normalized))
    return candidates


def _select_bootstrap_participants(
    catalogs: Sequence[ModelCatalogEntry],
    *,
    preset: str,
) -> list[Dict[str, Any]]:
    aliases = _default_aliases(preset)
    candidates = _bootstrap_candidate_models(catalogs)
    if not candidates:
        return []

    selected: list[tuple[str, str]] = []
    seen_providers: set[str] = set()
    for provider_id, model_ref in candidates:
        if provider_id in seen_providers:
            continue
        seen_providers.add(provider_id)
        selected.append((provider_id, model_ref))
        if len(selected) == len(aliases):
            break

    if len(selected) < len(aliases):
        for candidate in candidates:
            if candidate in selected:
                continue
            selected.append(candidate)
            if len(selected) == len(aliases):
                break

    while len(selected) < len(aliases):
        selected.append(selected[-1])

    participants: list[Dict[str, Any]] = []
    for alias, (provider_id, model_ref) in zip(aliases, selected):
        participants.append(
            {
                "custom_id": alias,
                "provider_id": provider_id,
                "model_ref": model_ref,
            }
        )
    return participants


def _build_workspace_payload(
    *,
    cwd: str,
    goal: str,
    preset: str,
) -> Dict[str, Any]:
    normalized_cwd = str(cwd or "").strip()
    if normalized_cwd and (normalized_cwd.startswith(("/", "\\")) or (len(normalized_cwd) >= 2 and normalized_cwd[1] == ":")):
        root_path = normalized_cwd.replace("\\", "/")
    else:
        root_path = str(Path(normalized_cwd).expanduser().resolve(strict=False))
    display_name = Path(root_path).name or root_path
    return {
        "root_path": root_path,
        "display_name": display_name,
        "scan_excludes": [],
        "selected_paths": [],
        "index_status": "pending",
        "capabilities": build_workspace_capabilities_payload(
            goal=goal,
            preset=preset,
        ),
    }


def create_default_workspace_session(
    client: MmdClient,
    *,
    cwd: str,
    goal: str,
    output_fn: Callable[[str], None] = print,
) -> Optional[Dict[str, Any]]:
    preset = build_quick_setup_plan(goal).preset
    catalogs = load_model_catalogs(client)
    participants = _select_bootstrap_participants(catalogs, preset=preset)
    if not participants:
        output_fn("No discovered models were available to bootstrap a session.")
        output_fn("Use `mmd providers` after configuring provider auth, then try again.")
        return None

    output_fn("Creating a new workspace session...")
    output_fn(
        "Auto-selected participants: "
        + ", ".join(
            format_participant_model_selection(
                participant.get("provider_id"),
                str(participant.get("model_ref") or ""),
            )
            for participant in participants
        )
    )

    payload: Dict[str, Any] = {
        "topic": goal,
        "mode": "code_workspace",
        "participants": participants,
        "workspace": _build_workspace_payload(
            cwd=cwd,
            goal=goal,
            preset=preset,
        ),
    }

    created = client.create_session(payload)
    session_id = str(created.get("id") or "").strip()
    if not session_id:
        output_fn("Session creation returned no session id.")
        return None
    return client.get_session(session_id)


def resolve_default_session(
    client: MmdClient,
    *,
    cwd: Optional[str] = None,
    session_id: Optional[str] = None,
    fallback_to_recent: bool = True,
) -> Optional[Dict[str, Any]]:
    if session_id:
        try:
            return client.get_session(session_id)
        except Exception:
            return None

    sessions = client.list_sessions()
    if not sessions:
        return None

    cwd_norm = _normalize_path(cwd or os.getcwd())

    for item in sessions:
        candidate_id = str(item.get("id") or "").strip()
        if not candidate_id:
            continue
        try:
            session = client.get_session(candidate_id)
        except Exception:
            continue
        root_path = _session_workspace_root(session)
        if root_path and _normalize_path(root_path) == cwd_norm:
            return session

    if not fallback_to_recent:
        return None

    first_id = str(sessions[0].get("id") or "").strip()
    if not first_id:
        return None
    try:
        return client.get_session(first_id)
    except Exception:
        return None


def run_default_terminal(
    client: MmdClient,
    *,
    cwd: Optional[str] = None,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    session_id: Optional[str] = None,
) -> int:
    session = resolve_default_session(
        client,
        cwd=cwd,
        session_id=session_id,
        fallback_to_recent=False,
    )
    if session is None:
        output_fn("No session matched this workspace.")
        goal = input_fn("Goal for this workspace [repair current code]: ").strip()
        if not goal:
            goal = "repair current code"
        session = create_default_workspace_session(
            client,
            cwd=cwd or os.getcwd(),
            goal=goal,
            output_fn=output_fn,
        )
        if session is None:
            return 1

    output_fn(
        "Entering session "
        f"{session.get('id')} | {session.get('title') or session.get('topic') or 'session'}"
    )
    MmdShell(client, session, input_fn=input_fn, output_fn=output_fn).run()
    return 0
