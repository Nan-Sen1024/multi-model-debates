from __future__ import annotations

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Dict, Iterable, List, Optional

from .exceptions import ProviderUnavailableError, ValidationError
from .workspace_capabilities import MCPServerConfig, WorkspaceCapabilityManifest

try:
    from mcp import ClientSession, StdioServerParameters  # type: ignore
    from mcp.client.stdio import stdio_client  # type: ignore
    from mcp.client.streamable_http import streamable_http_client  # type: ignore

    _MCP_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    ClientSession = None  # type: ignore[assignment]
    StdioServerParameters = None  # type: ignore[assignment]
    stdio_client = None  # type: ignore[assignment]
    streamable_http_client = None  # type: ignore[assignment]
    _MCP_AVAILABLE = False


@dataclass
class WorkspaceMCPTool:
    server_name: str
    name: str
    description: str
    input_schema: Dict[str, Any]


@dataclass
class WorkspaceMCPToolResult:
    server_name: str
    tool_name: str
    text: str
    raw_content: List[Any]


SessionOpener = Callable[[MCPServerConfig], Any]


class WorkspaceMCPRuntime:
    def __init__(
        self,
        manifest: Optional[WorkspaceCapabilityManifest],
        session_opener: Optional[SessionOpener] = None,
    ) -> None:
        self.manifest = manifest
        self._session_opener = session_opener or self._open_session

    async def list_tools(self, participant_id: str) -> List[WorkspaceMCPTool]:
        tools: List[WorkspaceMCPTool] = []
        for server_config in self._allowed_servers(participant_id):
            async with self._session_opener(server_config) as session:
                await _initialize_session(session)
                result = await session.list_tools()
                for tool in _extract_tools(result):
                    if not _tool_is_allowed(server_config, _tool_name(tool)):
                        continue
                    tools.append(
                        WorkspaceMCPTool(
                            server_name=server_config.name,
                            name=_tool_name(tool),
                            description=_tool_description(tool),
                            input_schema=_tool_input_schema(tool),
                        )
                    )
        return tools

    async def call_tool(
        self,
        participant_id: str,
        server_name: str,
        tool_name: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> WorkspaceMCPToolResult:
        server_config = self._resolve_server(participant_id, server_name)
        if not _tool_is_allowed(server_config, tool_name):
            raise ValidationError(
                f"MCP 工具 {tool_name} 未被允许，server={server_name}",
                field="tool_name",
            )

        async with self._session_opener(server_config) as session:
            await _initialize_session(session)
            available_tools = {
                _tool_name(tool)
                for tool in _extract_tools(await session.list_tools())
                if _tool_is_allowed(server_config, _tool_name(tool))
            }
            if tool_name not in available_tools:
                raise ValidationError(
                    f"MCP 工具 {tool_name} 不存在或当前参与者不可用，server={server_name}",
                    field="tool_name",
                )

            result = await session.call_tool(tool_name, arguments or {})
            content = list(getattr(result, "content", []) or [])
            return WorkspaceMCPToolResult(
                server_name=server_name,
                tool_name=tool_name,
                text=_normalize_tool_result_text(content),
                raw_content=content,
            )

    def _allowed_servers(self, participant_id: str) -> List[MCPServerConfig]:
        if self.manifest is None:
            return []

        enabled_servers = [server for server in self.manifest.mcp_servers if server.enabled]
        if not enabled_servers:
            return []

        participant_override = self.manifest.participant_overrides.get(participant_id)
        if participant_override is not None and participant_override.mcp_servers:
            allowed_names = {name.strip() for name in participant_override.mcp_servers if name.strip()}
            return [server for server in enabled_servers if server.name in allowed_names]

        allowed_defaults = {
            name.strip()
            for name in self.manifest.agent_defaults.allowed_mcp_servers
            if name.strip()
        }
        if allowed_defaults:
            return [server for server in enabled_servers if server.name in allowed_defaults]
        return enabled_servers

    def _resolve_server(self, participant_id: str, server_name: str) -> MCPServerConfig:
        for server in self._allowed_servers(participant_id):
            if server.name == server_name:
                return server
        raise ValidationError(
            f"MCP server {server_name} 对当前参与者不可用",
            field="server_name",
        )

    @asynccontextmanager
    async def _open_session(self, server_config: MCPServerConfig) -> AsyncIterator[Any]:
        if not _MCP_AVAILABLE:
            raise ProviderUnavailableError(
                "mcp 依赖未安装，无法连接 MCP server",
                provider=server_config.name,
            )

        transport = server_config.transport.strip().lower()
        if transport == "stdio":
            if not server_config.command:
                raise ValidationError(
                    f"MCP stdio server {server_config.name} 缺少 command",
                    field="command",
                )
            server_params = StdioServerParameters(  # type: ignore[operator]
                command=server_config.command,
                args=list(server_config.args),
                env=dict(server_config.env),
            )
            async with stdio_client(server_params) as (read_stream, write_stream):  # type: ignore[misc]
                async with ClientSession(read_stream, write_stream) as session:  # type: ignore[misc]
                    yield session
            return

        if transport in {"streamable_http", "streamable-http", "http"}:
            if not server_config.url:
                raise ValidationError(
                    f"MCP HTTP server {server_config.name} 缺少 url",
                    field="url",
                )
            async with streamable_http_client(server_config.url) as (  # type: ignore[misc]
                read_stream,
                write_stream,
                _,
            ):
                async with ClientSession(read_stream, write_stream) as session:  # type: ignore[misc]
                    yield session
            return

        raise ValidationError(
            f"暂不支持的 MCP transport: {server_config.transport}",
            field="transport",
        )


async def _initialize_session(session: Any) -> None:
    initialize = getattr(session, "initialize", None)
    if callable(initialize):
        await initialize()


def _extract_tools(result: Any) -> List[Any]:
    tools = getattr(result, "tools", None)
    if isinstance(tools, list):
        return tools
    return []


def _tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", "") or "").strip()


def _tool_description(tool: Any) -> str:
    return str(getattr(tool, "description", "") or "").strip()


def _tool_input_schema(tool: Any) -> Dict[str, Any]:
    schema = getattr(tool, "inputSchema", None)
    if isinstance(schema, dict):
        return schema
    return {"type": "object", "properties": {}}


def _tool_is_allowed(server_config: MCPServerConfig, tool_name: str) -> bool:
    if not tool_name:
        return False
    if not server_config.tools_allowlist:
        return True
    return tool_name in server_config.tools_allowlist


def _normalize_tool_result_text(content: Iterable[Any]) -> str:
    parts: List[str] = []
    for item in content:
        if isinstance(item, str):
            normalized = item.strip()
            if normalized:
                parts.append(normalized)
            continue

        text = getattr(item, "text", None)
        if isinstance(text, str) and text.strip():
            parts.append(text.strip())
            continue

        if isinstance(item, dict):
            if isinstance(item.get("text"), str) and item["text"].strip():
                parts.append(item["text"].strip())
                continue
            parts.append(json.dumps(item, ensure_ascii=False))
            continue

        parts.append(str(item))
    return "\n".join(part for part in parts if part)
