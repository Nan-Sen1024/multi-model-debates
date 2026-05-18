"""
Property tests for message storage and history formatting.
"""
from __future__ import annotations

import asyncio
import string
from datetime import datetime, timezone
from tempfile import TemporaryDirectory

import aiosqlite
from hypothesis import given, settings, strategies as st

from backend.database import init_db
from backend.enums import MessageType
from backend.message_store import MessageStore
from backend.models import CollaborationMessage


def utc_now_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def run(coro):
    return asyncio.run(coro)


def make_store() -> MessageStore:
    temp_dir = TemporaryDirectory()
    store = MessageStore(db_path=f"{temp_dir.name}/property-message.db")
    store._property_temp_dir = temp_dir
    return store


async def seed_session(db_path: str, session_id: str) -> None:
    await init_db(db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO collaboration_sessions
                (id, topic, mode, status, config, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, "测试话题", "chat", "active", "{}", 0, 0),
        )
        await db.commit()


async def _list_index_names(db_path: str, table_name: str) -> list[str]:
    await init_db(db_path)
    async with aiosqlite.connect(db_path) as db:
        async with db.execute(f"PRAGMA index_list('{table_name}')") as cursor:
            rows = await cursor.fetchall()
    return [row[1] for row in rows]


single_line_text = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cc", "Cs"),
        blacklist_characters="\r\n",
    ),
    min_size=0,
    max_size=200,
)


message_strategy = st.builds(
    CollaborationMessage,
    id=st.uuids().map(str),
    session_id=st.uuids().map(str),
    sender_id=st.text(min_size=1, max_size=12).filter(lambda value: value.strip() != ""),
    message_type=st.sampled_from(list(MessageType)),
    content=single_line_text,
    is_masked=st.booleans(),
    is_compressed=st.booleans(),
    drift_score=st.one_of(st.none(), st.floats(min_value=0, max_value=1, allow_nan=False, allow_infinity=False)),
    round_number=st.integers(min_value=0, max_value=20),
    created_at=st.just(utc_now_naive()),
)


# Feature: multi-model-debate, Property 4: message persistence round-trip
@given(message_strategy)
@settings(max_examples=30, deadline=5000)
def test_property_message_round_trip(message):
    store = make_store()
    run(seed_session(store.db_path, message.session_id))
    run(store.store_message(message))
    loaded = run(store.load_messages(message.session_id))
    assert len(loaded) == 1
    round_trip = loaded[0]
    assert round_trip.id == message.id
    assert round_trip.session_id == message.session_id
    assert round_trip.sender_id == message.sender_id
    assert round_trip.message_type == message.message_type
    assert round_trip.content == message.content
    assert round_trip.is_masked == message.is_masked
    assert round_trip.is_compressed == message.is_compressed
    assert round_trip.round_number == message.round_number
    assert round_trip.drift_score == message.drift_score


history_message_strategy = st.builds(
    CollaborationMessage,
    id=st.uuids().map(str),
    session_id=st.just("sess-history"),
    sender_id=st.text(
        alphabet=string.ascii_letters + string.digits + "_-",
        min_size=1,
        max_size=10,
    ),
    message_type=st.sampled_from(list(MessageType)),
    content=single_line_text,
    is_masked=st.booleans(),
    is_compressed=st.booleans(),
    drift_score=st.none(),
    round_number=st.integers(min_value=0, max_value=5),
    created_at=st.just(utc_now_naive()),
)


# Feature: multi-model-debate, Property 5: message history prefix formatting
@given(st.lists(history_message_strategy, min_size=1, max_size=8))
@settings(max_examples=30, deadline=5000)
def test_property_message_history_prefix_format(messages):
    store = make_store()
    history = store.build_message_history(messages)
    lines = history.splitlines()
    assert len(lines) == len(messages)
    for line, message in zip(lines, messages):
        if message.is_compressed:
            assert line == f"[历史摘要]: {message.content}"
        elif message.is_masked:
            assert line == f"[{message.sender_id}|{message.message_type.value}]: [工具输出已遮蔽]"
        else:
            assert line == f"[{message.sender_id}|{message.message_type.value}]: {message.content}"


def test_init_db_creates_session_list_indexes():
    store = make_store()
    message_indexes = run(_list_index_names(store.db_path, "collaboration_messages"))
    session_indexes = run(_list_index_names(store.db_path, "collaboration_sessions"))

    assert "idx_messages_session_created_at" in message_indexes
    assert "idx_sessions_updated_created_id" in session_indexes
