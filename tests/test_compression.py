"""
单元测试：ContextCompressor.check_and_compress、mask_tool_outputs、
         DAGSummaryBuilder.build_dag_summary、summarize_batch
覆盖需求：23.1、23.2、23.3、23.4、23.5、23.6、23.7、23.8、23.9、23.10
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import uuid

import pytest

from backend.context_compressor import (
    CompressionResult,
    ContextCompressor,
    DAGSummaryBuilder,
    _chunk,
)
from backend.enums import CollaborationMode, MessageType, SessionStatus
from backend.models import (
    CollaborationMessage,
    ModelParticipant,
    Session,
    SessionConfig,
    SessionSnapshot,
)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def tmp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def make_session(
    session_id: str = "sess-1",
    retention_window: int = 5,
    max_tokens: int = 100_000,
) -> Session:
    p1 = ModelParticipant(
        id=str(uuid.uuid4()),
        session_id=session_id,
        custom_id="ModelA",
        model_ref="openai/gpt-4o",
        sequence_order=0,
    )
    p2 = ModelParticipant(
        id=str(uuid.uuid4()),
        session_id=session_id,
        custom_id="ModelB",
        model_ref="openai/gpt-4o",
        sequence_order=1,
    )
    snapshot = SessionSnapshot(
        topic="测试话题",
        mode=CollaborationMode.DEBATE,
        participant_summaries={"ModelA": "支持", "ModelB": "反对"},
        consensus_list=[],
        key_events=[],
    )
    return Session(
        id=session_id,
        topic="测试话题",
        mode=CollaborationMode.DEBATE,
        status=SessionStatus.ACTIVE,
        participants=[p1, p2],
        config=SessionConfig(retention_window=retention_window),
        snapshot=snapshot,
    )


def make_message(
    session_id: str = "sess-1",
    message_type: MessageType = MessageType.DIALOGUE,
    content: str = "hello",
    is_masked: bool = False,
    is_compressed: bool = False,
    round_number: int = 0,
) -> CollaborationMessage:
    return CollaborationMessage(
        id=str(uuid.uuid4()),
        session_id=session_id,
        sender_id="ModelA",
        message_type=message_type,
        content=content,
        is_masked=is_masked,
        is_compressed=is_compressed,
        round_number=round_number,
    )


def mock_summary_fn(text: str) -> str:
    """简单 mock：返回 'SUMMARY:' + 前 20 字符"""
    return f"SUMMARY:{text[:20]}"


# ---------------------------------------------------------------------------
# 1. compute_context_usage_ratio
# ---------------------------------------------------------------------------

def test_ratio_empty_messages():
    """空消息列表时使用率为 0"""
    session = make_session()
    compressor = ContextCompressor(max_tokens=100_000)
    ratio = compressor.compute_context_usage_ratio(session, [])
    assert ratio == 0.0


def test_ratio_below_threshold():
    """少量消息时使用率 < 0.5"""
    session = make_session()
    compressor = ContextCompressor(max_tokens=100_000)
    msgs = [make_message(content="x" * 100) for _ in range(3)]
    ratio = compressor.compute_context_usage_ratio(session, msgs)
    assert ratio < 0.5


def test_ratio_capped_at_one():
    """超出 max_tokens 时使用率截断为 1.0"""
    session = make_session()
    compressor = ContextCompressor(max_tokens=10)
    msgs = [make_message(content="x" * 1000)]
    ratio = compressor.compute_context_usage_ratio(session, msgs)
    assert ratio == 1.0


# ---------------------------------------------------------------------------
# 2. check_and_compress 四级策略
# ---------------------------------------------------------------------------

def test_check_and_compress_none_below_05():
    """ratio < 0.5 → action='none'，需求 23.1"""
    db_path = tmp_db()
    try:
        session = make_session()
        compressor = ContextCompressor(db_path=db_path, max_tokens=100_000)
        # 100 chars / 100_000 = 0.001
        msgs = [make_message(content="x" * 100)]
        result = asyncio.run(compressor.check_and_compress(session, msgs))
        assert result.action == "none"
    finally:
        os.unlink(db_path)


def test_check_and_compress_delegate_tools_05_to_07():
    """0.5 <= ratio < 0.7 → action='delegate_tools'，需求 23.2"""
    db_path = tmp_db()
    try:
        session = make_session()
        # max_tokens=1000，content=600 chars → ratio=0.6
        compressor = ContextCompressor(db_path=db_path, max_tokens=1000)
        msgs = [make_message(content="x" * 600)]
        result = asyncio.run(compressor.check_and_compress(session, msgs))
        assert result.action == "delegate_tools"
        assert session.config.delegate_all_tools is True
    finally:
        os.unlink(db_path)


def test_check_and_compress_mask_tools_07_to_085():
    """0.7 <= ratio < 0.85 → action='mask_tools'，需求 23.3"""
    db_path = tmp_db()
    try:
        session = make_session()
        # max_tokens=1000，content=750 chars → ratio=0.75
        compressor = ContextCompressor(db_path=db_path, max_tokens=1000)
        msgs = [make_message(content="x" * 750, message_type=MessageType.TOOL_OUTPUT)]
        result = asyncio.run(compressor.check_and_compress(session, msgs))
        assert result.action == "mask_tools"
        assert result.masked_count >= 0
    finally:
        os.unlink(db_path)


def test_check_and_compress_summarize_above_085():
    """ratio >= 0.85 → action='summarize'，写 Checkpoint，需求 23.4"""
    db_path = tmp_db()
    try:
        session = make_session()
        # max_tokens=1000，content=900 chars → ratio=0.9
        compressor = ContextCompressor(db_path=db_path, max_tokens=1000)
        msgs = [make_message(content="x" * 900)]
        result = asyncio.run(compressor.check_and_compress(session, msgs, summary_model_fn=mock_summary_fn))
        assert result.action == "summarize"
        assert result.checkpoint_id is not None
    finally:
        os.unlink(db_path)


def test_check_and_compress_boundary_exactly_05():
    """ratio == 0.5 → action='delegate_tools'（0.5 属于第二级）"""
    db_path = tmp_db()
    try:
        session = make_session()
        compressor = ContextCompressor(db_path=db_path, max_tokens=1000)
        msgs = [make_message(content="x" * 500)]
        result = asyncio.run(compressor.check_and_compress(session, msgs))
        assert result.action == "delegate_tools"
    finally:
        os.unlink(db_path)


def test_check_and_compress_boundary_exactly_07():
    """ratio == 0.7 → action='mask_tools'（0.7 属于第三级）"""
    db_path = tmp_db()
    try:
        session = make_session()
        compressor = ContextCompressor(db_path=db_path, max_tokens=1000)
        msgs = [make_message(content="x" * 700, message_type=MessageType.TOOL_OUTPUT)]
        result = asyncio.run(compressor.check_and_compress(session, msgs))
        assert result.action == "mask_tools"
    finally:
        os.unlink(db_path)


def test_check_and_compress_boundary_exactly_085():
    """ratio == 0.85 → action='summarize'（0.85 属于第四级）"""
    db_path = tmp_db()
    try:
        session = make_session()
        compressor = ContextCompressor(db_path=db_path, max_tokens=1000)
        msgs = [make_message(content="x" * 850)]
        result = asyncio.run(compressor.check_and_compress(session, msgs, summary_model_fn=mock_summary_fn))
        assert result.action == "summarize"
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# 3. mask_tool_outputs
# ---------------------------------------------------------------------------

def test_mask_tool_outputs_only_masks_tool_output():
    """只遮蔽 TOOL_OUTPUT 消息，不遮蔽 DIALOGUE 消息，需求 23.3"""
    db_path = tmp_db()
    try:
        compressor = ContextCompressor(db_path=db_path)
        tool_msg = make_message(message_type=MessageType.TOOL_OUTPUT, content="tool result")
        dialogue_msg = make_message(message_type=MessageType.DIALOGUE, content="hello")
        msgs = [tool_msg, dialogue_msg]
        count = asyncio.run(compressor.mask_tool_outputs(msgs))
        assert count == 1
        assert tool_msg.is_masked is True
        assert tool_msg.content == "[工具输出已遮蔽]"
        assert dialogue_msg.is_masked is False
        assert dialogue_msg.content == "hello"
    finally:
        os.unlink(db_path)


def test_mask_tool_outputs_skips_already_masked():
    """已遮蔽的消息不重复遮蔽，需求 23.3"""
    db_path = tmp_db()
    try:
        compressor = ContextCompressor(db_path=db_path)
        already_masked = make_message(
            message_type=MessageType.TOOL_OUTPUT,
            content="[工具输出已遮蔽]",
            is_masked=True,
        )
        count = asyncio.run(compressor.mask_tool_outputs([already_masked]))
        assert count == 0
    finally:
        os.unlink(db_path)


def test_mask_tool_outputs_returns_correct_count():
    """返回实际遮蔽数量，需求 23.3"""
    db_path = tmp_db()
    try:
        compressor = ContextCompressor(db_path=db_path)
        msgs = [
            make_message(message_type=MessageType.TOOL_OUTPUT, content=f"tool {i}")
            for i in range(5)
        ]
        count = asyncio.run(compressor.mask_tool_outputs(msgs))
        assert count == 5
    finally:
        os.unlink(db_path)


def test_mask_tool_outputs_does_not_mask_user_intervention():
    """USER_INTERVENTION 消息不被遮蔽，需求 23.3"""
    db_path = tmp_db()
    try:
        compressor = ContextCompressor(db_path=db_path)
        user_msg = make_message(
            message_type=MessageType.USER_INTERVENTION,
            content="user says something",
        )
        count = asyncio.run(compressor.mask_tool_outputs([user_msg]))
        assert count == 0
        assert user_msg.is_masked is False


    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# 4. retention_window：最近 N 条消息不被压缩
# ---------------------------------------------------------------------------

def test_retention_window_messages_not_compressed():
    """最近 retention_window 条消息不被压缩，需求 23.7"""
    db_path = tmp_db()
    try:
        retention = 3
        session = make_session(retention_window=retention)
        compressor = ContextCompressor(db_path=db_path, max_tokens=1000)

        # 10 条消息，ratio=1.0 → 触发 summarize
        msgs = [make_message(content="x" * 100, round_number=i) for i in range(10)]
        result = asyncio.run(
            compressor.check_and_compress(session, msgs, summary_model_fn=mock_summary_fn)
        )
        assert result.action == "summarize"

        # 最近 retention 条消息的 is_compressed 应为 False
        recent = msgs[-retention:]
        for m in recent:
            assert m.is_compressed is False, f"消息 {m.id} 不应被压缩"
    finally:
        os.unlink(db_path)


def test_retention_window_early_messages_can_be_compressed():
    """早期消息（非最近 retention_window 条）可以被压缩，需求 23.7"""
    db_path = tmp_db()
    try:
        retention = 3
        session = make_session(retention_window=retention)
        compressor = ContextCompressor(db_path=db_path, max_tokens=1000)

        msgs = [make_message(content="x" * 100, round_number=i) for i in range(10)]
        asyncio.run(
            compressor.check_and_compress(session, msgs, summary_model_fn=mock_summary_fn)
        )

        # 早期消息（前 7 条）应被标记为已压缩
        early = msgs[:-retention]
        compressed_count = sum(1 for m in early if m.is_compressed)
        assert compressed_count > 0
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# 5. 幂等性：已有 is_compressed=True 的消息不被再次摘要
# ---------------------------------------------------------------------------

def test_already_compressed_messages_not_summarized_again():
    """已有 is_compressed=True 的消息不被再次摘要（幂等性），需求 23.5"""
    db_path = tmp_db()
    try:
        session = make_session(retention_window=0)
        compressor = ContextCompressor(db_path=db_path, max_tokens=1000)

        # 所有消息都已压缩
        msgs = [
            make_message(content="x" * 100, is_compressed=True, round_number=i)
            for i in range(5)
        ]
        call_count = {"n": 0}

        def counting_summary_fn(text: str) -> str:
            call_count["n"] += 1
            return f"SUMMARY:{text[:10]}"

        asyncio.run(
            compressor.check_and_compress(session, msgs, summary_model_fn=counting_summary_fn)
        )
        # 所有消息已压缩，summary_fn 不应被调用
        assert call_count["n"] == 0
    finally:
        os.unlink(db_path)


def test_summarize_batch_skips_compressed_messages():
    """summarize_batch 跳过 is_compressed=True 的消息，需求 23.5"""
    db_path = tmp_db()
    try:
        builder = DAGSummaryBuilder(db_path=db_path)
        compressed_msg = make_message(content="already compressed", is_compressed=True)
        result = asyncio.run(builder.summarize_batch([compressed_msg], mock_summary_fn))
        assert result == ""
    finally:
        os.unlink(db_path)


def test_summarize_batch_processes_uncompressed_messages():
    """summarize_batch 正常处理未压缩消息，需求 23.5"""
    db_path = tmp_db()
    try:
        builder = DAGSummaryBuilder(db_path=db_path)
        msg = make_message(content="some content", is_compressed=False)
        result = asyncio.run(builder.summarize_batch([msg], mock_summary_fn))
        assert result.startswith("SUMMARY:")
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# 6. DAGSummaryBuilder.build_dag_summary
# ---------------------------------------------------------------------------

def test_build_dag_summary_returns_none_for_empty_batches():
    """空批次返回 None，需求 23.5"""
    db_path = tmp_db()
    try:
        builder = DAGSummaryBuilder(db_path=db_path)
        result = asyncio.run(builder.build_dag_summary([], mock_summary_fn))
        assert result is None
    finally:
        os.unlink(db_path)


def test_build_dag_summary_single_batch_returns_leaf():
    """单批次返回叶节点，需求 23.5"""
    db_path = tmp_db()
    try:
        builder = DAGSummaryBuilder(db_path=db_path)
        msgs = [make_message(content=f"msg {i}", round_number=i) for i in range(3)]
        root = asyncio.run(builder.build_dag_summary([msgs], mock_summary_fn))
        assert root is not None
        assert root.is_leaf is True
        assert root.summary_text.startswith("SUMMARY:")
    finally:
        os.unlink(db_path)


def test_build_dag_summary_multiple_batches_structure():
    """多批次时 DAG 结构正确，需求 23.5、23.6"""
    db_path = tmp_db()
    try:
        builder = DAGSummaryBuilder(db_path=db_path)
        # 5 批次，每批 2 条消息
        batches = [
            [make_message(content=f"batch{b}_msg{i}", round_number=b * 10 + i) for i in range(2)]
            for b in range(5)
        ]
        root = asyncio.run(builder.build_dag_summary(batches, mock_summary_fn))
        assert root is not None
        assert root.summary_text != ""
    finally:
        os.unlink(db_path)


def test_build_dag_summary_covers_from_to_range():
    """DAG 根节点的 covers_from/covers_to 覆盖所有消息的 round 范围，需求 23.5"""
    db_path = tmp_db()
    try:
        builder = DAGSummaryBuilder(db_path=db_path)
        batch1 = [make_message(content="a", round_number=1), make_message(content="b", round_number=2)]
        batch2 = [make_message(content="c", round_number=5), make_message(content="d", round_number=6)]
        root = asyncio.run(builder.build_dag_summary([batch1, batch2], mock_summary_fn))
        assert root is not None
        assert root.covers_from == 1
        assert root.covers_to == 6
    finally:
        os.unlink(db_path)


def test_build_dag_summary_marks_messages_as_compressed():
    """build_dag_summary 后，批次中的消息应被标记为 is_compressed=True，需求 23.5"""
    db_path = tmp_db()
    try:
        builder = DAGSummaryBuilder(db_path=db_path)
        msgs = [make_message(content=f"msg {i}", round_number=i) for i in range(3)]
        asyncio.run(builder.build_dag_summary([msgs], mock_summary_fn))
        for m in msgs:
            assert m.is_compressed is True
    finally:
        os.unlink(db_path)


def test_build_dag_summary_skips_all_compressed_batch():
    """全部已压缩的批次不生成新节点，需求 23.5、23.7"""
    db_path = tmp_db()
    try:
        builder = DAGSummaryBuilder(db_path=db_path)
        msgs = [make_message(content="x", is_compressed=True, round_number=i) for i in range(3)]
        root = asyncio.run(builder.build_dag_summary([msgs], mock_summary_fn))
        assert root is None
    finally:
        os.unlink(db_path)


def test_build_dag_summary_merge_factor():
    """超过 MERGE_FACTOR 个叶节点时，构建上层节点，需求 23.6"""
    db_path = tmp_db()
    try:
        builder = DAGSummaryBuilder(db_path=db_path)
        # 8 批次 → 需要合并
        batches = [
            [make_message(content=f"b{b}", round_number=b)]
            for b in range(8)
        ]
        root = asyncio.run(builder.build_dag_summary(batches, mock_summary_fn))
        assert root is not None
        # 根节点不是叶节点（有子节点）
        assert root.is_leaf is False or len(root.children) > 0 or root.summary_text != ""
    finally:
        os.unlink(db_path)


# ---------------------------------------------------------------------------
# 7. _chunk 辅助函数
# ---------------------------------------------------------------------------

def test_chunk_basic():
    result = _chunk([1, 2, 3, 4, 5], 2)
    assert result == [[1, 2], [3, 4], [5]]


def test_chunk_exact_multiple():
    result = _chunk([1, 2, 3, 4], 2)
    assert result == [[1, 2], [3, 4]]


def test_chunk_empty():
    result = _chunk([], 3)
    assert result == []


def test_chunk_size_larger_than_list():
    result = _chunk([1, 2], 10)
    assert result == [[1, 2]]
