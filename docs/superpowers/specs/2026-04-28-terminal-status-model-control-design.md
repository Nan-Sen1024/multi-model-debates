# multi-model-debates 终端状态与模型控制设计
Date: 2026-04-28

## 1. 目标

把当前 `mmd` 终端从“能发消息”升级成“能看状态、能选模型、能切目标”的控制台，体验尽量靠近 Codex / Hermes / OpenClaw：

- 一眼看到当前会话状态
- 一眼看到当前使用的模型和 provider 健康情况
- 一眼看到当前焦点参与者
- 在终端里切换默认模型，而不是手工改嵌套 JSON
- 保留 `@alias + /command` 的输入风格

这个设计不改变现有多参与者协作架构，仍然保留：

- `@alias` 用于路由目标
- `provider/model` 作为 canonical model ref
- `mmd new` / `/add` 继续支持显式模型

## 2. 核心判断

### 2.1 先做“状态面板”，再做“模型控制”

参考 OpenClaw 的经验，`status` 应该是一个轻量、可退出、可诊断的命令，而不是启动一整套运行时。

因此第一层只做读取：

- `mmd status`
- shell 内 `/status`
- `mmd providers status`
- `mmd models status`

### 2.2 模型选择要分成两种语义

1. **会话默认模型**
   - 影响后续新增参与者的默认值
   - 影响 shell 中不写模型时的默认绑定
   - 适合 `Codex/Hermes` 风格的“当前模型”概念

2. **参与者模型**
   - `@coder`、`@reviewer` 这种 session-local alias 仍然对应具体参与者
   - 参与者仍然可继续在创建时显式指定 `provider/model`
   - 后续如果要改已有参与者模型，再单独加一个 participant update 接口

这两个概念不要混成一个命令，否则状态会变得很难理解。

## 3. 命令树

### 3.1 顶层命令

- `mmd status`
  - 输出当前 session、焦点、默认模型、provider 健康、workspace 摘要
- `mmd providers`
  - 现有行为保留
- `mmd providers status`
  - 输出 provider 的 auth 状态、健康状态、fallback 信息
- `mmd models`
  - 输出模型目录
- `mmd models status`
  - 输出当前 session 的默认模型、已绑定参与者模型、是否存在歧义
- `mmd models list`
  - 列出发现到的模型
- `mmd models set <provider/model>`
  - 设置当前 session 的默认模型
- `mmd attach <session_id>`
  - 进入已有 session
- `mmd new`
  - 创建新 session

### 3.2 会话内命令

- `/status`
  - 等价于 `mmd status`
- `/model`
  - 显示当前默认模型
- `/model status`
  - 显示当前 session 的模型状态
- `/model set <provider/model>`
  - 设置当前 session 默认模型
- `/model clear`
  - 清除 session 默认模型，回到“必须显式指定”
- `/add <alias> [model] [role description]`
  - `model` 可选；如果省略则使用 session 默认模型
- `/remove <alias>`
- `/rename <old> <new>`
- `/clone [topic]`
- `/to <alias...>`
  - 设置当前焦点参与者
- `@alias`
  - 单独一行时切换焦点
- `@all`
  - 单独一行时切换到广播焦点

## 4. 状态面板规范

### 4.1 `status` 必须显示的字段

1. Session
   - session id
   - topic/title
   - mode
   - status
   - current round
2. Focus
   - 当前焦点 alias 列表
   - 是否广播 `@all`
3. Model
   - session default model
   - 当前参与者与各自的 `provider/model`
   - 裸模型是否存在歧义
4. Provider
   - provider id/name/type
   - `auth_status`
   - `health`
   - `fallback_ids`
5. Workspace
   - root path
   - `index_status`
   - `can_write`
   - agent 默认策略
   - skills / mcp 概览

### 4.2 展示风格

终端中的状态应是紧凑块状输出，而不是长 JSON：

```text
Session: Demo [code_workspace] status=active round=34
Focus: @coder
Default model: openai/gpt-5.4
Participants:
  @coder -> openai/gpt-5.4
  @reviewer -> anthropic/claude-sonnet
Providers:
  openai-prod  auth=ready  health=ok
  anthropic    auth=ready  health=degraded
Workspace:
  root=...
  index=ready
  can_write=true
```

这和 Codex/Hermes 的状态栏目标一致，但保留了 multi-model-debates 的多参与者信息。

## 5. 模型选择语义

### 5.1 Canonical 规则

- `provider/model` 仍然是后端 canonical 格式
- 裸模型名只在唯一匹配 provider 时自动绑定
- 如果多个 provider 命中同一模型，必须让用户显式选择
- 如果没有命中，必须报错，不要猜

### 5.2 `model set` 的语义

`/model set <provider/model>` 或 `mmd models set <provider/model>` 的作用是：

- 记录当前 session 的默认模型
- 影响后续 `/add <alias>` 的默认值
- 影响 `mmd new` 的 bootstrap 选择
- 让 `status` 能看到一个“当前默认模型”

### 5.3 默认模型和参与者模型的关系

- 默认模型不是参与者模型
- 参与者模型仍然是 session 里每个 alias 自己的 `provider/model`
- 如果要改已有参与者模型，建议后续再做一个显式的 participant update 接口
- v1 不做“悄悄替换已有参与者模型”，避免误操作

## 6. 后端最小扩展

### 6.1 必须有的最小扩展

为了让 `model set` 可持久化，建议给 session config 增加一个字段：

- `default_model_ref: Optional[str]`

并增加一个 session config 更新接口，例如：

- `PATCH /api/sessions/{session_id}/config`

请求体示意：

```json
{
  "default_model_ref": "openai/gpt-5.4"
}
```

这个接口只负责 session-level config，不碰 participant 列表。

### 6.2 可选的后续扩展

如果未来要支持“给已有参与者换模型”，再加：

- `PATCH /api/sessions/{session_id}/participants/{custom_id}/model`

但这一项不应阻塞 v1。

## 7. CLI 行为

### 7.1 进入 shell 时

- prompt 中显示当前焦点
- prompt 中可选显示默认模型
- 如果 session 没有默认模型，prompt 只显示焦点

示意：

```text
mmd:Demo [@coder | openai/gpt-5.4]>
```

### 7.2 `/status` / `mmd status`

- 不启动模型调用
- 不进入生成流程
- 只做读取和聚合
- 必须立即返回，不能挂住

### 7.3 `/model`

- 无参数时显示当前默认模型
- `set` 时更新默认模型
- `clear` 时清除默认模型

### 7.4 `@alias` 与 `/to`

- `@alias` 单独一行时，相当于设置焦点
- `/to reviewer coder` 可以直接设置多目标焦点
- 焦点只影响消息路由，不改变模型配置

## 8. 参考依据

这个方案参考了公开可见的终端模式：

- Codex：status bar + 命令驱动的模型控制
- Hermes：`/model` 只切换已配置的 provider/model
- OpenClaw：`status` 是 compact diagnostics，`model` 和 `status` 分离

对应公开资料：

- OpenAI Codex CLI: https://developers.openai.com/codex/cli
- Hermes CLI commands: https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/cli-commands.md
- OpenClaw gateway status: https://github.com/openclaw/openclaw/blob/main/docs/cli/gateway.md
- OpenClaw session status / model surfaces: https://github.com/openclaw/openclaw/blob/main/docs/reference/session-management-compaction.md

## 9. Rollout

### Phase 1

- 增加 `mmd status`
- 增加 `/status`
- 增加 `mmd models status/list`
- prompt 显示 focus 与默认模型

### Phase 2

- 增加 session-level `default_model_ref`
- 增加 `mmd models set`
- 让 `/add <alias>` 可以复用默认模型

### Phase 3

- 如有必要，再增加 participant-level model update
- 再考虑把状态面板渲染得更像 Codex/Hermes 的 persistent status bar

## 10. 非目标

- 不把 shell 变成完整 IDE
- 不在 v1 做“已有参与者原地换模型”
- 不用 raw JSON 作为终端主交互
- 不让 status 命令启动长时间运行任务

