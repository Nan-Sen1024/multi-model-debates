# multi-model-debates 研发工作台重定位设计
Date: 2026-05-08

## 1. 背景

`multi-model-debates` 已经具备以下可工作的核心能力：

- 多 Provider 管理与认证
- 多模型会话与 SSE 流式输出
- 本地代码工作区注入
- `write_file / run_command` 本地工具执行
- Skills / MCP / Agent Loop
- SQLite 本地持久化
- 前端控制台与终端入口

从系统能力看，这个项目已经不再是“多模型聊天 Demo”。但从产品表达和页面结构看，它仍然更接近：

- 一个以会话和模式为中心的聊天控制台
- 一个把底层能力直接暴露给用户的技术面板
- 一个把娱乐性模式、代码模式、Provider 配置混排在同一层级的实验场

这导致两个直接问题：

1. 用户第一次进入产品时，不知道主任务是什么。
2. 最强能力没有形成清晰主路径，仍然被包装在“创建会话 + 开始下一轮”的聊天语义里。

## 2. 核心判断

下一阶段不应继续把这个项目定义为“多模型辩论平台”或“支持很多模式的聊天系统”。

更合理的产品定义是：

`面向个人开发者和小团队的本地优先多模型研发工作台`

它的核心价值应聚焦于围绕真实代码仓库完成以下高频任务：

- 仓库理解
- 多模型评审
- 定向修复与验证
- Provider / Model 路由与效果对比

这个判断意味着：

- `code_workspace` 相关能力应成为主叙事中心
- 多模式聊天能力应下沉为模板或实验层
- 前端和 API 都应从“会话驱动”转向“任务驱动”

## 3. 目标

1. 把产品定位从“多模式聊天/辩论”收敛为“多模型研发工作台”。
2. 用 `Workspace / Task / Run / Review / Provider` 重建产品对象模型。
3. 把前端主路径从 `Provider -> Create Session -> Start Next Round` 改为 `Workspace -> Task -> Run -> Result`。
4. 保留现有 orchestrator、workspace agent、tool runtime 等核心执行内核，优先做产品语义重构而非重写执行骨架。
5. 为下一阶段实现评审、结构化结果、路由策略、交付闭环建立稳定 API 契约。

## 4. 非目标

1. 本设计不要求第一阶段重写现有 orchestrator。
2. 本设计不要求第一阶段删除现有 `session` / `participant` / `round` 存储结构。
3. 本设计不要求第一阶段移除娱乐型模式，但这些模式将下沉为次级层或实验层。
4. 本设计不要求第一阶段实现完整 IDE、完整 Git 托管工作流或完整向量检索系统。

## 5. 用户与核心任务

### 5.1 目标用户

优先服务两类用户：

1. 独立开发者
   - 在本地仓库里分析、修复、验证
   - 希望快速切换模型或比较模型表现
2. 小团队技术负责人 / 高级工程师
   - 用多模型做代码评审、方案对照、修复分流
   - 需要可解释的过程和可验证的结果

### 5.2 核心 Jobs To Be Done

1. “给我一个仓库，让我快速理解它怎么组织、哪里重要、哪里有风险。”
2. “给我一个任务，让模型去修，并且把改动和验证结果交出来。”
3. “给我一段改动/一个 diff，让多个模型做结构化评审。”
4. “给我一个任务，让不同模型或不同策略跑一遍，比较结果。”

## 6. 产品定位与能力边界

产品应收敛为四个主能力面：

- `Workspace`
  - 管理代码仓库、上下文范围、权限边界、规则和工具来源
- `Runs`
  - 管理任务执行、过程可视化、产物和验证结果
- `Review`
  - 管理结构化代码评审与结论输出
- `Providers`
  - 管理模型接入、认证、路由和 fallback

以下内容不再作为一级导航或一级心智入口：

- `chat`
- `brainstorm`
- `debate`
- `werewolf`
- `murder_mystery`
- `story_chain`
- 其他娱乐或泛聊天模式

这些能力如需保留，应以两种形式存在：

1. 作为 `Task Template`
2. 作为 `Labs / Experimental`

## 7. 信息架构

当前信息架构按系统部件组织，下一阶段应改为按用户任务组织。

建议一级导航为：

1. `Home`
2. `Workspace`
3. `Runs`
4. `Review`
5. `Providers`

### 7.1 Home

作用：

- 承接首次进入
- 展示最近工作区、最近 runs、最近 review、provider 状态
- 用三个主入口回答“你现在要做什么”

主入口：

- `Analyze Repo`
- `Run Task`
- `Review Changes`

### 7.2 Workspace

作用：

- 选择仓库
- 管理目录树和上下文范围
- 配置规则/skills/MCP
- 设定权限等级

### 7.3 Runs

作用：

- 成为产品主工作页
- 展示 Task 的执行过程和结果

### 7.4 Review

作用：

- 独立承载多模型评审
- 接受仓库、文件、diff、commit range 等输入
- 输出结构化 findings

### 7.5 Providers

作用：

- 管理模型基础设施层
- 明确与任务流解耦

## 8. 核心对象模型

下一阶段应将核心产品对象定义为：

1. `Workspace`
2. `Task`
3. `Run`
4. `Review`
5. `Provider`

### 8.1 Workspace

表示一个代码仓库或工作目录，负责长期存在的上下文与权限配置。

### 8.2 Task

表示一次明确用户目标，例如：

- 分析仓库结构
- 评审某段改动
- 修复某个 bug
- 比较不同模型执行效果

### 8.3 Run

表示 Task 的一次执行。  
Run 是产品最关键的结果对象。

它必须同时承载：

- 执行状态
- 执行过程
- 执行产物
- 验证结果
- 可继续动作

### 8.4 Review

表示一次结构化评审结果，而不是普通 transcript。

### 8.5 Provider

表示模型接入与路由基础设施，不属于主任务流对象。

## 9. 主流程重构

建议统一主流程为四步：

1. `Select Workspace`
2. `Create Task`
3. `Run Execution`
4. `Review Result`

### 9.1 Select Workspace

用户选择仓库、上下文范围和权限等级。

### 9.2 Create Task

用户输入任务目标，并从少量模板中选择任务类型：

- `Analyze`
- `Review`
- `Fix`
- `Compare`

### 9.3 Run Execution

系统创建并推进一个 run。  
前端主对象应是 run，而不是聊天 transcript。

### 9.4 Review Result

系统输出结构化结果，用户可继续执行、换模型重跑、分叉尝试或导出结果。

## 10. 页面与交互重构

### 10.1 Home

关键内容：

- 最近工作区
- 最近任务
- 最近 runs
- Provider 健康概览

### 10.2 Workspace

关键内容：

- 仓库选择
- 文件树
- 文件内容查看
- 上下文范围
- 规则 / skills / MCP
- 权限等级

### 10.3 New Task

替代“创建会话”。

关键内容：

- 任务目标输入框
- 模板选择
- 执行模型或路由策略
- 权限确认
- 高级参数折叠面板

### 10.4 Run Detail

建议三栏布局：

- 左侧：Task / Run 列表
- 中间：执行时间线与实时输出
- 右侧：任务侧栏

右侧任务侧栏优先显示：

- 任务目标
- 当前状态
- 影响文件
- 验证结果
- 阻塞项
- 下一步动作

### 10.5 Review

应独立成页，支持：

- 输入源选择
- 评审维度选择
- 模型选择
- findings 列表
- 汇总结论
- 一键转修复任务

## 11. 关键功能优先级

### 11.1 P0

1. 任务驱动入口替代会话驱动入口
2. Run 结果对象化
3. Workspace 权限分级
4. 执行目标可见化
5. 右侧面板改为任务侧栏

### 11.2 P1

1. Review 独立化
2. Provider 路由策略产品化
3. 执行产物视图
4. 可重跑与分叉
5. 任务模板预设

### 11.3 P2

1. Repo Map / Symbol Graph
2. 规则系统产品化
3. Benchmark / Compare 中心
4. 交付闭环（patch / branch / PR-ready summary）

## 12. 后端演进方案

后端应采用：

`语义重构，执行内核复用`

### 12.1 第一层：API 语义重构

对前端暴露 `Workspace / Task / Run / Review` 新对象，但内部短期继续复用现有 `session` 和 `dispatch`。

### 12.2 第二层：持久化对象拆分

在现有数据库之上逐步增加：

- `workspaces`
- `tasks`
- `task_runs`
- `run_artifacts`

### 12.3 第三层：执行结果结构化

保留 SSE 事件流，但新增 run 结果聚合层，负责沉淀：

- `status`
- `summary`
- `files_changed`
- `commands_run`
- `verification_result`
- `errors`
- `blockers`
- `next_actions`

## 13. 兼容策略

### 13.1 API 兼容

- 继续保留现有 session API
- 新前端逐步切到 task/run API

### 13.2 SSE 兼容

保留现有事件：

- `phase_start`
- `chunk`
- `tool_call`
- `tool_result`
- `turn_end`

新增高层事件：

- `run_status`
- `artifact_update`
- `verification_update`
- `run_result`

### 13.3 模式兼容

- 现有 mode enum 暂不删除
- 前端先降级显示
- 后端继续保留策略注册

## 14. 数据模型建议

### 14.1 Workspace

建议字段：

- `id`
- `root_path`
- `display_name`
- `repo_fingerprint`
- `context_scope`
- `permissions`
- `rules`
- `skills`
- `mcp_servers`
- `created_at`
- `updated_at`

### 14.2 Task

建议字段：

- `id`
- `workspace_id`
- `title`
- `goal`
- `template`
- `input_source`
- `execution_strategy`
- `status`
- `latest_run_id`
- `created_at`
- `updated_at`

建议模板枚举：

- `analyze`
- `review`
- `fix`
- `compare`

### 14.3 Run

建议字段：

- `id`
- `task_id`
- `status`
- `started_at`
- `finished_at`
- `summary`
- `active_model_refs`
- `timeline`
- `artifacts`
- `verification`
- `blockers`

### 14.4 Review

建议字段：

- `id`
- `workspace_id`
- `source`
- `review_axes`
- `models`
- `summary`
- `findings`
- `decision`
- `created_at`

### 14.5 Provider

建议在现有配置之上补充产品层输出字段：

- `auth_status`
- `health_status`
- `default_model_ref`
- `routing_role`
- `last_checked_at`

## 15. API 契约建议

第一批建议资源：

- `POST /api/workspaces`
- `GET /api/workspaces`
- `GET /api/workspaces/:id`
- `POST /api/tasks`
- `GET /api/tasks`
- `GET /api/tasks/:id`
- `POST /api/tasks/:id/runs`
- `GET /api/runs/:id`
- `GET /api/runs/:id/stream`
- `POST /api/reviews`
- `GET /api/reviews/:id`
- `GET /api/providers`

### 15.1 过程与结果分离

- `GET /api/runs/:id/stream`：过程事件
- `GET /api/runs/:id`：结果对象

### 15.2 错误契约

错误结构建议统一为：

```json
{
  "error": {
    "code": "WORKSPACE_PERMISSION_DENIED",
    "message": "当前任务权限不允许执行本地命令。",
    "field": "permissions.mode",
    "retryable": false
  }
}
```

## 16. 成功指标

本阶段不以“支持更多模式”作为成功标准，而以主路径完成度作为成功标准。

### 16.1 产品指标

1. 新用户首次完成一个 `Analyze` 或 `Fix` 任务的成功率
2. 从进入产品到首次发起任务的时间
3. 单次任务完成后的结构化结果查看率
4. Review 结果转修复任务的转化率

### 16.2 运行指标

1. Run 成功完成率
2. 有验证结果的 Run 占比
3. 有结构化产物的 Run 占比
4. Provider fallback 生效率

### 16.3 体验指标

1. 用户是否能在不理解 `@alias` / `round` / `participant` 内部概念的前提下完成任务
2. 用户是否能在 1 屏内看清当前状态、改动范围和验证结果

## 17. 版本边界

### 17.1 V1

目标：

- 完成产品定位切换
- 完成 P0
- 前端主路径切到 `Workspace -> Task -> Run -> Result`

### 17.2 V1.5

目标：

- 完成 P1
- 让 Review、路由策略、产物视图和重跑分叉变得稳定可用

### 17.3 V2

目标：

- 完成 P2
- 建立 repo-level 理解、规则系统和 benchmark 差异化能力

## 18. 不做事项

当前阶段明确不做：

1. 不以“增加更多模式”作为主线
2. 不在第一阶段重写 orchestrator
3. 不在第一阶段大规模重构数据库后再做前端
4. 不把前端继续做成 transcript 优先的聊天页
5. 不把权限模型继续保留为单一 `can_write` 布尔开关

## 19. 风险与缓解

### 19.1 风险：前端重命名但后端语义未真正切换

缓解：

- 明确引入 `Task / Run` API
- 让前端真的围绕 run 结果对象渲染

### 19.2 风险：结构化结果聚合不稳定

缓解：

- 保留 SSE 明细流
- 单独实现 run result 聚合器
- 优先从现有事件中抽取最低必要字段

### 19.3 风险：权限设计不清导致用户不敢使用写模式

缓解：

- 引入四级权限模型
- 在 UI 中显式展示能力边界和后果

### 19.4 风险：数据库改造过早导致返工

缓解：

- 先做 API 语义层
- 后做持久化拆分
- 保持一段时间的兼容层

## 20. 与现有代码的映射

### 20.1 可以继续复用的核心模块

- `backend/orchestrator.py`
- `backend/workspace_agent.py`
- `backend/workspace_executor.py`
- `backend/workspace_context.py`
- `backend/workspace_router.py`
- `backend/llm_gateway.py`
- `backend/auth_flow.py`

### 20.2 需要产品语义升级的层

- `frontend/src/App.tsx`
- `frontend/src/WorkspaceMode.tsx`
- `frontend/src/api.ts`
- `backend/api.py`
- `backend/database.py`

### 20.3 可以逐步下沉的层

- `frontend/src/modeOptions.ts`
- `backend/strategies.py` 中的非研发工作流模式

## 21. 外部参考

本设计方向与当前主流研发工作台/agent 产品形态一致，参考重点如下：

- MCP 架构强调 `tools + resources + prompts`
- VS Code Agents 强调任务、会话、权限和 MCP 扩展
- GitHub Copilot cloud agent 强调隔离执行环境、自动测试和交付闭环
- Continue 强调项目级 rules
- Aider 强调 repo map 对大仓库理解的重要性
- OpenRouter 强调模型路由与 fallback 的产品化表达

这些参考不要求照搬交互，但说明：

- 行业成熟方向已经从“聊天页”转向“任务与执行结果”
- 这个项目当前最有价值的进化路径是研发工作台，而不是继续扩展泛聊天模式

## 22. 结论

`multi-model-debates` 的下一阶段不应被继续建设成“支持很多模式的多模型聊天系统”，而应被重构为：

`一个围绕真实代码任务运行的本地优先多模型研发工作台`

短期最优策略不是重写执行引擎，而是：

1. 收敛定位
2. 重构信息架构
3. 引入 `Workspace / Task / Run / Review / Provider` 产品对象
4. 让 Run 结果结构化
5. 在兼容现有后端内核的前提下逐步演进 API 和数据库

如果按此路线执行，现有代码资产可以被高比例复用，且产品心智会明显提升。
