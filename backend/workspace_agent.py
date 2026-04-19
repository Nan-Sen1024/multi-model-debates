from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .enums import MessageType
from .models import CollaborationMessage, ModelParticipant, Session
from .workspace_capabilities import AgentProfileConfig, WorkspaceCapabilityManifest


ModelStreamFactory = Callable[[List[Dict[str, str]]], Awaitable[str]]
PersistToolMessage = Callable[[CollaborationMessage], Awaitable[None]]


@dataclass
class WorkspaceAgentDirective:
    action: str
    server_name: Optional[str] = None
    tool_name: Optional[str] = None
    arguments: Dict[str, Any] = field(default_factory=dict)
    plan: Optional[str] = None


@dataclass
class WorkspaceAgentRunResult:
    final_content: str
    emitted_chunks: List["WorkspaceAgentEvent"] = field(default_factory=list)
    persisted_messages: List[CollaborationMessage] = field(default_factory=list)
    terminated: bool = False


@dataclass
class WorkspaceAgentEvent:
    event: str
    participant_id: Optional[str] = None
    content: str = ""
    round_number: int = 0
    metadata: Dict[str, object] = field(default_factory=dict)


class WorkspaceAgentRunner:
    def __init__(self, tool_runtime: Any) -> None:
        self.tool_runtime = tool_runtime

    async def run(
        self,
        session: Session,
        participant: ModelParticipant,
        prompt_messages: List[Dict[str, str]],
        round_number: int,
        model_stream_factory: Callable[[List[Dict[str, str]]], Awaitable[str]],
        persist_tool_message: PersistToolMessage,
    ) -> WorkspaceAgentRunResult:
        profile = resolve_workspace_agent_profile(
            session.config.workspace.capabilities if session.config.workspace else None,
            participant.custom_id,
        )
        if profile is None or profile.mode in {"disabled", "none"}:
            final_text = await model_stream_factory(prompt_messages)
            return WorkspaceAgentRunResult(
                final_content=final_text,
                emitted_chunks=[
                    WorkspaceAgentEvent(
                        "chunk",
                        participant_id=participant.custom_id,
                        content=final_text,
                        round_number=round_number,
                    )
                ]
                if final_text
                else [],
            )

        working_messages = list(prompt_messages)
        max_steps = max(1, int(profile.max_steps))
        emitted_chunks: List[WorkspaceAgentEvent] = []
        persisted_messages: List[CollaborationMessage] = []
        for step in range(max_steps):
            raw_output = await model_stream_factory(working_messages)
            directive = parse_workspace_agent_directive(raw_output)
            if directive is None:
                if raw_output:
                    emitted_chunks.append(
                        WorkspaceAgentEvent(
                            "chunk",
                            participant_id=participant.custom_id,
                            content=raw_output,
                            round_number=round_number,
                        )
                    )
                return WorkspaceAgentRunResult(
                    final_content=raw_output,
                    emitted_chunks=emitted_chunks,
                    persisted_messages=persisted_messages,
                )

            if directive.action == "plan":
                emitted_chunks.append(
                    WorkspaceAgentEvent(
                        "agent_plan",
                        participant_id=participant.custom_id,
                        content=directive.plan or raw_output,
                        round_number=round_number,
                    )
                )
                return WorkspaceAgentRunResult(
                    final_content="",
                    emitted_chunks=emitted_chunks,
                    persisted_messages=persisted_messages,
                )

            if directive.action != "tool_call" or not directive.server_name or not directive.tool_name:
                break

            tool_result = await self.tool_runtime.call_tool(
                participant.custom_id,
                directive.server_name,
                directive.tool_name,
                directive.arguments,
            )
            tool_message = CollaborationMessage(
                id=str(__import__("uuid").uuid4()),
                session_id=session.id,
                sender_id=f"{participant.custom_id}.tool",
                message_type=MessageType.TOOL_OUTPUT,
                content=(
                    f"[工具输出]\n"
                    f"server={directive.server_name}\n"
                    f"tool={directive.tool_name}\n"
                    f"{tool_result.text}"
                ),
                round_number=round_number,
            )
            await persist_tool_message(tool_message)
            persisted_messages.append(tool_message)
            emitted_chunks.extend(
                [
                    WorkspaceAgentEvent(
                        "tool_call",
                        participant_id=participant.custom_id,
                        round_number=round_number,
                        metadata={
                            "server_name": directive.server_name,
                            "tool_name": directive.tool_name,
                            "arguments": directive.arguments,
                        },
                    ),
                    WorkspaceAgentEvent(
                        "tool_result",
                        participant_id=participant.custom_id,
                        round_number=round_number,
                        metadata={
                            "server_name": directive.server_name,
                            "tool_name": directive.tool_name,
                            "text": tool_result.text,
                        },
                    ),
                ]
            )
            follow_up_messages = build_tool_result_follow_up_messages(
                working_messages,
                directive.server_name,
                directive.tool_name,
                tool_result.text,
            )
            working_messages = follow_up_messages
            continue

        return WorkspaceAgentRunResult(
            final_content="",
            emitted_chunks=[
                *emitted_chunks,
                WorkspaceAgentEvent(
                    "error",
                    participant_id=participant.custom_id,
                    round_number=round_number,
                    metadata={
                        "code": "AGENT_MAX_STEPS",
                        "message": "workspace agent 达到最大执行步数，已停止。",
                    },
                ),
            ],
            persisted_messages=persisted_messages,
            terminated=True,
        )


def resolve_workspace_agent_profile(
    manifest: Optional[WorkspaceCapabilityManifest],
    participant_id: str,
) -> Optional[AgentProfileConfig]:
    if manifest is None:
        return None
    participant_override = manifest.participant_overrides.get(participant_id)
    if participant_override is not None and participant_override.agent is not None:
        return participant_override.agent
    return manifest.agent_defaults


def parse_workspace_agent_directive(text: str) -> Optional[WorkspaceAgentDirective]:
    normalized = _strip_json_code_fence(text.strip())
    if not normalized:
        return None
    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    action = str(payload.get("action") or "").strip()
    if not action:
        return None
    if action == "tool_call":
        return WorkspaceAgentDirective(
            action=action,
            server_name=str(payload.get("server") or "").strip() or None,
            tool_name=str(payload.get("tool") or "").strip() or None,
            arguments=payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {},
        )
    if action == "plan":
        return WorkspaceAgentDirective(
            action=action,
            plan=str(payload.get("plan") or "").strip() or None,
        )
    return WorkspaceAgentDirective(action=action)


def build_tool_result_follow_up_messages(
    prompt_messages: List[Dict[str, str]],
    server_name: str,
    tool_name: str,
    tool_text: str,
) -> List[Dict[str, str]]:
    return [
        *prompt_messages,
        {
            "role": "user",
            "content": (
                f"[工具执行结果]\n"
                f"server={server_name}\n"
                f"tool={tool_name}\n"
                f"output:\n{tool_text}\n\n"
                "请基于工具结果继续完成任务，直接给出最终可执行答复。"
            ),
        },
    ]


def _strip_json_code_fence(text: str) -> str:
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return text
