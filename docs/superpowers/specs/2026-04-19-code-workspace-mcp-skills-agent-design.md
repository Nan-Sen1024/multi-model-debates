# 代码工作区能力层设计

日期：2026-04-19

## 1. 背景

现有 `code_workspace` 已经支持本地仓库路径、`@alias` 路由、共享工作区上下文和 SSE 流式输出。但当前工作区里的模型仍然只是“看上下文后回答”，还没有真正的 agent 能力层。

用户现在要的是三件事都能配置：

- `skills`：本地自写 skills 和公共 skills 都能加载。
- `MCP`：能按配置接入一个或多个 MCP server。
- `agent`：每个参与者都能进入 plan -> tool -> result -> next step 的执行循环。

目标不是另起一套系统，而是在现有 `code_workspace` 里把能力层补齐，并保持普通会话模式不变。

## 2. 目标

1. 为 `code_workspace` 增加可配置的能力栈：`skills`、`MCP`、`agent`。
2. 支持 workspace 级默认配置和 participant 级覆盖配置。
3. 支持本地 skills 和公共 skills 目录统一发现、加载和版本化。
4. 支持 MCP server 的声明式配置、工具白名单和运行时调用。
5. 支持 agent loop：模型先产出计划，再按需调用工具，最后继续生成直到完成或超步数。
6. 保持现有 SSE、会话历史、Provider、`@alias` 路由兼容。

## 3. 非目标

1. 首版不做跨仓库工作区。
2. 首版不做自动写文件的高危能力默认开启。
3. 首版不把普通 chat / debate / review 模式升级成 agent 模式。
4. 首版不要求所有模型都原生支持 tool calling。

## 4. 总体方案

`code_workspace` 继续作为入口模式，但它的 runtime 从“prompt 拼接”升级为“能力编排”。

每个 workspace session 会持有一份 manifest，描述：

- 哪些 skills 可用。
- 哪些 MCP server 可用。
- 哪些 participant 可以用哪些 skills / tools。
- 每个 participant 的 agent 行为约束。

调度时，orchestrator 会按 participant 构建一个 `WorkspaceExecutionContext`：

1. 读取仓库上下文、历史输出、`@alias` 目标。
2. 解析该 participant 可用的 skills。
3. 解析该 participant 可用的 MCP tools。
4. 进入 agent loop。
5. 把模型输出转成 SSE 事件流。

## 5. 配置模型

### 5.1 Workspace Manifest

建议把能力配置继续放在现有 session config 里，新增一个嵌套 manifest，而不是拆成独立表。

```python
@dataclass
class WorkspaceCapabilityManifest:
    skill_sources: List[SkillSourceConfig]
    mcp_servers: List[MCPServerConfig]
    agent_defaults: AgentProfileConfig
    participant_overrides: Dict[str, ParticipantCapabilityConfig]
```

### 5.2 Skills

`skills` 以 `SKILL.md` 为核心，统一遵循声明式目录约定。

支持两类来源：

- 本地 skills：workspace 根目录下或项目约定目录下的 skills。
- 公共 skills：全局 skills 目录或可配置的公共仓库目录。

每个 skill 至少包含：

- `name`
- `description`
- 可选 `dependencies`
- 可选 `tools`
- 可选 `permissions`

skills 只作为“上下文 + 行为约束 + 任务模板”输入，默认不执行任意脚本。

### 5.3 MCP

MCP 以声明式 server 配置为主。

支持：

- `stdio` 方式
- `http/sse` 方式
- 工具白名单
- 每个 participant 的 server 白名单

建议的最小配置字段：

```python
@dataclass
class MCPServerConfig:
    name: str
    transport: str
    command: Optional[str] = None
    args: List[str] = field(default_factory=list)
    url: Optional[str] = None
    env: Dict[str, str] = field(default_factory=dict)
    tools_allowlist: List[str] = field(default_factory=list)
```

### 5.4 Agent Profile

agent 作为每个参与者的执行策略：

- `disabled`
- `plan_only`
- `tool_loop`
- `full_agent`

最小配置字段：

```python
@dataclass
class AgentProfileConfig:
    mode: str
    max_steps: int = 6
    can_write: bool = False
    allowed_skills: List[str] = field(default_factory=list)
    allowed_mcp_servers: List[str] = field(default_factory=list)
    memory_scope: str = "workspace_shared"
```

participant override 优先于 workspace 默认值。

## 6. Runtime 设计

### 6.1 Skill Registry

新增 skill registry，负责：

- 扫描本地和公共 skills 目录。
- 读取 `SKILL.md`。
- 解析 frontmatter 和正文。
- 生成可供模型引用的 skill 卡片。
- 按 participant / workspace 配置做过滤。

skill registry 不负责执行代码，只负责“发现”和“装配上下文”。

### 6.2 MCP Registry

新增 MCP registry，负责：

- 按 manifest 创建 / 管理 server session。
- 暴露 tool descriptors 给 agent runtime。
- 校验 tool allowlist。
- 收集 tool result，并转成统一可读文本。

### 6.3 Agent Loop

agent loop 放在 `code_workspace` 的调度分支里，基本流程：

1. 组装任务、仓库上下文、skills、tools、历史 transcript。
2. 让模型输出一个计划或工具请求。
3. 如果需要工具，执行 MCP tool call。
4. 把 tool result 回灌给模型。
5. 重复直到完成、达到 `max_steps`，或遇到错误。

兼容策略：

- 优先用模型原生 tool calling。
- 没有原生能力时，使用统一的结构化 action 协议。
- 对模型隐藏 chain-of-thought，只展示简短计划、工具调用和结果摘要。

### 6.4 SSE 事件

新增或明确这些可见事件：

- `agent_plan`
- `tool_call`
- `tool_result`
- `chunk`
- `turn_end`
- `round_end`
- `error`

前端仍然按 chunk 流式追加，不等待整轮结束。

## 7. 权限与安全

1. MCP tools 默认只读，写操作显式允许。
2. Skills 默认只读、不可执行脚本。
3. 每个 participant 的 tools / skills / servers 必须有 allowlist。
4. workspace 只能访问配置的本地仓库路径。
5. agent loop 有硬超步数限制，避免无限调用。

## 8. 前端

`code_workspace` 配置页增加三块：

- skills 选择
- MCP server 选择
- agent profile 选择

任务编辑区保留 `@alias`，但参与者卡片要能直接看到当前启用的 skills / tools / agent mode。

## 9. 测试策略

后端至少补：

- skills registry 能发现本地和公共 skills。
- MCP 配置能被持久化和恢复。
- participant override 覆盖 workspace 默认值。
- agent loop 在工具返回后继续生成。
- agent loop 达到 `max_steps` 后安全退出。

前端至少补：

- workspace 配置能展示 skills / MCP / agent 三类配置项。
- 参与者卡片能显示当前能力状态。
- SSE 新事件不会打断流式输出。

## 10. 实施顺序

1. 先把 manifest / 数据模型落地。
2. 再接 skills registry。
3. 再接 MCP registry。
4. 再接 agent loop。
5. 最后补前端配置 UI 和回归测试。

## 11. 约束

- 不重写现有 session / SSE / provider 体系。
- 不要求普通模式进入 agent loop。
- 不硬编码固定 skills / MCP / agent 名单，全部走配置。
- 先保证可用，再逐步增强自动化能力。
