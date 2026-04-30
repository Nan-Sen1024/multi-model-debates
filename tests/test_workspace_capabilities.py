from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

import backend.api as api_module
from backend.api import app, orchestrator


def test_workspace_capability_manifest_round_trip(tmp_path: Path, monkeypatch):
    db_path = str(tmp_path / "workspace-capabilities.db")
    local_orchestrator = orchestrator.__class__(db_path=db_path)
    monkeypatch.setattr(api_module, "DB_PATH", db_path)
    monkeypatch.setattr(api_module, "orchestrator", local_orchestrator)

    (tmp_path / "skills").mkdir()

    client = TestClient(api_module.app)
    response = client.post(
        "/api/sessions",
        json={
            "topic": "构建带能力层的代码工作区",
            "mode": "code_workspace",
            "participants": [
                {"custom_id": "claude", "model_ref": "anthropic/claude-4.6"},
                {"custom_id": "codex", "model_ref": "openai/gpt-5.4"},
            ],
            "workspace": {
                "root_path": str(tmp_path),
                "display_name": "capability-demo",
                "capabilities": {
                    "skill_sources": [
                        {
                            "path": str(tmp_path / "skills"),
                            "source_type": "local",
                            "label": "local-skills",
                        }
                    ],
                    "mcp_servers": [
                        {
                            "name": "filesystem",
                            "transport": "stdio",
                            "command": "node",
                            "args": ["mcp-server.js"],
                            "tools_allowlist": ["read_file", "list_directory"],
                        }
                    ],
                    "agent_defaults": {
                        "mode": "tool_loop",
                        "max_steps": 4,
                        "can_write": False,
                        "allowed_skills": ["code-review"],
                        "allowed_mcp_servers": ["filesystem"],
                        "memory_scope": "workspace_shared",
                    },
                    "participant_overrides": {
                        "codex": {
                            "skills": ["code-write"],
                            "mcp_servers": ["filesystem"],
                            "agent": {
                                "mode": "full_agent",
                                "max_steps": 2,
                                "can_write": True,
                                "allowed_skills": ["code-write"],
                                "allowed_mcp_servers": ["filesystem"],
                                "memory_scope": "workspace_shared",
                            },
                        }
                    },
                },
            },
        },
    )

    assert response.status_code == 200
    session_id = response.json()["id"]

    loaded = asyncio.run(local_orchestrator.load_session(session_id))
    capabilities = loaded.config.workspace.capabilities

    assert capabilities is not None
    assert capabilities.skill_sources[0].path == str(tmp_path / "skills")
    assert capabilities.skill_sources[0].label == "local-skills"
    assert capabilities.mcp_servers[0].name == "filesystem"
    assert capabilities.mcp_servers[0].tools_allowlist == ["read_file", "list_directory"]
    assert capabilities.agent_defaults.max_steps == 4
    assert capabilities.agent_defaults.allowed_skills == ["code-review"]
    assert capabilities.participant_overrides["codex"].skills == ["code-write"]
    assert capabilities.participant_overrides["codex"].agent is not None
    assert capabilities.participant_overrides["codex"].agent.can_write is True


def test_workspace_can_write_patch_updates_existing_session(tmp_path: Path, monkeypatch):
    db_path = str(tmp_path / "workspace-can-write.db")
    local_orchestrator = orchestrator.__class__(db_path=db_path)
    monkeypatch.setattr(api_module, "DB_PATH", db_path)
    monkeypatch.setattr(api_module, "orchestrator", local_orchestrator)

    client = TestClient(api_module.app)
    response = client.post(
        "/api/sessions",
        json={
            "topic": "切换已有会话写入模式",
            "mode": "code_workspace",
            "participants": [
                {"custom_id": "claude", "model_ref": "anthropic/claude-4.6"},
                {"custom_id": "codex", "model_ref": "openai/gpt-5.4"},
            ],
            "workspace": {
                "root_path": str(tmp_path),
                "display_name": "capability-demo",
                "capabilities": {
                    "agent_defaults": {
                        "mode": "tool_loop",
                        "max_steps": 4,
                        "can_write": False,
                        "allowed_skills": [],
                        "allowed_mcp_servers": [],
                        "memory_scope": "workspace_shared",
                    },
                    "participant_overrides": {
                        "claude": {
                            "agent": {
                                "mode": "tool_loop",
                                "max_steps": 2,
                                "can_write": False,
                                "allowed_skills": [],
                                "allowed_mcp_servers": [],
                                "memory_scope": "workspace_shared",
                            },
                            "skills": [],
                            "mcp_servers": [],
                        }
                    },
                },
            },
        },
    )

    assert response.status_code == 200
    session_id = response.json()["id"]

    patch_response = client.patch(
        f"/api/sessions/{session_id}/workspace/can-write",
        json={"can_write": True},
    )

    assert patch_response.status_code == 200
    payload = patch_response.json()
    assert payload["workspace"]["capabilities"]["agent_defaults"]["can_write"] is True
    assert payload["workspace"]["capabilities"]["participant_overrides"]["claude"]["agent"]["can_write"] is True

    loaded = asyncio.run(local_orchestrator.load_session(session_id))
    capabilities = loaded.config.workspace.capabilities
    assert capabilities is not None
    assert capabilities.agent_defaults.can_write is True
    assert capabilities.participant_overrides["claude"].agent is not None
    assert capabilities.participant_overrides["claude"].agent.can_write is True


def test_workspace_can_write_patch_initializes_capabilities_when_missing(tmp_path: Path, monkeypatch):
    db_path = str(tmp_path / "workspace-can-write-init.db")
    local_orchestrator = orchestrator.__class__(db_path=db_path)
    monkeypatch.setattr(api_module, "DB_PATH", db_path)
    monkeypatch.setattr(api_module, "orchestrator", local_orchestrator)

    client = TestClient(api_module.app)
    response = client.post(
        "/api/sessions",
        json={
            "topic": "为旧会话补齐能力层",
            "mode": "code_workspace",
            "participants": [
                {"custom_id": "claude", "model_ref": "anthropic/claude-4.6"},
                {"custom_id": "codex", "model_ref": "openai/gpt-5.4"},
            ],
            "workspace": {
                "root_path": str(tmp_path),
                "display_name": "legacy-workspace",
            },
        },
    )

    assert response.status_code == 200
    session_id = response.json()["id"]

    patch_response = client.patch(
        f"/api/sessions/{session_id}/workspace/can-write",
        json={"can_write": True},
    )

    assert patch_response.status_code == 200
    payload = patch_response.json()
    assert payload["workspace"]["capabilities"]["agent_defaults"]["can_write"] is True
    assert payload["workspace"]["capabilities"]["agent_defaults"]["mode"] == "tool_loop"
    assert payload["workspace"]["capabilities"]["participant_overrides"] == {}

    loaded = asyncio.run(local_orchestrator.load_session(session_id))
    capabilities = loaded.config.workspace.capabilities
    assert capabilities is not None
    assert capabilities.agent_defaults.can_write is True
    assert capabilities.agent_defaults.mode == "tool_loop"
