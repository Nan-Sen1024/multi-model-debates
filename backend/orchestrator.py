from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import AsyncIterator, Dict, List, Optional

import aiosqlite

from .anchor_injector import AnchorInjector
from .context_compressor import ContextCompressor
from .database import DB_PATH, init_db
from .drift_detector import DriftDetector
from .enums import APIFormat, AuthType, CollaborationMode, MessageType, ProviderType, SessionStatus
from .exceptions import ValidationError
from .llm_gateway import LLMGatewayClient, ProviderRouter, deserialize_auth_config, validate_model_ref
from .message_store import MessageStore
from .models import AuthConfig, Checkpoint, CollaborationMessage, ModelParticipant, ProviderConfig, Session, SessionConfig, SessionSnapshot, WorkspaceConfig
from .workspace_context import build_workspace_file_context
from .workspace_capabilities import workspace_capabilities_from_dict, workspace_capabilities_to_dict
from .workspace_router import resolve_workspace_targets
from .workspace_scanner import scan_workspace
from .snapshot_manager import SnapshotManager
from .strategies import StrategyRegistry

logger = logging.getLogger(__name__)
USER_SENDER_ID = "[用户]"


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
        self._session_locks: Dict[str, asyncio.Lock] = {}
        self._session_runtime: Dict[str, SessionRuntimeState] = {}

    async def create_session(self, config: CreateSessionRequest) -> Session:
        if not config.topic or not config.topic.strip():
            raise ValidationError("Topic 不能为空，请输入有效的话题或任务描述。", field="topic")
        if len(config.participants) < 2:
            raise ValidationError("至少需要 2 个参与模型。", field="participants")
        if len(config.participants) > 10:
            raise ValidationError("最多支持 10 个参与模型。", field="participants")

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
                raise ValidationError(f"会话不存在：{session_id}", field="session_id")
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
                prompt_messages = self._build_dispatch_messages(session, participant, renderable_messages, runtime.manual_topic_reminder, strategy.build_system_prompt(participant, session))
                runtime.manual_topic_reminder = False
                try:
                    async for chunk in self._iter_model_stream(participant, prompt_messages):
                        full_content += chunk
                        yield StreamChunk("chunk", participant_id=participant.custom_id, content=chunk, round_number=session.current_round)
                except Exception as exc:
                    logger.exception("参与者 %s 调用失败", participant.custom_id)
                    self._advance_to_next_participant(session)
                    await self._persist_session_runtime(session)
                    await self._flush_pending_user_messages(session, runtime)
                    yield StreamChunk("error", participant_id=participant.custom_id, round_number=session.current_round, metadata={"code": "PROVIDER_UNAVAILABLE", "message": str(exc)})
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
                if chunk.event == "turn_end":
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
                raise ValidationError("code_workspace 会话缺少 workspace 配置。", field="workspace")

            try:
                workspace_scan = scan_workspace(
                    session.config.workspace.root_path,
                    session.config.workspace.scan_excludes,
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

            targets = resolve_workspace_targets(latest_user_message, session.participants)
            if not targets:
                return

            runtime.is_generating = True
            try:
                for participant in targets:
                    prompt_messages = self._build_workspace_dispatch_messages(
                        session,
                        participant,
                        messages,
                        workspace_scan,
                    )
                    full_content = ""
                    try:
                        async for chunk in self._iter_model_stream(participant, prompt_messages):
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
                            "error",
                            participant_id=participant.custom_id,
                            round_number=session.current_round,
                            metadata={"code": "PROVIDER_UNAVAILABLE", "message": str(exc)},
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
                    await self.message_store.store_message(message)
                    self.snapshot_manager.update(session, participant, message.content)
                    messages.append(message)
                    yield StreamChunk("turn_end", participant_id=participant.custom_id, round_number=session.current_round)

                await self._persist_session_runtime(session)
                yield StreamChunk("round_end", round_number=session.current_round)
            finally:
                runtime.is_generating = False

    async def inject_user_message(self, session_id: str, content: str) -> None:
        if not content or not content.strip():
            raise ValidationError("用户消息不能为空。", field="content")
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
            custom_id = item.custom_id or f"Model_{index + 1}"
            if not (1 <= len(custom_id) <= 32):
                raise ValidationError(f"Custom_ID '{custom_id}' 长度必须在 1 至 32 个字符之间。", field=f"participants[{index}].custom_id")
            if custom_id in seen:
                raise ValidationError(f"Custom_ID 重复：'{custom_id}' 已被其他参与者使用。", field=f"participants[{index}].custom_id")
            seen.add(custom_id)
            resolved.append(ParticipantInput(item.model_ref, item.provider_id, custom_id, item.role_desc))
        return resolved

    def _build_session_summary_text(
        self,
        session: Session,
        messages: List[CollaborationMessage],
        reason: str,
    ) -> str:
        summary_text = self.strategy_registry.get(session.mode).build_summary(session, messages, reason)
        if len(messages) < 2:
            summary_text = f"会话因 {reason} 结束，消息不足，未生成详细摘要。"
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
        session.updated_at = datetime.utcnow()
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
                self.snapshot_manager.add_key_event(session, f"用户插入：{message.content}")
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
    ) -> List[Dict[str, str]]:
        system_prompt = self.strategy_registry.get(session.mode).build_system_prompt(participant, session)
        parts = [self.anchor_injector.build_anchor(participant, session, include_topic=False)]
        parts.append(self.snapshot_manager.render_snapshot(session.snapshot, include_topic=False))
        history = self.message_store.build_message_history(messages, participant)
        if history:
            parts.append(history)
        parts.append(build_workspace_file_context(session, workspace_scan))
        return [{"role": "system", "content": system_prompt}, {"role": "user", "content": "\n\n".join(parts)}]

    async def _iter_model_stream(
        self,
        participant: ModelParticipant,
        prompt_messages: List[Dict[str, str]],
    ) -> AsyncIterator[str]:
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
        for provider in provider_candidates:
            emitted_output = False
            try:
                async for chunk in self.gateway.chat_stream(
                    participant.model_ref,
                    prompt_messages,
                    provider_config=provider,
                ):
                    emitted_output = True
                    yield chunk
                return
            except Exception as exc:
                if emitted_output:
                    raise
                last_error = exc
                logger.warning(
                    "Provider %s failed before emitting output for %s",
                    provider.name,
                    participant.custom_id,
                )

        if last_error is not None:
            raise last_error

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
                raise ValidationError(
                    f"Provider '{participant.provider_id}' does not exist",
                    field=f"participants[{index}].provider_id",
                )

    async def _resolve_provider_candidates(self, participant: ModelParticipant) -> List[ProviderConfig]:
        if participant.provider_id:
            primary = await self._load_provider_by_id(participant.provider_id)
            if primary is None:
                return []
            return await self._expand_provider_chain([primary], primary.fallback_ids)

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
        self.snapshot_manager.add_key_event(session, f"用户插入：{content}")
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
