# 代码工作区 Mode 设计

日期：2026-04-19

## 1. 背景

当前项目已有多模型会话、SSE 流式输出、Provider 管理和会话历史能力，但“代码协作”仍停留在通用对话层，缺少面向本地仓库的开发工作区能力。

用户希望的是一个更接近 IDE 的模式：

- 只针对一个本地仓库路径。
- 多个模型可以同时看到同一个代码库和彼此的输出。
- 用户可以直接在输入框里 `@alias` 指定某个模型执行任务。
- `claude`、`codex`、`deepseek`、`kimi` 这类角色是用户自由定义的，不是硬编码角色。
- 模型输出必须持续流式到前端，而不是整轮结束后一次性展示。

## 2. 目标

1. 在现有会话系统里新增一个 `code_workspace` mode。
2. 支持用户输入本地仓库路径，后端自动识别仓库结构并生成工作区上下文。
3. 支持参与者别名 `alias`，并用 `@alias` 直接路由任务。
4. 同一工作区内的所有模型都能看到：
   - 仓库摘要
   - 选中文件/目录
   - 所有模型的历史输出
   - 当前任务和最近执行结果
5. 保持现有会话、SSE、Provider、历史记录体系，避免重写 orchestrator。

## 3. 非目标

1. 首版不做跨仓库工作区。
2. 首版不做自动写文件或自动应用 patch。
3. 首版不做完整向量索引或外部检索服务。
4. 首版不替换现有普通聊天、辩论、评审等模式。

## 4. 用户体验

### 4.1 创建会话

当用户选择 `code_workspace` mode 时，创建会话页展示一套开发向表单：

- 本地仓库路径
- 仓库名称/显示名
- 参与者列表
- 每个参与者的别名 `alias`
- 参与者的 provider/model 绑定
- 参与者角色描述
- 可选的初始文件选择

其中 `participant.custom_id` 继续作为参与者的用户可见别名，前端在这个 mode 下把它展示成 `alias`。

### 4.2 工作区页面

`code_workspace` 使用单独的布局，不和普通 mode 共用同一个聊天视觉层：

- 左侧：仓库树、文件搜索、选中文件。
- 中间：任务流、模型输出、用户输入框。
- 右侧：工作区摘要、参与者别名、当前任务、上下文裁剪状态。

### 4.3 `@` 提及

输入框支持：

- `@codex 请实现这个函数`
- `@claude @deepseek 帮我看一下这个方案`

规则：

- `@alias` 匹配工作区中的参与者别名。
- 一个消息可以命中一个或多个参与者。
- `@all` 可作为广播目标。
- 未命中别名时，消息仍进入共享 transcript，但不会路由到错误的参与者。

## 5. 数据模型

### 5.1 工作区配置

建议把工作区配置作为现有 session config 的一部分持久化，而不是拆成独立系统。

新增一个嵌套结构：

```python
@dataclass
class WorkspaceConfig:
    root_path: str
    display_name: Optional[str] = None
    repo_fingerprint: Optional[str] = None
    scan_excludes: List[str] = field(default_factory=list)
    selected_paths: List[str] = field(default_factory=list)
    index_status: str = "pending"
    last_scanned_at: Optional[int] = None
    summary: Optional[str] = None
```

### 5.2 参与者别名

不新增强制字段，继续使用 `ParticipantConfig.custom_id` 作为用户可见 alias。

这可以最小化改动，也能让 `@alias` 和历史会话兼容。

### 5.3 工作区状态

工作区状态建议继续存于现有 session 持久化层：

- `root_path`
- `selected_paths`
- `repo_fingerprint`
- `index_status`
- `summary`
- 最近一次扫描结果的时间戳

第一版不必持久化完整索引树，只保留可重建状态和必要摘要。

## 6. 后端设计

### 6.1 新增职责

建议新增以下模块：

- `backend/workspace_scanner.py`
  - 扫描本地仓库目录
  - 生成目录树、文件计数、排除规则
  - 判断是否是有效仓库

- `backend/workspace_context.py`
  - 构建模型 prompt 需要的工作区上下文
  - 组合仓库摘要、选中文件、历史输出、任务指令

- `backend/workspace_router.py`
  - 解析 `@alias`
  - 决定本轮要调用的参与者集合

### 6.2 Orchestrator 集成

`backend/orchestrator.py` 保持主调度逻辑，但 `code_workspace` 需要一条专门分支：

- 读取工作区配置。
- 拉取仓库摘要和选中文件。
- 解析用户消息中的 `@alias`。
- 选择目标参与者。
- 为每个参与者构建共享工作区上下文。
- 按 chunk 方式流式返回结果。

### 6.3 上下文构建策略

每次模型调用都应包含以下内容：

1. 当前任务说明。
2. 仓库摘要。
3. 选中文件内容或相关代码片段。
4. 所有参与者的历史输出摘要。
5. 最近的测试/审查结果。
6. 本次目标参与者的别名和角色描述。

上下文裁剪原则：

- 先保留任务说明和仓库摘要。
- 再保留选中文件。
- 最后按相关性拼接最近输出和片段。
- 超出 token 预算时，用现有压缩机制保留摘要和引用，不丢任务结论。

### 6.4 流式输出

`code_workspace` 不改 SSE 入口形态，但必须确保：

- 模型 chunk 到一段就发一段。
- 前端收到 chunk 立即追加到当前消息气泡。
- `turn_end` 代表某个参与者输出结束。
- `round_end` 代表本轮工作流结束。
- `ping` 用于维持连接和前端保活。

## 7. 前端设计

### 7.1 模式入口

`frontend/src/modeOptions.ts` 新增 `code_workspace`，文案建议为 `代码工作区`。

### 7.2 工作区组件

建议拆出独立组件：

- `WorkspaceHeader`
- `WorkspaceTreePanel`
- `WorkspaceTranscriptPanel`
- `WorkspaceContextPanel`
- `WorkspaceComposer`

### 7.3 交互细节

- 仓库路径输入后立即做一次预览/扫描。
- 文件树支持展开、搜索、勾选。
- 参与者卡片显示 `alias`、provider、模型、角色。
- 输入框支持 `@` 补全。
- 输出区必须显示实时增量内容，不等待整轮结束。

## 8. 测试策略

### 8.1 后端测试

至少补这些回归：

- 代码工作区 session 能正确保存和恢复 `WorkspaceConfig`。
- 仓库扫描会排除常见噪音目录。
- `@alias` 能正确路由到指定参与者。
- 同一工作区中的所有参与者都能读取共享 transcript 摘要。
- SSE chunk 可以持续流式送达，不会等到结束才返回。

### 8.2 前端测试

- `code_workspace` mode 会展示工作区布局。
- `@alias` 输入能够选中正确参与者。
- 模型输出会随着 chunk 追加到 UI，而不是一次性渲染。

## 9. 实施顺序

1. 扩展 `CollaborationMode` 和前端 mode 列表。
2. 增加 `WorkspaceConfig` 和会话持久化支持。
3. 实现仓库扫描与上下文构建。
4. 实现 `@alias` 路由和多参与者任务分发。
5. 落地工作区 UI。
6. 补后端和前端测试。

## 10. 约束

- 首版只支持单本地仓库路径。
- 首版默认只读，不自动修改代码。
- 首版保留现有普通会话模式不变。
- 首版优先保证可用性和上下文正确性，不追求自动化 agent 化。
