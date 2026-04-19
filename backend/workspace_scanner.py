from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional

from .exceptions import ValidationError

DEFAULT_SCAN_EXCLUDES = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    "out",
    "target",
    "coverage",
}


@dataclass
class WorkspaceTreeEntry:
    name: str
    path: str
    kind: str
    children: List["WorkspaceTreeEntry"] = field(default_factory=list)


@dataclass
class WorkspaceScanResult:
    root_path: str
    display_name: str
    files: List[str]
    tree: List[WorkspaceTreeEntry]
    summary: str
    scanned_at: int
    repo_fingerprint: str


_WINDOWS_DRIVE_PATH = re.compile(r"^([A-Za-z]):[\\/](.+)$")


def _translate_windows_workspace_root(root_path: str) -> Optional[str]:
    normalized = root_path.strip()
    match = _WINDOWS_DRIVE_PATH.match(normalized)
    if not match:
        return None

    drive = match.group(1).lower()
    rest = match.group(2).replace("\\", "/").lstrip("/")
    if not rest:
        return None
    return f"/mnt/{drive}/{rest}"


def _resolve_workspace_root(root_path: str) -> Path:
    raw_root = Path(root_path).expanduser()
    if raw_root.exists():
        return raw_root

    translated_root = _translate_windows_workspace_root(root_path)
    if translated_root:
        candidate = Path(translated_root).expanduser()
        if candidate.exists():
            return candidate

    return raw_root


def scan_workspace(
    root_path: str,
    scan_excludes: Optional[Iterable[str]] = None,
) -> WorkspaceScanResult:
    root = _resolve_workspace_root(root_path)
    if not root.exists():
        raise ValidationError(f"工作区路径不存在：{root_path}", field="workspace.root_path")
    if not root.is_dir():
        raise ValidationError(f"工作区路径不是目录：{root_path}", field="workspace.root_path")

    excluded_names = {item.strip() for item in DEFAULT_SCAN_EXCLUDES}
    if scan_excludes is not None:
        excluded_names.update(item.strip() for item in scan_excludes if str(item).strip())

    normalized_root = root.resolve()
    files: List[str] = []
    tree_map: dict[str, WorkspaceTreeEntry] = {}

    for current_root, dirnames, filenames in os.walk(normalized_root):
        dirnames[:] = sorted(
            dirname for dirname in dirnames if dirname not in excluded_names
        )
        rel_dir = Path(current_root).relative_to(normalized_root)

        for filename in sorted(filenames):
            if filename in excluded_names:
                continue
            file_path = Path(current_root, filename)
            rel_path = file_path.relative_to(normalized_root).as_posix()
            files.append(rel_path)
            _insert_tree_entry(tree_map, rel_path)

    files.sort()
    tree = _tree_entries_from_map(tree_map)
    scanned_at = int(time.time())
    fingerprint_source = "\n".join(files).encode("utf-8")
    repo_fingerprint = hashlib.sha1(fingerprint_source).hexdigest() if files else hashlib.sha1(normalized_root.as_posix().encode("utf-8")).hexdigest()
    summary = f"{len(files)} 个文件，{len(tree)} 个顶层目录/文件"

    return WorkspaceScanResult(
        root_path=normalized_root.as_posix(),
        display_name=normalized_root.name or normalized_root.as_posix(),
        files=files,
        tree=tree,
        summary=summary,
        scanned_at=scanned_at,
        repo_fingerprint=repo_fingerprint,
    )


def _insert_tree_entry(tree_map: dict[str, WorkspaceTreeEntry], rel_path: str) -> None:
    parts = rel_path.split("/")
    current_path = ""
    parent: Optional[WorkspaceTreeEntry] = None

    for index, part in enumerate(parts):
        current_path = part if not current_path else f"{current_path}/{part}"
        node = tree_map.get(current_path)
        kind = "file" if index == len(parts) - 1 else "dir"
        if node is None:
            node = WorkspaceTreeEntry(
                name=part,
                path=current_path,
                kind=kind,
            )
            tree_map[current_path] = node
            if parent is not None:
                parent.children.append(node)
        parent = node


def _tree_entries_from_map(tree_map: dict[str, WorkspaceTreeEntry]) -> List[WorkspaceTreeEntry]:
    top_level = [entry for path, entry in tree_map.items() if "/" not in path]
    return sorted(top_level, key=lambda item: (item.kind != "dir", item.name.lower()))
