from __future__ import annotations

import json
import time
import uuid
from typing import Any, Dict, List, Optional

import aiosqlite
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .database import DB_PATH, init_db
from .enums import APIFormat, AuthType, CollaborationMode, ProviderType
from .exceptions import ValidationError
from .auth_flow import (
    AuthFlowManager,
    FLOW_AWS_IAM,
    FLOW_OPENAI_CODEX,
    FLOW_GENERIC_OAUTH,
)
from .llm_gateway import (
    LLMGatewayClient,
    check_provider_connectivity,
    deserialize_auth_config,
    discover_ollama_models,
    serialize_auth_config,
)
from .models import AuthConfig, ProviderConfig
from .orchestrator import CreateSessionRequest, ParticipantInput, SessionOrchestrator

app = FastAPI(title="Multi-Model Debate Backend")
orchestrator = SessionOrchestrator()
auth_flow_manager = AuthFlowManager()


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


class ProviderPayload(BaseModel):
    name: str
    provider_type: ProviderType
    base_url: Optional[str] = None
    api_format: APIFormat = APIFormat.OPENAI_COMPLETIONS
    auth_type: AuthType = AuthType.IAM
    auth_value: Optional[str] = None
    fallback_ids: List[str] = Field(default_factory=list)


def error_response(exc: ValidationError, status_code: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=exc.to_dict())


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
            )
        )
        return {"id": session.id, "status": session.status.value, "mode": session.mode.value}
    except ValidationError as exc:
        return error_response(exc)


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    try:
        session = await orchestrator.get_session(session_id)
        return {
            "id": session.id,
            "topic": session.topic,
            "mode": session.mode.value,
            "status": session.status.value,
            "current_round": session.current_round,
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


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    try:
        summary = await orchestrator.terminate_session(session_id, "user_terminated")
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


@app.get("/api/sessions/{session_id}/stream")
async def stream_session(session_id: str):
    async def event_stream():
        async for chunk in orchestrator.dispatch_next(session_id):
            yield f"event: {chunk.event}\ndata: {json.dumps({'participant_id': chunk.participant_id, 'content': chunk.content, 'round': chunk.round_number, **chunk.metadata}, ensure_ascii=False)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


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
    return [
        {
            "id": row["id"],
            "name": row["name"],
            "provider_type": row["provider_type"],
            "base_url": row["base_url"],
            "api_format": row["api_format"],
            "auth_type": row["auth_type"],
            "fallback_ids": json.loads(row["fallback_ids"] or "[]"),
            "is_active": bool(row["is_active"]),
        }
        for row in rows
    ]


@app.post("/api/providers")
async def add_provider(payload: ProviderPayload):
    await init_db(DB_PATH)
    provider_id = str(uuid.uuid4())
    auth_config = AuthConfig(auth_type=payload.auth_type)
    if payload.auth_type == AuthType.API_KEY:
        auth_config.api_key = payload.auth_value
    elif payload.auth_type == AuthType.BEARER:
        auth_config.bearer_token = payload.auth_value
    elif payload.auth_type == AuthType.HELPER:
        auth_config.helper_script = payload.auth_value

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
                json.dumps(serialize_auth_config(auth_config), ensure_ascii=False),
                json.dumps(payload.fallback_ids, ensure_ascii=False),
            ),
        )
        await db.commit()
    return {"id": provider_id}


@app.post("/api/providers/{provider_id}/health")
async def provider_health(provider_id: str):
    await init_db(DB_PATH)
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM provider_configs WHERE id = ?", (provider_id,)) as cursor:
            row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="provider not found")

    provider = ProviderConfig(
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
async def update_provider(provider_id: str, payload: ProviderPayload):
    await init_db(DB_PATH)
    auth_config = AuthConfig(auth_type=payload.auth_type)
    if payload.auth_type == AuthType.API_KEY and payload.auth_value:
        auth_config.api_key = payload.auth_value
    elif payload.auth_type == AuthType.BEARER and payload.auth_value:
        auth_config.bearer_token = payload.auth_value
    elif payload.auth_type == AuthType.HELPER and payload.auth_value:
        auth_config.helper_script = payload.auth_value

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
                json.dumps(serialize_auth_config(auth_config), ensure_ascii=False),
                json.dumps(payload.fallback_ids, ensure_ascii=False),
                provider_id,
            ),
        )
        await db.commit()
    return {"updated": provider_id}


# ---------------------------------------------------------------------------
# 认证流程端点（Device_Code_Flow）
# ---------------------------------------------------------------------------

class AuthFlowStartPayload(BaseModel):
    """启动认证流程的请求体"""
    flow_type: str  # aws_iam | openai_codex | generic_oauth
    # openai_codex / generic_oauth
    token_endpoint: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = None
    scope: Optional[str] = None
    # aws_iam
    sso_start_url: Optional[str] = None
    sso_region: Optional[str] = None


class BindRolePayload(BaseModel):
    account_id: str
    role_name: str


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
            client_id=payload.client_id,
            client_secret=payload.client_secret,
            scope=payload.scope,
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
