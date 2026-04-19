from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

from .models import ModelParticipant, Session
from .workspace_skills import (
    discover_workspace_skills,
    render_workspace_skill_context,
    select_workspace_skills_for_participant,
)
from .workspace_scanner import WorkspaceScanResult


def build_workspace_file_context(session: Session, scan_result: WorkspaceScanResult) -> str:
    workspace = session.config.workspace
    if workspace is None:
        return "[工作区文件上下文]\n未配置工作区。\n[工作区文件上下文结束]"

    selected_paths = _normalize_selected_paths(workspace.selected_paths)
    if not selected_paths:
        selected_paths = scan_result.files[:10]

    lines: List[str] = ["[工作区文件上下文]"]
    lines.append(f"工作区：{workspace.display_name or scan_result.display_name}")
    lines.append(f"根路径：{scan_result.root_path}")
    if scan_result.summary:
        lines.append(f"扫描摘要：{scan_result.summary}")
    lines.append("选中文件：")
    lines.extend(f"- {path}" for path in selected_paths)
    lines.append("")

    root = Path(scan_result.root_path)
    for rel_path in selected_paths:
        file_path = root / rel_path
        lines.append(f"### {rel_path}")
        if not file_path.exists() or not file_path.is_file():
            lines.append("[文件不存在或不可读取]")
            lines.append("")
            continue

        content = file_path.read_text(encoding="utf-8", errors="replace")
        lines.append("```text")
        lines.append(content)
        lines.append("```")
        lines.append("")

    lines.append("[工作区文件上下文结束]")
    return "\n".join(lines)


def build_workspace_skill_context(session: Session, participant: ModelParticipant) -> str:
    workspace = session.config.workspace
    if workspace is None or workspace.capabilities is None or not workspace.capabilities.skill_sources:
        return ""

    discovered = discover_workspace_skills(workspace.root_path, workspace.capabilities)
    selected = select_workspace_skills_for_participant(
        discovered,
        workspace.capabilities,
        participant.custom_id,
    )
    return render_workspace_skill_context(selected)


def _normalize_selected_paths(paths: Iterable[str]) -> List[str]:
    result: List[str] = []
    seen = set()
    for path in paths:
        normalized = str(path).strip().replace("\\", "/")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result
