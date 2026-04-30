from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, MutableMapping


@dataclass(frozen=True)
class QuickSetupPlan:
    preset: str
    mode: str
    can_write: bool
    max_steps: int


def infer_preset(goal: str | None) -> str:
    text = str(goal or "").strip().lower()
    if any(token in text for token in ("fix", "repair", "bug", "patch", "refactor", "run pytest", "test")):
        return "repair"
    if any(token in text for token in ("review", "audit", "inspect", "critique", "security")):
        return "review"
    if any(token in text for token in ("plan", "brainstorm", "design", "spec")):
        return "planner"
    return "coder"


def build_quick_setup_plan(goal: str | None, preset: str | None = None) -> QuickSetupPlan:
    resolved_preset = (preset or infer_preset(goal)).strip().lower() or "coder"
    if resolved_preset == "review":
        return QuickSetupPlan(preset="review", mode="disabled", can_write=False, max_steps=3)
    if resolved_preset == "planner":
        return QuickSetupPlan(preset="planner", mode="disabled", can_write=False, max_steps=2)
    if resolved_preset == "repair":
        return QuickSetupPlan(preset="repair", mode="tool_loop", can_write=True, max_steps=6)
    return QuickSetupPlan(preset="coder", mode="tool_loop", can_write=True, max_steps=6)


def build_workspace_capabilities_payload(
    *,
    goal: str | None = None,
    preset: str | None = None,
    skill_sources: Iterable[str] = (),
    mcp_servers: Iterable[MutableMapping[str, object]] = (),
) -> MutableMapping[str, object]:
    plan = build_quick_setup_plan(goal, preset)
    return {
        "skill_sources": [
            {
                "path": str(path).strip(),
                "source_type": "local",
                "enabled": True,
            }
            for path in skill_sources
            if str(path).strip()
        ],
        "mcp_servers": [
            dict(server)
            for server in mcp_servers
            if isinstance(server, dict)
        ],
        "agent_defaults": {
            "mode": plan.mode,
            "max_steps": plan.max_steps,
            "can_write": plan.can_write,
            "allowed_skills": [],
            "allowed_mcp_servers": [],
            "memory_scope": "workspace_shared",
        },
        "participant_overrides": {},
    }
