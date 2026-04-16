"""
单元测试：SessionOrchestrator.create_session
覆盖需求：1.1、1.2、1.3、1.4、1.5、2.1、2.3、2.4
"""
import asyncio
import time

import pytest

from backend.enums import CollaborationMode, SessionStatus
from backend.exceptions import ValidationError
from backend.orchestrator import CreateSessionRequest, ParticipantInput, SessionOrchestrator


def run(coro):
    """在同步测试中运行协程"""
    return asyncio.run(coro)


def make_participants(n: int, prefix: str = "openai/gpt-4o") -> list:
    return [ParticipantInput(model_ref=prefix) for _ in range(n)]


@pytest.fixture
def orchestrator(tmp_path):
    db = str(tmp_path / "test.db")
    return SessionOrchestrator(db_path=db)


# ---------------------------------------------------------------------------
# 需求 1.1 / 1.4：参与者数量约束
# ---------------------------------------------------------------------------

def test_create_session_too_few_participants(orchestrator):
    req = CreateSessionRequest(
        topic="测试话题",
        mode=CollaborationMode.CHAT,
        participants=make_participants(1),
    )
    with pytest.raises(ValidationError) as exc_info:
        run(orchestrator.create_session(req))
    assert "2" in exc_info.value.message
    assert exc_info.value.field == "participants"


def test_create_session_zero_participants(orchestrator):
    req = CreateSessionRequest(
        topic="测试话题",
        mode=CollaborationMode.CHAT,
        participants=[],
    )
    with pytest.raises(ValidationError) as exc_info:
        run(orchestrator.create_session(req))
    assert exc_info.value.field == "participants"


def test_create_session_too_many_participants(orchestrator):
    req = CreateSessionRequest(
        topic="测试话题",
        mode=CollaborationMode.CHAT,
        participants=make_participants(11),
    )
    with pytest.raises(ValidationError) as exc_info:
        run(orchestrator.create_session(req))
    assert "10" in exc_info.value.message
    assert exc_info.value.field == "participants"


def test_create_session_min_participants(orchestrator):
    req = CreateSessionRequest(
        topic="测试话题",
        mode=CollaborationMode.CHAT,
        participants=make_participants(2),
    )
    session = run(orchestrator.create_session(req))
    assert len(session.participants) == 2


def test_create_session_max_participants(orchestrator):
    req = CreateSessionRequest(
        topic="测试话题",
        mode=CollaborationMode.CHAT,
        participants=make_participants(10),
    )
    session = run(orchestrator.create_session(req))
    assert len(session.participants) == 10


# ---------------------------------------------------------------------------
# 需求 1.5：Topic 非空校验
# ---------------------------------------------------------------------------

def test_create_session_empty_topic(orchestrator):
    req = CreateSessionRequest(
        topic="",
        mode=CollaborationMode.CHAT,
        participants=make_participants(2),
    )
    with pytest.raises(ValidationError) as exc_info:
        run(orchestrator.create_session(req))
    assert exc_info.value.field == "topic"


def test_create_session_whitespace_topic(orchestrator):
    for ws in ["   ", "\t", "\n", "  \t\n  "]:
        req = CreateSessionRequest(
            topic=ws,
            mode=CollaborationMode.CHAT,
            participants=make_participants(2),
        )
        with pytest.raises(ValidationError):
            run(orchestrator.create_session(req))


# ---------------------------------------------------------------------------
# 需求 2.1 / 2.3：Custom_ID 长度与唯一性
# ---------------------------------------------------------------------------

def test_create_session_duplicate_custom_id(orchestrator):
    req = CreateSessionRequest(
        topic="测试话题",
        mode=CollaborationMode.CHAT,
        participants=[
            ParticipantInput(model_ref="openai/gpt-4o", custom_id="ModelA"),
            ParticipantInput(model_ref="anthropic/claude-3", custom_id="ModelA"),
        ],
    )
    with pytest.raises(ValidationError) as exc_info:
        run(orchestrator.create_session(req))
    assert "重复" in exc_info.value.message
    assert "ModelA" in exc_info.value.message


def test_create_session_custom_id_too_long(orchestrator):
    long_id = "A" * 33
    req = CreateSessionRequest(
        topic="测试话题",
        mode=CollaborationMode.CHAT,
        participants=[
            ParticipantInput(model_ref="openai/gpt-4o", custom_id=long_id),
            ParticipantInput(model_ref="anthropic/claude-3", custom_id="ModelB"),
        ],
    )
    with pytest.raises(ValidationError) as exc_info:
        run(orchestrator.create_session(req))
    assert "长度" in exc_info.value.message


def test_create_session_custom_id_max_length(orchestrator):
    max_id = "A" * 32
    req = CreateSessionRequest(
        topic="测试话题",
        mode=CollaborationMode.CHAT,
        participants=[
            ParticipantInput(model_ref="openai/gpt-4o", custom_id=max_id),
            ParticipantInput(model_ref="anthropic/claude-3", custom_id="ModelB"),
        ],
    )
    session = run(orchestrator.create_session(req))
    assert session.participants[0].custom_id == max_id


def test_create_session_custom_id_min_length(orchestrator):
    req = CreateSessionRequest(
        topic="测试话题",
        mode=CollaborationMode.CHAT,
        participants=[
            ParticipantInput(model_ref="openai/gpt-4o", custom_id="A"),
            ParticipantInput(model_ref="anthropic/claude-3", custom_id="B"),
        ],
    )
    session = run(orchestrator.create_session(req))
    assert session.participants[0].custom_id == "A"


# ---------------------------------------------------------------------------
# 需求 2.4：缺省 Custom_ID 自动生成
# ---------------------------------------------------------------------------

def test_create_session_default_custom_id(orchestrator):
    req = CreateSessionRequest(
        topic="测试话题",
        mode=CollaborationMode.CHAT,
        participants=[
            ParticipantInput(model_ref="openai/gpt-4o"),
            ParticipantInput(model_ref="anthropic/claude-3"),
            ParticipantInput(model_ref="ollama/llama3"),
        ],
    )
    session = run(orchestrator.create_session(req))
    ids = [p.custom_id for p in session.participants]
    assert ids == ["Model_1", "Model_2", "Model_3"]


def test_create_session_mixed_custom_id(orchestrator):
    """部分指定 Custom_ID，部分缺省"""
    req = CreateSessionRequest(
        topic="测试话题",
        mode=CollaborationMode.CHAT,
        participants=[
            ParticipantInput(model_ref="openai/gpt-4o", custom_id="Alice"),
            ParticipantInput(model_ref="anthropic/claude-3"),  # 缺省 -> Model_2
        ],
    )
    session = run(orchestrator.create_session(req))
    assert session.participants[0].custom_id == "Alice"
    assert session.participants[1].custom_id == "Model_2"


# ---------------------------------------------------------------------------
# 需求 1.2 / 1.3：返回 session_id，3 秒内完成
# ---------------------------------------------------------------------------

def test_create_session_returns_session_id(orchestrator):
    req = CreateSessionRequest(
        topic="测试话题",
        mode=CollaborationMode.DEBATE,
        participants=make_participants(2),
    )
    session = run(orchestrator.create_session(req))
    assert session.id
    assert len(session.id) == 36  # UUID 格式


def test_create_session_within_3_seconds(orchestrator):
    req = CreateSessionRequest(
        topic="测试话题",
        mode=CollaborationMode.CHAT,
        participants=make_participants(3),
    )
    start = time.time()
    session = run(orchestrator.create_session(req))
    elapsed = time.time() - start
    assert elapsed < 3.0, f"会话创建耗时 {elapsed:.2f}s，超过 3 秒限制"
    assert session.status == SessionStatus.ACTIVE


# ---------------------------------------------------------------------------
# 持久化验证：会话写入数据库后可读取
# ---------------------------------------------------------------------------

def test_create_session_persisted(orchestrator):
    import aiosqlite

    req = CreateSessionRequest(
        topic="持久化测试",
        mode=CollaborationMode.BRAINSTORM,
        participants=[
            ParticipantInput(model_ref="openai/gpt-4o", custom_id="Bot1"),
            ParticipantInput(model_ref="anthropic/claude-3", custom_id="Bot2"),
        ],
    )
    session = run(orchestrator.create_session(req))

    async def _check():
        async with aiosqlite.connect(orchestrator.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM collaboration_sessions WHERE id = ?", (session.id,)
            ) as cursor:
                row = await cursor.fetchone()
            assert row is not None
            assert row["topic"] == "持久化测试"
            assert row["mode"] == "brainstorm"

            async with db.execute(
                "SELECT custom_id FROM model_participants WHERE session_id = ? ORDER BY sequence_order",
                (session.id,),
            ) as cursor:
                rows = await cursor.fetchall()
            assert [r["custom_id"] for r in rows] == ["Bot1", "Bot2"]

    run(_check())
