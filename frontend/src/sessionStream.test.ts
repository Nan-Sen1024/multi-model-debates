import { applyStreamEvent } from "./sessionStream";

describe("applyStreamEvent agent events", () => {
  test("renders agent plan, tool call, and tool result as system messages", () => {
    let state = {
      messages: [],
      liveMessage: null,
      streamState: "idle" as const,
    };

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
  });
});
