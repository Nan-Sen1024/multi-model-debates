import { applyStreamEvent } from "./sessionStream";

describe("applyStreamEvent agent events", () => {
  test("tracks agent execution events in the live execution log without mirroring file activity into chat", () => {
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
          summary: "已读取文件",
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

  test("keeps streamed terminal output visible while command execution is mirrored into chat", () => {
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
          summary: "命令执行完成",
          detail: expect.stringContaining("退出码：0"),
        }),
      ]),
    );
    expect(state.executionEvents.find((event) => event.event === "tool_result")).toMatchObject({
      event: "tool_result",
      kind: "tool",
      status: "done",
      summary: "命令执行完成",
      detail: expect.stringContaining("退出码：0"),
    });
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0]).toMatchObject({
      type: "execution",
      executionTitle: "命令执行完成",
      executionKind: "tool",
      executionDetail: expect.stringContaining("退出码：0"),
      content: expect.stringContaining("命令执行完成"),
    });
  });

  test("mirrors successful run command results even when no tool call event was received", () => {
    let state = {
      messages: [],
      liveMessage: null,
      streamState: "idle" as const,
      executionEvents: [],
    };

    state = applyStreamEvent(state, "tool_result", {
      participant_id: "claude",
      round: 4,
      server_name: "workspace",
      tool_name: "run_command",
      text: "command=npm test -- --runInBand\ncwd=D:/repo/demo\nexit_code=0\nstdout:\n5 passed",
    });

    expect(state.executionEvents).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event: "tool_result",
          summary: "命令执行完成",
          detail: expect.stringContaining("退出码：0"),
        }),
      ]),
    );
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0]).toMatchObject({
      type: "execution",
      executionTitle: "命令执行完成",
      executionKind: "tool",
      executionDetail: expect.stringContaining("命令：npm test -- --runInBand"),
      content: expect.stringContaining("命令执行完成"),
    });
  });

  test("keeps file access tool calls in execution events without mirroring them into chat", () => {
    let state = {
      messages: [],
      liveMessage: null,
      streamState: "idle" as const,
      executionEvents: [],
    };

    state = applyStreamEvent(state, "tool_call", {
      participant_id: "claude",
      round: 5,
      server_name: "workspace",
      tool_name: "read_file",
      arguments: { path: "backend/orchestrator.py" },
    });

    expect(state.executionEvents).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event: "tool_call",
          kind: "tool",
          summary: "读取文件 backend/orchestrator.py",
          detail: expect.stringContaining("路径：backend/orchestrator.py"),
        }),
      ]),
    );
    expect(state.messages).toHaveLength(0);
  });

  test("formats run command execution into readable structured detail", () => {
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
      arguments: {
        command: "npm",
        args: ["test", "--", "--runInBand"],
        cwd: "D:/repo/demo",
      },
    });
    state = applyStreamEvent(state, "tool_result", {
      participant_id: "claude",
      round: 4,
      server_name: "workspace",
      tool_name: "run_command",
      text: "command=npm test -- --runInBand\ncwd=D:/repo/demo\nexit_code=1\nstdout:\n1 failed\nstderr:\nwarning detail",
    });

    const toolResult = state.executionEvents.find((event) => event.event === "tool_result");

    expect(toolResult).toMatchObject({
      summary: "命令执行失败",
      detail: expect.stringContaining("目录：D:/repo/demo"),
      metadata: expect.objectContaining({
        arguments: expect.objectContaining({
          command: "npm",
        }),
      }),
    });
    expect(toolResult?.detail).toContain("命令：npm test -- --runInBand");
    expect(toolResult?.detail).toContain("退出码：1");
    expect(toolResult?.detail).toContain("标准输出");
    expect(toolResult?.detail).toContain("标准错误");
  });

  test("maps research events into structured execution records and mirrored chat messages", () => {
    let state = {
      messages: [],
      liveMessage: null,
      streamState: "idle" as const,
      executionEvents: [],
    };

    state = applyStreamEvent(state, "research_search", {
      participant_id: "deepseek",
      round: 6,
      query: "中美之间的大事件",
      result_count: 43,
      summary: "搜索到 43 个网页",
    });
    state = applyStreamEvent(state, "research_open_pages", {
      participant_id: "deepseek",
      round: 6,
      page_count: 13,
      items: [
        "中美通话不到48小时，美方重量级代表落地北京",
        "中美如何做大人工智能合作的蛋糕",
        "查看全部",
      ],
      summary: "浏览 13 个页面",
    });
    state = applyStreamEvent(state, "research_note", {
      participant_id: "deepseek",
      round: 6,
      content: "这些结果涵盖贸易、外交、军事、科技等多个方面。",
      summary: "这些结果涵盖了多个方面",
    });

    expect(state.executionEvents).toHaveLength(1);
    expect(state.executionEvents[0]).toMatchObject({
      event: "research_search",
      kind: "note",
      summary: "搜索到 43 个网页",
      detail: expect.stringContaining("查询：中美之间的大事件"),
    });
    expect(state.executionEvents[0].detail).toContain("浏览 13 个页面");
    expect(state.executionEvents[0].detail).toContain("中美如何做大人工智能合作的蛋糕");
    expect(state.executionEvents[0].detail).toContain("这些结果涵盖了多个方面");
    expect(state.messages.filter((message) => message.type === "execution")).toHaveLength(1);
    expect(state.messages.some((message) => message.type === "execution" && message.content.includes("搜索到 43 个网页"))).toBe(true);
    expect(state.messages.some((message) => message.type === "execution" && message.content.includes("浏览 13 个页面"))).toBe(true);
  });

  test("summarizes noisy bash command-not-found stderr into a readable diagnosis", () => {
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
      arguments: {
        command: "bash",
        args: ["-lc", "python - <<'PY'"],
        cwd: ".",
        shell: "bash",
        timeout_seconds: 120,
      },
    });
    state = applyStreamEvent(state, "tool_result", {
      participant_id: "claude",
      round: 4,
      server_name: "workspace",
      tool_name: "run_command",
      text: "command=bash -lc python - <<'PY'\ncwd=.\nexit_code=127\nstderr:\nwsl: \uFFFDhKm0R localhost \uFFFDN\u0006tM\uFFFDn\u007F\f\uFFFDFO*g\\\uFFFD\uFFFDP0R WSL\u00020NAT !j\u000F_\u000BN\uFFFDv WSL \rN/e\u0001c localhost \uFFFDN\u0006t\u00020\n/bin/bash: line 1: python: command not found",
    });

    const toolResult = state.executionEvents.find((event) => event.event === "tool_result");

    expect(toolResult?.summary).toBe("命令执行失败");
    expect(toolResult?.detail).toContain("bash 环境中未找到 python 命令");
    expect(toolResult?.detail).toContain("建议：改用 python3，或切换到已安装 Python 的 shell。");
    expect(toolResult?.detail).not.toContain("wsl:");
    expect(toolResult?.detail).not.toContain("/bin/bash: line 1: python: command not found");
    expect(toolResult?.detail).not.toContain("�");
  });

  test("collapses raw tool_call JSON model output into a generic execution record", () => {
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
      summary: "模型准备调用工具",
    });
    expect(state.executionEvents[0].detail).toBeUndefined();
    expect(state.messages).toHaveLength(0);
  });

  test("collapses concatenated tool_call protocol noise into a generic execution record", () => {
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
      content:
        "{\"action\":\"tool_call\",\"server\":\"workspace\",\"tool\":\"read_file\"}{\"action\":\"tool_call\",\"server\":\"workspace\",\"tool\":\"run_command\"}",
    });

    expect(state.executionEvents).toHaveLength(1);
    expect(state.executionEvents[0]).toMatchObject({
      event: "model_output",
      kind: "model",
      status: "info",
      summary: "模型准备调用工具",
    });
    expect(state.executionEvents[0].detail).toBeUndefined();
  });

  test("humanizes state_write events instead of exposing raw step metadata", () => {
    let state = {
      messages: [],
      liveMessage: null,
      streamState: "idle" as const,
      executionEvents: [],
    };

    state = applyStreamEvent(state, "state_write", {
      participant_id: "r7",
      round: 7,
      target: "message",
      summary: "step",
    });

    expect(state.executionEvents).toHaveLength(1);
    expect(state.executionEvents[0]).toMatchObject({
      event: "state_write",
      kind: "state",
      summary: "已写入消息",
    });
    expect(state.executionEvents[0].detail).toBeUndefined();
  });

  test("keeps normal natural-language model output visible in execution record", () => {
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
      content: "先阅读工作区文件，",
    });
    state = applyStreamEvent(state, "model_output", {
      participant_id: "claude",
      round: 4,
      model_ref: "anthropic/claude-4.6",
      content: "再汇总发现。",
    });

    expect(state.executionEvents).toHaveLength(1);
    expect(state.executionEvents[0]).toMatchObject({
      event: "model_output",
      kind: "model",
      status: "info",
      detail: "先阅读工作区文件，再汇总发现。",
    });
  });

  test("suppresses chunk-level tool_call protocol noise from the live transcript", () => {
    let state = {
      messages: [],
      liveMessage: null,
      streamState: "idle" as const,
      executionEvents: [],
    };

    state = applyStreamEvent(state, "chunk", {
      participant_id: "claude",
      round: 4,
      content: "{\"action\":\"tool_call\",\"server\":\"workspace\",\"tool\":\"read_file\",\"arguments\":{\"path\":\"README.md\"}}",
    });

    expect(state.liveMessage).toBeNull();
    expect(state.messages).toHaveLength(0);
  });

  test("suppresses split chunk tool_call protocol noise before it reaches the live transcript", () => {
    let state = {
      messages: [],
      liveMessage: null,
      streamState: "idle" as const,
      executionEvents: [],
    };

    state = applyStreamEvent(state, "chunk", {
      participant_id: "claude",
      round: 4,
      content: "{\"action\"",
    });
    state = applyStreamEvent(state, "chunk", {
      participant_id: "claude",
      round: 4,
      content: ":\"tool_call\",\"server\":\"workspace\",\"tool\":\"read_file\"}",
    });

    expect(state.liveMessage).toBeNull();
    expect(state.messages).toHaveLength(0);
  });

  test("keeps natural-language chunk output visible in the live transcript", () => {
    let state = {
      messages: [],
      liveMessage: null,
      streamState: "idle" as const,
      executionEvents: [],
    };

    state = applyStreamEvent(state, "chunk", {
      participant_id: "claude",
      round: 4,
      content: "我先查看 README，再继续定位问题。",
    });

    expect(state.liveMessage).toMatchObject({
      senderId: "claude",
      type: "model",
      content: "我先查看 README，再继续定位问题。",
      status: "streaming",
    });
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
    expect(state.executionEvents).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event: "turn_start",
          participantId: "claude",
          status: "running",
        }),
        expect.objectContaining({
          event: "participant_error",
          participantId: "claude",
          status: "warning",
          detail: expect.stringContaining("PROVIDER_UNAVAILABLE"),
        }),
      ]),
    );
    expect(state.executionEvents.find((event) => event.event === "participant_error")).toMatchObject({
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

  test("shows step-budget pauses as a paused run state instead of a generic failure", () => {
    let state = {
      messages: [],
      liveMessage: null,
      streamState: "idle" as const,
      executionEvents: [],
    };

    state = applyStreamEvent(state, "participant_error", {
      participant_id: "wg54",
      round: 8,
      code: "AGENT_MAX_STEPS",
      message: "本轮执行已暂停：达到当前步数预算。",
      configured_max_steps: 6,
    } as never);

    expect(state.executionEvents[0]).toMatchObject({
      event: "participant_error",
      participantId: "wg54",
      summary: "wg54 本轮已暂停",
      status: "warning",
    });
    expect(state.executionEvents[0].detail).toContain("当前预算：6 步");
    expect(state.executionEvents[0].detail).toContain("可继续执行");
    expect(state.messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          type: "execution",
          executionTitle: "wg54 本轮已暂停",
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
      summary: "g54 认证不可用",
      status: "warning",
      detail: expect.stringContaining("模型：gpt-5.4"),
    });
    expect(state.executionEvents[0].detail).toContain("Provider：cc (provider-cc)");
    expect(state.executionEvents[0].detail).toContain("认证方式：oauth");
    expect(state.executionEvents[0].detail).toContain("AUTHENTICATION_REQUIRED");
  });

  test("special-cases auth-required copy for api keys instead of expired wording", () => {
    let state = {
      messages: [],
      liveMessage: null,
      streamState: "idle" as const,
      executionEvents: [],
    };

    state = applyStreamEvent(state, "participant_error", {
      participant_id: "g54",
      round: 62,
      code: "AUTHENTICATION_REQUIRED",
      message: "API key invalid",
      model_ref: "gpt-5.4",
      provider_name: "openai",
      provider_id: "provider-openai",
      auth_type: "api_key",
      remediation: "请更新该 Provider 的 API Key，或切换到可用 fallback。",
    });

    expect(state.executionEvents[0]).toMatchObject({
      event: "participant_error",
      participantId: "g54",
      summary: "g54 认证不可用",
      status: "warning",
    });
    expect(state.executionEvents[0].detail).toContain("认证方式：api_key");
    expect(state.executionEvents[0].detail).toContain("API key invalid");
  });

  test("records provider fallback target transitions", () => {
    let state = {
      messages: [],
      liveMessage: null,
      streamState: "idle" as const,
      executionEvents: [],
    };

    state = applyStreamEvent(state, "provider_fallback", {
      participant_id: "g54",
      round: 63,
      provider_name: "openai-primary",
      provider_id: "provider-openai-primary",
      fallback_provider_name: "openai-backup",
      fallback_provider_id: "provider-openai-backup",
      code: "AUTHENTICATION_REQUIRED",
      message: "API key invalid",
    });

    expect(state.executionEvents[0]).toMatchObject({
      event: "provider_fallback",
      participantId: "g54",
      status: "warning",
      summary: "g54 已切换到备用路由",
    });
    expect(state.executionEvents[0].detail).toContain("主 Provider：openai-primary (provider-openai-primary)");
    expect(state.executionEvents[0].detail).toContain("Fallback Provider：openai-backup (provider-openai-backup)");
    expect(state.messages).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          type: "execution",
          executionTitle: "g54 已切换到备用路由",
          status: "warning",
        }),
      ]),
    );
  });

  test("keeps file access results in execution events without mirroring them into chat", () => {
    let state = {
      messages: [],
      liveMessage: null,
      streamState: "idle" as const,
      executionEvents: [],
    };

    state = applyStreamEvent(state, "tool_result", {
      participant_id: "claude",
      round: 64,
      server_name: "workspace",
      tool_name: "read_file",
      text: "README content",
      arguments: { path: "README.md" },
    });

    expect(state.executionEvents).toHaveLength(1);
    expect(state.messages).toHaveLength(0);
  });
});
