from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class SkillSourceConfig:
    path: str
    source_type: str = "local"
    label: Optional[str] = None
    recursive: bool = True
    enabled: bool = True


@dataclass
class MCPServerConfig:
    name: str
    transport: str
    command: Optional[str] = None
    args: List[str] = field(default_factory=list)
    url: Optional[str] = None
    env: Dict[str, str] = field(default_factory=dict)
    tools_allowlist: List[str] = field(default_factory=list)
    enabled: bool = True


@dataclass
class AgentProfileConfig:
    mode: str = "tool_loop"
    max_steps: int = 6
    can_write: bool = False
    allowed_skills: List[str] = field(default_factory=list)
    allowed_mcp_servers: List[str] = field(default_factory=list)
    memory_scope: str = "workspace_shared"


@dataclass
class ParticipantCapabilityConfig:
    agent: Optional[AgentProfileConfig] = None
    skills: List[str] = field(default_factory=list)
    mcp_servers: List[str] = field(default_factory=list)


@dataclass
class WorkspaceCapabilityManifest:
    skill_sources: List[SkillSourceConfig] = field(default_factory=list)
    mcp_servers: List[MCPServerConfig] = field(default_factory=list)
    agent_defaults: AgentProfileConfig = field(default_factory=AgentProfileConfig)
    participant_overrides: Dict[str, ParticipantCapabilityConfig] = field(default_factory=dict)


def _normalize_str_list(values: object) -> List[str]:
    if not isinstance(values, list):
        return []
    result: List[str] = []
    for item in values:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if normalized:
            result.append(normalized)
    return result


def _skill_source_from_dict(payload: object) -> Optional[SkillSourceConfig]:
    if not isinstance(payload, dict):
        return None
    path = str(payload.get("path") or "").strip()
    if not path:
        return None
    return SkillSourceConfig(
        path=path,
        source_type=str(payload.get("source_type") or "local"),
        label=str(payload["label"]).strip() if isinstance(payload.get("label"), str) and str(payload.get("label")).strip() else None,
        recursive=bool(payload.get("recursive", True)),
        enabled=bool(payload.get("enabled", True)),
    )


def _mcp_server_from_dict(payload: object) -> Optional[MCPServerConfig]:
    if not isinstance(payload, dict):
        return None
    name = str(payload.get("name") or "").strip()
    transport = str(payload.get("transport") or "").strip()
    if not name or not transport:
        return None
    env = payload.get("env")
    return MCPServerConfig(
        name=name,
        transport=transport,
        command=str(payload["command"]).strip() if isinstance(payload.get("command"), str) and str(payload.get("command")).strip() else None,
        args=_normalize_str_list(payload.get("args")),
        url=str(payload["url"]).strip() if isinstance(payload.get("url"), str) and str(payload.get("url")).strip() else None,
        env={str(key): str(value) for key, value in env.items()} if isinstance(env, dict) else {},
        tools_allowlist=_normalize_str_list(payload.get("tools_allowlist")),
        enabled=bool(payload.get("enabled", True)),
    )


def _agent_profile_from_dict(payload: object) -> Optional[AgentProfileConfig]:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        return None
    return AgentProfileConfig(
        mode=str(payload.get("mode") or "tool_loop"),
        max_steps=int(payload.get("max_steps", 6)),
        can_write=bool(payload.get("can_write", False)),
        allowed_skills=_normalize_str_list(payload.get("allowed_skills")),
        allowed_mcp_servers=_normalize_str_list(payload.get("allowed_mcp_servers")),
        memory_scope=str(payload.get("memory_scope") or "workspace_shared"),
    )


def _participant_capability_from_dict(payload: object) -> Optional[ParticipantCapabilityConfig]:
    if not isinstance(payload, dict):
        return None
    agent = _agent_profile_from_dict(payload.get("agent"))
    skills = _normalize_str_list(payload.get("skills"))
    servers = _normalize_str_list(payload.get("mcp_servers"))
    if agent is None and not skills and not servers:
        return None
    return ParticipantCapabilityConfig(
        agent=agent,
        skills=skills,
        mcp_servers=servers,
    )


def workspace_capabilities_from_dict(payload: object) -> Optional[WorkspaceCapabilityManifest]:
    if not isinstance(payload, dict):
        return None

    skill_sources = [
        item
        for item in (
            _skill_source_from_dict(entry)
            for entry in payload.get("skill_sources", [])
        )
        if item is not None
    ]
    mcp_servers = [
        item
        for item in (
            _mcp_server_from_dict(entry)
            for entry in payload.get("mcp_servers", [])
        )
        if item is not None
    ]
    agent_defaults = _agent_profile_from_dict(payload.get("agent_defaults")) or AgentProfileConfig()
    participant_overrides_payload = payload.get("participant_overrides")
    participant_overrides: Dict[str, ParticipantCapabilityConfig] = {}
    if isinstance(participant_overrides_payload, dict):
        for key, value in participant_overrides_payload.items():
            participant = _participant_capability_from_dict(value)
            if participant is None:
                continue
            normalized_key = str(key).strip()
            if normalized_key:
                participant_overrides[normalized_key] = participant

    if not skill_sources and not mcp_servers and not participant_overrides and agent_defaults == AgentProfileConfig():
        return None

    return WorkspaceCapabilityManifest(
        skill_sources=skill_sources,
        mcp_servers=mcp_servers,
        agent_defaults=agent_defaults,
        participant_overrides=participant_overrides,
    )


def workspace_capabilities_to_dict(manifest: Optional[WorkspaceCapabilityManifest]) -> Optional[Dict[str, Any]]:
    if manifest is None:
        return None

    return {
        "skill_sources": [
            {
                "path": source.path,
                "source_type": source.source_type,
                "label": source.label,
                "recursive": source.recursive,
                "enabled": source.enabled,
            }
            for source in manifest.skill_sources
        ],
        "mcp_servers": [
            {
                "name": server.name,
                "transport": server.transport,
                "command": server.command,
                "args": list(server.args),
                "url": server.url,
                "env": dict(server.env),
                "tools_allowlist": list(server.tools_allowlist),
                "enabled": server.enabled,
            }
            for server in manifest.mcp_servers
        ],
        "agent_defaults": {
            "mode": manifest.agent_defaults.mode,
            "max_steps": manifest.agent_defaults.max_steps,
            "can_write": manifest.agent_defaults.can_write,
            "allowed_skills": list(manifest.agent_defaults.allowed_skills),
            "allowed_mcp_servers": list(manifest.agent_defaults.allowed_mcp_servers),
            "memory_scope": manifest.agent_defaults.memory_scope,
        },
        "participant_overrides": {
            key: {
                "agent": (
                    {
                        "mode": value.agent.mode,
                        "max_steps": value.agent.max_steps,
                        "can_write": value.agent.can_write,
                        "allowed_skills": list(value.agent.allowed_skills),
                        "allowed_mcp_servers": list(value.agent.allowed_mcp_servers),
                        "memory_scope": value.agent.memory_scope,
                    }
                    if value.agent is not None
                    else None
                ),
                "skills": list(value.skills),
                "mcp_servers": list(value.mcp_servers),
            }
            for key, value in manifest.participant_overrides.items()
        },
    }
