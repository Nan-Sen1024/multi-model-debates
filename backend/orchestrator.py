from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Dict, List, Optional, Union

import aiosqlite

from .anchor_injector import AnchorInjector
from .context_compressor import ContextCompressor
from .database import DB_PATH, init_db
from .drift_detector import DriftDetector
from .enums import APIFormat, AuthType, CollaborationMode, MessageType, ProviderType, SessionStatus
from .exceptions import AuthenticationError, ValidationError
from .llm_gateway import (
    LLMGatewayClient,
    ProviderRouter,
    deserialize_auth_config,
    serialize_auth_config,
    validate_model_ref,
)
from .message_store import MessageStore
from .models import AuthConfig, Checkpoint, CollaborationMessage, ModelParticipant, ProviderConfig, Session, SessionConfig, SessionSnapshot, WorkspaceConfig
from .workspace_context import build_workspace_file_context, build_workspace_skill_context
from .workspace_capabilities import (
    AgentProfileConfig,
    WorkspaceCapabilityManifest,
    workspace_capabilities_from_dict,
    workspace_capabilities_to_dict,
)
from .workspace_agent import (
    WorkspaceAgentEvent,
    WorkspaceAgentRunResult,
    WorkspaceAgentRunner,
    resolve_workspace_agent_profile,
)
from .workspace_executor import WorkspaceExecutionRuntime
from .workspace_mcp import WorkspaceMCPRuntime
from .workspace_router import resolve_workspace_targets
from .workspace_scanner import scan_workspace
from .snapshot_manager import SnapshotManager
from .strategies import StrategyRegistry

logger = logging.getLogger(__name__)
USER_SENDER_ID = "[用户]"


def _participant_error_code(exc: Exception) -> str:
    if isinstance(exc, AuthenticationError):
        return "AUTHENTICATION_REQUIRED"
    return "PROVIDER_UNAVAILABLE"


def _participant_error_metadata(
    exc: Exception,
    participant: ModelParticipant,
    provider_config: Optional[ProviderConfig] = None,
) -> Dict[str, object]:
    provider = provider_config or getattr(exc, "_mmd_provider_config", None)
    code = _participant_error_code(exc)
    metadata: Dict[str, object] = {
        "code": code,
        "message": str(exc),
        "model_ref": participant.model_ref,
    }
    if provider is not None:
        metadata.update(
            {
                "provider_id": provider.id,
                "provider_name": provider.name,
                "auth_type": provider.auth_type.value,
            }
        )
    if code == "AUTHENTICATION_REQUIRED":
        metadata["summary"] = "认证已过期或失效"
        metadata["remediation"] = "请重新登录该 Provider，或切换到可用 fallback。"
    return metadata


@dataclass
class ParticipantInput:
    model_ref: str
    provider_id: Optional[str] = None
    custom_id: Optional[str] = None
    role_desc: Optional[str] = None


@dataclass
class CreateSessionRequest:
    topic: str
    mode: CollaborationMode
    participants: List[ParticipantInput]
    max_rounds: int = 20
    drift_threshold: float = 0.4
    retention_window: int = 10
    context_threshold: float = 0.7
    summary_model: Optional[str] = None
    workspace: Optional[WorkspaceConfig] = None


@dataclass
class StreamChunk:
    event: str
    participant_id: Optional[str] = None
    content: str = ""
    round_number: int = 0
    metadata: Dict[str, object] = field(default_factory=dict)


@dataclass
class SessionSummary:
    session_id: str
    reason: str
    summary_text: str


@dataclass
class SessionListItem:
    id: str
    title: str
    topic: str
    mode: CollaborationMode
    status: SessionStatus
    current_round: int
    updated_at: int
    participant_count: int
    last_message_preview: str


@dataclass
class SessionRuntimeState:
    is_generating: bool = False
    pending_user_messages: List[str] = field(default_factory=list)
    manual_topic_reminder: bool = False


class SessionOrchestrator:
    def __init__(
        self,
        db_path: str = DB_PATH,
        gateway: Optional[LLMGatewayClient] = None,
        message_store: Optional[MessageStore] = None,
        anchor_injector: Optional[AnchorInjector] = None,
        snapshot_manager: Optional[SnapshotManager] = None,
        context_compressor: Optional[ContextCompressor] = None,
        drift_detector: Optional[DriftDetector] = None,
        mcp_runtime_factory: Optional[callable] = None,
    ):
        self.db_path = db_path
        self.gateway = gateway or LLMGatewayClient()
        self.message_store = message_store or MessageStore(db_path=db_path)
        self.anchor_injector = anchor_injector or AnchorInjector()
        self.snapshot_manager = snapshot_manager or SnapshotManager()
        self.context_compressor = context_compressor or ContextCompressor(db_path=db_path)
        self.drift_detector = drift_detector or DriftDetector()
        self.strategy_registry = StrategyRegistry()
        self.provider_router = ProviderRouter()
        self._mcp_runtime_factory = mcp_runtime_factory or (
            lambda manifest: WorkspaceMCPRuntime(manifest=manifest)
        )
        self._session_locks: Dict[str, asyncio.Lock] = {}
        self._session_runtime: Dict[str, SessionRuntimeState] = {}

    async def create_session(self, config: CreateSessionRequest) -> Session:
        if not config.topic or not config.topic.strip():
            raise ValidationError("topic 不能为空", field="topic")
        if len(config.participants) < 2:
            raise ValidationError("participants 至少需要 2 个参与者", field="participants")
        if len(config.participants) > 10:
            raise ValidationError("participants 最多支持 10 个参与者", field="participants")

        inputs = self._resolve_custom_ids(config.participants)
        await self._validate_provider_bindings(inputs)
        session_id = str(uuid.uuid4())
        participants = [
            ModelParticipant(
                id=str(uuid.uuid4()),
                session_id=session_id,
                custom_id=item.custom_id or f"Model_{index + 1}",
                model_ref=item.model_ref,
                provider_id=item.provider_id,
                sequence_order=index + 1,
                role_desc=item.role_desc,
            )
            for index, item in enumerate(inputs)
        ]
        session = Session(
            id=session_id,
            topic=config.topic,
            mode=config.mode,
            status=SessionStatus.ACTIVE,
            participants=participants,
            config=SessionConfig(
                max_rounds=config.max_rounds,
                drift_threshold=config.drift_threshold,
                retention_window=config.retention_window,
                context_threshold=config.context_threshold,
                summary_model=config.summary_model,
                default_model_ref=None,
                display_title=config.topic,
                workspace=config.workspace,
            ),
            snapshot=SessionSnapshot(
                topic=config.topic,
                mode=config.mode,
                participant_summaries={p.custom_id: "" for p in participants},
                consensus_list=[],
                key_events=[],
            ),
            current_round=0,
            next_speaker_index=0,
        )
        self.strategy_registry.get(session.mode).initialize_session(session)
        await self._persist_session(session)
        self._session_runtime[session.id] = SessionRuntimeState()
        return session

    async def load_session(self, session_id: str) -> Session:
        await init_db(self.db_path)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM collaboration_sessions WHERE id = ?", (session_id,)) as cursor:
                session_row = await cursor.fetchone()
            if session_row is None:
                raise ValidationError('validation error', field="session_id")
            async with db.execute(
                "SELECT * FROM model_participants WHERE session_id = ? ORDER BY sequence_order ASC",
                (session_id,),
            ) as cursor:
                participant_rows = await cursor.fetchall()

        cfg = json.loads(session_row["config"])
        participants = [
            ModelParticipant(
                id=row["id"],
                session_id=row["session_id"],
                custom_id=row["custom_id"],
                display_name=row["display_name"],
                model_ref=row["model_ref"],
                provider_id=row["provider_id"],
                role_desc=row["role_desc"],
                private_info=row["private_info"],
                sequence_order=row["sequence_order"],
                is_active=bool(row["is_active"]),
            )
            for row in participant_rows
        ]
        session = Session(
            id=session_row["id"],
            topic=session_row["topic"],
            mode=CollaborationMode(session_row["mode"]),
            status=SessionStatus(session_row["status"]),
            participants=participants,
            config=SessionConfig(
                max_rounds=cfg.get("max_rounds", 20),
                drift_threshold=cfg.get("drift_threshold", 0.4),
                retention_window=cfg.get("retention_window", 10),
                context_threshold=cfg.get("context_threshold", 0.7),
                summary_model=cfg.get("summary_model"),
                default_model_ref=cfg.get("default_model_ref"),
                delegate_all_tools=cfg.get("delegate_all_tools", False),
                display_title=cfg.get("display_title"),
                workspace=self._deserialize_workspace_config(cfg.get("workspace")),
            ),
            snapshot=SessionSnapshot(
                topic=session_row["topic"],
                mode=CollaborationMode(session_row["mode"]),
                participant_summaries={p.custom_id: "" for p in participants},
                consensus_list=[],
                key_events=[],
            ),
            current_round=cfg.get("current_round", 0),
            next_speaker_index=cfg.get("next_speaker_index", 0),
            created_at=self._from_timestamp(session_row["created_at"]),
            updated_at=self._from_timestamp(session_row["updated_at"]),
        )
        messages = await self.message_store.load_messages(session_id)
        self._rebuild_snapshot(session, messages)
        self._apply_snapshot_override(session, cfg)
        if "current_round" not in cfg:
            session.current_round = max((m.round_number for m in messages), default=0)
        if "next_speaker_index" not in cfg:
            session.next_speaker_index = self._infer_next_speaker_index(session, messages)
        self._session_runtime.setdefault(session_id, SessionRuntimeState())
        return session

    async def get_session(self, session_id: str) -> Session:
        return await self.load_session(session_id)

    async def list_sessions(self) -> List[SessionListItem]:
        await init_db(self.db_path)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    s.id,
                    s.topic,
                    s.mode,
                    s.status,
                    s.config,
                    s.updated_at,
                    (
                        SELECT COUNT(*)
                        FROM model_participants p
                        WHERE p.session_id = s.id
                    ) AS participant_count,
                    (
                        SELECT m.content
                        FROM collaboration_messages m
                        WHERE m.session_id = s.id
                        ORDER BY m.created_at DESC, m.rowid DESC
                        LIMIT 1
                    ) AS last_message_preview
                FROM collaboration_sessions s
                ORDER BY s.updated_at DESC, s.created_at DESC, s.id DESC
                """
            ) as cursor:
                rows = await cursor.fetchall()

        items: List[SessionListItem] = []
        for row in rows:
            config = json.loads(row["config"] or "{}")
            items.append(
                SessionListItem(
                    id=row["id"],
                    title=str(config.get("display_title") or row["topic"]),
                    topic=row["topic"],
                    mode=CollaborationMode(row["mode"]),
                    status=SessionStatus(row["status"]),
                    current_round=int(config.get("current_round", 0)),
                    updated_at=int(row["updated_at"] or 0),
                    participant_count=int(row["participant_count"] or 0),
                    last_message_preview=row["last_message_preview"] or "",
                )
            )
        return items

    async def get_snapshot(self, session_id: str) -> SessionSnapshot:
        return (await self.load_session(session_id)).snapshot

    async def get_messages(self, session_id: str) -> List[CollaborationMessage]:
        await self.load_session(session_id)
        return await self.message_store.load_messages(session_id)

    async def update_session_title(
        self,
        session_id: str,
        title: Optional[str],
    ) -> Session:
        async with self._get_session_lock(session_id):
            session = await self.load_session(session_id)
            normalized = title.strip() if isinstance(title, str) else ""
            session.config.display_title = normalized or session.topic
            await self._persist_session_runtime(session)
            return session

    async def update_session_default_model(
        self,
        session_id: str,
        default_model_ref: Optional[str],
    ) -> Session:
        async with self._get_session_lock(session_id):
            session = await self.load_session(session_id)
            normalized = default_model_ref.strip() if isinstance(default_model_ref, str) else ""
            if normalized:
                validate_model_ref(normalized)
                session.config.default_model_ref = normalized
            else:
                session.config.default_model_ref = None
            await self._persist_session_runtime(session)
            return session

    async def update_workspace_can_write(
        self,
        session_id: str,
        can_write: bool,
    ) -> Session:
        async with self._get_session_lock(session_id):
            session = await self.load_session(session_id)
            workspace = session.config.workspace
            if workspace is None:
                raise ValidationError('validation error', field="workspace")

            if workspace.capabilities is None:
                workspace.capabilities = WorkspaceCapabilityManifest(
                    agent_defaults=AgentProfileConfig(
                        mode="tool_loop",
                        max_steps=6,
                        can_write=bool(can_write),
                    ),
                )
                await self._persist_session_runtime(session)
                return session

            workspace.capabilities.agent_defaults.can_write = bool(can_write)
            for override in workspace.capabilities.participant_overrides.values():
                if override.agent is not None:
                    override.agent.can_write = bool(can_write)

            await self._persist_session_runtime(session)
            return session

    async def append_participant(
        self,
        session_id: str,
        participant: ParticipantInput,
    ) -> Session:
        async with self._get_session_lock(session_id):
            session = await self.load_session(session_id)
            if session.status != SessionStatus.ACTIVE:
                raise ValidationError('validation error', field="session_status")

            runtime = self._session_runtime.setdefault(session_id, SessionRuntimeState())
            if runtime.is_generating:
                raise ValidationError('validation error', field="session_state")

            if len(session.participants) >= 10:
                raise ValidationError('validation error', field="participants")

            existing_custom_ids = {self._custom_id_key(item.custom_id) for item in session.participants}
            custom_id = self._resolve_appended_custom_id(
                participant.custom_id,
                existing_custom_ids,
            )
            model_ref = self._resolve_appended_model_ref(participant, field_name="model_ref")
            resolved_input = ParticipantInput(
                model_ref=model_ref,
                provider_id=participant.provider_id,
                custom_id=custom_id,
                role_desc=participant.role_desc,
            )
            await self._validate_provider_bindings([resolved_input])

            next_order = max((item.sequence_order for item in session.participants), default=0) + 1
            new_participant = ModelParticipant(
                id=str(uuid.uuid4()),
                session_id=session.id,
                custom_id=custom_id,
                model_ref=model_ref,
                provider_id=participant.provider_id,
                sequence_order=next_order,
                role_desc=participant.role_desc,
            )

            await init_db(self.db_path)
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    INSERT INTO model_participants
                        (id, session_id, custom_id, display_name, model_ref, provider_id, role_desc, private_info, sequence_order, is_active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_participant.id,
                        new_participant.session_id,
                        new_participant.custom_id,
                        new_participant.display_name,
                        new_participant.model_ref,
                        new_participant.provider_id,
                        new_participant.role_desc,
                        new_participant.private_info,
                        new_participant.sequence_order,
                        new_participant.is_active,
                    ),
                )
                await db.commit()

            return await self.load_session(session_id)

    async def append_participants(
        self,
        session_id: str,
        participants: List[ParticipantInput],
    ) -> Session:
        async with self._get_session_lock(session_id):
            session = await self.load_session(session_id)
            self._validate_session_can_accept_participants(session_id, session, len(participants))
            resolved_inputs = await self._resolve_appended_inputs(session, participants)
            appended_participants = self._build_appended_participants(session, resolved_inputs)
            await self._insert_model_participants(appended_participants)
            return await self.load_session(session_id)

    async def rename_participant(
        self,
        session_id: str,
        current_custom_id: str,
        new_custom_id: str,
    ) -> Session:
        async with self._get_session_lock(session_id):
            session = await self.load_session(session_id)
            if session.status != SessionStatus.ACTIVE:
                raise ValidationError('validation error', field="session_status")

            runtime = self._session_runtime.setdefault(session_id, SessionRuntimeState())
            if runtime.is_generating:
                raise ValidationError('validation error', field="session_state")

            participant = self._find_participant(session, current_custom_id)
            if participant is None:
                raise ValidationError('validation error', field="participant.custom_id")

            normalized_new_custom_id = self._normalize_custom_id(
                new_custom_id,
                field_name="participant.custom_id",
            )
            if self._custom_id_key(normalized_new_custom_id) == self._custom_id_key(participant.custom_id):
                return session

            existing_custom_ids = {
                self._custom_id_key(item.custom_id)
                for item in session.participants
                if item.id != participant.id
            }
            if self._custom_id_key(normalized_new_custom_id) in existing_custom_ids:
                raise ValidationError('validation error', field="participant.custom_id")

            old_custom_id = participant.custom_id
            participant.custom_id = normalized_new_custom_id
            participant.display_name = participant.display_name or normalized_new_custom_id
            self._rename_snapshot_participant_key(session, old_custom_id, normalized_new_custom_id)

            await init_db(self.db_path)
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "UPDATE model_participants SET custom_id = ?, display_name = ? WHERE session_id = ? AND id = ?",
                    (
                        participant.custom_id,
                        participant.display_name,
                        session_id,
                        participant.id,
                    ),
                )
                await db.commit()

            await self._persist_session_runtime(session)
            return await self.load_session(session_id)

    async def remove_participant(self, session_id: str, custom_id: str) -> Session:
        async with self._get_session_lock(session_id):
            session = await self.load_session(session_id)
            if session.status != SessionStatus.ACTIVE:
                raise ValidationError('validation error', field="session_status")

            runtime = self._session_runtime.setdefault(session_id, SessionRuntimeState())
            if runtime.is_generating:
                raise ValidationError('validation error', field="session_state")

            participant = self._find_participant(session, custom_id)
            if participant is None:
                raise ValidationError('validation error', field="participant.custom_id")

            if len(session.participants) <= 1:
                raise ValidationError('validation error', field="participants")

            session.participants = [item for item in session.participants if item.id != participant.id]
            self._remove_snapshot_participant_key(session, participant.custom_id)
            if session.participants:
                session.next_speaker_index = min(session.next_speaker_index, len(session.participants) - 1)
            else:
                session.next_speaker_index = 0

            await init_db(self.db_path)
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "DELETE FROM model_participants WHERE session_id = ? AND id = ?",
                    (session_id, participant.id),
                )
                await db.commit()

            await self._persist_session_runtime(session)
            return await self.load_session(session_id)

    async def update_snapshot(
        self,
        session_id: str,
        topic: Optional[str] = None,
        mode: Optional[CollaborationMode] = None,
        participant_summaries: Optional[Dict[str, str]] = None,
        consensus_list: Optional[List[str]] = None,
        key_events: Optional[List[str]] = None,
    ) -> SessionSnapshot:
        async with self._get_session_lock(session_id):
            session = await self.load_session(session_id)
            if topic is not None:
                session.topic = topic
                session.snapshot.topic = topic
            if mode is not None:
                session.mode = mode
                session.snapshot.mode = mode
            if participant_summaries is not None:
                session.snapshot.participant_summaries.update(participant_summaries)
            if consensus_list is not None:
                session.snapshot.consensus_list = list(consensus_list)
            if key_events is not None:
                session.snapshot.key_events = list(key_events)
            await self._persist_session_runtime(session)
            return session.snapshot

    async def create_checkpoint(self, session_id: str) -> Checkpoint:
        return await self.context_compressor.write_checkpoint(await self.load_session(session_id))

    async def restore_from_checkpoint(self, checkpoint_id: str) -> Session:
        checkpoint, snapshot = await self.context_compressor.restore_from_checkpoint(checkpoint_id, self.db_path)
        session = await self.load_session(checkpoint.session_id)
        session.snapshot = snapshot
        await self._persist_session_runtime(session)
        return session

    async def dispatch_next(self, session_id: str) -> AsyncIterator[StreamChunk]:
        session = await self.load_session(session_id)
        if session.mode == CollaborationMode.CODE_WORKSPACE:
            async for chunk in self._dispatch_workspace_round(session_id):
                yield chunk
            return

        runtime = self._session_runtime.setdefault(session_id, SessionRuntimeState())
        async with self._get_session_lock(session_id):
            session = await self.load_session(session_id)
            if session.status != SessionStatus.ACTIVE:
                return
            strategy = self.strategy_registry.get(session.mode)
            messages = await self.message_store.load_messages(session_id)
            comp = await self.context_compressor.check_and_compress(session, messages, self._summarize_for_compression)
            if comp.action != "none":
                yield StreamChunk("compression", round_number=session.current_round, metadata={"action": comp.action, "masked_count": comp.masked_count, "checkpoint_id": comp.checkpoint_id})
            participant = strategy.get_next_speaker(session, messages)
            if participant is None:
                summary = await self.terminate_session(session.id, "no_active_participants")
                yield StreamChunk("session_end", round_number=session.current_round, metadata={"reason": summary.reason, "summary": summary.summary_text})
                return
            if session.next_speaker_index == 0:
                session.current_round += 1
            runtime.is_generating = True
            try:
                full_content = ""
                renderable_messages = await self.message_store.load_renderable_messages(session_id)
                yield self._stream_chunk(
                    "phase_start",
                    round_number=session.current_round,
                    participant_id=participant.custom_id,
                    phase="build_prompt",
                    summary="step",
                )
                prompt_messages = self._build_dispatch_messages(session, participant, renderable_messages, runtime.manual_topic_reminder, strategy.build_system_prompt(participant, session))
                runtime.manual_topic_reminder = False
                yield self._stream_chunk(
                    "phase_end",
                    round_number=session.current_round,
                    participant_id=participant.custom_id,
                    phase="build_prompt",
                    summary="step",
                    input_message_count=len(prompt_messages),
                )
                yield StreamChunk(
                    "turn_start",
                    participant_id=participant.custom_id,
                    round_number=session.current_round,
                    metadata={"execution_mode": "stream"},
                )
                try:
                    yield self._stream_chunk(
                        "model_request",
                        round_number=session.current_round,
                        participant_id=participant.custom_id,
                        summary="发起模型请求",
                        model_ref=participant.model_ref,
                    )
                    emitted_model_response = False
                    async for chunk in self._iter_model_stream(
                        participant,
                        prompt_messages,
                        round_number=session.current_round,
                    ):
                        if isinstance(chunk, StreamChunk):
                            yield chunk
                            continue
                        if not emitted_model_response:
                            emitted_model_response = True
                            yield self._stream_chunk(
                                "model_response",
                                round_number=session.current_round,
                                participant_id=participant.custom_id,
                                summary="step",
                                model_ref=participant.model_ref,
                            )
                        full_content += chunk
                        yield StreamChunk("chunk", participant_id=participant.custom_id, content=chunk, round_number=session.current_round)
                except Exception as exc:
                    logger.exception("参与者 %s 调用失败", participant.custom_id)
                    self._advance_to_next_participant(session)
                    await self._persist_session_runtime(session)
                    await self._flush_pending_user_messages(session, runtime)
                    yield StreamChunk(
                        "participant_error",
                        participant_id=participant.custom_id,
                        round_number=session.current_round,
                        metadata=_participant_error_metadata(exc, participant),
                    )
                    return

                message = CollaborationMessage(
                    id=str(uuid.uuid4()),
                    session_id=session.id,
                    sender_id=participant.custom_id,
                    message_type=MessageType.DIALOGUE,
                    content=full_content,
                    round_number=session.current_round,
                )
                drift = self.drift_detector.check_drift(message, session)
                if drift.score is not None:
                    message.drift_score = drift.score
                strategy.on_message_received(message, session)
                await self.message_store.store_message(message)
                yield self._stream_chunk(
                    "state_write",
                    round_number=session.current_round,
                    participant_id=participant.custom_id,
                    target="message",
                    summary="step",
                )
                if drift.is_drifted:
                    runtime.manual_topic_reminder = self._should_schedule_topic_reminder(session, messages, drift.score)
                    yield StreamChunk("drift_alert", participant_id=participant.custom_id, round_number=session.current_round, metadata={"score": drift.score})
                self.snapshot_manager.update(session, participant, message.content)
                self._advance_to_next_participant(session)
                await self._persist_session_runtime(session)
                await self._flush_pending_user_messages(session, runtime)
                yield StreamChunk("turn_end", participant_id=participant.custom_id, round_number=session.current_round)
                termination = strategy.check_termination(session, await self.message_store.load_messages(session.id))
                if termination and termination.should_terminate:
                    summary = await self.terminate_session(session.id, termination.reason)
                    yield StreamChunk("session_end", round_number=session.current_round, metadata={"reason": summary.reason, "summary": summary.summary_text})
            finally:
                runtime.is_generating = False

    async def dispatch_round(self, session_id: str) -> AsyncIterator[StreamChunk]:
        session = await self.load_session(session_id)
        if session.mode == CollaborationMode.CODE_WORKSPACE:
            async for chunk in self._dispatch_workspace_round(session_id):
                yield chunk
            return

        emitted_turn = False

        while True:
            emitted_any = False
            async for chunk in self.dispatch_next(session_id):
                emitted_any = True
                if chunk.event in {"turn_end", "participant_error"}:
                    emitted_turn = True
                yield chunk
                if chunk.event in {"session_end", "error"}:
                    return

            if not emitted_any:
                return

            session = await self.load_session(session_id)
            if session.status != SessionStatus.ACTIVE:
                return

            if emitted_turn and session.next_speaker_index == 0:
                yield StreamChunk("round_end", round_number=session.current_round)
                return

    async def _dispatch_workspace_round(self, session_id: str) -> AsyncIterator[StreamChunk]:
        runtime = self._session_runtime.setdefault(session_id, SessionRuntimeState())
        async with self._get_session_lock(session_id):
            session = await self.load_session(session_id)
            if session.status != SessionStatus.ACTIVE:
                return
            if session.config.workspace is None:
                raise ValidationError('validation error', field="workspace")

            try:
                yield self._stream_chunk(
                    "phase_start",
                    round_number=session.current_round,
                    phase="scan_workspace",
                    summary="step",
                )
                workspace_scan = scan_workspace(
                    session.config.workspace.root_path,
                    session.config.workspace.scan_excludes,
                )
                yield self._stream_chunk(
                    "phase_end",
                    round_number=session.current_round,
                    phase="scan_workspace",
                    summary="step",
                    file_count=len(workspace_scan.files),
                )
            except ValidationError as exc:
                yield StreamChunk(
                    "error",
                    round_number=session.current_round,
                    metadata={"code": "WORKSPACE_INVALID", "message": exc.message},
                )
                return

            messages = await self.message_store.load_renderable_messages(session_id)
            session.current_round += 1
            session.next_speaker_index = 0

            latest_user_message = ""
            for message in reversed(messages):
                if message.message_type == MessageType.USER_INTERVENTION:
                    latest_user_message = message.content
                    break

            yield self._stream_chunk(
                "phase_start",
                round_number=session.current_round,
                phase="resolve_targets",
                summary="step",
            )
            targets = resolve_workspace_targets(latest_user_message, session.participants)
            yield self._stream_chunk(
                "phase_end",
                round_number=session.current_round,
                phase="resolve_targets",
                summary="step",
                target_count=len(targets),
            )
            if not targets:
                return

            runtime.is_generating = True
            try:
                mcp_runtime = self._mcp_runtime_factory(
                    session.config.workspace.capabilities if session.config.workspace else None
                )
                workspace_runtime = WorkspaceExecutionRuntime(
                    workspace_root=session.config.workspace.root_path if session.config.workspace else None,
                    manifest=session.config.workspace.capabilities if session.config.workspace else None,
                    mcp_runtime=mcp_runtime,
                )
                for participant in targets:
                    yield self._stream_chunk(
                        "phase_start",
                        round_number=session.current_round,
                        participant_id=participant.custom_id,
                        phase="build_prompt",
                        summary="构建工作区上下文",
                    )
                    prompt_messages = self._build_workspace_dispatch_messages(
                        session,
                        participant,
                        messages,
                        workspace_scan,
                        await workspace_runtime.render_tool_context(participant.custom_id),
                    )
                    yield self._stream_chunk(
                        "phase_end",
                        round_number=session.current_round,
                        participant_id=participant.custom_id,
                        phase="build_prompt",
                        summary="工作区上下文构建完成",
                        input_message_count=len(prompt_messages),
                    )
                    agent_profile = resolve_workspace_agent_profile(
                        session.config.workspace.capabilities if session.config.workspace else None,
                        participant.custom_id,
                    )
                    execution_mode = (
                        "agent"
                        if agent_profile is not None and agent_profile.mode not in {"disabled", "none"}
                        else "stream"
                    )
                    yield StreamChunk(
                        "turn_start",
                        participant_id=participant.custom_id,
                        round_number=session.current_round,
                        metadata={"execution_mode": execution_mode},
                    )
                    if agent_profile is not None and agent_profile.mode not in {"disabled", "none"}:
                        agent_event_queue: asyncio.Queue[Optional[WorkspaceAgentEvent]] = asyncio.Queue()

                        async def emit_agent_event(event: WorkspaceAgentEvent) -> None:
                            await agent_event_queue.put(event)

                        try:
                            runner = WorkspaceAgentRunner(workspace_runtime)

                            async def collect_agent_model_output(
                                current_prompt_messages: List[Dict[str, str]],
                                current_participant: ModelParticipant = participant,
                            ) -> str:
                                output = ""
                                sequence = 0
                                async for model_chunk in self._iter_model_stream(
                                    current_participant,
                                    current_prompt_messages,
                                    round_number=session.current_round,
                                ):
                                    if isinstance(model_chunk, StreamChunk):
                                        await emit_agent_event(
                                            WorkspaceAgentEvent(
                                                model_chunk.event,
                                                participant_id=model_chunk.participant_id,
                                                content=model_chunk.content,
                                                round_number=model_chunk.round_number,
                                                metadata=dict(model_chunk.metadata),
                                            )
                                        )
                                        continue
                                    if not model_chunk:
                                        continue
                                    output += model_chunk
                                    sequence += 1
                                    await emit_agent_event(
                                        WorkspaceAgentEvent(
                                            "model_output",
                                            participant_id=current_participant.custom_id,
                                            content=model_chunk,
                                            round_number=session.current_round,
                                            metadata={
                                                "summary": "模型输出中",
                                                "model_ref": current_participant.model_ref,
                                                "sequence": sequence,
                                            },
                                        )
                                    )
                                return output

                            async def run_agent() -> WorkspaceAgentRunResult:
                                try:
                                    return await runner.run(
                                        session=session,
                                        participant=participant,
                                        prompt_messages=prompt_messages,
                                        round_number=session.current_round,
                                        model_stream_factory=collect_agent_model_output,
                                        persist_tool_message=self.message_store.store_message,
                                        emit_event=emit_agent_event,
                                    )
                                finally:
                                    await agent_event_queue.put(None)

                            agent_task = asyncio.create_task(run_agent())
                            failed = False
                            while True:
                                agent_event = await agent_event_queue.get()
                                if agent_event is None:
                                    break
                                if agent_event.event == "participant_error":
                                    failed = True
                                yield StreamChunk(
                                    agent_event.event,
                                    participant_id=agent_event.participant_id,
                                    content=agent_event.content,
                                    round_number=agent_event.round_number,
                                    metadata=dict(agent_event.metadata),
                                )
                            agent_result = await agent_task
                        except Exception as exc:
                            logger.exception("工作区 agent 参与者 %s 执行失败", participant.custom_id)
                            yield StreamChunk(
                                "participant_error",
                                participant_id=participant.custom_id,
                                round_number=session.current_round,
                                metadata={"code": "WORKSPACE_AGENT_ERROR", "message": str(exc)},
                            )
                            continue
                        for tool_message in agent_result.persisted_messages:
                            messages.append(tool_message)
                        full_content = agent_result.final_content
                        if failed:
                            if agent_result.failure_summary:
                                failure_message = CollaborationMessage(
                                    id=str(uuid.uuid4()),
                                    session_id=session.id,
                                    sender_id=participant.custom_id,
                                    message_type=MessageType.DIALOGUE,
                                    content=agent_result.failure_summary,
                                    round_number=session.current_round,
                                )
                                await self.message_store.store_message(failure_message)
                                yield StreamChunk(
                                    "chunk",
                                    participant_id=participant.custom_id,
                                    content=failure_message.content,
                                    round_number=session.current_round,
                                )
                                yield self._stream_chunk(
                                    "state_write",
                                    round_number=session.current_round,
                                    participant_id=participant.custom_id,
                                    target="message",
                                    summary="step",
                                )
                                self.snapshot_manager.update(session, participant, failure_message.content)
                                messages.append(failure_message)
                            continue
                    else:
                        full_content = ""
                        try:
                            yield self._stream_chunk(
                                "model_request",
                                round_number=session.current_round,
                                participant_id=participant.custom_id,
                                summary="发起模型请求",
                                model_ref=participant.model_ref,
                            )
                            emitted_model_response = False
                            async for chunk in self._iter_model_stream(
                                participant,
                                prompt_messages,
                                round_number=session.current_round,
                            ):
                                if isinstance(chunk, StreamChunk):
                                    yield chunk
                                    continue
                                if not emitted_model_response:
                                    emitted_model_response = True
                                    yield self._stream_chunk(
                                        "model_response",
                                        round_number=session.current_round,
                                        participant_id=participant.custom_id,
                                        summary="step",
                                        model_ref=participant.model_ref,
                                    )
                                full_content += chunk
                                yield StreamChunk(
                                    "chunk",
                                    participant_id=participant.custom_id,
                                    content=chunk,
                                    round_number=session.current_round,
                                )
                        except Exception as exc:
                            logger.exception("工作区参与者 %s 调用失败", participant.custom_id)
                            yield StreamChunk(
                                "participant_error",
                                participant_id=participant.custom_id,
                                round_number=session.current_round,
                                metadata=_participant_error_metadata(exc, participant),
                            )
                            continue

                    if not full_content.strip():
                        yield StreamChunk("turn_end", participant_id=participant.custom_id, round_number=session.current_round)
                        continue

                    message = CollaborationMessage(
                        id=str(uuid.uuid4()),
                        session_id=session.id,
                        sender_id=participant.custom_id,
                        message_type=MessageType.DIALOGUE,
                        content=full_content,
                        round_number=session.current_round,
                    )
                    drift = self.drift_detector.check_drift(message, session)
                    if drift.score is not None:
                        message.drift_score = drift.score
                    await self.message_store.store_message(message)
                    yield self._stream_chunk(
                        "state_write",
                        round_number=session.current_round,
                        participant_id=participant.custom_id,
                        target="message",
                        summary="step",
                    )
                    self.snapshot_manager.update(session, participant, message.content)
                    messages.append(message)
                    yield StreamChunk("turn_end", participant_id=participant.custom_id, round_number=session.current_round)

                await self._persist_session_runtime(session)
                yield self._stream_chunk(
                    "state_write",
                    round_number=session.current_round,
                    target="session",
                    summary="step",
                )
                yield StreamChunk("round_end", round_number=session.current_round)
            finally:
                runtime.is_generating = False

    async def inject_user_message(self, session_id: str, content: str) -> None:
        if not content or not content.strip():
            raise ValidationError('validation error', field="content")
        runtime = self._session_runtime.setdefault(session_id, SessionRuntimeState())
        if runtime.is_generating:
            runtime.pending_user_messages.append(content)
            return
        async with self._get_session_lock(session_id):
            session = await self.load_session(session_id)
            await self._store_user_message(session, content)
            await self._persist_session_runtime(session)

    async def export_session_history(self, session_id: str) -> str:
        session = await self.load_session(session_id)
        messages = await self.message_store.load_messages(session_id)
        lines = [f"Session ID: {session.id}", f"Topic: {session.topic}", f"Mode: {session.mode.value}", ""]
        for message in messages:
            msg_type = message.message_type.value if hasattr(message.message_type, "value") else str(message.message_type)
            lines.append(f"[Round {message.round_number}] [{message.sender_id}|{msg_type}] {message.content}")
        return "\n".join(lines)

    async def clone_session(self, source_session_id: str, topic: Optional[str] = None, mode: Optional[CollaborationMode] = None) -> Session:
        source = await self.load_session(source_session_id)
        exported = await self.export_session_history(source_session_id)
        session = await self.create_session(
            CreateSessionRequest(
                topic=topic or f"{source.topic}（延续会话）",
                mode=mode or source.mode,
                participants=[ParticipantInput(p.model_ref, p.provider_id, p.custom_id, p.role_desc) for p in source.participants],
                max_rounds=source.config.max_rounds,
                drift_threshold=source.config.drift_threshold,
                retention_window=source.config.retention_window,
                context_threshold=source.config.context_threshold,
                summary_model=source.config.summary_model,
                workspace=source.config.workspace,
            )
        )
        await self.inject_user_message(session.id, f"[历史继承]\n{exported}\n[历史继承结束]")
        return session

    async def terminate_session(self, session_id: str, reason: str) -> SessionSummary:
        session = await self.load_session(session_id)
        messages = await self.message_store.load_messages(session_id)
        summary_text = self._build_session_summary_text(session, messages, reason)
        await init_db(self.db_path)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE collaboration_sessions SET status = ?, updated_at = ? WHERE id = ?", (SessionStatus.ENDED.value, int(time.time()), session.id))
            await db.commit()
        return SessionSummary(session.id, reason, summary_text)

    async def delete_session(self, session_id: str, reason: str = "user_deleted") -> SessionSummary:
        session = await self.load_session(session_id)
        messages = await self.message_store.load_messages(session_id)
        summary_text = self._build_session_summary_text(session, messages, reason)

        await init_db(self.db_path)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys=ON;")
            await db.execute("DELETE FROM model_participants WHERE session_id = ?", (session_id,))
            await db.execute("DELETE FROM collaboration_messages WHERE session_id = ?", (session_id,))
            await db.execute("DELETE FROM compressed_summaries WHERE session_id = ?", (session_id,))
            await db.execute("DELETE FROM checkpoints WHERE session_id = ?", (session_id,))
            await db.execute("DELETE FROM collaboration_sessions WHERE id = ?", (session_id,))
            await db.commit()

        self._session_runtime.pop(session_id, None)
        self._session_locks.pop(session_id, None)
        return SessionSummary(session.id, reason, summary_text)

    def _resolve_custom_ids(self, inputs: List[ParticipantInput]) -> List[ParticipantInput]:
        resolved, seen = [], set()
        for index, item in enumerate(inputs):
            custom_id = self._normalize_custom_id(
                item.custom_id or f"Model_{index + 1}",
                field_name=f"participants[{index}].custom_id",
            )
            custom_id_key = self._custom_id_key(custom_id)
            if custom_id_key in seen:
                raise ValidationError(
                    f"custom_id 不能重复: {custom_id}",
                    field=f"participants[{index}].custom_id",
                )
            seen.add(custom_id_key)
            resolved.append(ParticipantInput(item.model_ref, item.provider_id, custom_id, item.role_desc))
        return resolved

    def _validate_session_can_accept_participants(
        self,
        session_id: str,
        session: Session,
        append_count: int,
    ) -> None:
        if session.status != SessionStatus.ACTIVE:
            raise ValidationError('validation error', field="session_status")

        runtime = self._session_runtime.setdefault(session_id, SessionRuntimeState())
        if runtime.is_generating:
            raise ValidationError('validation error', field="session_state")

        if append_count <= 0:
            raise ValidationError('validation error', field="participants")

        if len(session.participants) + append_count > 10:
            raise ValidationError('validation error', field="participants")

    @staticmethod
    def _custom_id_key(custom_id: str) -> str:
        return custom_id.strip().lower()

    def _normalize_custom_id(self, custom_id: Optional[str], *, field_name: str) -> str:
        normalized = custom_id.strip() if isinstance(custom_id, str) else ""
        if not (1 <= len(normalized) <= 32):
            raise ValidationError("custom_id 长度必须在 1-32 之间", field=field_name)
        if self._custom_id_key(normalized) == "all":
            raise ValidationError("custom_id 不能使用保留别名 all", field=field_name)
        return normalized

    async def _resolve_appended_inputs(
        self,
        session: Session,
        participants: List[ParticipantInput],
    ) -> List[ParticipantInput]:
        existing_custom_ids = {self._custom_id_key(item.custom_id) for item in session.participants}
        resolved: List[ParticipantInput] = []

        for index, participant in enumerate(participants):
            custom_id = self._resolve_appended_custom_id(
                participant.custom_id,
                existing_custom_ids,
                field_name=f"participants[{index}].custom_id",
            )
            model_ref = self._resolve_appended_model_ref(
                participant,
                field_name=f"participants[{index}].model_ref",
            )
            resolved_input = ParticipantInput(
                model_ref=model_ref,
                provider_id=participant.provider_id,
                custom_id=custom_id,
                role_desc=participant.role_desc,
            )
            existing_custom_ids.add(self._custom_id_key(custom_id))
            resolved.append(resolved_input)

        await self._validate_provider_bindings(resolved)
        return resolved

    def _find_participant(self, session: Session, custom_id: str) -> Optional[ModelParticipant]:
        target_key = self._custom_id_key(custom_id)
        for participant in session.participants:
            if self._custom_id_key(participant.custom_id) == target_key:
                return participant
        return None

    def _rename_snapshot_participant_key(self, session: Session, old_custom_id: str, new_custom_id: str) -> None:
        old_key = self._custom_id_key(old_custom_id)
        renamed: Dict[str, str] = {}
        updated = False
        for key, value in session.snapshot.participant_summaries.items():
            if self._custom_id_key(key) == old_key:
                renamed[new_custom_id] = value
                updated = True
            else:
                renamed[key] = value
        if not updated:
            renamed[new_custom_id] = session.snapshot.participant_summaries.get(old_custom_id, "")
        session.snapshot.participant_summaries = renamed

    def _remove_snapshot_participant_key(self, session: Session, custom_id: str) -> None:
        target_key = self._custom_id_key(custom_id)
        session.snapshot.participant_summaries = {
            key: value
            for key, value in session.snapshot.participant_summaries.items()
            if self._custom_id_key(key) != target_key
        }

    def _resolve_appended_model_ref(
        self,
        participant: ParticipantInput,
        field_name: str,
    ) -> str:
        model_ref = participant.model_ref.strip() if isinstance(participant.model_ref, str) else ""
        if not model_ref:
            raise ValidationError('validation error', field=field_name)

        if "/" in model_ref:
            validate_model_ref(model_ref)
            return model_ref

        if participant.provider_id:
            return model_ref

        raise ValidationError('validation error', field=field_name)

    def _build_appended_participants(
        self,
        session: Session,
        participants: List[ParticipantInput],
    ) -> List[ModelParticipant]:
        next_order = max((item.sequence_order for item in session.participants), default=0) + 1
        resolved: List[ModelParticipant] = []

        for offset, participant in enumerate(participants):
            resolved.append(
                ModelParticipant(
                    id=str(uuid.uuid4()),
                    session_id=session.id,
                    custom_id=participant.custom_id or "",
                    model_ref=participant.model_ref,
                    provider_id=participant.provider_id,
                    sequence_order=next_order + offset,
                    role_desc=participant.role_desc,
                )
            )

        return resolved

    async def _insert_model_participants(
        self,
        participants: List[ModelParticipant],
    ) -> None:
        if not participants:
            return

        await init_db(self.db_path)
        async with aiosqlite.connect(self.db_path) as db:
            for participant in participants:
                await db.execute(
                    """
                    INSERT INTO model_participants
                        (id, session_id, custom_id, display_name, model_ref, provider_id, role_desc, private_info, sequence_order, is_active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        participant.id,
                        participant.session_id,
                        participant.custom_id,
                        participant.display_name,
                        participant.model_ref,
                        participant.provider_id,
                        participant.role_desc,
                        participant.private_info,
                        participant.sequence_order,
                        participant.is_active,
                    ),
                )
            await db.commit()

    def _resolve_appended_custom_id(
        self,
        requested_custom_id: Optional[str],
        existing_custom_ids: set[str],
        field_name: str = "participant.custom_id",
    ) -> str:
        if requested_custom_id and requested_custom_id.strip():
            custom_id = self._normalize_custom_id(requested_custom_id, field_name=field_name)
        else:
            candidate_index = len(existing_custom_ids) + 1
            custom_id = f"Model_{candidate_index}"
            while self._custom_id_key(custom_id) in existing_custom_ids:
                candidate_index += 1
                custom_id = f"Model_{candidate_index}"

        if self._custom_id_key(custom_id) in existing_custom_ids:
            raise ValidationError(f"custom_id 不能重复: {custom_id}", field=field_name)
        return custom_id

    def _build_session_summary_text(
        self,
        session: Session,
        messages: List[CollaborationMessage],
        reason: str,
    ) -> str:
        summary_text = self.strategy_registry.get(session.mode).build_summary(session, messages, reason)
        if len(messages) < 2:
            summary_text = f"session ended: {reason}; insufficient messages, no detailed summary generated."
        return summary_text

    async def _persist_session(self, session: Session) -> None:
        await init_db(self.db_path)
        now_ts = int(time.time())
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA foreign_keys=ON;")
            await db.execute("INSERT INTO collaboration_sessions (id, topic, mode, status, config, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)", (session.id, session.topic, session.mode.value, session.status.value, self._serialize_session_config(session), now_ts, now_ts))
            for participant in session.participants:
                await db.execute("INSERT INTO model_participants (id, session_id, custom_id, display_name, model_ref, provider_id, role_desc, private_info, sequence_order, is_active) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (participant.id, participant.session_id, participant.custom_id, participant.display_name, participant.model_ref, participant.provider_id, participant.role_desc, participant.private_info, participant.sequence_order, participant.is_active))
            await db.commit()

    async def _persist_session_runtime(self, session: Session) -> None:
        session.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await init_db(self.db_path)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("UPDATE collaboration_sessions SET topic = ?, mode = ?, config = ?, updated_at = ? WHERE id = ?", (session.topic, session.mode.value, self._serialize_session_config(session), int(session.updated_at.timestamp()), session.id))
            await db.commit()

    def _serialize_session_config(self, session: Session) -> str:
        payload: Dict[str, object] = {
            "max_rounds": session.config.max_rounds,
            "drift_threshold": session.config.drift_threshold,
            "retention_window": session.config.retention_window,
            "context_threshold": session.config.context_threshold,
            "summary_model": session.config.summary_model,
            "default_model_ref": session.config.default_model_ref,
            "delegate_all_tools": session.config.delegate_all_tools,
            "display_title": session.config.display_title or session.topic,
            "current_round": session.current_round,
            "next_speaker_index": session.next_speaker_index,
            "snapshot_override": {
                "topic": session.snapshot.topic,
                "mode": session.snapshot.mode.value if hasattr(session.snapshot.mode, "value") else str(session.snapshot.mode),
                "participant_summaries": session.snapshot.participant_summaries,
                "consensus_list": session.snapshot.consensus_list,
                "key_events": session.snapshot.key_events,
            },
        }
        workspace = self._serialize_workspace_config(session.config.workspace)
        if workspace is not None:
            payload["workspace"] = workspace
        return json.dumps(payload, ensure_ascii=False)

    def _serialize_workspace_config(self, workspace: Optional[WorkspaceConfig]) -> Optional[Dict[str, object]]:
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

    def _deserialize_workspace_config(self, workspace: object) -> Optional[WorkspaceConfig]:
        if not isinstance(workspace, dict):
            return None
        root_path = str(workspace.get("root_path") or "").strip()
        if not root_path:
            return None
        scan_excludes = workspace.get("scan_excludes")
        selected_paths = workspace.get("selected_paths")
        return WorkspaceConfig(
            root_path=root_path,
            display_name=str(workspace["display_name"]).strip() if isinstance(workspace.get("display_name"), str) and str(workspace.get("display_name")).strip() else None,
            repo_fingerprint=str(workspace["repo_fingerprint"]).strip() if isinstance(workspace.get("repo_fingerprint"), str) and str(workspace.get("repo_fingerprint")).strip() else None,
            scan_excludes=[str(item).strip() for item in scan_excludes if str(item).strip()] if isinstance(scan_excludes, list) else [],
            selected_paths=[str(item).strip() for item in selected_paths if str(item).strip()] if isinstance(selected_paths, list) else [],
            index_status=str(workspace.get("index_status") or "pending"),
            last_scanned_at=int(workspace["last_scanned_at"]) if isinstance(workspace.get("last_scanned_at"), (int, float)) else None,
            summary=str(workspace["summary"]).strip() if isinstance(workspace.get("summary"), str) and str(workspace.get("summary")).strip() else None,
            capabilities=workspace_capabilities_from_dict(workspace.get("capabilities")),
        )

    def _rebuild_snapshot(self, session: Session, messages: List[CollaborationMessage]) -> None:
        session.snapshot = self.snapshot_manager.init_snapshot(session)
        strategy = self.strategy_registry.get(session.mode)
        participant_map = {p.custom_id: p for p in session.participants}
        for message in messages:
            if message.message_type == MessageType.USER_INTERVENTION:
                self.snapshot_manager.add_key_event(session, f"User injected: {message.content}")
            elif message.message_type == MessageType.DIALOGUE and message.sender_id in participant_map:
                self.snapshot_manager.update(session, participant_map[message.sender_id], message.content)
                strategy.on_message_received(message, session)

    def _apply_snapshot_override(self, session: Session, config: Dict[str, object]) -> None:
        override = config.get("snapshot_override")
        if not isinstance(override, dict):
            return
        if "topic" in override:
            session.topic = str(override["topic"])
            session.snapshot.topic = session.topic
        if "mode" in override:
            try:
                mode = CollaborationMode(str(override["mode"]))
                session.mode = mode
                session.snapshot.mode = mode
            except ValueError:
                pass
        if isinstance(override.get("participant_summaries"), dict):
            session.snapshot.participant_summaries.update(override["participant_summaries"])
        if isinstance(override.get("consensus_list"), list):
            session.snapshot.consensus_list = list(override["consensus_list"])
        if isinstance(override.get("key_events"), list):
            session.snapshot.key_events = list(override["key_events"])

    def _infer_next_speaker_index(self, session: Session, messages: List[CollaborationMessage]) -> int:
        suffix = messages
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].message_type == MessageType.USER_INTERVENTION:
                suffix = messages[index + 1 :]
                break
        participant_ids = {p.custom_id for p in session.participants}
        return len([m for m in suffix if m.message_type == MessageType.DIALOGUE and m.sender_id in participant_ids]) % max(len(session.participants), 1)

    def _get_session_lock(self, session_id: str) -> asyncio.Lock:
        self._session_locks.setdefault(session_id, asyncio.Lock())
        return self._session_locks[session_id]

    def _stream_chunk(
        self,
        event: str,
        *,
        round_number: int,
        participant_id: Optional[str] = None,
        content: str = "",
        **metadata: object,
    ) -> StreamChunk:
        return StreamChunk(
            event,
            participant_id=participant_id,
            content=content,
            round_number=round_number,
            metadata={key: value for key, value in metadata.items() if value is not None},
        )

    def _advance_to_next_participant(self, session: Session) -> None:
        session.next_speaker_index = 0 if not session.participants else (session.next_speaker_index + 1) % len(session.participants)

    def _build_dispatch_messages(self, session: Session, participant: ModelParticipant, messages: List[CollaborationMessage], include_topic_reminder: bool, system_prompt: str) -> List[Dict[str, str]]:
        parts = [self.anchor_injector.build_anchor(participant, session)]
        if include_topic_reminder:
            parts.append(self.drift_detector.build_topic_reminder(session))
        parts.append(self.snapshot_manager.render_snapshot(session.snapshot))
        history = self.message_store.build_message_history(messages, participant)
        if history:
            parts.append(history)
        return [{"role": "system", "content": system_prompt}, {"role": "user", "content": "\n\n".join(parts)}]

    def _build_workspace_dispatch_messages(
        self,
        session: Session,
        participant: ModelParticipant,
        messages: List[CollaborationMessage],
        workspace_scan,
        tool_context: str = "",
    ) -> List[Dict[str, str]]:
        system_prompt = self.strategy_registry.get(session.mode).build_system_prompt(participant, session)
        parts = [self.anchor_injector.build_anchor(participant, session, include_topic=False)]
        parts.append(self.snapshot_manager.render_snapshot(session.snapshot, include_topic=False))
        history = self.message_store.build_message_history(messages, participant)
        if history:
            parts.append(history)
        skill_context = build_workspace_skill_context(session, participant)
        if skill_context:
            parts.append(skill_context)
        if tool_context:
            parts.append(tool_context)
        parts.append(build_workspace_file_context(session, workspace_scan))
        return [{"role": "system", "content": system_prompt}, {"role": "user", "content": "\n\n".join(parts)}]

    async def _collect_model_output(
        self,
        participant: ModelParticipant,
        prompt_messages: List[Dict[str, str]],
    ) -> str:
        output = ""
        async for chunk in self._iter_model_stream(participant, prompt_messages):
            if isinstance(chunk, StreamChunk):
                continue
            output += chunk
        return output

    async def _iter_model_stream(
        self,
        participant: ModelParticipant,
        prompt_messages: List[Dict[str, str]],
        round_number: int = 0,
    ) -> AsyncIterator[Union[str, StreamChunk]]:
        provider_candidates = await self._resolve_provider_candidates(participant)
        if not provider_candidates:
            async for chunk in self.gateway.chat_stream(
                participant.model_ref,
                prompt_messages,
                self._resolve_auth_config(participant),
            ):
                yield chunk
            return

        last_error: Optional[Exception] = None
        for index, provider in enumerate(provider_candidates):
            emitted_output = False
            try:
                async for chunk in self.gateway.chat_stream(
                    participant.model_ref,
                    prompt_messages,
                    provider_config=provider,
                    on_auth_update=self._provider_auth_update_callback(provider),
                ):
                    emitted_output = True
                    yield chunk
                return
            except Exception as exc:
                if emitted_output:
                    raise
                setattr(exc, "_mmd_provider_config", provider)
                last_error = exc
                logger.warning(
                    "Provider %s failed before emitting output for %s",
                    provider.name,
                    participant.custom_id,
                )
                if index < len(provider_candidates) - 1:
                    yield StreamChunk(
                        "participant_error",
                        participant_id=participant.custom_id,
                        round_number=round_number,
                        metadata=_participant_error_metadata(exc, participant, provider),
                    )

        if last_error is not None:
            raise last_error

    def _provider_auth_update_callback(self, provider: ProviderConfig):
        async def _callback(updated_auth_config: AuthConfig) -> None:
            await self._persist_provider_auth_update(provider, updated_auth_config)

        return _callback

    async def _persist_provider_auth_update(
        self,
        provider: ProviderConfig,
        updated_auth_config: AuthConfig,
    ) -> None:
        if updated_auth_config.auth_type != AuthType.OAUTH or updated_auth_config.oauth_token is None:
            return

        provider.auth_type = AuthType.OAUTH
        provider.auth_config = updated_auth_config
        auth_json = json.dumps(serialize_auth_config(updated_auth_config), ensure_ascii=False)

        await init_db(self.db_path)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE provider_configs SET auth_type = ?, auth_config = ? WHERE id = ?",
                (AuthType.OAUTH.value, auth_json, provider.id),
            )
            await db.commit()
        logger.info("OAuth token refresh persisted for provider %s", provider.name)

    def _resolve_auth_config(self, participant: ModelParticipant) -> AuthConfig:
        del participant
        return AuthConfig(auth_type=AuthType.IAM)

    async def _validate_provider_bindings(self, participants: List[ParticipantInput]) -> None:
        provider_ids = sorted({item.provider_id for item in participants if item.provider_id})
        if not provider_ids:
            return

        await init_db(self.db_path)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            placeholders = ",".join("?" for _ in provider_ids)
            query = f"SELECT id FROM provider_configs WHERE id IN ({placeholders})"
            async with db.execute(query, provider_ids) as cursor:
                rows = await cursor.fetchall()
        existing = {row["id"] for row in rows}
        for index, participant in enumerate(participants):
            if participant.provider_id and participant.provider_id not in existing:
                raise ValidationError('validation error', field=f"participants[{index}].provider_id")

    async def _resolve_provider_candidates(self, participant: ModelParticipant) -> List[ProviderConfig]:
        if participant.provider_id:
            primary = await self._load_provider_by_id(participant.provider_id)
            if primary is None:
                return []
            candidates = await self._expand_provider_chain([primary], primary.fallback_ids)
            compatible_fallbacks = await self._load_compatible_model_fallbacks(
                primary,
                participant.model_ref,
                {provider.id for provider in candidates},
            )
            return [*candidates, *compatible_fallbacks]

        try:
            provider_key, _ = validate_model_ref(participant.model_ref)
        except ValidationError:
            return []

        named = await self._load_providers_by_query(
            "SELECT * FROM provider_configs WHERE is_active = 1 AND name = ? ORDER BY name ASC",
            (provider_key,),
        )
        if named:
            primary = named[0]
            return await self._expand_provider_chain([primary], primary.fallback_ids)

        typed = await self._load_providers_by_query(
            "SELECT * FROM provider_configs WHERE is_active = 1 AND provider_type = ? ORDER BY name ASC",
            (provider_key,),
        )
        if not typed:
            return []

        primary = self.provider_router.route(participant.model_ref, typed)
        ordered = [primary, *[provider for provider in typed if provider.id != primary.id]]
        return await self._expand_provider_chain(ordered, primary.fallback_ids)

    async def _expand_provider_chain(
        self,
        providers: List[ProviderConfig],
        fallback_ids: List[str],
    ) -> List[ProviderConfig]:
        resolved: List[ProviderConfig] = []
        seen = set()
        for provider in providers:
            if provider.is_active and provider.id not in seen:
                resolved.append(provider)
                seen.add(provider.id)
        for fallback_id in fallback_ids:
            fallback = await self._load_provider_by_id(fallback_id)
            if fallback and fallback.is_active and fallback.id not in seen:
                resolved.append(fallback)
                seen.add(fallback.id)
        return resolved

    async def _load_compatible_model_fallbacks(
        self,
        primary: ProviderConfig,
        model_ref: str,
        seen: set[str],
    ) -> List[ProviderConfig]:
        requested_model = model_ref.split("/", 1)[-1].strip()
        if not requested_model:
            return []
        candidates = await self._load_providers_by_query(
            """SELECT * FROM provider_configs
               WHERE is_active = 1
                 AND id != ?
                 AND api_format = ?
               ORDER BY name ASC""",
            (primary.id, primary.api_format.value),
        )
        resolved: List[ProviderConfig] = []
        for provider in candidates:
            if provider.id in seen:
                continue
            default_ref = provider.auth_config.metadata.get("default_model_ref")
            if default_ref is None:
                continue
            normalized_default = str(default_ref).split("/", 1)[-1].strip()
            if normalized_default != requested_model:
                continue
            resolved.append(provider)
            seen.add(provider.id)
        return resolved

    async def _load_provider_by_id(self, provider_id: str) -> Optional[ProviderConfig]:
        providers = await self._load_providers_by_query(
            "SELECT * FROM provider_configs WHERE id = ? LIMIT 1",
            (provider_id,),
        )
        return providers[0] if providers else None

    async def _load_providers_by_query(
        self,
        query: str,
        params: tuple,
    ) -> List[ProviderConfig]:
        await init_db(self.db_path)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
        return [self._provider_from_row(row) for row in rows]

    def _provider_from_row(self, row: aiosqlite.Row) -> ProviderConfig:
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

    async def _flush_pending_user_messages(self, session: Session, runtime: SessionRuntimeState) -> None:
        if not runtime.pending_user_messages:
            return
        pending = list(runtime.pending_user_messages)
        runtime.pending_user_messages.clear()
        for content in pending:
            await self._store_user_message(session, content)
        await self._persist_session_runtime(session)

    async def _store_user_message(self, session: Session, content: str) -> None:
        await self.message_store.store_message(CollaborationMessage(id=str(uuid.uuid4()), session_id=session.id, sender_id=USER_SENDER_ID, message_type=MessageType.USER_INTERVENTION, content=content, round_number=session.current_round))
        self.snapshot_manager.add_key_event(session, f"User inserted: {content}")
        session.next_speaker_index = 0

    def _should_schedule_topic_reminder(self, session: Session, messages: List[CollaborationMessage], current_score: Optional[float]) -> bool:
        if current_score is None:
            return False
        prior = []
        for message in reversed(messages):
            if message.message_type == MessageType.DIALOGUE and message.drift_score is not None:
                prior.append(message.drift_score)
                if len(prior) == 2:
                    break
        return current_score < session.config.drift_threshold and len(prior) == 2 and all(score < session.config.drift_threshold for score in prior)

    def _summarize_for_compression(self, text: str) -> str:
        return text.strip() if len(text.strip()) <= 200 else f"{text.strip()[:200]}..."

    @staticmethod
    def _from_timestamp(timestamp: int) -> datetime:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)



