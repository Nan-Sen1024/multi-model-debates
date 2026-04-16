"""
属性测试：Checkpoint / Snapshot round-trip
"""
from __future__ import annotations

import asyncio
from tempfile import TemporaryDirectory
import uuid

from hypothesis import given, settings, strategies as st

from backend.context_compressor import ContextCompressor
from backend.enums import CollaborationMode, SessionStatus
from backend.models import ModelParticipant, Session, SessionConfig, SessionSnapshot


def run(coro):
    return asyncio.run(coro)


snapshot_text = st.text(max_size=60)


@given(
    topic=st.text(min_size=1, max_size=80).filter(lambda value: value.strip() != ""),
    mode=st.sampled_from(list(CollaborationMode)),
    summary_a=snapshot_text,
    summary_b=snapshot_text,
    consensus=st.lists(snapshot_text, max_size=5),
)
@settings(max_examples=20, deadline=5000)
def test_property_checkpoint_round_trip(topic, mode, summary_a, summary_b, consensus):
    with TemporaryDirectory() as temp_dir:
        compressor = ContextCompressor(db_path=f"{temp_dir}/checkpoint-property.db")
        session = Session(
            id=str(uuid.uuid4()),
            topic=topic,
            mode=mode,
            status=SessionStatus.ACTIVE,
            participants=[
                ModelParticipant(
                    id=str(uuid.uuid4()),
                    session_id="sess",
                    custom_id="A",
                    model_ref="openai/gpt-4o",
                    sequence_order=0,
                ),
                ModelParticipant(
                    id=str(uuid.uuid4()),
                    session_id="sess",
                    custom_id="B",
                    model_ref="openai/gpt-4o",
                    sequence_order=1,
                ),
            ],
            config=SessionConfig(),
            snapshot=SessionSnapshot(
                topic=topic,
                mode=mode,
                participant_summaries={"A": summary_a, "B": summary_b},
                consensus_list=consensus,
                key_events=[],
            ),
        )
        checkpoint = run(compressor.write_checkpoint(session))
        restored_checkpoint, restored_snapshot = run(
            compressor.restore_from_checkpoint(checkpoint.id, compressor.db_path)
        )

        assert restored_checkpoint.topic == session.snapshot.topic
        assert restored_snapshot.topic == session.snapshot.topic
        assert restored_snapshot.mode == session.snapshot.mode
        assert restored_snapshot.participant_summaries == session.snapshot.participant_summaries
        assert restored_snapshot.consensus_list == session.snapshot.consensus_list
