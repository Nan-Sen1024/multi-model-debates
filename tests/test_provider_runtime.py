from __future__ import annotations

import asyncio
import json

import aiosqlite

from backend.database import init_db
from backend.enums import APIFormat, AuthType, CollaborationMode, ProviderType
from backend.llm_gateway import serialize_auth_config
from backend.models import AuthConfig
from backend.orchestrator import CreateSessionRequest, ParticipantInput, SessionOrchestrator


def run(coro):
    return asyncio.run(coro)


class FakeGateway:
    def __init__(self) -> None:
        self.calls = []

    async def chat_stream(self, model_ref, messages, auth_config=None, provider_config=None):
        self.calls.append(
            {
                "model_ref": model_ref,
                "messages": messages,
                "auth_config": auth_config,
                "provider_config": provider_config,
            }
        )
        yield "hello"


async def insert_provider(
    db_path: str,
    provider_id: str,
    name: str,
    provider_type: ProviderType,
    base_url: str = "http://127.0.0.1:11434/v1",
) -> None:
    await init_db(db_path)
    auth_config = AuthConfig(auth_type=AuthType.API_KEY, api_key="sk-test-provider")
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
