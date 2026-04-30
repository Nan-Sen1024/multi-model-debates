from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .enums import MessageType
from .exceptions import AuthenticationError, ProviderUnavailableError
from .models import CollaborationMessage, ModelParticipant, Session
from .workspace_capabilities import AgentProfileConfig, WorkspaceCapabilityManifest


ModelStreamFactory = Callable[[List[Dict[str, str]]], Awaitable[str]]
PersistToolMessage = Callable[[CollaborationMessage], Awaitable[None]]
EmitAgentEvent = Callable[["WorkspaceAgentEvent"], Awaitable[None]]


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
    failure_summary: Optional[str] = None


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
        emit_event: Optional[EmitAgentEvent] = None,
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
        extend_for_repair = _should_extend_step_budget(
            profile=profile,
            prompt_messages=working_messages,
        )
        step_budget_extended = False
        emitted_chunks: List[WorkspaceAgentEvent] = []
        persisted_messages: List[CollaborationMessage] = []
        step = 0
        while extend_for_repair or step < max_steps:
            step += 1
            if step > max_steps and not step_budget_extended:
                step_budget_extended = True
                budget_event = WorkspaceAgentEvent(
                    "reasoning_note",
                    participant_id=participant.custom_id,
                    content=(
                        f"已达到配置步数 {max_steps}，但当前是可写修复任务。"
                        "继续执行，不再因为步数上限停止；如需中止请手动停止会话。"
                    ),
                    round_number=round_number,
                    metadata={
                        "summary": "已进入 Codex 风格持续修复循环",
                        "step": step,
                        "configured_max_steps": max_steps,
                        "unbounded": True,
                    },
                )
                await _record_agent_event(emitted_chunks, budget_event, emit_event)
                working_messages = build_step_budget_extension_follow_up_messages(
                    working_messages,
                    max_steps=max_steps,
                )

            await _record_agent_event(
                emitted_chunks,
                WorkspaceAgentEvent(
                    "model_request",
                    participant_id=participant.custom_id,
                    round_number=round_number,
                    metadata={
                        "summary": "发起模型请求",
                        "model_ref": participant.model_ref,
                        "step": step,
                    },
                ),
                emit_event,
            )
            try:
                raw_output = await model_stream_factory(working_messages)
            except Exception as exc:
                return await self._failure_result(
                    participant=participant,
                    round_number=round_number,
                    emitted_chunks=emitted_chunks,
                    persisted_messages=persisted_messages,
                    code=_workspace_agent_error_code(exc),
                    error_message=str(exc),
                    emit_event=emit_event,
                )
            await _record_agent_event(
                emitted_chunks,
                WorkspaceAgentEvent(
                    "model_response",
                    participant_id=participant.custom_id,
                    round_number=round_number,
                    metadata={
                        "summary": "模型返回输出",
                        "model_ref": participant.model_ref,
                        "step": step,
                    },
                ),
                emit_event,
            )
            directive = parse_workspace_agent_directive(raw_output)
            if directive is None:
                if _should_retry_missing_tool_call(
                    profile=profile,
                    prompt_messages=working_messages,
                    raw_output=raw_output,
                    persisted_messages=persisted_messages,
                ):
                    await _record_agent_event(
                        emitted_chunks,
                        WorkspaceAgentEvent(
                            "reasoning_note",
                            participant_id=participant.custom_id,
                            content=_truncate_event_content(raw_output),
                            round_number=round_number,
                            metadata={
                                "summary": "模型没有发起工具调用，已要求继续执行",
                                "step": step,
                            },
                        ),
                        emit_event,
                    )
                    working_messages = build_missing_tool_call_follow_up_messages(
                        working_messages,
                        raw_output,
                    )
                    continue
                if raw_output:
                    await _record_agent_event(
                        emitted_chunks,
                        WorkspaceAgentEvent(
                            "chunk",
                            participant_id=participant.custom_id,
                            content=raw_output,
                            round_number=round_number,
                        ),
                        emit_event,
                    )
                return WorkspaceAgentRunResult(
                    final_content=raw_output,
                    emitted_chunks=emitted_chunks,
                    persisted_messages=persisted_messages,
                )

            if directive.action == "plan":
                await _record_agent_event(
                    emitted_chunks,
                    WorkspaceAgentEvent(
                        "agent_plan",
                        participant_id=participant.custom_id,
                        content=directive.plan or raw_output,
                        round_number=round_number,
                    ),
                    emit_event,
                )
                if _should_continue_after_plan(profile=profile, prompt_messages=working_messages):
                    working_messages = build_plan_follow_up_messages(
                        working_messages,
                        directive.plan or raw_output,
                    )
                    continue
                return WorkspaceAgentRunResult(
                    final_content="",
                    emitted_chunks=emitted_chunks,
                    persisted_messages=persisted_messages,
                )

            if directive.action != "tool_call" or not directive.server_name or not directive.tool_name:
                break

            await _record_agent_event(
                emitted_chunks,
                WorkspaceAgentEvent(
                    "phase_start",
                    participant_id=participant.custom_id,
                    round_number=round_number,
                    metadata={
                        "phase": "call_tool",
                        "summary": f"调用工具 {directive.server_name}.{directive.tool_name}",
                        "step": step,
                    },
                ),
                emit_event,
            )
            await _record_agent_event(
                emitted_chunks,
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
                emit_event,
            )

            async def emit_tool_output(payload: Dict[str, object]) -> None:
                await _record_agent_event(
                    emitted_chunks,
                    WorkspaceAgentEvent(
                        "tool_output",
                        participant_id=participant.custom_id,
                        content=str(payload.get("text") or ""),
                        round_number=round_number,
                        metadata={
                            "server_name": directive.server_name,
                            "tool_name": directive.tool_name,
                            "arguments": directive.arguments,
                            **payload,
                        },
                    ),
                    emit_event,
                )

            try:
                tool_result = await self.tool_runtime.call_tool(
                    participant.custom_id,
                    directive.server_name,
                    directive.tool_name,
                    directive.arguments,
                    emit_event=emit_tool_output,
                )
            except Exception as exc:
                return await self._failure_result(
                    participant=participant,
                    round_number=round_number,
                    emitted_chunks=emitted_chunks,
                    persisted_messages=persisted_messages,
                    code="WORKSPACE_TOOL_ERROR",
                    error_message=str(exc),
                    emit_event=emit_event,
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
            for event in [
                WorkspaceAgentEvent(
                    "tool_result",
                    participant_id=participant.custom_id,
                    round_number=round_number,
                    metadata={
                        "server_name": directive.server_name,
                        "tool_name": directive.tool_name,
                        "text": _truncate_event_content(tool_result.text, limit=4_000),
                    },
                ),
                WorkspaceAgentEvent(
                    "state_write",
                    participant_id=participant.custom_id,
                    round_number=round_number,
                    metadata={
                        "target": "tool_output",
                        "summary": "已持久化工具输出",
                        "server_name": directive.server_name,
                        "tool_name": directive.tool_name,
                    },
                ),
            ]:
                await _record_agent_event(emitted_chunks, event, emit_event)
            follow_up_messages = build_tool_result_follow_up_messages(
                working_messages,
                directive.server_name,
                directive.tool_name,
                tool_result.text,
            )
            working_messages = follow_up_messages
            continue

        max_step_message = "workspace agent 达到最大执行步数，已停止。"
        max_step_code = "AGENT_MAX_STEPS"
        max_step_event = WorkspaceAgentEvent(
            "participant_error",
            participant_id=participant.custom_id,
            round_number=round_number,
            metadata={
                "code": max_step_code,
                "message": max_step_message,
                "configured_max_steps": max_steps,
            },
        )
        await _record_agent_event(emitted_chunks, max_step_event, emit_event)
        return WorkspaceAgentRunResult(
            final_content="",
            emitted_chunks=emitted_chunks,
            persisted_messages=persisted_messages,
            terminated=True,
            failure_summary=_build_workspace_agent_failure_summary(
                participant.custom_id,
                persisted_messages,
                max_step_message,
            ),
        )

    async def _failure_result(
        self,
        participant: ModelParticipant,
        round_number: int,
        emitted_chunks: List["WorkspaceAgentEvent"],
        persisted_messages: List[CollaborationMessage],
        code: str,
        error_message: str,
        emit_event: Optional[EmitAgentEvent] = None,
    ) -> WorkspaceAgentRunResult:
        failure_event = WorkspaceAgentEvent(
            "participant_error",
            participant_id=participant.custom_id,
            round_number=round_number,
            metadata={
                "code": code,
                "message": error_message,
            },
        )
        await _record_agent_event(emitted_chunks, failure_event, emit_event)
        return WorkspaceAgentRunResult(
            final_content="",
            emitted_chunks=emitted_chunks,
            persisted_messages=persisted_messages,
            terminated=True,
            failure_summary=_build_workspace_agent_failure_summary(
                participant.custom_id,
                persisted_messages,
                error_message,
            ),
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


def _workspace_agent_error_code(exc: Exception) -> str:
    if isinstance(exc, AuthenticationError):
        return "AUTHENTICATION_REQUIRED"
    if isinstance(exc, ProviderUnavailableError):
        return "PROVIDER_UNAVAILABLE"
    return "WORKSPACE_AGENT_ERROR"


async def _record_agent_event(
    emitted_chunks: List[WorkspaceAgentEvent],
    event: WorkspaceAgentEvent,
    emit_event: Optional[EmitAgentEvent],
) -> None:
    emitted_chunks.append(event)
    if emit_event is not None:
        await emit_event(event)


def _should_extend_step_budget(
    *,
    profile: AgentProfileConfig,
    prompt_messages: List[Dict[str, str]],
) -> bool:
    return bool(getattr(profile, "can_write", False)) and _latest_user_request_expects_workspace_action(
        prompt_messages
    )


def _should_continue_after_plan(
    *,
    profile: AgentProfileConfig,
    prompt_messages: List[Dict[str, str]],
) -> bool:
    return _should_extend_step_budget(profile=profile, prompt_messages=prompt_messages)


def parse_workspace_agent_directive(text: str) -> Optional[WorkspaceAgentDirective]:
    normalized = _strip_json_code_fence(text.strip())
    if not normalized:
        return None
    payload = _extract_first_json_object(normalized)
    if not isinstance(payload, dict):
        return None
    directive = _directive_from_payload(payload)
    if directive is not None:
        return directive
    action = str(payload.get("action") or "").strip()
    if not action:
        return None
    if action == "plan":
        return WorkspaceAgentDirective(
            action=action,
            plan=str(payload.get("plan") or "").strip() or None,
        )
    return WorkspaceAgentDirective(action=action)


def _directive_from_payload(payload: dict[str, object]) -> Optional[WorkspaceAgentDirective]:
    action = _payload_str(payload, "action").lower()
    if action == "plan":
        return WorkspaceAgentDirective(
            action="plan",
            plan=_payload_str(payload, "plan") or None,
        )

    tool_name = _normalize_tool_name(
        _payload_str(payload, "tool")
        or _payload_str(payload, "tool_name")
        or _payload_str(payload, "name")
    )
    server_name = (
        _payload_str(payload, "server")
        or _payload_str(payload, "server_name")
        or "workspace"
    )

    if action == "tool_call" or tool_name:
        if not tool_name:
            return WorkspaceAgentDirective(
                action="tool_call",
                server_name=server_name or None,
                tool_name=None,
                arguments=_payload_dict(payload, "arguments"),
            )
        return WorkspaceAgentDirective(
            action="tool_call",
            server_name=server_name or "workspace",
            tool_name=tool_name,
            arguments=_tool_arguments_from_payload(payload, tool_name),
        )

    if action in {"run_command", "command", "cmd", "shell", "terminal", "execute"} or _looks_like_command_payload(payload):
        return WorkspaceAgentDirective(
            action="tool_call",
            server_name="workspace",
            tool_name="run_command",
            arguments=_command_arguments_from_payload(payload),
        )

    if action in {"write_file", "write", "create_file", "save_file", "edit_file"} or _looks_like_write_payload(payload):
        return WorkspaceAgentDirective(
            action="tool_call",
            server_name="workspace",
            tool_name="write_file",
            arguments=_write_arguments_from_payload(payload),
        )

    return None


def _tool_arguments_from_payload(payload: dict[str, object], tool_name: str) -> Dict[str, Any]:
    arguments = dict(_payload_dict(payload, "arguments"))
    if tool_name == "run_command":
        return _command_arguments_from_payload(payload, arguments)
    if tool_name == "write_file":
        return _write_arguments_from_payload(payload, arguments)
    if tool_name in {"read_file", "list_files"}:
        path = _first_payload_value(payload, ("path", "file_path", "filepath", "file", "filename"))
        if isinstance(path, str) and "path" not in arguments:
            arguments["path"] = path
    return arguments


def _command_arguments_from_payload(
    payload: dict[str, object],
    seed: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    arguments: Dict[str, Any] = dict(seed or {})
    if "command" not in arguments:
        command = _first_payload_value(payload, ("command", "cmd"))
        if command is not None:
            arguments["command"] = command
    if "command_line" not in arguments:
        command_line = _first_payload_value(payload, ("command_line", "shell_command", "script"))
        if command_line is not None:
            arguments["command_line"] = command_line
    for key in ("args", "argv", "cwd", "timeout_seconds", "shell", "os", "system", "platform"):
        if key not in arguments and key in payload:
            arguments[key] = payload[key]
    if "timeout_seconds" not in arguments and "timeout" in payload:
        arguments["timeout_seconds"] = payload["timeout"]
    return arguments


def _write_arguments_from_payload(
    payload: dict[str, object],
    seed: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    arguments: Dict[str, Any] = dict(seed or {})
    source = payload
    files = payload.get("files")
    if isinstance(files, list):
        first_file = next((item for item in files if isinstance(item, dict)), None)
        if isinstance(first_file, dict):
            source = first_file  # type: ignore[assignment]

    if "path" not in arguments:
        path = _first_payload_value(source, ("path", "file_path", "filepath", "file", "filename"))
        if path is not None:
            arguments["path"] = path
    if "content" not in arguments:
        content = _first_payload_value(source, ("content", "text", "body", "data"))
        if content is not None:
            arguments["content"] = content
    if "overwrite" not in arguments and "overwrite" in source:
        arguments["overwrite"] = source["overwrite"]
    return arguments


def _looks_like_command_payload(payload: dict[str, object]) -> bool:
    return any(key in payload for key in ("command", "cmd", "argv", "command_line", "shell_command", "script"))


def _looks_like_write_payload(payload: dict[str, object]) -> bool:
    if isinstance(payload.get("content"), str) and any(
        key in payload for key in ("path", "file_path", "filepath", "file", "filename")
    ):
        return True
    files = payload.get("files")
    if isinstance(files, list):
        return any(isinstance(item, dict) and _looks_like_write_payload(item) for item in files)
    return False


def _normalize_tool_name(tool_name: str) -> str:
    normalized = tool_name.strip().lower()
    aliases = {
        "command": "run_command",
        "cmd": "run_command",
        "execute": "run_command",
        "shell": "run_command",
        "terminal": "run_command",
        "write": "write_file",
        "create_file": "write_file",
        "save_file": "write_file",
        "edit_file": "write_file",
    }
    return aliases.get(normalized, normalized)


def _payload_str(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    return value.strip() if isinstance(value, str) else ""


def _payload_dict(payload: dict[str, object], key: str) -> Dict[str, Any]:
    value = payload.get(key)
    return dict(value) if isinstance(value, dict) else {}


def _first_payload_value(payload: dict[str, object], keys: tuple[str, ...]) -> object:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str):
            if value.strip():
                return value
            continue
        if value is not None:
            return value
    return None


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
                "请基于工具结果继续判断是否还需要修改文件或运行验证命令。"
                "如果任务要求修复、实现、更新代码或执行本地验证，不要停在解释层面。"
                "如果任务还没有修复完成，请继续发起 tool_call；"
                "如果命令失败，请根据输出继续修改文件并再次运行验证命令；"
                "只有在修改完成并验证通过后，才输出最终答复。"
                "最终答复请简洁列出：修改了哪些文件、运行了哪些命令、验证结果如何、是否仍有阻塞。"
            ),
        },
    ]


def build_plan_follow_up_messages(
    prompt_messages: List[Dict[str, str]],
    plan_text: str,
) -> List[Dict[str, str]]:
    return [
        *prompt_messages,
        {
            "role": "assistant",
            "content": plan_text,
        },
        {
            "role": "user",
            "content": (
                "计划已记录并展示。当前是可写 code_workspace 修复任务，不能在计划阶段停止。"
                "请立即继续执行，只输出一个 workspace tool_call JSON。"
                "先读取必要文件，再写入修复，随后运行验证命令。"
                "只有在代码已修改且验证通过或明确阻塞后，才输出最终总结。"
            ),
        },
    ]


def build_step_budget_extension_follow_up_messages(
    prompt_messages: List[Dict[str, str]],
    *,
    max_steps: int,
) -> List[Dict[str, str]]:
    return [
        *prompt_messages,
        {
            "role": "user",
            "content": (
                f"你已经用完配置步数 {max_steps}，但当前修复尚未完成。"
                "系统已进入持续修复循环，不会再因为步数上限停止。"
                "不要总结未完成状态；继续发起下一个 workspace tool_call。"
                "如果刚才命令失败，请根据输出修改文件并重新验证。"
                "如果已经验证通过，请输出最终总结，列出修改文件、执行命令、验证结果和剩余风险。"
            ),
        },
    ]


def build_missing_tool_call_follow_up_messages(
    prompt_messages: List[Dict[str, str]],
    raw_output: str,
) -> List[Dict[str, str]]:
    return [
        *prompt_messages,
        {
            "role": "assistant",
            "content": raw_output,
        },
        {
            "role": "user",
            "content": (
                "你刚才只描述了计划，没有发起工具调用。"
                "当前是可写 code_workspace 修复任务，不能把“我先修复/我将执行”当最终结果。"
                "请立即只输出一个可执行的 workspace tool_call JSON。"
                "优先用 read_file/list_files 获取上下文，用 write_file 写入文件，用 run_command 运行验证。"
                "如果要运行命令，输出例如："
                "{\"action\":\"tool_call\",\"server\":\"workspace\",\"tool\":\"run_command\",\"arguments\":{\"command\":\"python3 -m pytest tests/test_workspace_agent.py -q\"}}。"
                "如果要写入文件，输出例如："
                "{\"action\":\"tool_call\",\"server\":\"workspace\",\"tool\":\"write_file\",\"arguments\":{\"path\":\"backend/example.py\",\"content\":\"...\"}}。"
                "不要再输出自然语言承诺；没有工具输出就没有修复过程。"
            ),
        },
    ]


def _strip_json_code_fence(text: str) -> str:
    if text.startswith("```") and text.endswith("```"):
        lines = text.splitlines()
        if len(lines) >= 3:
            return "\n".join(lines[1:-1]).strip()
    return text


def _should_retry_missing_tool_call(
    *,
    profile: AgentProfileConfig,
    prompt_messages: List[Dict[str, str]],
    raw_output: str,
    persisted_messages: List[CollaborationMessage],
) -> bool:
    if not bool(getattr(profile, "can_write", False)):
        return False
    if not _latest_user_request_expects_workspace_action(prompt_messages):
        return False
    if not persisted_messages:
        return True
    return _looks_like_incomplete_or_promissory_output(raw_output)


def _latest_user_request_expects_workspace_action(prompt_messages: List[Dict[str, str]]) -> bool:
    latest_request = _latest_user_intervention_text(prompt_messages)
    if not latest_request:
        return False
    normalized = latest_request.lower()
    return any(keyword in normalized for keyword in _WORKSPACE_ACTION_KEYWORDS)


def _latest_user_intervention_text(prompt_messages: List[Dict[str, str]]) -> str:
    for message in reversed(prompt_messages):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        content = str(message.get("content") or "")
        matches = list(_USER_INTERVENTION_PATTERN.finditer(content))
        if matches:
            return matches[-1].group(1).strip()
        if content.strip():
            return content.strip()
    return ""


def _looks_like_incomplete_or_promissory_output(raw_output: str) -> bool:
    normalized = raw_output.strip().lower()
    if not normalized:
        return True
    return any(marker in normalized for marker in _INCOMPLETE_OR_PROMISSORY_MARKERS)


def _truncate_event_content(text: str, limit: int = 1_000) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... [truncated {len(text) - limit} characters]"


_USER_INTERVENTION_PATTERN = re.compile(
    r"\[\[用户\]\|user_intervention\]: (.*?)(?=\n\[[^\n]+\|[a-z_]+\]:|\Z)",
    re.S,
)
_WORKSPACE_ACTION_KEYWORDS = (
    "修复",
    "继续修",
    "可以修",
    "直接执行",
    "执行命令",
    "运行命令",
    "写入",
    "修改",
    "落地",
    "跑验证",
    "验证",
    "补丁",
    "改代码",
    "fix",
    "repair",
    "patch",
    "implement",
    "update",
    "modify",
    "edit",
    "write",
    "run",
    "execute",
    "verify",
    "test",
)
_INCOMPLETE_OR_PROMISSORY_MARKERS = (
    "我先",
    "我会",
    "我将",
    "准备",
    "下一步",
    "继续",
    "还没",
    "未完成",
    "没有实际",
    "不能宣称",
    "需要你",
    "阻塞",
    "will",
    "i'll",
    "i will",
    "going to",
    "next step",
    "not complete",
    "blocked",
)


def _extract_first_json_object(text: str) -> Optional[dict[str, object]]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _build_workspace_agent_failure_summary(
    participant_id: str,
    persisted_messages: List[CollaborationMessage],
    error_message: str,
) -> str:
    tool_names: List[str] = []
    commands: List[str] = []
    updated_files: List[str] = []

    for message in persisted_messages:
        if message.message_type != MessageType.TOOL_OUTPUT:
            continue
        server_name = ""
        tool_name = ""
        for line in message.content.splitlines():
            if line.startswith("server="):
                server_name = line.split("=", 1)[1].strip()
            elif line.startswith("tool="):
                tool_name = line.split("=", 1)[1].strip()
            elif line.startswith("command="):
                command = line.split("=", 1)[1].strip()
                if command and command not in commands:
                    commands.append(command)
            elif line.startswith("Wrote ") and " to " in line:
                updated_path = line.rsplit(" to ", 1)[1].strip()
                if updated_path and updated_path not in updated_files:
                    updated_files.append(updated_path)
        tool_label = ".".join(part for part in (server_name, tool_name) if part)
        if tool_label and tool_label not in tool_names:
            tool_names.append(tool_label)

    return "\n".join(
        [
            "本轮未完成修复。",
            f"- 参与者：{participant_id}",
            f"- 已执行的工具：{', '.join(tool_names) if tool_names else '无'}",
            f"- 已修改的文件：{', '.join(updated_files) if updated_files else '未确认'}",
            f"- 运行的命令：{'; '.join(commands) if commands else '无'}",
            "- 验证结果：未确认通过",
            f"- 失败原因：{error_message}",
        ]
    )
