from __future__ import annotations

import asyncio
import contextlib
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiosqlite
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .database import DB_PATH, init_db
from .enums import APIFormat, AuthType, CollaborationMode, ProviderType
from .exceptions import ValidationError
from .auth_flow import (
    AuthFlowManager,
    FLOW_AWS_IAM,
    FLOW_AWS_SSO_PKCE,
    FLOW_OPENAI_CODEX,
    FLOW_GENERIC_OAUTH,
    FLOW_BROWSER_OAUTH,
)
from .llm_gateway import (
    LLMGatewayClient,
    check_provider_connectivity,
    deserialize_auth_config,
    discover_ollama_models,
    serialize_auth_config,
)
from .models import AuthConfig, ProviderConfig, WorkspaceConfig
from .workspace_scanner import scan_workspace
from .workspace_capabilities import workspace_capabilities_from_dict, workspace_capabilities_to_dict
from .orchestrator import CreateSessionRequest, ParticipantInput, SessionOrchestrator

app = FastAPI(title="Multi-Model Debate Backend")
orchestrator = SessionOrchestrator()
auth_flow_manager = AuthFlowManager()
SSE_KEEPALIVE_INTERVAL_SECONDS = 15.0


class ParticipantPayload(BaseModel):
    model_ref: str
    provider_id: Optional[str] = None
    custom_id: Optional[str] = None
    role_desc: Optional[str] = None


class SessionCreatePayload(BaseModel):
    topic: str
    mode: CollaborationMode
    participants: List[ParticipantPayload]
    max_rounds: int = 20
    drift_threshold: float = 0.4
    retention_window: int = 10
    context_threshold: float = 0.7
    summary_model: Optional[str] = None
    workspace: Optional["WorkspacePayload"] = None


class UserMessagePayload(BaseModel):
    content: str


class SnapshotPatchPayload(BaseModel):
    topic: Optional[str] = None
    mode: Optional[CollaborationMode] = None
    participant_summaries: Optional[Dict[str, str]] = None
    consensus_list: Optional[List[str]] = None
    key_events: Optional[List[str]] = None


class RestorePayload(BaseModel):
    checkpoint_id: str


class SessionPatchPayload(BaseModel):
    title: Optional[str] = None


class WorkspacePayload(BaseModel):
    root_path: str
    display_name: Optional[str] = None
    repo_fingerprint: Optional[str] = None
    scan_excludes: List[str] = Field(default_factory=list)
    selected_paths: List[str] = Field(default_factory=list)
    index_status: Optional[str] = None
    last_scanned_at: Optional[int] = None
    summary: Optional[str] = None
    capabilities: Optional["WorkspaceCapabilityManifestPayload"] = None


class SkillSourcePayload(BaseModel):
    path: str
    source_type: str = "local"
    label: Optional[str] = None
    recursive: bool = True
    enabled: bool = True


class MCPServerPayload(BaseModel):
    name: str
    transport: str
    command: Optional[str] = None
    args: List[str] = Field(default_factory=list)
    url: Optional[str] = None
    env: Dict[str, str] = Field(default_factory=dict)
    tools_allowlist: List[str] = Field(default_factory=list)
    enabled: bool = True


class AgentProfilePayload(BaseModel):
    mode: str = "tool_loop"
    max_steps: int = 6
    can_write: bool = False
    allowed_skills: List[str] = Field(default_factory=list)
    allowed_mcp_servers: List[str] = Field(default_factory=list)
    memory_scope: str = "workspace_shared"


class ParticipantCapabilityPayload(BaseModel):
    agent: Optional[AgentProfilePayload] = None
    skills: List[str] = Field(default_factory=list)
    mcp_servers: List[str] = Field(default_factory=list)


class WorkspaceCapabilityManifestPayload(BaseModel):
    skill_sources: List[SkillSourcePayload] = Field(default_factory=list)
    mcp_servers: List[MCPServerPayload] = Field(default_factory=list)
    agent_defaults: AgentProfilePayload = Field(default_factory=AgentProfilePayload)
    participant_overrides: Dict[str, ParticipantCapabilityPayload] = Field(default_factory=dict)


SessionCreatePayload.model_rebuild()
WorkspacePayload.model_rebuild()


class ProviderPayload(BaseModel):
    name: str
    provider_type: ProviderType
    base_url: Optional[str] = None
    api_format: APIFormat = APIFormat.OPENAI_COMPLETIONS
    auth_type: AuthType = AuthType.IAM
    auth_value: Optional[str] = None
    auth_metadata: Dict[str, Any] = Field(default_factory=dict)
    fallback_ids: List[str] = Field(default_factory=list)


def error_response(exc: ValidationError, status_code: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=exc.to_dict())


def _build_auth_config(auth_type: AuthType, auth_value: Optional[str], auth_metadata: Dict[str, Any]) -> AuthConfig:
    auth_config = AuthConfig(auth_type=auth_type)
    if auth_type == AuthType.API_KEY and auth_value:
        auth_config.api_key = auth_value
    elif auth_type == AuthType.BEARER and auth_value:
        auth_config.bearer_token = auth_value
    elif auth_type == AuthType.HELPER and auth_value:
        auth_config.helper_script = auth_value
    auth_config.metadata = dict(auth_metadata or {})
    return auth_config


def _workspace_config_from_payload(payload: Optional[WorkspacePayload]) -> Optional[WorkspaceConfig]:
    if payload is None:
        return None
    root_path = payload.root_path.strip()
    if not root_path:
        return None
    return WorkspaceConfig(
        root_path=root_path,
        display_name=payload.display_name.strip() if isinstance(payload.display_name, str) and payload.display_name.strip() else None,
        repo_fingerprint=payload.repo_fingerprint.strip() if isinstance(payload.repo_fingerprint, str) and payload.repo_fingerprint.strip() else None,
        scan_excludes=[item.strip() for item in payload.scan_excludes if item.strip()],
        selected_paths=[item.strip() for item in payload.selected_paths if item.strip()],
        index_status=payload.index_status or "pending",
        last_scanned_at=payload.last_scanned_at,
        summary=payload.summary.strip() if isinstance(payload.summary, str) and payload.summary.strip() else None,
        capabilities=workspace_capability_config_from_payload(payload.capabilities),
    )


def workspace_capability_config_from_payload(
    payload: Optional[WorkspaceCapabilityManifestPayload],
) -> Optional["WorkspaceCapabilityManifest"]:
    if payload is None:
        return None
    return workspace_capabilities_from_dict(payload.model_dump())


def _workspace_config_payload(workspace: Optional[WorkspaceConfig]) -> Optional[Dict[str, Any]]:
    if workspace is None:
        return None
    return {
        "root_path": workspace.root_path,
        "display_name": workspace.display_name,
        "repo_fingerprint": workspace.repo_fingerprint,
        "scan_excludes": list(workspace.scan_excludes),
        "selected_paths": list(workspace.selected_paths),
        "index_status": workspace.index_status,
        "last_scanned_at": workspace.last_scanned_at,
        "summary": workspace.summary,
        "capabilities": workspace_capabilities_to_dict(workspace.capabilities),
    }


def _workspace_tree_payload(entries: List[Any]) -> List[Dict[str, Any]]:
    payload: List[Dict[str, Any]] = []
    for entry in entries:
        payload.append(
            {
                "name": entry.name,
                "path": entry.path,
                "kind": entry.kind,
                "children": _workspace_tree_payload(entry.children),
            }
        )
    return payload


def _provider_config_from_row(row: aiosqlite.Row) -> ProviderConfig:
    return ProviderConfig(
        id=row["id"],
        name=row["name"],
        provider_type=ProviderType(row["provider_type"]),
        base_url=row["base_url"],
        api_format=APIFormat(row["api_format"]),
        auth_type=AuthType(row["auth_type"]),
        auth_config=deserialize_auth_config(row["auth_type"], row["auth_config"]),
        fallback_ids=json.loads(row["fallback_ids"] or "[]"),
        is_active=bool(row["is_active"]),
    )


def _provider_config_from_payload(provider_id: str, payload: ProviderPayload) -> ProviderConfig:
    return ProviderConfig(
        id=provider_id,
        name=payload.name,
        provider_type=payload.provider_type,
        base_url=payload.base_url,
        api_format=payload.api_format,
        auth_type=payload.auth_type,
        auth_config=_build_auth_config(payload.auth_type, payload.auth_value, payload.auth_metadata),
        fallback_ids=list(payload.fallback_ids),
        is_active=True,
    )


async def _load_provider_config(provider_id: str) -> Optional[ProviderConfig]:
    await init_db(DB_PATH)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM provider_configs WHERE id = ? LIMIT 1", (provider_id,)) as cursor:
            row = await cursor.fetchone()
    if row is None:
        return None
    return _provider_config_from_row(row)


def _normalize_utc_datetime(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _provider_auth_status(auth_config: AuthConfig) -> tuple[str, Optional[int]]:
    oauth_token = auth_config.oauth_token
    if auth_config.auth_type == AuthType.OAUTH:
        if oauth_token is None or not oauth_token.access_token:
            return "missing", None
        normalized_expires_at = _normalize_utc_datetime(oauth_token.expires_at)
        expires_at = (
            int(normalized_expires_at.timestamp())
            if normalized_expires_at is not None
            else None
        )
        if (
            normalized_expires_at is not None
            and normalized_expires_at <= datetime.now(timezone.utc)
        ):
            return ("refreshable" if oauth_token.refresh_token else "expired"), expires_at
        return "ready", expires_at
    if auth_config.auth_type == AuthType.API_KEY:
        return ("ready" if auth_config.api_key else "missing"), None
    if auth_config.auth_type == AuthType.BEARER:
        return ("ready" if auth_config.bearer_token else "missing"), None
    if auth_config.auth_type == AuthType.HELPER:
        return ("ready" if auth_config.helper_script else "missing"), None
    if auth_config.auth_type in {AuthType.IAM, AuthType.ADC}:
        return ("ready" if auth_config.metadata else "missing"), None
    return "missing", None


@app.post("/api/sessions")
async def create_session(payload: SessionCreatePayload):
    try:
        session = await orchestrator.create_session(
            CreateSessionRequest(
                topic=payload.topic,
                mode=payload.mode,
                participants=[
                    ParticipantInput(
                        model_ref=item.model_ref,
                        provider_id=item.provider_id,
                        custom_id=item.custom_id,
                        role_desc=item.role_desc,
                    )
                    for item in payload.participants
                ],
                max_rounds=payload.max_rounds,
                drift_threshold=payload.drift_threshold,
                retention_window=payload.retention_window,
                context_threshold=payload.context_threshold,
                summary_model=payload.summary_model,
                workspace=_workspace_config_from_payload(payload.workspace),
            )
        )
        return {"id": session.id, "status": session.status.value, "mode": session.mode.value}
    except ValidationError as exc:
        return error_response(exc)


@app.get("/api/sessions")
async def list_sessions():
    sessions = await orchestrator.list_sessions()
    return [
        {
            "id": session.id,
            "title": session.title,
            "topic": session.topic,
            "mode": session.mode.value,
            "status": session.status.value,
            "current_round": session.current_round,
            "updated_at": session.updated_at,
            "participant_count": session.participant_count,
            "last_message_preview": session.last_message_preview,
        }
        for session in sessions
    ]


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    try:
        session = await orchestrator.get_session(session_id)
        return {
            "id": session.id,
            "title": session.config.display_title or session.topic,
            "topic": session.topic,
            "mode": session.mode.value,
            "status": session.status.value,
            "current_round": session.current_round,
            "workspace": _workspace_config_payload(session.config.workspace),
            "participants": [
                {
                    "id": participant.id,
                    "custom_id": participant.custom_id,
                    "model_ref": participant.model_ref,
                    "provider_id": participant.provider_id,
                    "role_desc": participant.role_desc,
                    "is_active": participant.is_active,
                }
                for participant in session.participants
            ],
        }
    except ValidationError as exc:
        return error_response(exc, 404)


@app.patch("/api/sessions/{session_id}")
async def patch_session(session_id: str, payload: SessionPatchPayload):
    try:
        session = await orchestrator.update_session_title(session_id, payload.title)
        return {
            "id": session.id,
            "title": session.config.display_title or session.topic,
            "topic": session.topic,
            "mode": session.mode.value,
            "status": session.status.value,
            "current_round": session.current_round,
            "workspace": _workspace_config_payload(session.config.workspace),
            "participants": [
                {
                    "id": participant.id,
                    "custom_id": participant.custom_id,
                    "model_ref": participant.model_ref,
                    "provider_id": participant.provider_id,
                    "role_desc": participant.role_desc,
                    "is_active": participant.is_active,
                }
                for participant in session.participants
            ],
        }
    except ValidationError as exc:
        return error_response(exc, 404)


@app.get("/api/sessions/{session_id}/workspace")
async def get_session_workspace(session_id: str):
    try:
        session = await orchestrator.get_session(session_id)
        workspace = session.config.workspace
        if workspace is None:
            raise ValidationError("会话未配置 workspace。", field="workspace")

        scan_result = scan_workspace(workspace.root_path, workspace.scan_excludes)
        workspace.repo_fingerprint = scan_result.repo_fingerprint
        workspace.summary = scan_result.summary
        workspace.last_scanned_at = scan_result.scanned_at
        workspace.index_status = "ready"
        await orchestrator._persist_session_runtime(session)
        return {
            "root_path": workspace.root_path,
            "display_name": workspace.display_name or scan_result.display_name,
            "repo_fingerprint": workspace.repo_fingerprint or scan_result.repo_fingerprint,
            "scan_excludes": list(workspace.scan_excludes),
            "selected_paths": list(workspace.selected_paths),
            "index_status": workspace.index_status,
            "last_scanned_at": scan_result.scanned_at,
            "summary": scan_result.summary,
            "files": scan_result.files,
            "tree": _workspace_tree_payload(scan_result.tree),
        }
    except ValidationError as exc:
        return error_response(exc, 404)


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    try:
        summary = await orchestrator.delete_session(session_id)
        return {"reason": summary.reason, "summary": summary.summary_text}
    except ValidationError as exc:
        return error_response(exc, 404)


@app.post("/api/sessions/{session_id}/messages")
async def inject_user_message(session_id: str, payload: UserMessagePayload):
    try:
        await orchestrator.inject_user_message(session_id, payload.content)
        return {"status": "queued"}
    except ValidationError as exc:
        return error_response(exc)


@app.get("/api/sessions/{session_id}/messages")
async def list_session_messages(session_id: str):
    try:
        messages = await orchestrator.get_messages(session_id)
        return [
            {
                "id": message.id,
                "sender_id": message.sender_id,
                "message_type": (
                    message.message_type.value
                    if hasattr(message.message_type, "value")
                    else str(message.message_type)
                ),
                "content": message.content,
                "is_masked": message.is_masked,
                "is_compressed": message.is_compressed,
                "drift_score": message.drift_score,
                "round_number": message.round_number,
                "created_at": int(message.created_at.timestamp()),
            }
            for message in messages
        ]
    except ValidationError as exc:
        return error_response(exc, 404)


@app.get("/api/sessions/{session_id}/stream")
async def stream_session(session_id: str):
    def format_stream_chunk(event: str, payload: Dict[str, Any]) -> str:
        return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    async def event_stream():
        queue: asyncio.Queue[object] = asyncio.Queue()
        sentinel = object()

        async def produce() -> None:
            try:
                async for chunk in orchestrator.dispatch_round(session_id):
                    await queue.put(chunk)
            except Exception as exc:
                await queue.put(exc)
            finally:
                await queue.put(sentinel)

        producer = asyncio.create_task(produce())

        try:
            yield format_stream_chunk("ping", {"ts": int(time.time())})
            while True:
                try:
                    item = await asyncio.wait_for(
                        queue.get(),
                        timeout=SSE_KEEPALIVE_INTERVAL_SECONDS,
                    )
                except asyncio.TimeoutError:
                    yield format_stream_chunk("ping", {"ts": int(time.time())})
                    continue

                if item is sentinel:
                    break
                if isinstance(item, Exception):
                    raise item

                chunk = item
                yield format_stream_chunk(
                    chunk.event,
                    {
                        "participant_id": chunk.participant_id,
                        "content": chunk.content,
                        "round": chunk.round_number,
                        **chunk.metadata,
                    },
                )
        finally:
            if not producer.done():
                producer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await producer

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/sessions/{session_id}/snapshot")
async def get_snapshot(session_id: str):
    try:
        snapshot = await orchestrator.get_snapshot(session_id)
        return {
            "topic": snapshot.topic,
            "mode": snapshot.mode.value if hasattr(snapshot.mode, "value") else str(snapshot.mode),
            "participant_summaries": snapshot.participant_summaries,
            "consensus_list": snapshot.consensus_list,
            "key_events": snapshot.key_events,
        }
    except ValidationError as exc:
        return error_response(exc, 404)


@app.patch("/api/sessions/{session_id}/snapshot")
async def patch_snapshot(session_id: str, payload: SnapshotPatchPayload):
    try:
        snapshot = await orchestrator.update_snapshot(
            session_id=session_id,
            topic=payload.topic,
            mode=payload.mode,
            participant_summaries=payload.participant_summaries,
            consensus_list=payload.consensus_list,
            key_events=payload.key_events,
        )
        return {
            "topic": snapshot.topic,
            "mode": snapshot.mode.value if hasattr(snapshot.mode, "value") else str(snapshot.mode),
            "participant_summaries": snapshot.participant_summaries,
            "consensus_list": snapshot.consensus_list,
            "key_events": snapshot.key_events,
        }
    except ValidationError as exc:
        return error_response(exc)


@app.get("/api/sessions/{session_id}/export")
async def export_session(session_id: str):
    try:
        return {"content": await orchestrator.export_session_history(session_id)}
    except ValidationError as exc:
        return error_response(exc, 404)


@app.post("/api/sessions/{session_id}/checkpoints")
async def create_checkpoint(session_id: str):
    try:
        checkpoint = await orchestrator.create_checkpoint(session_id)
        return {"checkpoint_id": checkpoint.id}
    except ValidationError as exc:
        return error_response(exc, 404)


@app.post("/api/sessions/restore")
async def restore_session(payload: RestorePayload):
    try:
        session = await orchestrator.restore_from_checkpoint(payload.checkpoint_id)
        return {"id": session.id, "topic": session.topic, "mode": session.mode.value}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/api/providers")
async def list_providers():
    await init_db(DB_PATH)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM provider_configs ORDER BY name ASC") as cursor:
            rows = await cursor.fetchall()
    providers = []
    for row in rows:
        auth_config = (
            deserialize_auth_config(row["auth_type"], row["auth_config"])
            if row["auth_config"]
            else AuthConfig(auth_type=AuthType(row["auth_type"]))
        )
        auth_status, auth_expires_at = _provider_auth_status(auth_config)
        providers.append(
            {
                "id": row["id"],
                "name": row["name"],
                "provider_type": row["provider_type"],
                "base_url": row["base_url"],
                "api_format": row["api_format"],
                "auth_type": row["auth_type"],
                "auth_metadata": auth_config.metadata,
                "auth_status": auth_status,
                "auth_expires_at": auth_expires_at,
                "fallback_ids": json.loads(row["fallback_ids"] or "[]"),
                "is_active": bool(row["is_active"]),
            }
        )
    return providers


@app.post("/api/providers")
async def add_provider(payload: ProviderPayload):
    await init_db(DB_PATH)
    provider_id = str(uuid.uuid4())
    provider = _provider_config_from_payload(provider_id, payload)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT INTO provider_configs
                (id, name, provider_type, base_url, api_format, auth_type, auth_config, fallback_ids, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                provider_id,
                payload.name,
                payload.provider_type.value,
                payload.base_url,
                payload.api_format.value,
                payload.auth_type.value,
                json.dumps(serialize_auth_config(provider.auth_config), ensure_ascii=False),
                json.dumps(payload.fallback_ids, ensure_ascii=False),
            ),
        )
        await db.commit()
    return {"id": provider_id}


@app.post("/api/providers/{provider_id}/health")
async def provider_health(provider_id: str):
    provider = await _load_provider_config(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="provider not found")
    return {"healthy": await check_provider_connectivity(provider)}


@app.get("/api/providers/local/discover")
async def discover_local_provider_models():
    models = await discover_ollama_models("http://127.0.0.1:11434")
    return {"provider": "ollama", "models": models, "detected_at": int(time.time())}


@app.delete("/api/providers/{provider_id}")
async def delete_provider(provider_id: str):
    await init_db(DB_PATH)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM provider_configs WHERE id = ?", (provider_id,)) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="provider not found")
        await db.execute("DELETE FROM provider_configs WHERE id = ?", (provider_id,))
        await db.commit()
    return {"deleted": provider_id}


@app.put("/api/providers/{provider_id}")
@app.patch("/api/providers/{provider_id}")
async def update_provider(provider_id: str, payload: ProviderPayload):
    await init_db(DB_PATH)
    provider = _provider_config_from_payload(provider_id, payload)

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id FROM provider_configs WHERE id = ?", (provider_id,)) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="provider not found")
        await db.execute(
            """UPDATE provider_configs SET
               name=?, provider_type=?, base_url=?, api_format=?,
               auth_type=?, auth_config=?, fallback_ids=?
               WHERE id=?""",
            (
                payload.name,
                payload.provider_type.value,
                payload.base_url,
                payload.api_format.value,
                payload.auth_type.value,
                json.dumps(serialize_auth_config(provider.auth_config), ensure_ascii=False),
                json.dumps(payload.fallback_ids, ensure_ascii=False),
                provider_id,
            ),
        )
        await db.commit()
    return {"updated": provider_id}


class ModelCatalogDiscoverPayload(BaseModel):
    provider_id: Optional[str] = None
    provider: Optional[ProviderPayload] = None


@app.post("/api/model-catalog/discover")
async def discover_model_catalog(payload: ModelCatalogDiscoverPayload):
    if payload.provider_id:
        provider = await _load_provider_config(payload.provider_id)
        if provider is None:
            raise HTTPException(status_code=404, detail="provider not found")
    elif payload.provider is not None:
        provider = _provider_config_from_payload("__draft__", payload.provider)
    else:
        raise HTTPException(status_code=400, detail="provider_id or provider is required")

    gateway = LLMGatewayClient()
    models = await gateway.discover_provider_models(provider)
    return {
        "provider_id": provider.id,
        "provider_name": provider.name,
        "provider_type": provider.provider_type.value,
        "models": models,
        "detected_at": int(time.time()),
    }


# ---------------------------------------------------------------------------
# 认证流程端点（Device_Code_Flow）
# ---------------------------------------------------------------------------

class AuthFlowStartPayload(BaseModel):
    """启动认证流程的请求体"""
    flow_type: str  # aws_iam | openai_codex | generic_oauth | browser_oauth
    # openai_codex / generic_oauth / browser_oauth
    token_endpoint: Optional[str] = None
    authorization_endpoint: Optional[str] = None
    device_authorization_endpoint: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    scope: Optional[str] = None
    login_variant: Optional[str] = None
    # aws_iam
    sso_start_url: Optional[str] = None
    sso_region: Optional[str] = None


class BindRolePayload(BaseModel):
    account_id: str
    role_name: str


@app.get("/api/providers/{provider_id}/auth/callback")
async def aws_pkce_callback(
    provider_id: str,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    error_description: Optional[str] = None,
):
    """
    AWS SSO PKCE 回调端点。
    AWS 登录完成后自动跳转到此 URL，前端试用轮询状态确认完成。
    """
    def _html_page(title: str, body: str, success: bool = True) -> HTMLResponse:
        color = "#22c55e" if success else "#ef4444"
        icon = "✅" if success else "❌"
        return HTMLResponse(f"""
<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <style>
    body {{ font-family: -apple-system, sans-serif; display: flex;
            align-items: center; justify-content: center;
            min-height: 100vh; margin: 0; background: #0f172a; color: #e2e8f0; }}
    .card {{ text-align: center; padding: 48px; background: #1e293b;
             border-radius: 16px; max-width: 400px; }}
    h2 {{ color: {color}; font-size: 1.5rem; margin-bottom: 12px; }}
    p {{ color: #94a3b8; }}
  </style>
  <script>setTimeout(() => window.close(), 2000);</script>
</head>
<body>
  <div class="card">
    <div style="font-size:48px;margin-bottom:16px">{icon}</div>
    <h2>{title}</h2>
    <p>{body}</p>
  </div>
</body>
</html>
        """)

    # AWS 返回错误
    if error:
        msg = error_description or error
        if state:
            await auth_flow_manager._update_auth_session(
                state, "failed", error_message=msg
            )
        return _html_page("登录失败", f"错误：{msg}。请关闭此窗口并重试。", success=False)

    if not code or not state:
        return _html_page("参数缺失", "请关闭此窗口并重新登录。", success=False)

    try:
        await auth_flow_manager.handle_interactive_callback(
            auth_session_id=state,
            code=code,
        )
    except Exception as exc:
        import traceback
        traceback.print_exc()
        return _html_page("登录失败", f"凭证换取失败：{exc}。请关闭此窗口并重试。", success=False)

    return _html_page("登录成功", "授权已完成！此窗口将自动关闭，请回到应用选择账号和角色。")


@app.post("/api/providers/{provider_id}/auth/start")
async def start_auth_flow(provider_id: str, payload: AuthFlowStartPayload):
    """
    启动 Device_Code_Flow 认证。
    返回 verification_uri 和 user_code 展示给用户，同时在后台开始轮询。
    """
    # 确认 provider 存在
    await init_db(DB_PATH)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id FROM provider_configs WHERE id = ?", (provider_id,)) as cursor:
            row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="provider not found")

    try:
        result = await auth_flow_manager.start_flow(
            provider_id=provider_id,
            flow_type=payload.flow_type,
            token_endpoint=payload.token_endpoint,
            authorization_endpoint=payload.authorization_endpoint,
            device_authorization_endpoint=payload.device_authorization_endpoint,
            client_id=payload.client_id,
            client_secret=payload.client_secret,
            scope=payload.scope,
            login_variant=payload.login_variant,
            sso_start_url=payload.sso_start_url,
            sso_region=payload.sso_region,
        )
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "auth_session_id": result.auth_session_id,
        "verification_uri": result.verification_uri,
        "user_code": result.user_code,
        "expires_in": result.expires_in,
        "interval": result.interval,
        "flow_type": result.flow_type,
    }


@app.get("/api/providers/{provider_id}/auth/status/{auth_session_id}")
async def get_auth_status(provider_id: str, auth_session_id: str):
    """
    查询认证会话状态。
    status: pending | completed | failed | expired | awaiting_role
    awaiting_role 时返回 accounts 列表供用户选择。
    """
    try:
        result = await auth_flow_manager.get_status(auth_session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    response: Dict[str, Any] = {
        "auth_session_id": result.auth_session_id,
        "status": result.status,
        "flow_type": result.flow_type,
    }
    if result.accounts is not None:
        response["accounts"] = result.accounts
    if result.error_message:
        response["error_message"] = result.error_message
    return response


@app.post("/api/providers/{provider_id}/auth/cancel/{auth_session_id}")
async def cancel_auth_flow(provider_id: str, auth_session_id: str):
    row = await auth_flow_manager._load_auth_session(auth_session_id)
    if row is None or row["provider_id"] != provider_id:
        raise HTTPException(status_code=404, detail="auth session not found")

    try:
        result = await auth_flow_manager.cancel_flow(auth_session_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {
        "auth_session_id": result.auth_session_id,
        "status": result.status,
        "flow_type": result.flow_type,
        "error_message": result.error_message,
    }


@app.post("/api/providers/{provider_id}/auth/logout")
async def logout_provider_auth(provider_id: str):
    try:
        await auth_flow_manager.logout_provider(provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    return {
        "provider_id": provider_id,
        "status": "logged_out",
    }


@app.post("/api/providers/{provider_id}/auth/bind-role/{auth_session_id}")
async def bind_aws_role(provider_id: str, auth_session_id: str, payload: BindRolePayload):
    """
    AWS 专用：用户选择账号和角色后，换取 Sigv4_Credentials 并写回 provider。
    """
    try:
        result = await auth_flow_manager.bind_aws_role(
            auth_session_id=auth_session_id,
            account_id=payload.account_id,
            role_name=payload.role_name,
        )
    except (ValueError, Exception) as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "auth_session_id": result.auth_session_id,
        "account_id": result.account_id,
        "role_name": result.role_name,
        "status": "completed",
    }
