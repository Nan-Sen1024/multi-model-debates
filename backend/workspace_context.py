from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Set

from .models import ModelParticipant, Session
from .workspace_skills import (
    discover_workspace_skills,
    render_workspace_skill_context,
    select_workspace_skills_for_participant,
)
from .workspace_scanner import WorkspaceScanResult

DEFAULT_WORKSPACE_CONTEXT_FILE_LIMIT = 12
MAX_WORKSPACE_CONTEXT_FILE_LIMIT = 24
MAX_WORKSPACE_INDEX_ENTRIES = 200
MAX_WORKSPACE_FILE_CHARS = 12_000
SOURCE_FILE_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".md",
    ".txt",
    ".sh",
    ".ps1",
    ".java",
    ".go",
    ".rs",
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
}
IMPORTANT_ROOT_FILES = {
    "readme.md",
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "cargo.toml",
    "go.mod",
    ".gitignore",
    "ag ents.md".replace(" ", ""),
}
NOISY_WORKSPACE_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".pyc",
    ".pyo",
}


def build_workspace_file_context(session: Session, scan_result: WorkspaceScanResult) -> str:
    workspace = session.config.workspace
    if workspace is None:
        return "[工作区文件上下文]\n未配置工作区。\n[工作区文件上下文结束]"

    requested_paths = _normalize_selected_paths(workspace.selected_paths)
    filtered_index_files = _filter_workspace_index_files(session, scan_result.files)
    selected_files = _resolve_workspace_context_files(
        requested_paths,
        scan_result.files,
    )
    if not selected_files:
        selected_files = _choose_default_workspace_context_files(filtered_index_files)

    lines: List[str] = ["[工作区文件上下文]"]
    lines.append(f"工作区：{workspace.display_name or scan_result.display_name}")
    lines.append(f"根路径：{scan_result.root_path}")
    if scan_result.summary:
        lines.append(f"扫描摘要：{scan_result.summary}")
    lines.append(f"仓库文件索引（最多 {MAX_WORKSPACE_INDEX_ENTRIES} 项）：")
    lines.extend(f"- {path}" for path in filtered_index_files[:MAX_WORKSPACE_INDEX_ENTRIES])
    if len(filtered_index_files) > MAX_WORKSPACE_INDEX_ENTRIES:
        lines.append(f"... 还有 {len(filtered_index_files) - MAX_WORKSPACE_INDEX_ENTRIES} 个文件")
    lines.append("")
    lines.append("请求路径：")
    if requested_paths:
        lines.extend(f"- {path}" for path in requested_paths)
    else:
        lines.append("- [未指定，自动选择源码文件]")
    lines.append("")
    lines.append("展开文件：")
    lines.extend(f"- {path}" for path in selected_files)
    lines.append("")

    root = Path(scan_result.root_path)
    for rel_path in selected_files:
        file_path = root / rel_path
        lines.append(f"### {rel_path}")
        if not file_path.exists() or not file_path.is_file():
            lines.append("[文件不存在或不可读取]")
            lines.append("")
            continue

        content = file_path.read_text(encoding="utf-8", errors="replace")
        if len(content) > MAX_WORKSPACE_FILE_CHARS:
            content = (
                f"{content[:MAX_WORKSPACE_FILE_CHARS]}\n"
                f"... [文件内容已截断，原始长度 {len(content)} 字符]"
            )
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


def _resolve_workspace_context_files(
    selected_paths: List[str],
    available_files: List[str],
) -> List[str]:
    if not selected_paths:
        return []

    available_set = set(available_files)
    ranked_available = _rank_workspace_paths(available_files)
    expanded: List[str] = []
    seen = set()

    for path in selected_paths:
        normalized = path.rstrip("/")
        if normalized in available_set and normalized not in seen:
            expanded.append(normalized)
            seen.add(normalized)
            if len(expanded) >= MAX_WORKSPACE_CONTEXT_FILE_LIMIT:
                break
            continue

        prefix = f"{normalized}/"
        matching = [item for item in ranked_available if item.startswith(prefix)]
        for match in matching:
            if match in seen:
                continue
            expanded.append(match)
            seen.add(match)
            if len(expanded) >= MAX_WORKSPACE_CONTEXT_FILE_LIMIT:
                break
        if len(expanded) >= MAX_WORKSPACE_CONTEXT_FILE_LIMIT:
            break

    return expanded


def _choose_default_workspace_context_files(available_files: List[str]) -> List[str]:
    ranked = _rank_workspace_paths(available_files)
    return ranked[:DEFAULT_WORKSPACE_CONTEXT_FILE_LIMIT]


def _filter_workspace_index_files(
    session: Session,
    available_files: List[str],
) -> List[str]:
    workspace = session.config.workspace
    if workspace is None:
        return available_files

    excluded_prefixes = _workspace_skill_source_prefixes(session)
    filtered: List[str] = []
    for path in available_files:
        pure_path = Path(path)
        if pure_path.suffix.lower() in NOISY_WORKSPACE_SUFFIXES:
            continue
        if any(path == prefix or path.startswith(f"{prefix}/") for prefix in excluded_prefixes):
            continue
        filtered.append(path)
    return filtered


def _workspace_skill_source_prefixes(session: Session) -> Set[str]:
    workspace = session.config.workspace
    if workspace is None or workspace.capabilities is None:
        return set()

    workspace_root = Path(workspace.root_path).expanduser().resolve()
    prefixes: Set[str] = set()
    for source in workspace.capabilities.skill_sources:
        configured = str(source.path).strip()
        if not configured:
            continue
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            candidate = workspace_root / candidate
        try:
            rel_path = candidate.resolve().relative_to(workspace_root).as_posix().rstrip("/")
        except ValueError:
            continue
        if rel_path:
            prefixes.add(rel_path)
    return prefixes


def _rank_workspace_paths(paths: List[str]) -> List[str]:
    return sorted(paths, key=_workspace_path_priority)


def _workspace_path_priority(path: str) -> tuple[int, int, str]:
    pure_path = Path(path)
    parts = pure_path.parts
    first = parts[0].lower() if parts else ""
    basename = pure_path.name.lower()
    suffix = pure_path.suffix.lower()

    if first in {"backend", "frontend", "src", "app", "lib"} and suffix in SOURCE_FILE_EXTENSIONS:
        return (0, len(parts), path)
    if basename in IMPORTANT_ROOT_FILES:
        return (1, len(parts), path)
    if first == "tests" and suffix in SOURCE_FILE_EXTENSIONS:
        return (2, len(parts), path)
    if suffix in SOURCE_FILE_EXTENSIONS:
        return (3, len(parts), path)
    if basename.startswith("."):
        return (5, len(parts), path)
    return (4, len(parts), path)
