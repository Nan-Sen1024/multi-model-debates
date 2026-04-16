"""
单元测试：MessageStore
覆盖需求：9.1、22.1、22.2、22.3
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from datetime import datetime
from typing import Optional

import pytest

from backend.enums import MessageType
from backend.message_store import MessageStore
from backend.models import CollaborationMessage


# 使用 asyncio.run() 包装协程，不依赖 pytest-asyncio
def run(coro):
    return asyncio.run(coro)


def make_message(
    session_id: str,
    sender_id: str = "ModelA",
    message_type: MessageType = MessageType.DIALOGUE,
    content: str = "测试内容",
    is_masked: bool = False,
    is_compressed: bool = False,
    drift_score: Optional[float] = None,
    round_number: int = 0,
) -> CollaborationMessage:
    return CollaborationMessage(
        id=str(uuid.uuid4()),
        session_id=session_id,
        sender_id=sender_id,
        message_type=message_type,
        content=content,
        is_masked=is_masked,
        is_compressed=is_compressed,
        drift_score=drift_score,
        round_number=round_number,
        created_at=datetime.utcnow(),
    )


@pytest.fixture(scope="module")
def store():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = f.name
    f.close()
    s = MessageStore(db_path=db_path)
    yield s
    try:
        os.unlink(db_path)
    except OSError:
        pass


@pytest.fixture
def session_id():
    return str(uuid.uuid4())


async def _create_session(db_path: str, session_id: str) -> None:
    import aiosqlite
    from backend.database import init_db

    await init_db(db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys=ON;")
        await db.execute(
            "INSERT OR IGNORE INTO collaboration_sessions (id,topic,mode,status,config,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
            (session_id, "测试话题", "chat", "active", "{}", 0, 0),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# 3.1 store_message + load_messages Round-Trip（需求 9.1、22.1）
# ---------------------------------------------------------------------------

def test_store_and_load_round_trip(store, session_id):
    run(_create_session(store.db_path, session_id))
    msg = make_message(session_id=session_id, sender_id="ModelA",
                       message_type=MessageType.DIALOGUE, content="这是一条测试消息",
                       drift_score=0.75, round_number=3)
    run(store.store_message(msg))
    loaded = run(store.load_messages(session_id))
    assert len(loaded) == 1
    m = loaded[0]
    assert m.id == msg.id
    assert m.session_id == session_id
    assert m.sender_id == "ModelA"
    assert m.message_type == MessageType.DIALOGUE
    assert m.content == "这是一条测试消息"
    assert m.is_masked is False
    assert m.is_compressed is False
    assert abs(m.drift_score - 0.75) < 0.001
    assert m.round_number == 3


def test_store_multiple_messages_ordered(store, session_id):
    run(_create_session(store.db_path, session_id))
    msgs = [make_message(session_id, content=f"消息{i}", round_number=i) for i in range(5)]
    for m in msgs:
        run(store.store_message(m))
    loaded = run(store.load_messages(session_id))
    assert len(loaded) == 5
    for i, m in enumerate(loaded):
        assert m.round_number == i


def test_store_masked_message_round_trip(store, session_id):
    run(_create_session(store.db_path, session_id))
    msg = make_message(session_id, is_masked=True, message_type=MessageType.TOOL_OUTPUT)
    run(store.store_message(msg))
    loaded = run(store.load_messages(session_id))
    assert loaded[0].is_masked is True
    assert loaded[0].message_type == MessageType.TOOL_OUTPUT


def test_store_compressed_message_round_trip(store, session_id):
    run(_create_session(store.db_path, session_id))
    msg = make_message(session_id, is_compressed=True, content="历史摘要内容")
    run(store.store_message(msg))
    loaded = run(store.load_messages(session_id))
    assert loaded[0].is_compressed is True
    assert loaded[0].content == "历史摘要内容"


def test_load_messages_empty_session(store, session_id):
    run(_create_session(store.db_path, session_id))
    loaded = run(store.load_messages(session_id))
    assert loaded == []


def test_load_messages_only_own_session(store):
    sid1 = str(uuid.uuid4())
    sid2 = str(uuid.uuid4())
    run(_create_session(store.db_path, sid1))
    run(_create_session(store.db_path, sid2))
    run(store.store_message(make_message(sid1, content="属于 session1")))
    run(store.store_message(make_message(sid2, content="属于 session2")))
    loaded1 = run(store.load_messages(sid1))
    loaded2 = run(store.load_messages(sid2))
    assert len(loaded1) == 1
    assert loaded1[0].content == "属于 session1"
    assert len(loaded2) == 1
    assert loaded2[0].content == "属于 session2"


# ---------------------------------------------------------------------------
# 3.3 build_message_history 格式化（需求 22.2、22.3）—— 纯同步
# ---------------------------------------------------------------------------

def test_build_history_normal_message(store, session_id):
    msg = make_message(session_id, sender_id="ModelA", message_type=MessageType.DIALOGUE, content="你好，世界")
    assert store.build_message_history([msg]) == "[ModelA|dialogue]: 你好，世界"


def test_build_history_masked_message(store, session_id):
    msg = make_message(session_id, sender_id="ModelB", message_type=MessageType.TOOL_OUTPUT,
                       content="这是工具输出原文，不应显示", is_masked=True)
    result = store.build_message_history([msg])
    assert result == "[ModelB|tool_output]: [工具输出已遮蔽]"
    assert "不应显示" not in result


def test_build_history_compressed_message(store, session_id):
    msg = make_message(session_id, sender_id="ModelA", content="这是压缩后的摘要内容", is_compressed=True)
    result = store.build_message_history([msg])
    assert result == "[历史摘要]: 这是压缩后的摘要内容"
    assert "ModelA" not in result


def test_build_history_multiple_messages(store, session_id):
    msgs = [
        make_message(session_id, sender_id="ModelA", content="第一条"),
        make_message(session_id, sender_id="ModelB", content="第二条"),
        make_message(session_id, sender_id="ModelA", content="第三条"),
    ]
    lines = store.build_message_history(msgs).split("\n")
    assert lines == ["[ModelA|dialogue]: 第一条", "[ModelB|dialogue]: 第二条", "[ModelA|dialogue]: 第三条"]


def test_build_history_mixed_types(store, session_id):
    msgs = [
        make_message(session_id, sender_id="ModelA", content="普通消息"),
        make_message(session_id, sender_id="ModelB", message_type=MessageType.TOOL_OUTPUT,
                     content="工具原文", is_masked=True),
        make_message(session_id, sender_id="ModelA", content="摘要内容", is_compressed=True),
    ]
    lines = store.build_message_history(msgs).split("\n")
    assert lines[0] == "[ModelA|dialogue]: 普通消息"
    assert lines[1] == "[ModelB|tool_output]: [工具输出已遮蔽]"
    assert lines[2] == "[历史摘要]: 摘要内容"


def test_build_history_user_intervention(store, session_id):
    msg = make_message(session_id, sender_id="[用户]", message_type=MessageType.USER_INTERVENTION,
                       content="请重新聚焦话题")
    assert store.build_message_history([msg]) == "[[用户]|user_intervention]: 请重新聚焦话题"


def test_build_history_empty_list(store):
    assert store.build_message_history([]) == ""


async def _create_session(db_path: str, session_id: str) -> None:
    import aiosqlite
    from backend.database import init_db

    await init_db(db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys=ON;")
        await db.execute(
            """
            INSERT OR IGNORE INTO collaboration_sessions
                (id, topic, mode, status, config, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, "测试话题", "chat", "active", "{}", 0, 0),
        )
        await db.commit()


# ---------------------------------------------------------------------------
# 3.1 store_message + load_messages Round-Trip（需求 9.1、22.1）
# ---------------------------------------------------------------------------

def test_store_and_load_round_trip(store, session_id):
    run(_create_session(store.db_path, session_id))
    msg = make_message(
        session_id=session_id,
        sender_id="ModelA",
        message_type=MessageType.DIALOGUE,
        content="这是一条测试消息",
        drift_score=0.75,
        round_number=3,
    )
    run(store.store_message(msg))
    loaded = run(store.load_messages(session_id))
    assert len(loaded) == 1
    m = loaded[0]
    assert m.id == msg.id
    assert m.session_id == session_id
    assert m.sender_id == "ModelA"
    assert m.message_type == MessageType.DIALOGUE
    assert m.content == "这是一条测试消息"
    assert m.is_masked is False
    assert m.is_compressed is False
    assert abs(m.drift_score - 0.75) < 0.001
    assert m.round_number == 3


def test_store_multiple_messages_ordered(store, session_id):
    run(_create_session(store.db_path, session_id))
    msgs = [make_message(session_id, content=f"消息{i}", round_number=i) for i in range(5)]
    for m in msgs:
        run(store.store_message(m))
    loaded = run(store.load_messages(session_id))
    assert len(loaded) == 5
    for i, m in enumerate(loaded):
        assert m.round_number == i


def test_store_masked_message_round_trip(store, session_id):
    run(_create_session(store.db_path, session_id))
    msg = make_message(session_id, is_masked=True, message_type=MessageType.TOOL_OUTPUT)
    run(store.store_message(msg))
    loaded = run(store.load_messages(session_id))
    assert loaded[0].is_masked is True
    assert loaded[0].message_type == MessageType.TOOL_OUTPUT


def test_store_compressed_message_round_trip(store, session_id):
    run(_create_session(store.db_path, session_id))
    msg = make_message(session_id, is_compressed=True, content="历史摘要内容")
    run(store.store_message(msg))
    loaded = run(store.load_messages(session_id))
    assert loaded[0].is_compressed is True
    assert loaded[0].content == "历史摘要内容"


def test_load_messages_empty_session(store, session_id):
    run(_create_session(store.db_path, session_id))
    loaded = run(store.load_messages(session_id))
    assert loaded == []


def test_load_messages_only_own_session(store):
    sid1 = str(uuid.uuid4())
    sid2 = str(uuid.uuid4())
    run(_create_session(store.db_path, sid1))
    run(_create_session(store.db_path, sid2))
    run(store.store_message(make_message(sid1, content="属于 session1")))
    run(store.store_message(make_message(sid2, content="属于 session2")))
    loaded1 = run(store.load_messages(sid1))
    loaded2 = run(store.load_messages(sid2))
    assert len(loaded1) == 1
    assert loaded1[0].content == "属于 session1"
    assert len(loaded2) == 1
    assert loaded2[0].content == "属于 session2"


# ---------------------------------------------------------------------------
# 3.3 build_message_history 格式化（需求 22.2、22.3）—— 纯同步，无需 asyncio
# ---------------------------------------------------------------------------

def test_build_history_normal_message(store, session_id):
    msg = make_message(session_id, sender_id="ModelA", message_type=MessageType.DIALOGUE, content="你好，世界")
    assert store.build_message_history([msg]) == "[ModelA|dialogue]: 你好，世界"


def test_build_history_masked_message(store, session_id):
    msg = make_message(session_id, sender_id="ModelB", message_type=MessageType.TOOL_OUTPUT,
                       content="这是工具输出原文，不应显示", is_masked=True)
    result = store.build_message_history([msg])
    assert result == "[ModelB|tool_output]: [工具输出已遮蔽]"
    assert "不应显示" not in result


def test_build_history_compressed_message(store, session_id):
    msg = make_message(session_id, sender_id="ModelA", content="这是压缩后的摘要内容", is_compressed=True)
    result = store.build_message_history([msg])
    assert result == "[历史摘要]: 这是压缩后的摘要内容"
    assert "ModelA" not in result


def test_build_history_multiple_messages(store, session_id):
    msgs = [
        make_message(session_id, sender_id="ModelA", content="第一条"),
        make_message(session_id, sender_id="ModelB", content="第二条"),
        make_message(session_id, sender_id="ModelA", content="第三条"),
    ]
    lines = store.build_message_history(msgs).split("\n")
    assert lines == ["[ModelA|dialogue]: 第一条", "[ModelB|dialogue]: 第二条", "[ModelA|dialogue]: 第三条"]


def test_build_history_mixed_types(store, session_id):
    msgs = [
        make_message(session_id, sender_id="ModelA", content="普通消息"),
        make_message(session_id, sender_id="ModelB", message_type=MessageType.TOOL_OUTPUT,
                     content="工具原文", is_masked=True),
        make_message(session_id, sender_id="ModelA", content="摘要内容", is_compressed=True),
    ]
    lines = store.build_message_history(msgs).split("\n")
    assert lines[0] == "[ModelA|dialogue]: 普通消息"
    assert lines[1] == "[ModelB|tool_output]: [工具输出已遮蔽]"
    assert lines[2] == "[历史摘要]: 摘要内容"


def test_build_history_user_intervention(store, session_id):
    msg = make_message(session_id, sender_id="[用户]", message_type=MessageType.USER_INTERVENTION,
                       content="请重新聚焦话题")
    assert store.build_message_history([msg]) == "[[用户]|user_intervention]: 请重新聚焦话题"


def test_build_history_empty_list(store):
    assert store.build_message_history([]) == ""
