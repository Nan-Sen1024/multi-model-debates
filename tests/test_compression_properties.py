"""
属性测试：压缩优先级与保留窗口不变式
"""
from __future__ import annotations

import asyncio
from tempfile import TemporaryDirectory
import uuid

from hypothesis import assume, given, settings, strategies as st

from backend.context_compressor import ContextCompressor
from backend.enums import CollaborationMode, MessageType, SessionStatus
from backend.models import CollaborationMessage, ModelParticipant, Session, SessionConfig, SessionSnapshot


def run(coro):
    return asyncio.run(coro)


def make_session(retention_window: int) -> Session:
    return Session(
        id=str(uuid.uuid4()),
        topic="压缩测试",
        mode=CollaborationMode.CHAT,
        status=SessionStatus.ACTIVE,
        participants=[
            ModelParticipant(id=str(uuid.uuid4()), session_id="sess", custom_id="A", model_ref="openai/gpt-4o", sequence_order=0),
            ModelParticipant(id=str(uuid.uuid4()), session_id="sess", custom_id="B", model_ref="openai/gpt-4o", sequence_order=1),
        ],
        config=SessionConfig(retention_window=retention_window),
        snapshot=SessionSnapshot(topic="压缩测试", mode=CollaborationMode.CHAT, participant_summaries={"A": "", "B": ""}, consensus_list=[], key_events=[]),
    )


# Feature: multi-model-debate, Property 7: 压缩优先级与保留窗口不变式
@given(
    total_messages=st.integers(min_value=6, max_value=20),
    retention_window=st.integers(min_value=1, max_value=5),
)
@settings(max_examples=20, deadline=5000)
def test_property_retention_window_messages_remain_uncompressed(total_messages, retention_window):
    assume(retention_window < total_messages)
    with TemporaryDirectory() as temp_dir:
        compressor = ContextCompressor(
            db_path=f"{temp_dir}/compression-property.db",
            max_tokens=1000,
        )
        session = make_session(retention_window=retention_window)
        messages = [
            CollaborationMessage(
                id=str(uuid.uuid4()),
                session_id=session.id,
                sender_id="A" if index % 2 == 0 else "B",
                message_type=MessageType.DIALOGUE,
                content="x" * 200,
                round_number=index,
            )
            for index in range(total_messages)
        ]

        result = run(compressor.check_and_compress(session, messages, summary_model_fn=lambda text: text[:50]))
        assert result.action == "summarize"
        for message in messages[-retention_window:]:
            assert message.is_compressed is False


# Feature: multi-model-debate, Property 7: 压缩优先级与保留窗口不变式
@given(st.integers(min_value=700, max_value=849))
@settings(max_examples=20, deadline=5000)
def test_property_mask_tools_priority(content_size):
    with TemporaryDirectory() as temp_dir:
        compressor = ContextCompressor(db_path=f"{temp_dir}/compression-mask.db", max_tokens=1000)
        session = make_session(retention_window=2)
        message = CollaborationMessage(
            id=str(uuid.uuid4()),
            session_id=session.id,
            sender_id="A",
            message_type=MessageType.TOOL_OUTPUT,
            content="x" * content_size,
            round_number=0,
        )
        result = run(compressor.check_and_compress(session, [message]))
        assert result.action == "mask_tools"
        assert message.is_masked is True
        assert message.content == "[工具输出已遮蔽]"
