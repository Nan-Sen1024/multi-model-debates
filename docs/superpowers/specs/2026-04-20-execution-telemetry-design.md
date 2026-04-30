# 执行过程可视化与原子事件流设计

日期：2026-04-20

## 1. 背景

现有前端已经有 `执行过程` 面板，但粒度非常粗，只能看到：

- `turn_start`
- `agent_plan`
- `tool_call`
- `tool_result`
- `turn_end`
- `round_end`
- `error`

这会带来几个直接问题：

1. 执行过程不细。用户看不到“系统到底做了哪些真实动作”。
2. 中间聊天输出和右侧执行时间线不是同一条原子流，容易出现“模型说自己在修复”，但时间线看不到真正执行。
3. `code_workspace` 下的工作区扫描、上下文装配、模型请求、工具结果持久化等关键阶段都不可见。
4. 当前输出仍然大量依赖模型自然语言声明，不像 Codex / Hermes 这类工具那样由结构化事件驱动 UI。

用户要的目标很明确：

- 在主输出区也能看到具体执行过程，而不是只在右侧看概括
- 过程更细，最好能看到“做了什么、何时开始、何时结束”
- 输出和执行要一致，不能再靠模型自己声称“我正在修”

## 2. 目标

1. 把执行过程升级为“原子事件流”，而不是少量宽泛状态。
2. 让主聊天区和右侧执行时间线共享同一组结构化事件。
3. 优先展示系统真实发生的动作，而不是模型的自然语言自述。
4. 保持现有 SSE 方案，不引入新的 websocket 控制面。
5. 保持普通模式和 `code_workspace` 模式兼容。

## 3. 非目标

1. 首版不引入类似 Codex 那样完整的 `shell/edit` 内建运行时。
2. 首版不伪造命令执行事件。没有真实发生的命令，不显示成“已执行命令”。
3. 首版不重写整个前端消息系统，只在现有聊天流上扩展执行卡片。
4. 首版不改变 Provider / 会话 / 快照持久化模型。

## 4. 参考结论

本次方案参考了三类现成实现：

### 4.1 Codex / VS Code 扩展

Codex 的 app-server 使用结构化 `thread / turn / item` 模型来驱动富界面，进度不是靠最终答复文本推断出来的。VS Code 的 Agent Sessions 也明确显示运行中的进度和任务状态。

### 4.2 Hermes

Hermes 强调 `streaming tool output`，说明工具进度和最终回答是两类并行输出，不应全部折叠为普通 assistant 文本。

### 4.3 OpenClaw

OpenClaw 的 Gateway 更接近“结构化 session event 订阅”，不是单纯聊天字符串。它的方向证明“事件是 UI 的一等输入”。

## 5. 核心判断

当前项目缺的不是“多一个侧边栏”，而是“更细的真实事件模型”。

如果后端不产生更细的事件，前端再怎么美化，也只能继续显示：

- 模型声称的过程
- 非原子的概括性时间线

因此这次改造必须从后端事件模型开始。

## 6. 总体方案

采用 `混合展示方案`：

1. 后端增加更细的原子事件。
2. 前端右侧保留执行时间线。
3. 前端中间聊天区插入“执行卡片”，与最终模型答复同线程显示。

这意味着一次 turn 的所有信息都来自同一条 SSE 流：

- 真实执行事件
- 工具事件
- 最终模型文本流

而不是：

- 聊天区看模型说了什么
- 右侧面板看系统大概做了什么

## 7. 事件模型设计

### 7.1 新增事件

在现有 `turn_start / chunk / tool_call / tool_result / turn_end / round_end / error` 基础上，新增：

- `phase_start`
- `phase_end`
- `reasoning_note`
- `model_request`
- `model_response`
- `state_write`

### 7.2 各事件语义

#### `turn_start`

表示某个 participant 开始处理当前 turn。

附带：

- `participant_id`
- `round`
- `execution_mode`

#### `phase_start`

表示进入一个真实运行阶段。

首版建议使用这些 `phase`：

- `load_session`
- `scan_workspace`
- `resolve_targets`
- `build_prompt`
- `invoke_model`
- `parse_agent_output`
- `call_tool`
- `persist_message`
- `finalize_turn`

#### `phase_end`

表示该阶段结束，可附带：

- `phase`
- `status`
- `duration_ms`
- `summary`

#### `reasoning_note`

这不是 chain-of-thought，而是系统级解释说明，用来展示：

- 当前正在做什么
- 为什么当前没有可见文本输出
- 当前动作是系统行为还是工具行为

例如：

- `正在扫描工作区并构建文件索引`
- `正在等待模型返回第一段输出`
- `已将工具结果回灌给模型，准备继续生成`

#### `model_request`

表示已发起模型调用。

可附带：

- `provider_name`
- `model_ref`
- `input_message_count`
- `workspace_selected_path_count`

#### `model_response`

表示模型请求完成或首字节到达。

首版重点不是做完整 token 统计，而是表达：

- 请求已经返回
- 是首 chunk 到达还是整轮完成

#### `state_write`

表示系统写入了真实状态，例如：

- 保存消息
- 更新快照
- 持久化工具输出
- 标记 round 完成

### 7.3 不做的事件

首版不生成：

- `command_start`
- `command_stdout`
- `command_end`
- `file_write`

除非系统真的具备对应执行能力。当前项目还没有类似 Codex 的内建 shell/edit runtime，不能把模型口头描述伪装成命令执行。

## 8. 后端改造范围

### 8.1 普通模式

在 `dispatch_next / dispatch_round` 路径增加：

- `phase_start(load_session)`
- `phase_start(build_prompt)`
- `model_request`
- `model_response`
- `state_write(persist_message)`
- `phase_end(finalize_turn)`

### 8.2 `code_workspace`

在 `_dispatch_workspace_round` 路径增加：

- `phase_start(scan_workspace)`
- `phase_end(scan_workspace)`
- `phase_start(resolve_targets)`
- `phase_start(build_prompt)`
- `phase_start(invoke_model)`
- `phase_start(call_tool)` / `tool_call`
- `tool_result`
- `state_write`

### 8.3 Agent Loop

`WorkspaceAgentRunner` 目前只能输出：

- `agent_plan`
- `tool_call`
- `tool_result`
- `chunk`

需要补成更显式的阶段型输出：

- 解析模型 directive 前后
- 工具结果持久化前后
- 回灌 follow-up prompt 前后

## 9. 前端展示设计

### 9.1 主聊天区

在主聊天区新增 `execution` 类型消息卡片。

它和普通 `system` 消息不同：

- 有事件图标
- 有阶段标签
- 可折叠 detail
- 支持显示 JSON / 文本 detail
- 明确标记 `running / done / error`

这样用户在主输出区就能看到：

- `正在扫描工作区`
- `已选择 12 个路径构建上下文`
- `正在调用 deepseek/deepseek-chat`
- `filesystem.read_file 已返回结果`
- `已保存工具输出，准备继续生成`

### 9.2 右侧执行时间线

继续保留，但升级为：

- 按 turn 分组
- 显示 `phase`
- 显示时间顺序
- 可以看到 detail

右侧面板用于总览和回溯；主聊天区用于“边看边跟踪过程”。

### 9.3 一致性与原子性

聊天区和右侧面板都基于同一条 `executionEvents` 数据源，不允许：

- 一边插入 system message
- 一边单独再猜测一条 timeline summary

任何执行卡片都必须来源于真实 SSE 事件。

## 10. 数据结构调整

### 10.1 扩展 `StreamEventType`

增加：

- `phase_start`
- `phase_end`
- `reasoning_note`
- `model_request`
- `model_response`
- `state_write`

### 10.2 扩展 `ExecutionEventRecord`

新增字段：

- `phase?: string`
- `ts?: number`
- `kind?: "phase" | "model" | "tool" | "state" | "note"`
- `metadata?: Record<string, unknown>`

### 10.3 扩展 `ChatMessage`

增加：

- `type: "execution"`
- `executionEventId?: string`

## 11. 测试策略

### 后端

至少补这些测试：

1. 普通模式一轮中会发出新的阶段事件。
2. `code_workspace` 模式扫描和 prompt 构建阶段可见。
3. agent tool loop 中新增阶段事件与原有 tool 事件顺序一致。

### 前端

至少补这些测试：

1. 新事件会被归并成 execution timeline。
2. 新事件会在主聊天区生成执行卡片。
3. 执行卡片与右侧时间线共享同一条事件源。
4. 最终 assistant 文本和执行卡片顺序正确。

## 12. 风险

### 风险 1：事件过多，UI 太吵

缓解：

- 主聊天区只显示高价值事件
- 右侧时间线显示完整事件
- 部分 detail 默认折叠

### 风险 2：重复展示

缓解：

- 同一事件只生成一个 `ExecutionEventRecord`
- 聊天区和右侧时间线都从这个对象投影渲染

### 风险 3：继续混入模型自然语言自述

缓解：

- 只对真实后端事件生成 execution 卡片
- `agent_plan` 保留，但视觉上与真实系统阶段区分开

## 13. 实施顺序

1. 先扩展后端 SSE 事件模型。
2. 再扩展前端类型和 stream reducer。
3. 再把执行卡片插入主聊天区。
4. 最后升级右侧时间线和测试。

## 14. 预期结果

改完之后，用户在一次 `开始下一轮` 期间应该能看到：

1. 谁开始执行
2. 系统进入了哪些真实阶段
3. 是否调用了工具
4. 工具返回了什么
5. 什么时候进入模型流式输出
6. 什么时候状态被写回
7. 哪个阶段失败

这会让当前项目更接近 Codex / Hermes / OpenClaw 的“结构化执行可见性”，同时不假装具备当前仓库还没有的 shell/edit 原子运行时。
