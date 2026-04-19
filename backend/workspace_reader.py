from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .exceptions import ValidationError
from .workspace_scanner import WorkspaceScanResult

MAX_WORKSPACE_FILE_VIEW_CHARS = 50_000


@dataclass
class WorkspaceFileView:
    path: str
    content: str
    truncated: bool


def read_workspace_file(
    scan_result: WorkspaceScanResult,
    relative_path: str,
) -> WorkspaceFileView:
    normalized = _normalize_workspace_relative_path(relative_path)
    if normalized not in set(scan_result.files):
        raise ValidationError(
            f"工作区文件不存在：{relative_path}",
            field="workspace.path",
        )

    file_path = Path(scan_result.root_path) / normalized
    if not file_path.exists() or not file_path.is_file():
        raise ValidationError(
            f"工作区文件不可读取：{relative_path}",
            field="workspace.path",
        )

    content = file_path.read_text(encoding="utf-8", errors="replace")
    truncated = len(content) > MAX_WORKSPACE_FILE_VIEW_CHARS
    if truncated:
        content = (
            f"{content[:MAX_WORKSPACE_FILE_VIEW_CHARS]}\n"
            f"... [文件内容已截断，原始长度 {len(content)} 字符]"
        )

    return WorkspaceFileView(
        path=normalized,
        content=content,
        truncated=truncated,
    )


def _normalize_workspace_relative_path(relative_path: str) -> str:
    normalized = str(relative_path).strip().replace("\\", "/").strip("/")
    if not normalized:
        raise ValidationError("工作区文件路径不能为空。", field="workspace.path")

    pure_path = PurePosixPath(normalized)
    if pure_path.is_absolute() or ".." in pure_path.parts:
        raise ValidationError("工作区文件路径非法。", field="workspace.path")

    return pure_path.as_posix()
