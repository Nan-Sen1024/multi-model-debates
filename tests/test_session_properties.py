"""
Property tests for session creation constraints.
"""
from __future__ import annotations

import asyncio
import string
from tempfile import TemporaryDirectory

import pytest
from hypothesis import given, settings, strategies as st

from backend.enums import CollaborationMode
from backend.exceptions import ValidationError
from backend.orchestrator import CreateSessionRequest, ParticipantInput, SessionOrchestrator


def run(coro):
    return asyncio.run(coro)


def make_participants(count: int, custom_ids=None):
    ids = custom_ids or [None] * count
    return [
        ParticipantInput(
            model_ref=f"openai/model-{index}",
            custom_id=ids[index],
        )
        for index in range(count)
    ]


def make_orchestrator() -> SessionOrchestrator:
    temp_dir = TemporaryDirectory()
    orchestrator = SessionOrchestrator(db_path=f"{temp_dir.name}/property-session.db")
    orchestrator._property_temp_dir = temp_dir
    return orchestrator


# Feature: multi-model-debate, Property 1: participant count constraints
@given(st.integers(min_value=0, max_value=15))
@settings(max_examples=30, deadline=5000)
def test_property_participant_count_constraints(count):
    orchestrator = make_orchestrator()
    req = CreateSessionRequest(
        topic="有效话题",
        mode=CollaborationMode.CHAT,
        participants=make_participants(count),
    )
    if 2 <= count <= 10:
        session = run(orchestrator.create_session(req))
        assert len(session.participants) == count
    else:
        with pytest.raises(ValidationError):
            run(orchestrator.create_session(req))


# Feature: multi-model-debate, Property 2: blank topic rejected
@given(st.text(alphabet=string.whitespace, min_size=1, max_size=32))
@settings(max_examples=30, deadline=5000)
def test_property_blank_topic_rejected(topic):
    orchestrator = make_orchestrator()
    req = CreateSessionRequest(
        topic=topic,
        mode=CollaborationMode.CHAT,
        participants=make_participants(2),
    )
    with pytest.raises(ValidationError):
        run(orchestrator.create_session(req))


valid_custom_id = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs",),
        blacklist_characters="/\r\n\t",
    ),
    min_size=1,
    max_size=32,
).filter(lambda value: value.strip() != "")


# Feature: multi-model-debate, Property 3: valid custom ids accepted
@given(st.lists(valid_custom_id, min_size=2, max_size=10, unique=True))
@settings(max_examples=30, deadline=5000)
def test_property_valid_custom_ids_accepted(custom_ids):
    orchestrator = make_orchestrator()
    req = CreateSessionRequest(
        topic="有效话题",
        mode=CollaborationMode.CHAT,
        participants=make_participants(len(custom_ids), custom_ids=custom_ids),
    )
    session = run(orchestrator.create_session(req))
    assert [participant.custom_id for participant in session.participants] == custom_ids


# Feature: multi-model-debate, Property 3: invalid custom ids rejected
@given(
    st.one_of(
        st.text(min_size=33, max_size=40),
    )
)
@settings(max_examples=30, deadline=5000)
def test_property_invalid_custom_id_rejected(invalid_custom_id):
    orchestrator = make_orchestrator()
    participants = [
        ParticipantInput(model_ref="openai/a", custom_id=invalid_custom_id),
        ParticipantInput(model_ref="openai/b", custom_id="ValidB"),
    ]
    req = CreateSessionRequest(
        topic="有效话题",
        mode=CollaborationMode.CHAT,
        participants=participants,
    )
    with pytest.raises(ValidationError):
        run(orchestrator.create_session(req))


# Feature: multi-model-debate, Property 3: duplicate custom ids rejected
@given(valid_custom_id)
@settings(max_examples=30, deadline=5000)
def test_property_duplicate_custom_id_rejected(duplicate_id):
    orchestrator = make_orchestrator()
    req = CreateSessionRequest(
        topic="有效话题",
        mode=CollaborationMode.CHAT,
        participants=[
            ParticipantInput(model_ref="openai/a", custom_id=duplicate_id),
            ParticipantInput(model_ref="openai/b", custom_id=duplicate_id),
        ],
    )
    with pytest.raises(ValidationError):
        run(orchestrator.create_session(req))
