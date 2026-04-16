"""
单元测试：ContextCompressor.write_checkpoint 与 restore_from_checkpoint
覆盖需求：25.5、25.6、25.7
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import uuid

from backend.context_compressor import ContextCompressor
from backend.enums import CollaborationMode, SessionStatus
from backend.models import ModelParticipant, Session, SessionConfig, SessionSnapshot


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def make_participant(custom_id: str, session_id: str = "sess-cp-1") -> ModelParticipant:
    return ModelParticipant(
        id=str(uuid.uuid4()),
        session_id=session_id,
        custom_id=custom_id,
        model_ref="openai/gpt-4o",
        sequence_order=0,
    )


def make_session(
    session_id: str = "sess-cp-1",
    topic: str = "人工智能的未来",
    mode: CollaborationMode = CollaborationMode.DEBATE,
    participant_summaries: dict | None = None,
    consensus_list: list | None = None,
    key_events: list | None = None,
) -> Session:
    p1 = make_participant("ModelA", session_id)
    p2 = make_participant("ModelB", session_id)
    snapshot = SessionSnapshot(
        topic=topic,
        mode=mode,
        participant_summaries=participant_summaries or {"ModelA": "支持AI发展", "ModelB": "担忧安全"},
        consensus_list=consensus_list or ["需要监管"],
        key_events=key_events or ["用户插入：请聚焦话题"],
    )
    return Session(
        id=session_id,
        topic=topic,
        mode=mode,
        status=SessionStatus.ACTIVE,
        participants=[p1, p2],
        config=SessionConfig(),
        snapshot=snapshot,
    )


def tmp_db() -> str:
    """返回一个临时 SQLite 文件路径（测试结束后可删除）。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


# ---------------------------------------------------------------------------
# 1. write_checkpoint 写入数据库
# ---------------------------------------------------------------------------

def test_write_checkpoint_returns_checkpoint_object():
    """write_checkpoint 应返回 Checkpoint 对象，且 id 非空。"""
    db_path = tmp_db()
    try:
        session = make_session()
        compressor = ContextCompressor(db_path=db_path)
        checkpoint = asyncio.run(compressor.write_checkpoint(session))
        assert checkpoint.id
        assert checkpoint.session_id == session.id
        assert checkpoint.topic == session.topic
        assert checkpoint.mode == session.mode.value
        assert checkpoint.next_step == ""
    finally:
        os.unlink(db_path)


def test_write_checkpoint_persists_to_db():
    """write_checkpoint 写入后，数据库中应存在对应记录。"""
    import aiosqlite

    db_path = tmp_db()
    try:
        session = make_session()
        compressor = ContextCompressor(db_path=db_path)
        checkpoint = asyncio.run(compressor.write_checkpoint(session))

        async def _query():
            async with aiosqlite.connect(db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM checkpoints WHERE id = ?", (checkpoint.id,)
                ) as cur:
                    return await cur.fetchone()

        row = asyncio.run(_query())
        assert row is not None
        assert row["session_id"] == session.id
        assert row["topic"] == session.topic
        assert row["mode"] == session.mode.value
    finally:
        os.unlink(db_path)


def test_write_checkpoint_snapshot_json_stored():
    """write_checkpoint 应将 snapshot 序列化为 JSON 存入 snapshot_json 列。"""
    import json
    import aiosqlite

    db_path = tmp_db()
    try:
        session = make_session()
        compressor = ContextCompressor(db_path=db_path)
        checkpoint = asyncio.run(compressor.write_checkpoint(session))

        async def _query():
            async with aiosqlite.connect(db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT snapshot_json FROM checkpoints WHERE id = ?", (checkpoint.id,)
                ) as cur:
                    return await cur.fetchone()

        row = asyncio.run(_query())
        data = json.loads(row["snapshot_json"])
        assert data["topic"] == session.topic
        assert data["mode"] == session.mode.value
        assert data["participant_summaries"] == session.snapshot.participant_summaries
    finally:
        os.unlink(db_path)


def test_write_multiple_checkpoints_same_session():
    """同一会话可写入多个 Checkpoint，每个 id 唯一。"""
    db_path = tmp_db()
    try:
        session = make_session()
        compressor = ContextCompressor(db_path=db_path)
        cp1 = asyncio.run(compressor.write_checkpoint(session))
        cp2 = asyncio.run(compressor.write_checkpoint(session))
        assert cp1.id != cp2.id
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# 2. restore_from_checkpoint Round-Trip
# ---------------------------------------------------------------------------

def test_restore_from_checkpoint_round_trip():
    """restore_from_checkpoint 恢复后，Checkpoint 字段与写入前完全一致。"""
    db_path = tmp_db()
    try:
        session = make_session()
        compressor = ContextCompressor(db_path=db_path)
        written = asyncio.run(compressor.write_checkpoint(session))
        restored_cp, restored_snap = asyncio.run(
            compressor.restore_from_checkpoint(written.id, db_path)
        )
        assert restored_cp.id == written.id
        assert restored_cp.session_id == written.session_id
        assert restored_cp.topic == written.topic
        assert restored_cp.mode == written.mode
        assert restored_cp.next_step == written.next_step
    finally:
        os.unlink(db_path)


def test_restore_snapshot_topic_equals_original():
    """恢复后 snapshot.topic 与写入前一致。"""
    db_path = tmp_db()
    try:
        session = make_session(topic="量子计算的挑战")
        compressor = ContextCompressor(db_path=db_path)
        cp = asyncio.run(compressor.write_checkpoint(session))
        _, snap = asyncio.run(compressor.restore_from_checkpoint(cp.id, db_path))
        assert snap.topic == session.snapshot.topic
    finally:
        os.unlink(db_path)


def test_restore_snapshot_mode_equals_original():
    """恢复后 snapshot.mode 与写入前一致（枚举值）。"""
    db_path = tmp_db()
    try:
        session = make_session(mode=CollaborationMode.BRAINSTORM)
        compressor = ContextCompressor(db_path=db_path)
        cp = asyncio.run(compressor.write_checkpoint(session))
        _, snap = asyncio.run(compressor.restore_from_checkpoint(cp.id, db_path))
        assert snap.mode == CollaborationMode.BRAINSTORM
    finally:
        os.unlink(db_path)


def test_restore_snapshot_participant_summaries_equal():
    """恢复后 snapshot.participant_summaries 与写入前完全一致。"""
    db_path = tmp_db()
    try:
        summaries = {"ModelA": "支持AI监管", "ModelB": "反对过度监管"}
        session = make_session(participant_summaries=summaries)
        compressor = ContextCompressor(db_path=db_path)
        cp = asyncio.run(compressor.write_checkpoint(session))
        _, snap = asyncio.run(compressor.restore_from_checkpoint(cp.id, db_path))
        assert snap.participant_summaries == summaries
    finally:
        os.unlink(db_path)


def test_restore_snapshot_consensus_list_equal():
    """恢复后 snapshot.consensus_list 与写入前完全一致。"""
    db_path = tmp_db()
    try:
        consensus = ["需要监管", "技术是中性的"]
        session = make_session(consensus_list=consensus)
        compressor = ContextCompressor(db_path=db_path)
        cp = asyncio.run(compressor.write_checkpoint(session))
        _, snap = asyncio.run(compressor.restore_from_checkpoint(cp.id, db_path))
        assert snap.consensus_list == consensus
    finally:
        os.unlink(db_path)


def test_restore_snapshot_key_events_equal():
    """恢复后 snapshot.key_events 与写入前完全一致。"""
    db_path = tmp_db()
    try:
        events = ["用户插入：请聚焦话题", "用户插入：补充新信息"]
        session = make_session(key_events=events)
        compressor = ContextCompressor(db_path=db_path)
        cp = asyncio.run(compressor.write_checkpoint(session))
        _, snap = asyncio.run(compressor.restore_from_checkpoint(cp.id, db_path))
        assert snap.key_events == events
    finally:
        os.unlink(db_path)


def test_restore_returns_tuple_of_checkpoint_and_snapshot():
    """restore_from_checkpoint 返回 (Checkpoint, SessionSnapshot) 元组。"""
    from backend.models import Checkpoint, SessionSnapshot

    db_path = tmp_db()
    try:
        session = make_session()
        compressor = ContextCompressor(db_path=db_path)
        cp = asyncio.run(compressor.write_checkpoint(session))
        result = asyncio.run(compressor.restore_from_checkpoint(cp.id, db_path))
        assert isinstance(result, tuple)
        assert len(result) == 2
        assert isinstance(result[0], Checkpoint)
        assert isinstance(result[1], SessionSnapshot)
    finally:
        os.unlink(db_path)


def test_restore_nonexistent_checkpoint_raises():
    """restore_from_checkpoint 传入不存在的 id 时应抛出 ValueError。"""
    import pytest

    db_path = tmp_db()
    try:
        compressor = ContextCompressor(db_path=db_path)
        # 先初始化数据库
        from backend.database import init_db
        asyncio.run(init_db(db_path))
        with pytest.raises(ValueError, match="not found"):
            asyncio.run(compressor.restore_from_checkpoint("nonexistent-id", db_path))
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# 3. 不同模式的 Round-Trip
# ---------------------------------------------------------------------------

def test_round_trip_all_modes():
    """所有 CollaborationMode 枚举值均可正确 Round-Trip。"""
    db_path = tmp_db()
    try:
        compressor = ContextCompressor(db_path=db_path)
        for mode in CollaborationMode:
            session = make_session(
                session_id=str(uuid.uuid4()),
                mode=mode,
            )
            cp = asyncio.run(compressor.write_checkpoint(session))
            _, snap = asyncio.run(compressor.restore_from_checkpoint(cp.id, db_path))
            assert snap.mode == mode, f"Mode mismatch for {mode}"
    finally:
        os.unlink(db_path)
