"""
Property tests for context anchors.
"""
from __future__ import annotations

import string
import uuid

from hypothesis import assume, given, settings, strategies as st

from backend.anchor_injector import AnchorInjector, GAME_MODES, _count_tokens
from backend.enums import CollaborationMode, SessionStatus
from backend.models import ModelParticipant, Session, SessionConfig, SessionSnapshot


def make_session(participants, topic, mode):
    return Session(
        id=str(uuid.uuid4()),
        topic=topic,
        mode=mode,
        status=SessionStatus.ACTIVE,
        participants=participants,
        config=SessionConfig(),
        snapshot=SessionSnapshot(
            topic=topic,
            mode=mode,
            participant_summaries={participant.custom_id: "" for participant in participants},
            consensus_list=[],
            key_events=[],
        ),
    )


participant_id_strategy = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\n\r\t"),
    min_size=1,
    max_size=12,
).filter(lambda value: value.strip() != "")


# Feature: multi-model-debate, Property 6: anchor completeness and token bound
@given(
    topic=st.text(min_size=1, max_size=240).filter(lambda value: value.strip() != ""),
    custom_ids=st.lists(participant_id_strategy, min_size=2, max_size=10, unique=True),
    role_desc=st.text(min_size=0, max_size=800),
)
@settings(max_examples=30, deadline=5000)
def test_property_anchor_contains_required_fields_and_respects_token_limit(topic, custom_ids, role_desc):
    injector = AnchorInjector()
    participants = [
        ModelParticipant(
            id=str(uuid.uuid4()),
            session_id="sess-anchor",
            custom_id=custom_id,
            model_ref="openai/gpt-4o",
            sequence_order=index,
            role_desc=role_desc if index == 0 else "",
        )
        for index, custom_id in enumerate(custom_ids)
    ]
    session = make_session(participants, topic=topic, mode=CollaborationMode.CHAT)
    anchor = injector.build_anchor(participants[0], session)

    assert topic in anchor
    assert participants[0].custom_id in anchor
    assert anchor.startswith("[系统锚点]")
    assert anchor.endswith("[锚点结束]")
    assert _count_tokens(anchor) <= AnchorInjector.MAX_ANCHOR_TOKENS


secret_text = st.text(alphabet=string.ascii_letters + string.digits, min_size=2, max_size=80)


# Feature: multi-model-debate, Property 6: game-mode private info is isolated
@given(
    secret_a=secret_text,
    secret_b=secret_text,
    mode=st.sampled_from(sorted(GAME_MODES, key=lambda item: item.value)),
)
@settings(max_examples=30, deadline=5000)
def test_property_anchor_private_info_isolated(secret_a, secret_b, mode):
    assume(secret_a != secret_b)
    assume(secret_a not in secret_b)
    assume(secret_b not in secret_a)
    injector = AnchorInjector()
    participant_a = ModelParticipant(
        id=str(uuid.uuid4()),
        session_id="sess-private",
        custom_id="ModelA",
        model_ref="openai/gpt-4o",
        sequence_order=0,
        private_info=secret_a,
    )
    participant_b = ModelParticipant(
        id=str(uuid.uuid4()),
        session_id="sess-private",
        custom_id="ModelB",
        model_ref="openai/gpt-4o",
        sequence_order=1,
        private_info=secret_b,
    )
    session = make_session([participant_a, participant_b], topic="游戏话题", mode=mode)
    anchor_a = injector.build_anchor(participant_a, session)
    anchor_b = injector.build_anchor(participant_b, session)

    assert secret_a in anchor_a
    assert secret_b not in anchor_a
    assert secret_b in anchor_b
    assert secret_a not in anchor_b
