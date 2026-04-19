from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .workspace_capabilities import WorkspaceCapabilityManifest


@dataclass
class WorkspaceSkill:
    name: str
    description: str
    summary: str
    path: str
    source_type: str
    source_label: Optional[str] = None


_FRONTMATTER_KEY = re.compile(r"^([A-Za-z0-9_.-]+)\s*:\s*(.+?)\s*$")


def discover_workspace_skills(
    workspace_root: str,
    manifest: Optional[WorkspaceCapabilityManifest],
) -> List[WorkspaceSkill]:
    if manifest is None:
        return []

    discovered: List[WorkspaceSkill] = []
    seen_names = set()
    for source in manifest.skill_sources:
        if not source.enabled:
            continue
        for skill_file in _iter_skill_files(workspace_root, source.path, source.recursive):
            skill = _load_skill(skill_file, source.source_type, source.label)
            if skill is None:
                continue
            normalized_name = skill.name.lower()
            if normalized_name in seen_names:
                continue
            seen_names.add(normalized_name)
            discovered.append(skill)
    return discovered


def select_workspace_skills_for_participant(
    skills: Iterable[WorkspaceSkill],
    manifest: Optional[WorkspaceCapabilityManifest],
    participant_id: str,
) -> List[WorkspaceSkill]:
    skill_list = list(skills)
    if manifest is None:
        return skill_list

    participant_override = manifest.participant_overrides.get(participant_id)
    if participant_override is not None and participant_override.skills:
        return _filter_skills_by_name(skill_list, participant_override.skills)
    if manifest.agent_defaults.allowed_skills:
        return _filter_skills_by_name(skill_list, manifest.agent_defaults.allowed_skills)
    return skill_list


def render_workspace_skill_context(skills: Iterable[WorkspaceSkill]) -> str:
    skill_list = list(skills)
    if not skill_list:
        return "[工作区技能]\n未发现当前参与者可用的技能。\n[工作区技能结束]"

    lines = ["[工作区技能]", "当前参与者可用技能："]
    for skill in skill_list:
        lines.append(f"- {skill.name}: {skill.description}")
        if skill.summary:
            lines.append(f"  摘要: {skill.summary}")
        if skill.source_label:
            lines.append(f"  来源: {skill.source_label}")
    lines.append("[工作区技能结束]")
    return "\n".join(lines)


def _filter_skills_by_name(skills: List[WorkspaceSkill], allowed_names: Iterable[str]) -> List[WorkspaceSkill]:
    allowlist = {str(item).strip().lower() for item in allowed_names if str(item).strip()}
    if not allowlist:
        return skills
    return [skill for skill in skills if skill.name.lower() in allowlist]


def _iter_skill_files(workspace_root: str, configured_path: str, recursive: bool) -> List[Path]:
    root = _resolve_skill_root(workspace_root, configured_path)
    if root is None or not root.exists():
        return []
    if root.is_file():
        return [root] if root.name.upper() == "SKILL.MD" else []
    if recursive:
        return sorted(path for path in root.rglob("SKILL.md") if path.is_file())
    result: List[Path] = []
    direct = root / "SKILL.md"
    if direct.is_file():
        result.append(direct)
    result.extend(sorted(path for path in root.glob("*/SKILL.md") if path.is_file()))
    return result


def _resolve_skill_root(workspace_root: str, configured_path: str) -> Optional[Path]:
    normalized = str(configured_path).strip()
    if not normalized:
        return None
    path = Path(normalized).expanduser()
    if path.is_absolute():
        return path
    return Path(workspace_root).expanduser() / normalized


def _load_skill(skill_file: Path, source_type: str, source_label: Optional[str]) -> Optional[WorkspaceSkill]:
    try:
        raw = skill_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    frontmatter, body = _split_frontmatter(raw)
    metadata = _parse_frontmatter(frontmatter)
    name = str(metadata.get("name") or "").strip() or skill_file.parent.name
    description = str(metadata.get("description") or "").strip() or _extract_summary(body)
    if not name or not description:
        return None
    return WorkspaceSkill(
        name=name,
        description=description,
        summary=_extract_summary(body),
        path=str(skill_file),
        source_type=source_type,
        source_label=source_label,
    )


def _split_frontmatter(raw: str) -> Tuple[str, str]:
    if not raw.startswith("---"):
        return "", raw.strip()
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return "", raw.strip()
    return parts[1].strip(), parts[2].strip()


def _parse_frontmatter(frontmatter: str) -> Dict[str, str]:
    metadata: Dict[str, str] = {}
    for line in frontmatter.splitlines():
        match = _FRONTMATTER_KEY.match(line.strip())
        if not match:
            continue
        key = match.group(1).strip()
        value = match.group(2).strip().strip("\"'")
        if key and value:
            metadata[key] = value
    return metadata


def _extract_summary(body: str) -> str:
    for line in body.splitlines():
        normalized = line.strip()
        if not normalized:
            continue
        if normalized.startswith(("#", "<", "```")):
            continue
        return normalized[:240]
    return ""
