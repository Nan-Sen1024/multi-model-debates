"""
Property tests for private information isolation.
"""
from __future__ import annotations

import string
import uuid

from hypothesis import assume, given, settings, strategies as st

from backend.anchor_injector import AnchorInjector
from backend.enums import CollaborationMode, SessionStatus
from backend.game_master import FILTERED_CONTENT_PLACEHOLDER, GameMaster
from backend.models import ModelParticipant, Session, SessionConfig, SessionSnapshot


def build_session(mode, participants):
    return Session(
        id=str(uuid.uuid4()),
        topic="游戏任务",
        mode=mode,
        status=SessionStatus.ACTIVE,
        participants=participants,
        config=SessionConfig(),
        snapshot=SessionSnapshot(
            topic="游戏任务",
            mode=mode,
            participant_summaries={participant.custom_id: "" for participant in participants},
            consensus_list=[],
            key_events=[],
        ),
    )


secret_text = st.text(alphabet=string.ascii_letters + string.digits, min_size=3, max_size=40)


# Feature: multi-model-debate, Property 9: private info isolation
@given(
    secret_a=secret_text,
    secret_b=secret_text,
    mode=st.sampled_from([
        CollaborationMode.WEREWOLF,
        CollaborationMode.MURDER_MYSTERY,
        CollaborationMode.UNDERCOVER,
        CollaborationMode.NEGOTIATION,
    ]),
)
@settings(max_examples=30, deadline=5000)
def test_property_private_info_not_leaked_in_anchor_or_filtered_content(secret_a, secret_b, mode):
    assume(secret_a != secret_b)
    assume(secret_a not in secret_b)
    assume(secret_b not in secret_a)
    participant_a = ModelParticipant(
        id=str(uuid.uuid4()),
        session_id="sess-private",
        custom_id="A",
        model_ref="openai/gpt-4o",
        sequence_order=0,
        private_info=f"关键情报 {secret_a}",
    )
    participant_b = ModelParticipant(
        id=str(uuid.uuid4()),
        session_id="sess-private",
        custom_id="B",
        model_ref="openai/gpt-4o",
        sequence_order=1,
        private_info=f"关键情报 {secret_b}",
    )
    session = build_session(mode, [participant_a, participant_b])

    anchor = AnchorInjector().build_anchor(participant_a, session)
    assert participant_a.private_info in anchor
    assert participant_b.private_info not in anchor

    filtered = GameMaster().filter_private_info_leak(
        content=f"我想直接说出 {secret_b}",
        participant=participant_a,
        all_participants=session.participants,
    )
    assert filtered == FILTERED_CONTENT_PLACEHOLDER
