import { applyStreamEvent } from "./sessionStream";

describe("applyStreamEvent agent events", () => {
  test("tracks agent execution events alongside system messages", () => {
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

    expect(state.messages).toHaveLength(3);
    expect(state.messages[0]).toMatchObject({
      senderId: "system",
      type: "system",
      content: expect.stringContaining("先查看 README"),
    });
    expect(state.messages[1]).toMatchObject({
      senderId: "system",
      type: "system",
      content: expect.stringContaining("filesystem.read_file"),
    });
    expect(state.messages[2]).toMatchObject({
      senderId: "system",
      type: "system",
      content: expect.stringContaining("README content"),
    });
    expect(state.executionEvents).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          event: "turn_start",
          participantId: "claude",
          summary: expect.stringContaining("开始执行"),
        }),
        expect.objectContaining({
          event: "tool_call",
          participantId: "claude",
          summary: expect.stringContaining("filesystem.read_file"),
        }),
        expect.objectContaining({
          event: "tool_result",
          participantId: "claude",
          detail: expect.stringContaining("README content"),
        }),
      ]),
    );
  });
});
