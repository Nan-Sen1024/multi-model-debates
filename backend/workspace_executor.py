from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shlex
import shutil
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from .exceptions import ValidationError
from .workspace_agent import resolve_workspace_agent_profile
from .workspace_capabilities import WorkspaceCapabilityManifest
from .workspace_mcp import WorkspaceMCPTool, WorkspaceMCPToolResult
from .workspace_scanner import _resolve_workspace_root, _translate_windows_workspace_root

logger = logging.getLogger(__name__)

WorkspaceToolEventSink = Callable[[Dict[str, object]], Awaitable[None]]

LOCAL_WORKSPACE_SERVER_NAMES = {"workspace", "local"}
DEFAULT_ALLOWED_COMMANDS = {
    "bash",
    "cmd",
    "git",
    "go",
    "make",
    "npx",
    "npm",
    "node",
    "pnpm",
    "python",
    "python3",
    "pytest",
    "powershell",
    "pwsh",
    "sh",
    "uv",
    "cargo",
}


class WorkspaceExecutionRuntime:
    def __init__(
        self,
        workspace_root: Optional[str],
        manifest: Optional[WorkspaceCapabilityManifest],
        mcp_runtime: Any = None,
        allowed_commands: Optional[Iterable[str]] = None,
        command_timeout_seconds: int = 120,
        max_output_chars: int = 20_000,
    ) -> None:
        self.workspace_root = (
            _resolve_workspace_root(workspace_root).resolve(strict=False)
            if workspace_root
            else None
        )
        self.manifest = manifest
        self.mcp_runtime = mcp_runtime
        self.allowed_commands = {
            _normalize_command_name(command)
            for command in (allowed_commands or DEFAULT_ALLOWED_COMMANDS)
            if _normalize_command_name(command)
        }
        self.command_timeout_seconds = max(1, int(command_timeout_seconds))
        self.max_output_chars = max(1, int(max_output_chars))

    async def list_tools(self, participant_id: str) -> List[WorkspaceMCPTool]:
        tools = list(self._local_tools(participant_id))
        if self.mcp_runtime is not None:
            list_tools = getattr(self.mcp_runtime, "list_tools", None)
            if callable(list_tools):
                try:
                    tools.extend(await list_tools(participant_id))
                except Exception:  # pragma: no cover - defensive fallback
                    logger.warning("Failed to list MCP tools for participant %s", participant_id, exc_info=True)
        return tools

    async def render_tool_context(self, participant_id: str) -> str:
        tools = await self.list_tools(participant_id)
        return render_workspace_tool_context(tools)

    async def call_tool(
        self,
        participant_id: str,
        server_name: str,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
        emit_event: Optional[WorkspaceToolEventSink] = None,
    ) -> WorkspaceMCPToolResult:
        if server_name.strip().lower() in LOCAL_WORKSPACE_SERVER_NAMES:
            return await self._call_local_tool(participant_id, tool_name, arguments or {}, emit_event)

        if self.mcp_runtime is None:
            raise ValidationError(
                f"Unsupported workspace tool server: {server_name}",
                field="server_name",
            )

        return await self.mcp_runtime.call_tool(participant_id, server_name, tool_name, arguments or {})

    def _local_tools(self, participant_id: str) -> List[WorkspaceMCPTool]:
        can_write = self._can_write(participant_id)
        tools = [
            WorkspaceMCPTool(
                server_name="workspace",
                name="read_file",
                description="Read a UTF-8 file from the workspace root.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path inside the workspace root."},
                    },
                    "required": ["path"],
                    "additionalProperties": True,
                },
            ),
            WorkspaceMCPTool(
                server_name="workspace",
                name="list_files",
                description="List files or directories under the workspace root.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative directory path, defaults to '.'."},
                        "recursive": {"type": "boolean", "description": "Whether to recurse into subdirectories."},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 500},
                    },
                    "additionalProperties": True,
                },
            ),
        ]
        if can_write:
            tools.extend(
                [
                    WorkspaceMCPTool(
                        server_name="workspace",
                        name="write_file",
                        description="Write a file inside the workspace root. Directories are created automatically.",
                        input_schema={
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "Relative path inside the workspace root."},
                                "content": {"type": "string", "description": "UTF-8 text content to write."},
                                "overwrite": {"type": "boolean", "description": "Overwrite existing files when true."},
                            },
                            "required": ["path", "content"],
                            "additionalProperties": True,
                        },
                    ),
                    WorkspaceMCPTool(
                        server_name="workspace",
                        name="run_command",
                        description=(
                            "Run an allowlisted development command inside the workspace root. "
                            f"Allowed commands: {', '.join(sorted(self.allowed_commands))}"
                        ),
                        input_schema={
                            "type": "object",
                            "properties": {
                                "command": {"type": "string", "description": "Command name, argv string, or executable path to run."},
                                "args": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Command arguments.",
                                },
                                "command_line": {
                                    "type": "string",
                                    "description": "Optional full command line to execute through a requested shell.",
                                },
                                "shell": {
                                    "type": "string",
                                    "description": "Optional shell for command_line: bash, sh, cmd, powershell, or pwsh.",
                                },
                                "os": {
                                    "type": "string",
                                    "description": "Optional target shell family: linux or windows.",
                                },
                                "cwd": {
                                    "type": "string",
                                    "description": "Optional relative working directory inside the workspace root.",
                                },
                                "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": 600},
                            },
                            "additionalProperties": True,
                        },
                    ),
                ]
            )
        return tools

    async def _call_local_tool(
        self,
        participant_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
        emit_event: Optional[WorkspaceToolEventSink] = None,
    ) -> WorkspaceMCPToolResult:
        if tool_name == "read_file":
            return self._read_file(tool_name, arguments)
        if tool_name == "list_files":
            return self._list_files(tool_name, arguments)
        if tool_name == "write_file":
            if not self._can_write(participant_id):
                return self._error_result(
                    tool_name,
                    "write_file is disabled for this participant. Enable workspace agent can_write to allow edits.",
                )
            return self._write_file(tool_name, arguments)
        if tool_name == "run_command":
            if not self._can_write(participant_id):
                return self._error_result(
                    tool_name,
                    "run_command is disabled for this participant. Enable workspace agent can_write to allow commands.",
                )
            return await self._run_command(tool_name, arguments, emit_event)
        return self._error_result(tool_name, f"Unknown workspace tool: {tool_name}")

    def _read_file(self, tool_name: str, arguments: Dict[str, Any]) -> WorkspaceMCPToolResult:
        path_value = _read_path_argument(arguments)
        if path_value is None:
            return self._error_result(tool_name, "read_file requires a non-empty path")

        path = self._resolve_workspace_path(path_value)
        if path is None:
            return self._error_result(tool_name, f"Path escapes workspace root: {path_value}")
        if not path.exists():
            return self._error_result(tool_name, f"File does not exist: {path.relative_to(self.workspace_root).as_posix() if self.workspace_root else path}")
        if not path.is_file():
            return self._error_result(tool_name, f"Not a file: {path.relative_to(self.workspace_root).as_posix() if self.workspace_root else path}")

        content = path.read_text(encoding="utf-8", errors="replace")
        rendered = self._truncate_text(content)
        return self._result(tool_name, rendered)

    def _list_files(self, tool_name: str, arguments: Dict[str, Any]) -> WorkspaceMCPToolResult:
        root = self._workspace_root_or_error(tool_name)
        if root is None:
            return self._error_result(tool_name, "Workspace root is not configured")

        relative_path = _read_path_argument(arguments) or "."
        recursive = bool(arguments.get("recursive", True))
        limit = _safe_int(arguments.get("limit"), default=200, minimum=1, maximum=500)
        base = self._resolve_workspace_path(relative_path)
        if base is None:
            return self._error_result(tool_name, f"Path escapes workspace root: {relative_path}")
        if not base.exists():
            return self._error_result(tool_name, f"Path does not exist: {relative_path}")
        if not base.is_dir():
            return self._error_result(tool_name, f"Not a directory: {relative_path}")

        entries: List[str] = []
        if recursive:
            for current_root, dirnames, filenames in os.walk(base):
                current_path = Path(current_root)
                for name in sorted(dirnames):
                    rel = (current_path / name).relative_to(root).as_posix()
                    entries.append(f"{rel}/")
                    if len(entries) >= limit:
                        break
                if len(entries) >= limit:
                    break
                for name in sorted(filenames):
                    rel = (current_path / name).relative_to(root).as_posix()
                    entries.append(rel)
                    if len(entries) >= limit:
                        break
                if len(entries) >= limit:
                    break
        else:
            for child in sorted(base.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
                rel = child.relative_to(root).as_posix()
                entries.append(f"{rel}/" if child.is_dir() else rel)
                if len(entries) >= limit:
                    break

        suffix = ""
        if len(entries) >= limit:
            suffix = f"\n... truncated at {limit} entries"
        return self._result(tool_name, "\n".join(entries) + suffix if entries else "(empty)")

    def _write_file(self, tool_name: str, arguments: Dict[str, Any]) -> WorkspaceMCPToolResult:
        root = self._workspace_root_or_error(tool_name)
        if root is None:
            return self._error_result(tool_name, "Workspace root is not configured")

        path_value = _read_path_argument(arguments)
        content = arguments.get("content")
        if path_value is None:
            return self._error_result(tool_name, "write_file requires a non-empty path")
        if not isinstance(content, str):
            return self._error_result(tool_name, "write_file requires string content")

        path = self._resolve_workspace_path(path_value)
        if path is None:
            return self._error_result(tool_name, f"Path escapes workspace root: {path_value}")

        overwrite = bool(arguments.get("overwrite", True))
        if path.exists() and not overwrite:
            return self._error_result(tool_name, f"File already exists: {path.relative_to(root).as_posix()}")

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        rel = path.relative_to(root).as_posix()
        return self._result(tool_name, f"Wrote {len(content)} characters to {rel}")

    async def _run_command(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        emit_event: Optional[WorkspaceToolEventSink] = None,
    ) -> WorkspaceMCPToolResult:
        root = self._workspace_root_or_error(tool_name)
        if root is None:
            return self._error_result(tool_name, "Workspace root is not configured")

        argv = self._build_command_argv(arguments)
        if not argv:
            return self._error_result(tool_name, "run_command requires a command")

        command_name = _normalize_command_name(argv[0])
        if command_name not in self.allowed_commands:
            return self._error_result(
                tool_name,
                f"Command '{argv[0]}' is not allowed. Allowed commands: {', '.join(sorted(self.allowed_commands))}",
            )

        cwd = root
        cwd_value = arguments.get("cwd")
        if isinstance(cwd_value, str) and cwd_value.strip():
            resolved_cwd = self._resolve_workspace_path(cwd_value.strip())
            if resolved_cwd is None:
                return self._error_result(tool_name, f"cwd escapes workspace root: {cwd_value}")
            if not resolved_cwd.exists():
                return self._error_result(tool_name, f"cwd does not exist: {cwd_value}")
            if not resolved_cwd.is_dir():
                return self._error_result(tool_name, f"cwd is not a directory: {cwd_value}")
            cwd = resolved_cwd

        timeout_seconds = _safe_int(arguments.get("timeout_seconds"), default=self.command_timeout_seconds, minimum=1, maximum=600)
        command_display = " ".join(argv)

        stdout_parts: List[str] = []
        stderr_parts: List[str] = []
        emitted_chars = 0
        output_sequence = 0
        output_truncated_notice_sent = False

        async def emit_output(stream_name: str, text: str) -> None:
            nonlocal emitted_chars, output_sequence, output_truncated_notice_sent
            if emit_event is None or not text:
                return
            if emitted_chars >= self.max_output_chars:
                if not output_truncated_notice_sent:
                    output_truncated_notice_sent = True
                    output_sequence += 1
                    await emit_event(
                        {
                            "stream": "system",
                            "text": f"... [live output truncated at {self.max_output_chars} characters]\n",
                            "command": command_display,
                            "cwd": cwd.relative_to(root).as_posix() if cwd != root else ".",
                            "sequence": output_sequence,
                        }
                    )
                return
            available = self.max_output_chars - emitted_chars
            rendered = text[:available]
            emitted_chars += len(rendered)
            output_sequence += 1
            await emit_event(
                {
                    "stream": stream_name,
                    "text": rendered,
                    "command": command_display,
                    "cwd": cwd.relative_to(root).as_posix() if cwd != root else ".",
                    "sequence": output_sequence,
                }
            )

        await emit_output("command", f"$ {command_display}\n")

        if os.name != "nt":
            return await self._run_command_with_pty(
                tool_name=tool_name,
                argv=argv,
                cwd=cwd,
                root=root,
                command_display=command_display,
                timeout_seconds=timeout_seconds,
                stdout_parts=stdout_parts,
                emit_output=emit_output,
            )

        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            return self._error_result(tool_name, f"Command not found: {argv[0]}")

        async def read_stream(stream: Optional[asyncio.StreamReader], stream_name: str, parts: List[str]) -> None:
            if stream is None:
                return
            while True:
                chunk = await stream.readline()
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                parts.append(text)
                await emit_output(stream_name, text)

        stdout_task = asyncio.create_task(read_stream(process.stdout, "stdout", stdout_parts))
        stderr_task = asyncio.create_task(read_stream(process.stderr, "stderr", stderr_parts))
        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            timed_out = True
            process.kill()
            await process.wait()
        await asyncio.gather(stdout_task, stderr_task)

        if timed_out:
            await emit_output("system", f"Command timed out after {timeout_seconds} seconds\n")
            return self._error_result(tool_name, f"Command timed out after {timeout_seconds} seconds: {command_display}")

        stdout = self._truncate_text("".join(stdout_parts).strip())
        stderr = self._truncate_text("".join(stderr_parts).strip())
        lines = [
            f"command={command_display}",
            f"cwd={cwd.relative_to(root).as_posix() if cwd != root else '.'}",
            f"exit_code={process.returncode}",
        ]
        if stdout:
            lines.append("stdout:")
            lines.append(stdout)
        if stderr:
            lines.append("stderr:")
            lines.append(stderr)
        if process.returncode != 0 and not stdout and not stderr:
            lines.append("command completed with no output")
        return self._result(tool_name, "\n".join(lines))

    async def _run_command_with_pty(
        self,
        *,
        tool_name: str,
        argv: List[str],
        cwd: Path,
        root: Path,
        command_display: str,
        timeout_seconds: int,
        stdout_parts: List[str],
        emit_output: Callable[[str, str], Awaitable[None]],
    ) -> WorkspaceMCPToolResult:
        import pty

        master_fd: Optional[int] = None
        slave_fd: Optional[int] = None
        try:
            master_fd, slave_fd = pty.openpty()
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=slave_fd,
                stderr=slave_fd,
            )
        except FileNotFoundError:
            if master_fd is not None:
                os.close(master_fd)
            if slave_fd is not None:
                os.close(slave_fd)
            return self._error_result(tool_name, f"Command not found: {argv[0]}")

        if slave_fd is not None:
            os.close(slave_fd)
            slave_fd = None
        if master_fd is not None:
            os.set_blocking(master_fd, False)

        async def read_pty() -> None:
            if master_fd is None:
                return
            idle_after_exit = 0
            while True:
                try:
                    chunk = os.read(master_fd, 4096)
                except BlockingIOError:
                    if process.returncode is not None:
                        idle_after_exit += 1
                        if idle_after_exit >= 5:
                            break
                        await asyncio.sleep(0.02)
                    else:
                        await asyncio.sleep(0.02)
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                idle_after_exit = 0
                text = chunk.decode("utf-8", errors="replace")
                stdout_parts.append(text)
                await emit_output("terminal", text)

        read_task = asyncio.create_task(read_pty())
        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            timed_out = True
            process.kill()
            await process.wait()
        if master_fd is not None and timed_out:
            with contextlib.suppress(OSError):
                os.close(master_fd)
            master_fd = None

        try:
            await asyncio.wait_for(read_task, timeout=1)
        except asyncio.TimeoutError:
            if master_fd is not None:
                with contextlib.suppress(OSError):
                    os.close(master_fd)
                master_fd = None
            read_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await read_task
        if master_fd is not None:
            with contextlib.suppress(OSError):
                os.close(master_fd)

        if timed_out:
            await emit_output("system", f"Command timed out after {timeout_seconds} seconds\n")
            return self._error_result(tool_name, f"Command timed out after {timeout_seconds} seconds: {command_display}")

        stdout = self._truncate_text("".join(stdout_parts).strip())
        lines = [
            f"command={command_display}",
            f"cwd={cwd.relative_to(root).as_posix() if cwd != root else '.'}",
            f"exit_code={process.returncode}",
        ]
        if stdout:
            lines.append("stdout:")
            lines.append(stdout)
        if process.returncode != 0 and not stdout:
            lines.append("command completed with no output")
        return self._result(tool_name, "\n".join(lines))

    def _build_command_argv(self, arguments: Dict[str, Any]) -> List[str]:
        command_line = _read_command_line_argument(arguments)
        if command_line:
            return _build_shell_command_argv(arguments, command_line)

        command = arguments.get("command")
        if isinstance(command, list):
            return [str(item).strip() for item in command if str(item).strip()]
        if isinstance(command, str):
            normalized = command.strip()
            if not normalized:
                return []
            args = arguments.get("args")
            if isinstance(args, list):
                argv = [normalized]
                argv.extend(str(item).strip() for item in args if str(item).strip())
                return argv
            return _split_command_string(normalized)

        argv = arguments.get("argv")
        if isinstance(argv, list):
            return [str(item).strip() for item in argv if str(item).strip()]
        cmd = arguments.get("cmd")
        if isinstance(cmd, str) and cmd.strip():
            return _split_command_string(cmd.strip())
        return []

    def _result(self, tool_name: str, text: str) -> WorkspaceMCPToolResult:
        rendered = self._truncate_text(text)
        return WorkspaceMCPToolResult(
            server_name="workspace",
            tool_name=tool_name,
            text=rendered,
            raw_content=[{"type": "text", "text": rendered}],
        )

    def _error_result(self, tool_name: str, message: str) -> WorkspaceMCPToolResult:
        return self._result(tool_name, f"[error] {message}")

    def _truncate_text(self, text: str) -> str:
        if len(text) <= self.max_output_chars:
            return text
        return f"{text[: self.max_output_chars]}\n... [truncated {len(text) - self.max_output_chars} characters]"

    def _workspace_root_or_error(self, tool_name: str) -> Optional[Path]:
        if self.workspace_root is None:
            logger.warning("workspace root missing for tool %s", tool_name)
            return None
        return self.workspace_root

    def _resolve_workspace_path(self, path_value: str) -> Optional[Path]:
        if self.workspace_root is None:
            return None

        normalized_path_value = _normalize_workspace_path_value(path_value)
        raw_path = Path(normalized_path_value)
        candidate = raw_path if raw_path.is_absolute() else self.workspace_root / raw_path
        resolved = candidate.resolve(strict=False)
        if not _is_within_root(self.workspace_root, resolved):
            return None
        return resolved

    def _can_write(self, participant_id: str) -> bool:
        profile = resolve_workspace_agent_profile(self.manifest, participant_id)
        if profile is None:
            return False
        return bool(profile.can_write)


def render_workspace_tool_context(tools: Iterable[WorkspaceMCPTool]) -> str:
    grouped: Dict[str, List[WorkspaceMCPTool]] = {}
    for tool in tools:
        grouped.setdefault(tool.server_name, []).append(tool)

    if not grouped:
        return ""

    lines = [
        "[workspace tools]",
        "Use a single JSON object with action=tool_call, server, tool, and arguments.",
        "Examples:",
        "{\"action\":\"tool_call\",\"server\":\"workspace\",\"tool\":\"read_file\",\"arguments\":{\"path\":\"README.md\"}}",
    ]

    has_write_tools = any(
        tool.server_name.strip().lower() in LOCAL_WORKSPACE_SERVER_NAMES
        and tool.name in {"write_file", "run_command"}
        for tool in tools
    )
    if has_write_tools:
        lines.extend(
            [
                "[workspace repair workflow]",
                "Inspect with read_file/list_files, edit with write_file, verify with run_command, then repeat until the fix is clean.",
                "When the user asks you to fix, implement, patch, update code, or run local verification, prefer tool_call actions over plain advisory text.",
                "The runner accepts strict tool_call JSON and shorthand JSON: {\"command\":\"pytest -q\"}, {\"command_line\":\"pytest -q\",\"shell\":\"bash\"}, or {\"path\":\"src/app.py\",\"content\":\"...\"}.",
                "Use shell=bash or shell=sh for Linux commands; use shell=cmd, shell=powershell, or os=windows for Windows commands when the host provides those shells.",
                "Windows-style paths such as D:\\repo\\file.py and relative paths with backslashes are accepted, but paths must stay inside the workspace root.",
                "Do not stop at analysis or code review if the task requires an actual code change or a verification command.",
                "After changing code, run a relevant verification command before giving the final answer whenever an allowlisted command can check the result.",
                "If a command fails, inspect the output, update files, and run another verification command instead of ending with a partial diagnosis.",
                "When you are done, respond concisely with: what changed, which files were updated, which commands were run, whether verification passed, and any remaining blocker.",
                "Do not claim the task is finished if verification failed or if you could not run a relevant command.",
                "{\"action\":\"tool_call\",\"server\":\"workspace\",\"tool\":\"write_file\",\"arguments\":{\"path\":\"src/app.py\",\"content\":\"...\"}}",
                "{\"action\":\"tool_call\",\"server\":\"workspace\",\"tool\":\"run_command\",\"arguments\":{\"command\":\"pytest\"}}",
                "{\"action\":\"tool_call\",\"server\":\"workspace\",\"tool\":\"run_command\",\"arguments\":{\"command\":\"python3 -m pytest tests/test_workspace_executor.py -q\"}}",
                "{\"action\":\"tool_call\",\"server\":\"workspace\",\"tool\":\"run_command\",\"arguments\":{\"command_line\":\"dir\",\"shell\":\"cmd\",\"os\":\"windows\"}}",
                "Do not stop after inspection if the task still needs a code change or verification.",
            ]
        )

    for server_name in sorted(grouped.keys()):
        lines.append(f"- server={server_name}")
        for tool in sorted(grouped[server_name], key=lambda item: item.name):
            lines.append(f"  - {tool.name}: {tool.description}")
            summary = _summarize_input_schema(tool.input_schema)
            if summary:
                lines.append(f"    inputs: {summary}")

    return "\n".join(lines)


def _summarize_input_schema(schema: Dict[str, Any]) -> str:
    if not isinstance(schema, dict):
        return ""
    properties = schema.get("properties")
    required = schema.get("required")
    if not isinstance(properties, dict):
        return ""
    required_names = {str(item) for item in required} if isinstance(required, list) else set()
    fragments: List[str] = []
    for name, prop in properties.items():
        if not isinstance(prop, dict):
            continue
        prop_type = prop.get("type")
        type_label = str(prop_type) if isinstance(prop_type, str) else "value"
        fragment = f"{name}:{type_label}"
        if name in required_names:
            fragment += " (required)"
        fragments.append(fragment)
    return ", ".join(fragments)


def _read_path_argument(arguments: Dict[str, Any]) -> Optional[str]:
    path = arguments.get("path")
    if isinstance(path, str):
        normalized = path.strip()
        if normalized:
            return normalized
    return None


def _safe_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        return default
    return max(minimum, min(maximum, parsed))


def _normalize_workspace_path_value(path_value: str) -> str:
    normalized = path_value.strip()
    translated = _translate_windows_workspace_root(normalized)
    if translated:
        return translated
    if "\\" in normalized:
        return normalized.replace("\\", "/")
    return normalized


def _read_command_line_argument(arguments: Dict[str, Any]) -> str:
    for key in ("command_line", "shell_command", "script"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _build_shell_command_argv(arguments: Dict[str, Any], command_line: str) -> List[str]:
    shell = _read_shell_name(arguments)
    if shell in {"cmd", "cmd.exe"}:
        return [_shell_executable("cmd", prefer_windows_suffix=True), "/C", command_line]
    if shell in {"powershell", "powershell.exe"}:
        return [
            _shell_executable("powershell", prefer_windows_suffix=True),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command_line,
        ]
    if shell in {"pwsh", "pwsh.exe"}:
        return [
            _shell_executable("pwsh", prefer_windows_suffix=shell.endswith(".exe")),
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command_line,
        ]
    if shell == "sh":
        return [_shell_executable("sh"), "-c", command_line]
    return [_shell_executable("bash"), "-lc", command_line]


def _read_shell_name(arguments: Dict[str, Any]) -> str:
    requested_shell = str(arguments.get("shell") or "").strip().lower()
    if requested_shell:
        return requested_shell
    requested_system = str(
        arguments.get("os")
        or arguments.get("system")
        or arguments.get("platform")
        or ""
    ).strip().lower()
    if requested_system.startswith("win"):
        return "cmd"
    if requested_system in {"linux", "unix", "posix", "wsl"}:
        return "bash"
    return "bash" if shutil.which("bash") else "sh"


def _shell_executable(name: str, prefer_windows_suffix: bool = False) -> str:
    if prefer_windows_suffix:
        windows_name = f"{name}.exe"
        if shutil.which(windows_name):
            return windows_name
    return name


def _split_command_string(command: str) -> List[str]:
    try:
        return [item for item in shlex.split(command) if item]
    except ValueError:
        return [command]


def _normalize_command_name(command: str) -> str:
    normalized = str(command).strip().lower()
    if not normalized:
        return ""
    basename = Path(normalized).name
    if basename.endswith(".exe"):
        basename = basename[:-4]
    return basename


def _is_within_root(root: Path, candidate: Path) -> bool:
    try:
        return os.path.commonpath([str(root), str(candidate)]) == str(root)
    except ValueError:
        return False
