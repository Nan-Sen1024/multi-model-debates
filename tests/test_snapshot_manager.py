"""
单元测试：SnapshotManager
覆盖需求：25.1、25.2、25.3、25.4、25.8、25.9
"""
from __future__ import annotations

import uuid
from typing import List, Optional

from backend.enums import CollaborationMode, SessionStatus
from backend.models import ModelParticipant, Session, SessionConfig, SessionSnapshot
from backend.snapshot_manager import MAX_SUMMARY_CHARS, SnapshotManager


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def make_participant(
    custom_id: str,
    sequence_order: int = 0,
    session_id: str = "sess-1",
) -> ModelParticipant:
    return ModelParticipant(
        id=str(uuid.uuid4()),
        session_id=session_id,
        custom_id=custom_id,
        model_ref="openai/gpt-4o",
        sequence_order=sequence_order,
    )


def make_session(
    participants: List[ModelParticipant],
    mode: CollaborationMode = CollaborationMode.DEBATE,
    topic: str = "人工智能的未来",
) -> Session:
    snapshot = SessionSnapshot(
        topic=topic,
        mode=mode,
        participant_summaries={},
        consensus_list=[],
        key_events=[],
    )
    return Session(
        id="sess-1",
        topic=topic,
        mode=mode,
        status=SessionStatus.ACTIVE,
        participants=participants,
        config=SessionConfig(),
        snapshot=snapshot,
    )


mgr = SnapshotManager()


# ---------------------------------------------------------------------------
# 1. init_snapshot 初始化正确（需求 25.1）
# ---------------------------------------------------------------------------

def test_init_snapshot_all_summaries_empty():
    """init_snapshot 后所有参与者立场摘要为空字符串"""
    p1 = make_participant("ModelA")
    p2 = make_participant("ModelB")
    session = make_session([p1, p2])
    snapshot = mgr.init_snapshot(session)
    assert snapshot.participant_summaries["ModelA"] == ""
    assert snapshot.participant_summaries["ModelB"] == ""


def test_init_snapshot_topic_and_mode():
    """init_snapshot 后快照包含正确的 topic 和 mode"""
    p1 = make_participant("ModelA")
    session = make_session([p1], topic="量子计算", mode=CollaborationMode.CHAT)
    snapshot = mgr.init_snapshot(session)
    assert snapshot.topic == "量子计算"
    assert snapshot.mode == CollaborationMode.CHAT


def test_init_snapshot_empty_lists():
    """init_snapshot 后 consensus_list 和 key_events 均为空列表"""
    p1 = make_participant("ModelA")
    session = make_session([p1])
    snapshot = mgr.init_snapshot(session)
    assert snapshot.consensus_list == []
    assert snapshot.key_events == []


def test_init_snapshot_sets_session_snapshot():
    """init_snapshot 会将快照赋值给 session.snapshot"""
    p1 = make_participant("ModelA")
    session = make_session([p1])
    snapshot = mgr.init_snapshot(session)
    assert session.snapshot is snapshot


def test_init_snapshot_all_participants_present():
    """init_snapshot 后所有参与者都出现在 participant_summaries 中"""
    participants = [make_participant(f"Model{i}") for i in range(5)]
    session = make_session(participants)
    snapshot = mgr.init_snapshot(session)
    for p in participants:
        assert p.custom_id in snapshot.participant_summaries


# ---------------------------------------------------------------------------
# 2. update 更新立场摘要（截断到 100 字）（需求 25.3）
# ---------------------------------------------------------------------------

def test_update_short_content():
    """短内容直接存入摘要"""
    p1 = make_participant("ModelA")
    session = make_session([p1])
    mgr.init_snapshot(session)
    mgr.update(session, p1, "我认为人工智能将改变世界")
    assert session.snapshot.participant_summaries["ModelA"] == "我认为人工智能将改变世界"


def test_update_truncates_to_100_chars():
    """内容超过 100 字时截断到 100 字"""
    p1 = make_participant("ModelA")
    session = make_session([p1])
    mgr.init_snapshot(session)
    long_content = "这是一段很长的发言内容，" * 20  # 远超 100 字
    mgr.update(session, p1, long_content)
    summary = session.snapshot.participant_summaries["ModelA"]
    assert len(summary) <= MAX_SUMMARY_CHARS
    assert summary == long_content[:MAX_SUMMARY_CHARS]


def test_update_exactly_100_chars():
    """恰好 100 字的内容不截断"""
    p1 = make_participant("ModelA")
    session = make_session([p1])
    mgr.init_snapshot(session)
    content_100 = "A" * 100
    mgr.update(session, p1, content_100)
    assert session.snapshot.participant_summaries["ModelA"] == content_100


def test_update_101_chars_truncated():
    """101 字的内容截断为 100 字"""
    p1 = make_participant("ModelA")
    session = make_session([p1])
    mgr.init_snapshot(session)
    content_101 = "B" * 101
    mgr.update(session, p1, content_101)
    assert len(session.snapshot.participant_summaries["ModelA"]) == 100


def test_update_multiple_participants():
    """多个参与者各自更新，互不影响"""
    p1 = make_participant("ModelA")
    p2 = make_participant("ModelB")
    session = make_session([p1, p2])
    mgr.init_snapshot(session)
    mgr.update(session, p1, "ModelA 的立场")
    mgr.update(session, p2, "ModelB 的立场")
    assert session.snapshot.participant_summaries["ModelA"] == "ModelA 的立场"
    assert session.snapshot.participant_summaries["ModelB"] == "ModelB 的立场"


def test_update_overwrites_previous_summary():
    """多次更新同一参与者，最新内容覆盖旧内容"""
    p1 = make_participant("ModelA")
    session = make_session([p1])
    mgr.init_snapshot(session)
    mgr.update(session, p1, "第一次发言")
    mgr.update(session, p1, "第二次发言，更新了立场")
    assert session.snapshot.participant_summaries["ModelA"] == "第二次发言，更新了立场"


# ---------------------------------------------------------------------------
# 3. update 失败时保留上一次快照（需求 25.9）
# ---------------------------------------------------------------------------

def test_update_none_content_preserves_snapshot():
    """content 为 None 时保留上一次成功快照"""
    p1 = make_participant("ModelA")
    session = make_session([p1])
    mgr.init_snapshot(session)
    mgr.update(session, p1, "有效的立场摘要")
    # 现在用 None 更新，应保留上一次快照
    mgr.update(session, p1, None)
    assert session.snapshot.participant_summaries["ModelA"] == "有效的立场摘要"


def test_update_none_content_logs_warning(caplog):
    """content 为 None 时记录 warning 日志"""
    import logging
    p1 = make_participant("ModelA")
    session = make_session([p1])
    mgr.init_snapshot(session)
    with caplog.at_level(logging.WARNING, logger="backend.snapshot_manager"):
        mgr.update(session, p1, None)
    assert len(caplog.records) > 0
    assert any("None" in r.message or "保持不变" in r.message for r in caplog.records)


def test_update_none_does_not_affect_other_participants():
    """某参与者 content 为 None 时，其他参与者的摘要不受影响"""
    p1 = make_participant("ModelA")
    p2 = make_participant("ModelB")
    session = make_session([p1, p2])
    mgr.init_snapshot(session)
    mgr.update(session, p1, "ModelA 的立场")
    mgr.update(session, p2, "ModelB 的立场")
    mgr.update(session, p1, None)  # p1 更新失败
    assert session.snapshot.participant_summaries["ModelA"] == "ModelA 的立场"
    assert session.snapshot.participant_summaries["ModelB"] == "ModelB 的立场"


# ---------------------------------------------------------------------------
# 4. render_snapshot 格式正确（需求 25.2）
# ---------------------------------------------------------------------------

def test_render_snapshot_contains_header_and_footer():
    """渲染结果以 [会话快照] 开头，以 [快照结束] 结尾"""
    p1 = make_participant("ModelA")
    session = make_session([p1])
    mgr.init_snapshot(session)
    rendered = mgr.render_snapshot(session.snapshot)
    assert rendered.startswith("[会话快照]")
    assert rendered.endswith("[快照结束]")


def test_render_snapshot_contains_topic():
    """渲染结果包含话题"""
    p1 = make_participant("ModelA")
    session = make_session([p1], topic="气候变化")
    mgr.init_snapshot(session)
    rendered = mgr.render_snapshot(session.snapshot)
    assert "气候变化" in rendered
    assert "话题：气候变化" in rendered


def test_render_snapshot_contains_mode():
    """渲染结果包含模式"""
    p1 = make_participant("ModelA")
    session = make_session([p1], mode=CollaborationMode.DEBATE)
    mgr.init_snapshot(session)
    rendered = mgr.render_snapshot(session.snapshot)
    assert "模式：" in rendered
    assert "debate" in rendered


def test_render_snapshot_contains_participant_summaries():
    """渲染结果包含参与者状态"""
    p1 = make_participant("ModelA")
    p2 = make_participant("ModelB")
    session = make_session([p1, p2])
    mgr.init_snapshot(session)
    mgr.update(session, p1, "支持人工智能发展")
    mgr.update(session, p2, "担忧安全风险")
    rendered = mgr.render_snapshot(session.snapshot)
    assert "参与者状态：" in rendered
    assert "ModelA：支持人工智能发展" in rendered
    assert "ModelB：担忧安全风险" in rendered


def test_render_snapshot_consensus_format():
    """渲染结果中共识以分号分隔"""
    p1 = make_participant("ModelA")
    session = make_session([p1])
    mgr.init_snapshot(session)
    session.snapshot.consensus_list = ["共识一", "共识二", "共识三"]
    rendered = mgr.render_snapshot(session.snapshot)
    assert "共识：共识一；共识二；共识三" in rendered


def test_render_snapshot_key_events_format():
    """渲染结果中关键事件以分号分隔"""
    p1 = make_participant("ModelA")
    session = make_session([p1])
    mgr.init_snapshot(session)
    session.snapshot.key_events = ["用户插入：请聚焦话题", "用户插入：补充信息"]
    rendered = mgr.render_snapshot(session.snapshot)
    assert "关键事件：用户插入：请聚焦话题；用户插入：补充信息" in rendered


def test_render_snapshot_empty_consensus_and_events():
    """共识和关键事件为空时，对应行仍存在但内容为空"""
    p1 = make_participant("ModelA")
    session = make_session([p1])
    mgr.init_snapshot(session)
    rendered = mgr.render_snapshot(session.snapshot)
    assert "共识：" in rendered
    assert "关键事件：" in rendered


def test_render_snapshot_full_format():
    """完整格式验证"""
    p1 = make_participant("ModelA")
    p2 = make_participant("ModelB")
    session = make_session([p1, p2], topic="测试话题", mode=CollaborationMode.DEBATE)
    mgr.init_snapshot(session)
    mgr.update(session, p1, "立场A")
    mgr.update(session, p2, "立场B")
    session.snapshot.consensus_list = ["共识1"]
    session.snapshot.key_events = ["事件1"]
    rendered = mgr.render_snapshot(session.snapshot)
    lines = rendered.split("\n")
    assert lines[0] == "[会话快照]"
    assert lines[1] == "话题：测试话题"
    assert lines[2] == "模式：debate"
    assert lines[3] == "参与者状态："
    assert "  ModelA：立场A" in rendered
    assert "  ModelB：立场B" in rendered
    assert "共识：共识1" in rendered
    assert "关键事件：事件1" in rendered
    assert lines[-1] == "[快照结束]"


# ---------------------------------------------------------------------------
# 5. 关键事件记录（需求 25.4）
# ---------------------------------------------------------------------------

def test_add_key_event():
    """add_key_event 将事件添加到快照"""
    p1 = make_participant("ModelA")
    session = make_session([p1])
    mgr.init_snapshot(session)
    mgr.add_key_event(session, "用户插入：请重新聚焦话题")
    assert "用户插入：请重新聚焦话题" in session.snapshot.key_events


def test_add_multiple_key_events():
    """多次 add_key_event 按顺序追加"""
    p1 = make_participant("ModelA")
    session = make_session([p1])
    mgr.init_snapshot(session)
    mgr.add_key_event(session, "事件1")
    mgr.add_key_event(session, "事件2")
    mgr.add_key_event(session, "事件3")
    assert session.snapshot.key_events == ["事件1", "事件2", "事件3"]


def test_key_events_appear_in_render():
    """关键事件在渲染结果中正确显示"""
    p1 = make_participant("ModelA")
    session = make_session([p1])
    mgr.init_snapshot(session)
    mgr.add_key_event(session, "用户插入了新信息")
    rendered = mgr.render_snapshot(session.snapshot)
    assert "用户插入了新信息" in rendered


def test_add_consensus():
    """add_consensus 将共识添加到快照"""
    p1 = make_participant("ModelA")
    session = make_session([p1])
    mgr.init_snapshot(session)
    mgr.add_consensus(session, "双方同意人工智能需要监管")
    assert "双方同意人工智能需要监管" in session.snapshot.consensus_list
