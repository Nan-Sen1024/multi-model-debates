import { applyStreamEvent } from "./sessionStream";

describe("applyStreamEvent agent events", () => {
  test("tracks agent execution events in the live execution log", () => {
    let state = {
      messages: [],
      liveMessage: null,
      streamState: "idle" as const,
      executionEvents: [],
    };

    state = applyStreamEvent(state, "turn_start", {
      participant_id: "claude",
      round: 1,
      execution_mode: "agent",
    });
    state = applyStreamEvent(state, "agent_plan", {
      participant_id: "claude",
      content: "先查看 README，再调用工具。",
      round: 1,
    });
    state = applyStreamEvent(state, "tool_call", {
      participant_id: "claude",
      round: 1,
      server_name: "filesystem",
      tool_name: "read_file",
      arguments: { path: "README.md" },
    });
    state = applyStreamEvent(state, "tool_result", {
      participant_id: "claude",
      round: 1,
      server_name: "filesystem",
      tool_name: "read_file",
      text: "README content",
    });

    expect(state.messages).toHaveLength(0);
    expect(state.executionEvents).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event: "turn_start",
          participantId: "claude",
          summary: expect.stringContaining("开始执行"),
        }),
        expect.objectContaining({
          event: "tool_result",
          participantId: "claude",
          summary: expect.stringContaining("filesystem.read_file"),
          detail: expect.stringContaining("README content"),
        }),
      ]),
    );
  });

  test("maps phase and state events into execution log records", () => {
    let state = {
      messages: [],
      liveMessage: null,
      streamState: "idle" as const,
      executionEvents: [],
    };

    state = applyStreamEvent(state, "phase_start", {
      participant_id: "claude",
      round: 2,
      phase: "build_prompt",
      summary: "构建工作区上下文",
    });
    state = applyStreamEvent(state, "state_write", {
      participant_id: "claude",
      round: 2,
      target: "message",
      summary: "已写入参与者消息",
    });

    expect(state.executionEvents).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event: "phase_start",
          kind: "phase",
          phase: "build_prompt",
        }),
        expect.objectContaining({
          event: "state_write",
          kind: "state",
        }),
      ]),
    );
    expect(state.messages).toHaveLength(0);
  });

  test("merges phase start and end into one atomic execution record", () => {
    let state = {
      messages: [],
      liveMessage: null,
      streamState: "idle" as const,
      executionEvents: [],
    };

    state = applyStreamEvent(state, "phase_start", {
      participant_id: "claude",
      round: 3,
      phase: "build_prompt",
      summary: "构建会话上下文",
    });
    state = applyStreamEvent(state, "phase_end", {
      participant_id: "claude",
      round: 3,
      phase: "build_prompt",
      summary: "会话上下文构建完成",
      input_message_count: 4,
    });

    expect(state.executionEvents).toHaveLength(1);
    expect(state.executionEvents[0]).toMatchObject({
      event: "phase_end",
      kind: "phase",
      phase: "build_prompt",
      status: "done",
      summary: "会话上下文构建完成",
      detail: expect.stringContaining("input_message_count=4"),
    });
    expect(state.messages).toHaveLength(0);
  });

  test("keeps streamed terminal output visible while tool call/result are finalized", () => {
    let state = {
      messages: [],
      liveMessage: null,
      streamState: "idle" as const,
      executionEvents: [],
    };

    state = applyStreamEvent(state, "tool_call", {
      participant_id: "claude",
      round: 4,
      server_name: "workspace",
      tool_name: "run_command",
      arguments: { command: "pytest", args: ["-q"] },
    });
    state = applyStreamEvent(state, "tool_output", {
      participant_id: "claude",
      round: 4,
      server_name: "workspace",
      tool_name: "run_command",
      stream: "stdout",
      text: "one test passed\n",
    });
    state = applyStreamEvent(state, "tool_result", {
      participant_id: "claude",
      round: 4,
      server_name: "workspace",
      tool_name: "run_command",
      text: "command=pytest -q\ncwd=.\nexit_code=0\nstdout:\none test passed",
    });

    expect(state.executionEvents).toHaveLength(2);
    expect(state.executionEvents).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event: "tool_output",
          kind: "output",
          status: "info",
          detail: expect.stringContaining("one test passed"),
        }),
        expect.objectContaining({
          event: "tool_result",
          kind: "tool",
          status: "done",
          summary: "workspace.run_command 返回结果",
          detail: expect.stringContaining("exit_code=0"),
        }),
      ]),
    );
    expect(state.executionEvents.find((event) => event.event === "tool_result")).toMatchObject({
      event: "tool_result",
      kind: "tool",
      status: "done",
      summary: "workspace.run_command 返回结果",
      detail: expect.stringContaining("exit_code=0"),
    });
    expect(state.messages).toHaveLength(0);
  });

  test("streams model output chunks into a live execution record", () => {
    let state = {
      messages: [],
      liveMessage: null,
      streamState: "idle" as const,
      executionEvents: [],
    };

    state = applyStreamEvent(state, "model_output", {
      participant_id: "claude",
      round: 4,
      model_ref: "anthropic/claude-4.6",
      content: "{\"action\"",
    });
    state = applyStreamEvent(state, "model_output", {
      participant_id: "claude",
      round: 4,
      model_ref: "anthropic/claude-4.6",
      content: ":\"tool_call\"}",
    });

    expect(state.executionEvents).toHaveLength(1);
    expect(state.executionEvents[0]).toMatchObject({
      event: "model_output",
      kind: "model",
      status: "info",
      detail: "{\"action\":\"tool_call\"}",
    });
    expect(state.messages).toHaveLength(0);
  });

  test("marks participant errors as warnings without failing the stream", () => {
    let state = {
      messages: [],
      liveMessage: null,
      streamState: "idle" as const,
      executionEvents: [],
    };

    state = applyStreamEvent(state, "turn_start", {
      participant_id: "claude",
      round: 5,
      execution_mode: "agent",
    });
    state = applyStreamEvent(state, "chunk", {
      participant_id: "claude",
      round: 5,
      content: "partial output",
    });
    state = applyStreamEvent(state, "participant_error", {
      participant_id: "claude",
      round: 5,
      code: "PROVIDER_UNAVAILABLE",
      message: "provider down",
    });

    expect(state.streamState).toBe("streaming");
    expect(state.liveMessage).toBeNull();
    expect(state.executionEvents).toHaveLength(1);
    expect(state.executionEvents[0]).toMatchObject({
      event: "participant_error",
      participantId: "claude",
      status: "warning",
      detail: expect.stringContaining("PROVIDER_UNAVAILABLE"),
    });
    expect(state.messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          type: "execution",
          status: "warning",
        }),
        expect.objectContaining({
          type: "model",
          status: "warning",
          content: "partial output",
        }),
      ]),
    );
  });

  test("shows model and provider details for authentication expiry errors", () => {
    let state = {
      messages: [],
      liveMessage: null,
      streamState: "idle" as const,
      executionEvents: [],
    };

    state = applyStreamEvent(state, "participant_error", {
      participant_id: "g54",
      round: 61,
      code: "AUTHENTICATION_REQUIRED",
      message: "ChatGPT OAuth authentication failed; re-login required",
      model_ref: "gpt-5.4",
      provider_name: "cc",
      provider_id: "provider-cc",
      auth_type: "oauth",
      remediation: "请重新登录该 Provider，或切换到可用 fallback。",
    });

    expect(state.executionEvents[0]).toMatchObject({
      event: "participant_error",
      participantId: "g54",
      summary: "g54 认证过期",
      status: "warning",
      detail: expect.stringContaining("模型：gpt-5.4"),
    });
    expect(state.executionEvents[0].detail).toContain("Provider：cc (provider-cc)");
    expect(state.executionEvents[0].detail).toContain("认证方式：oauth");
    expect(state.executionEvents[0].detail).toContain("AUTHENTICATION_REQUIRED");
  });
});
