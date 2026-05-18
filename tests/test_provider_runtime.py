from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import aiosqlite

from backend.database import init_db
from backend.enums import APIFormat, AuthType, CollaborationMode, ProviderType
from backend.exceptions import AuthenticationError
from backend.llm_gateway import deserialize_auth_config, serialize_auth_config
from backend.models import AuthConfig, OAuthToken
from backend.orchestrator import CreateSessionRequest, ParticipantInput, SessionOrchestrator


def run(coro):
    return asyncio.run(coro)


class FakeGateway:
    def __init__(self) -> None:
        self.calls = []

    async def chat_stream(
        self,
        model_ref,
        messages,
        auth_config=None,
        provider_config=None,
        on_auth_update=None,
    ):
        self.calls.append(
            {
                "model_ref": model_ref,
                "messages": messages,
                "auth_config": auth_config,
                "provider_config": provider_config,
                "on_auth_update": on_auth_update,
            }
        )
        yield "hello"


class FlakyGateway:
    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls = []

    async def chat_stream(
        self,
        model_ref,
        messages,
        auth_config=None,
        provider_config=None,
        on_auth_update=None,
    ):
        call_index = len(self.calls)
        self.calls.append(
            {
                "model_ref": model_ref,
                "messages": messages,
                "auth_config": auth_config,
                "provider_config": provider_config,
                "on_auth_update": on_auth_update,
            }
        )
        outcome = self.outcomes[call_index]
        if isinstance(outcome, Exception):
            raise outcome
        for chunk in outcome:
            yield chunk


class AuthUpdatingGateway:
    def __init__(self) -> None:
        self.calls = []

    async def chat_stream(
        self,
        model_ref,
        messages,
        auth_config=None,
        provider_config=None,
        on_auth_update=None,
    ):
        self.calls.append(
            {
                "model_ref": model_ref,
                "messages": messages,
                "auth_config": auth_config,
                "provider_config": provider_config,
                "on_auth_update": on_auth_update,
            }
        )
        if on_auth_update is not None:
            await on_auth_update(
                AuthConfig(
                    auth_type=AuthType.OAUTH,
                    oauth_token=OAuthToken(
                        access_token="new-access-token",
                        refresh_token="new-refresh-token",
                        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                    ),
                    metadata={"account_id": "acct-new"},
                )
            )
        yield "hello"


async def insert_provider(
    db_path: str,
    provider_id: str,
    name: str,
    provider_type: ProviderType,
    base_url: str = "http://127.0.0.1:11434/v1",
    metadata: dict | None = None,
) -> None:
    await init_db(db_path)
    auth_config = AuthConfig(
        auth_type=AuthType.API_KEY,
        api_key="sk-test-provider",
        metadata=metadata or {},
    )
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            """
            INSERT INTO provider_configs
                (id, name, provider_type, base_url, api_format, auth_type, auth_config, fallback_ids, is_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """,
            (
                provider_id,
                name,
                provider_type.value,
                base_url,
                APIFormat.OPENAI_COMPLETIONS.value,
                AuthType.API_KEY.value,
                json.dumps(serialize_auth_config(auth_config), ensure_ascii=False),
                "[]",
            ),
        )
        await db.commit()


async def collect_events(orchestrator: SessionOrchestrator, session_id: str):
    return [chunk async for chunk in orchestrator.dispatch_next(session_id)]


async def collect_round_events(orchestrator: SessionOrchestrator, session_id: str):
    return [chunk async for chunk in orchestrator.dispatch_round(session_id)]


def test_dispatch_uses_explicit_provider_binding(tmp_path):
    db_path = str(tmp_path / "provider-explicit.db")
    gateway = FakeGateway()
    orchestrator = SessionOrchestrator(db_path=db_path, gateway=gateway)
    run(insert_provider(db_path, "provider-1", "local-ollama", ProviderType.OLLAMA))

    session = run(
        orchestrator.create_session(
            CreateSessionRequest(
                topic="test explicit provider binding",
                mode=CollaborationMode.CHAT,
                participants=[
                    ParticipantInput(
                        model_ref="ollama/qwen2.5:14b",
                        provider_id="provider-1",
                        custom_id="A",
                    ),
                    ParticipantInput(model_ref="openai/gpt-4o", custom_id="B"),
                ],
            )
        )
    )

    events = run(collect_events(orchestrator, session.id))

    assert any(event.event == "chunk" for event in events)
    assert gateway.calls[0]["provider_config"] is not None
    assert gateway.calls[0]["provider_config"].id == "provider-1"
    assert gateway.calls[0]["provider_config"].base_url == "http://127.0.0.1:11434/v1"
    assert gateway.calls[0]["provider_config"].auth_config.api_key is not None


def test_dispatch_auto_matches_provider_type_when_provider_id_missing(tmp_path):
    db_path = str(tmp_path / "provider-auto.db")
    gateway = FakeGateway()
    orchestrator = SessionOrchestrator(db_path=db_path, gateway=gateway)
    run(insert_provider(db_path, "provider-openai", "openai-primary", ProviderType.OPENAI, "https://api.openai.com/v1"))

    session = run(
        orchestrator.create_session(
            CreateSessionRequest(
                topic="test provider type auto match",
                mode=CollaborationMode.CHAT,
                participants=[
                    ParticipantInput(model_ref="openai/gpt-4o", custom_id="A"),
                    ParticipantInput(model_ref="anthropic/claude-3-5-sonnet", custom_id="B"),
                ],
            )
        )
    )

    run(collect_events(orchestrator, session.id))

    assert gateway.calls[0]["provider_config"] is not None
    assert gateway.calls[0]["provider_config"].id == "provider-openai"
    assert gateway.calls[0]["provider_config"].provider_type == ProviderType.OPENAI


def test_dispatch_round_runs_every_participant_before_round_end(tmp_path):
    db_path = str(tmp_path / "provider-round.db")
    gateway = FakeGateway()
    orchestrator = SessionOrchestrator(db_path=db_path, gateway=gateway)

    session = run(
        orchestrator.create_session(
            CreateSessionRequest(
                topic="test full round dispatch",
                mode=CollaborationMode.CHAT,
                participants=[
                    ParticipantInput(model_ref="openai/gpt-4o", custom_id="A"),
                    ParticipantInput(model_ref="anthropic/claude-3-5-sonnet", custom_id="B"),
                ],
            )
        )
    )

    events = run(collect_round_events(orchestrator, session.id))

    assert [call["model_ref"] for call in gateway.calls] == [
        "openai/gpt-4o",
        "anthropic/claude-3-5-sonnet",
    ]
    core_events = [
        (event.event, event.participant_id)
        for event in events
        if event.event in {"turn_start", "chunk", "turn_end", "round_end"}
    ]
    assert core_events == [
        ("turn_start", "A"),
        ("chunk", "A"),
        ("turn_end", "A"),
        ("turn_start", "B"),
        ("chunk", "B"),
        ("turn_end", "B"),
        ("round_end", None),
    ]


def test_dispatch_round_emits_atomic_execution_events_for_normal_turns(tmp_path):
    db_path = str(tmp_path / "provider-telemetry.db")
    gateway = FakeGateway()
    orchestrator = SessionOrchestrator(db_path=db_path, gateway=gateway)

    session = run(
        orchestrator.create_session(
            CreateSessionRequest(
                topic="test execution telemetry",
                mode=CollaborationMode.CHAT,
                participants=[
                    ParticipantInput(model_ref="openai/gpt-4o", custom_id="A"),
                    ParticipantInput(model_ref="anthropic/claude-3-5-sonnet", custom_id="B"),
                ],
            )
        )
    )

    events = run(collect_round_events(orchestrator, session.id))

    assert any(
        event.event == "phase_start" and event.metadata.get("phase") == "build_prompt"
        for event in events
    )
    assert any(
        event.event == "model_request" and event.participant_id == "A"
        for event in events
    )
    assert any(
        event.event == "model_response" and event.participant_id == "A"
        for event in events
    )
    assert any(
        event.event == "state_write" and event.metadata.get("target") == "message"
        for event in events
    )


def test_dispatch_round_continues_after_provider_failure(tmp_path):
    db_path = str(tmp_path / "provider-failure.db")
    gateway = FlakyGateway([
        RuntimeError("provider down"),
        ["fallback response"],
    ])
    orchestrator = SessionOrchestrator(db_path=db_path, gateway=gateway)

    session = run(
        orchestrator.create_session(
            CreateSessionRequest(
                topic="test provider failure fallback",
                mode=CollaborationMode.CHAT,
                participants=[
                    ParticipantInput(model_ref="openai/gpt-4o", custom_id="A"),
                    ParticipantInput(model_ref="anthropic/claude-3-5-sonnet", custom_id="B"),
                ],
            )
        )
    )

    events = run(collect_round_events(orchestrator, session.id))

    assert [call["model_ref"] for call in gateway.calls] == [
        "openai/gpt-4o",
        "anthropic/claude-3-5-sonnet",
    ]
    assert any(
        event.event == "participant_error"
        and event.participant_id == "A"
        and event.metadata.get("code") == "PROVIDER_UNAVAILABLE"
        for event in events
    )
    assert any(event.event == "chunk" and event.participant_id == "B" for event in events)
    assert any(event.event == "round_end" for event in events)


def test_explicit_provider_uses_compatible_default_model_fallback(tmp_path):
    db_path = str(tmp_path / "provider-compatible-fallback.db")
    gateway = FlakyGateway([
        AuthenticationError("ChatGPT OAuth authentication failed; re-login required"),
        ["fallback response"],
    ])
    orchestrator = SessionOrchestrator(db_path=db_path, gateway=gateway)
    run(insert_provider(db_path, "provider-cc", "cc", ProviderType.OPENAI, "https://api.openai.com/v1"))
    run(
        insert_provider(
            db_path,
            "provider-waicc",
            "waicc",
            ProviderType.OPENAI,
            "http://api.example.test/v1",
            metadata={"default_model_ref": "gpt-5.4"},
        )
    )

    session = run(
        orchestrator.create_session(
            CreateSessionRequest(
                topic="test compatible provider fallback",
                mode=CollaborationMode.CHAT,
                participants=[
                    ParticipantInput(
                        model_ref="gpt-5.4",
                        provider_id="provider-cc",
                        custom_id="A",
                    ),
                    ParticipantInput(model_ref="openai/gpt-4o", custom_id="B"),
                ],
            )
        )
    )

    events = run(collect_events(orchestrator, session.id))

    assert [call["provider_config"].name for call in gateway.calls] == ["cc", "waicc"]
    assert any(
        event.event == "provider_fallback"
        and event.metadata.get("provider_name") == "cc"
        and event.metadata.get("fallback_provider_name") == "waicc"
        for event in events
    )
    assert any(
        event.event == "participant_error"
        and event.metadata.get("code") == "AUTHENTICATION_REQUIRED"
        and event.metadata.get("model_ref") == "gpt-5.4"
        and event.metadata.get("provider_name") == "cc"
        for event in events
    )
    assert any(event.event == "chunk" and event.content == "fallback response" for event in events)


def test_explicit_provider_falls_back_to_same_type_provider_for_bare_model_ref(tmp_path):
    db_path = str(tmp_path / "provider-same-type-fallback.db")
    gateway = FlakyGateway([
        AuthenticationError("token invalid"),
        ["fallback response"],
    ])
    orchestrator = SessionOrchestrator(db_path=db_path, gateway=gateway)
    run(insert_provider(db_path, "provider-waicc", "waicc", ProviderType.OPENAI, "http://api.example.test/v1"))
    run(insert_provider(db_path, "provider-openai", "openai", ProviderType.OPENAI, "https://api.openai.com/v1"))

    session = run(
        orchestrator.create_session(
            CreateSessionRequest(
                topic="test same type provider fallback",
                mode=CollaborationMode.CHAT,
                participants=[
                    ParticipantInput(
                        model_ref="gpt-5.4",
                        provider_id="provider-waicc",
                        custom_id="A",
                    ),
                    ParticipantInput(model_ref="openai/gpt-4o", custom_id="B"),
                ],
            )
        )
    )

    events = run(collect_events(orchestrator, session.id))

    assert [call["provider_config"].name for call in gateway.calls] == ["waicc", "openai"]
    assert any(
        event.event == "provider_fallback"
        and event.metadata.get("provider_name") == "waicc"
        and event.metadata.get("fallback_provider_name") == "openai"
        for event in events
    )
    assert any(
        event.event == "participant_error"
        and event.metadata.get("code") == "AUTHENTICATION_REQUIRED"
        and event.metadata.get("provider_name") == "waicc"
        for event in events
    )
    assert any(event.event == "chunk" and event.content == "fallback response" for event in events)

    async def load_diagnostic():
        await init_db(db_path)
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                "SELECT last_diagnostic FROM provider_configs WHERE id = ?",
                ("provider-waicc",),
            ) as cursor:
                row = await cursor.fetchone()
        return json.loads(row[0])

    diagnostic = run(load_diagnostic())

    assert diagnostic["healthy"] is False
    assert diagnostic["fallback_provider_name"] == "openai"
    assert [item["status"] for item in diagnostic["history"]] == [
        "failed",
        "fallback_active",
    ]


def test_dispatch_persists_provider_oauth_refresh_update(tmp_path):
    db_path = str(tmp_path / "provider-oauth-refresh.db")
    gateway = AuthUpdatingGateway()
    orchestrator = SessionOrchestrator(db_path=db_path, gateway=gateway)
    run(insert_provider(db_path, "provider-cc", "cc", ProviderType.OPENAI, "https://api.openai.com/v1"))

    session = run(
        orchestrator.create_session(
            CreateSessionRequest(
                topic="test oauth refresh persistence",
                mode=CollaborationMode.CHAT,
                participants=[
                    ParticipantInput(
                        model_ref="gpt-5.4",
                        provider_id="provider-cc",
                        custom_id="A",
                    ),
                    ParticipantInput(model_ref="openai/gpt-4o", custom_id="B"),
                ],
            )
        )
    )

    events = run(collect_events(orchestrator, session.id))

    async def load_provider_auth():
        async with aiosqlite.connect(db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT auth_type, auth_config FROM provider_configs WHERE id = ?",
                ("provider-cc",),
            ) as cursor:
                row = await cursor.fetchone()
        return deserialize_auth_config(row["auth_type"], row["auth_config"])

    persisted_auth = run(load_provider_auth())

    assert any(event.event == "chunk" and event.content == "hello" for event in events)
    assert persisted_auth.auth_type == AuthType.OAUTH
    assert persisted_auth.oauth_token is not None
    assert persisted_auth.oauth_token.access_token == "new-access-token"
    assert persisted_auth.oauth_token.refresh_token == "new-refresh-token"
    assert persisted_auth.metadata["account_id"] == "acct-new"


def test_successful_primary_provider_persists_recovery_after_prior_failure(tmp_path):
    db_path = str(tmp_path / "provider-recovery.db")
    gateway = FakeGateway()
    orchestrator = SessionOrchestrator(db_path=db_path, gateway=gateway)
    run(insert_provider(db_path, "provider-openai", "openai-primary", ProviderType.OPENAI, "https://api.openai.com/v1"))

    async def seed_failure():
        await init_db(db_path)
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                "UPDATE provider_configs SET last_diagnostic = ? WHERE id = ?",
                (
                    json.dumps(
                        {
                            "healthy": False,
                            "code": "AUTHENTICATION_REQUIRED",
                            "summary": "主路鉴权失败，已切换 fallback",
                            "message": "API key invalid",
                            "checked_at": 1710000000,
                            "source": "session_runtime",
                            "fallback_provider_id": "provider-backup",
                            "fallback_provider_name": "openai-backup",
                            "history": [
                                {
                                    "status": "failed",
                                    "code": "AUTHENTICATION_REQUIRED",
                                    "summary": "主路鉴权失败",
                                    "message": "API key invalid",
                                    "checked_at": 1710000000,
                                },
                                {
                                    "status": "fallback_active",
                                    "code": "AUTHENTICATION_REQUIRED",
                                    "summary": "已切换到 openai-backup",
                                    "message": "Fallback 接管流量",
                                    "checked_at": 1710000001,
                                },
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    "provider-openai",
                ),
            )
            await db.commit()

    run(seed_failure())

    session = run(
        orchestrator.create_session(
            CreateSessionRequest(
                topic="test recovery detection",
                mode=CollaborationMode.CHAT,
                participants=[
                    ParticipantInput(
                        model_ref="openai/gpt-4o",
                        provider_id="provider-openai",
                        custom_id="A",
                    ),
                    ParticipantInput(
                        model_ref="anthropic/claude-3-5-sonnet",
                        custom_id="B",
                    ),
                ],
            )
        )
    )

    run(collect_events(orchestrator, session.id))

    async def load_diagnostic():
        await init_db(db_path)
        async with aiosqlite.connect(db_path) as db:
            async with db.execute(
                "SELECT last_diagnostic FROM provider_configs WHERE id = ?",
                ("provider-openai",),
            ) as cursor:
                row = await cursor.fetchone()
        return json.loads(row[0])

    diagnostic = run(load_diagnostic())

    assert diagnostic["healthy"] is True
    assert diagnostic["summary"] == "主路恢复"
    assert diagnostic["fallback_provider_name"] is None
    assert [item["status"] for item in diagnostic["history"]] == [
        "failed",
        "fallback_active",
        "recovered",
    ]


def test_append_participants_accepts_bare_model_ref_when_provider_is_bound(tmp_path):
    db_path = str(tmp_path / "provider-bare-model.db")
    gateway = FakeGateway()
    orchestrator = SessionOrchestrator(db_path=db_path, gateway=gateway)
    run(insert_provider(db_path, "provider-openai", "openai-primary", ProviderType.OPENAI, "https://api.openai.com/v1"))

    session = run(
        orchestrator.create_session(
            CreateSessionRequest(
                topic="test bare model ref append",
                mode=CollaborationMode.CHAT,
                participants=[
                    ParticipantInput(model_ref="openai/gpt-4o", custom_id="A"),
                    ParticipantInput(model_ref="anthropic/claude-3-5-sonnet", custom_id="B"),
                ],
            )
        )
    )

    updated = run(
        orchestrator.append_participants(
            session.id,
            [
                ParticipantInput(
                    model_ref="gpt-5.4",
                    provider_id="provider-openai",
                    custom_id="Reviewer",
                    role_desc="review changes",
                )
            ],
        )
    )

    assert [participant.custom_id for participant in updated.participants] == [
        "A",
        "B",
        "Reviewer",
    ]
    assert updated.participants[-1].provider_id == "provider-openai"
    assert updated.participants[-1].model_ref == "gpt-5.4"
