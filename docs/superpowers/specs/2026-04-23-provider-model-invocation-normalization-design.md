# Provider Model Invocation Normalization Design
日期：2026-04-23

## 1. 背景

`multi-model-debates` 当前已经具备一条可工作的 provider/model 主链：

- `ProviderConfig` 负责持久化 provider 实例配置，包括 `provider_type`、`api_format`、`auth_type`、`base_url`、`fallback_ids`
- `LLMGatewayClient.discover_provider_models()` 负责动态发现各 provider 的可用模型
- 前端通过 `/api/model-catalog/discover` 获取模型目录，并据此渲染下拉
- `SessionOrchestrator` 在调度时把 `model_ref` 与 `provider_id` 一起传入网关

这条链路已经能支持：

- 动态 provider 配置
- 动态模型同步
- OAuth / API Key / Helper / IAM 等不同认证方式
- ChatGPT OAuth runtime、OpenAI-compatible、Anthropic messages 等不同调用路径

但当前实现存在一个结构性问题：**`model_ref` 的语义在内部并不稳定。**

典型表现：

1. 有时要求 `model_ref` 必须是 `provider/model`
2. 有时在给定 `provider_config` 时，又允许 `model_ref` 只是裸模型名
3. 调用分流逻辑分散在 `llm_gateway.py` 多个分支中，新增 provider/runtime 时容易继续堆特判

这会导致 provider/model 接入虽然“能跑”，但长期演进成本较高。

## 2. 目标

1. 在**不修改现有外部 API 和前端交互**的前提下，统一后端内部的 provider/model 调用表示。
2. 明确“谁决定调用路径”：由 **provider 实例配置** 决定运行时，`model_ref` 只提供模型意图。
3. 让 `LLMGatewayClient.chat_stream()` 在进入实际调用前，先产出一个统一的内部调用计划。
4. 为后续扩展更多 provider/runtime 保持低风险、可测试。

## 3. 非目标

1. 本次不把 `provider/model` 字符串改成系统唯一主键。
2. 本次不重写 `SessionOrchestrator + LLMGatewayClient` 为新的 Gateway 架构。
3. 本次不改变数据库里 `ParticipantConfig`、`ProviderConfig` 的外部结构。
4. 本次不重做前端 provider/model 选择器。
5. 本次不硬编码供应商模型列表。

## 4. 问题判断

### 4.1 为什么不能直接照搬 OpenClaw / Hermes / PraisonAI 的 `provider/model` 方案

这些项目里，`model="provider/model"` 适合作为高层框架入口，因为：

- 它们更接近 SDK / agent runtime
- provider 通常是“逻辑提供商”，而不是“用户在本地配置出的具体实例”

但在 `multi-model-debates` 里，真正影响调用行为的并不是模型名本身，而是：

- 具体是哪个 `provider_id`
- 这个 provider 的 `provider_type`
- 这个 provider 的 `api_format`
- 这个 provider 的 `auth_type`
- 这个 provider 的 `base_url`
- 这个 provider 的 `fallback_ids`

也就是说，在本项目中：

**`provider 实例` 才是运行时主语，`model_ref` 只是调用意图。**

如果强行把 `provider/model` 提升为唯一主键，会丢掉“同一模型名对应不同 provider 实例行为不同”的关键信息。

### 4.2 为什么也不能一刀切改成“全部走 LiteLLM”

LiteLLM 对统一调用入口很有价值，但这个项目已经暴露出一些不能简单统一的 runtime 差异：

- 浏览器认证后的 ChatGPT / Codex 需要走 responses runtime
- 部分 provider 只能走自定义 httpx stream
- 不同 provider 的认证头、base URL、API 兼容级别并不一致

所以 LiteLLM 适合作为**某些 runtime 的执行后端**，不适合作为整个系统唯一抽象层。

## 5. 方案对比

### 方案 A：改成 `provider/model` 单串主导

做法：

- 把内部调用完全建立在 `provider/model` 字符串上
- 淡化 `provider_id`
- 运行时优先按 provider 名推断

优点：

- 看起来简单
- 与很多 agent 框架形式接近

缺点：

- 无法可靠区分多个同类 provider 实例
- 无法稳定表达不同认证/runtime 差异
- 会破坏当前“provider 实例优先”的设计基础

结论：拒绝。

### 方案 B：保持 provider 实例优先，新增内部调用计划

做法：

- 外部继续保留 `provider_id + model_ref`
- 内部新增统一标准类型，例如 `ResolvedInvocationPlan`
- 先把请求解析成调用计划，再根据计划执行

优点：

- 不破坏现有 API / UI / 数据
- 把复杂分流逻辑收敛到一个地方
- 后续新增 provider/runtime 风险更低

缺点：

- 需要补一层内部抽象
- 需要迁移一部分 `llm_gateway.py` 内部逻辑

结论：推荐。

### 方案 C：大重构成新 Gateway 分层

做法：

- 新建 Gateway / SessionManager / ProviderManager / ToolRegistry
- 把 `orchestrator.py` 和 `llm_gateway.py` 彻底拆开

优点：

- 从理论上更整洁

缺点：

- 范围过大
- 与当前用户最关心的问题不直接相关
- 很容易在已有认证/runtime/streaming 链路上引入回归

结论：当前阶段拒绝。

## 6. 选定方案

采用 **方案 B：保持 provider 实例优先，新增内部调用计划**。

## 7. 设计细节

### 7.1 新增内部标准类型

新增一个内部数据结构，例如：

```python
@dataclass
class ResolvedInvocationPlan:
    requested_model_ref: str
    requested_provider_key: str
    model_name: str
    provider_id: Optional[str]
    provider_name: str
    provider_type: ProviderType
    api_format: APIFormat
    auth_type: AuthType
    base_url: str
    runtime_kind: str
```

其职责：

- 保留原始请求意图
- 解析出最终模型名
- 明确本次调用要走哪个 provider 实例
- 明确本次调用应走哪个 runtime 分支

### 7.2 `runtime_kind` 的意义

`runtime_kind` 用来替代散落的 if/else 判断，例如：

- `chatgpt_oauth_responses`
- `litellm_openai_like`
- `httpx_openai_like`
- `httpx_anthropic_messages`

这样 `chat_stream()` 变成两步：

1. `build_invocation_plan(...)`
2. `execute_invocation_plan(plan, ...)`

### 7.3 `resolve_model_target()` 的处理方式

当前 `resolve_model_target()` 不直接删除，而是逐步降级为薄封装或兼容函数。

最终目标是：

- 入口统一改成 `build_invocation_plan()`
- 旧函数只在局部兼容场景保留

### 7.4 外部协议保持不变

本次不改：

- `ParticipantConfig.model_ref`
- `ParticipantConfig.provider_id`
- `/api/model-catalog/discover`
- 前端 `provider_id::model_ref` 选择值

这样可以保证：

- 现有会话数据不需要迁移
- 前端 UI 不需要同步改版
- 用户现有 provider 配置不受影响

### 7.5 为什么不在这一期扩展模型目录结构

未来可以考虑让模型发现接口返回更丰富的能力信息，例如：

- `supports_streaming`
- `supports_tools`
- `supports_reasoning`
- `family`

但这属于下一步增强，不纳入本次范围。

本次只做**内部调用标准化**，避免把范围扩大到前后端双改。

## 8. 安全边界

本次必须遵守：

1. 不新增绕过现有 provider 认证链路的快捷调用。
2. 不把裸 `model_ref` 直接当作外部调用主键。
3. 不为了“统一”而抹平 ChatGPT OAuth / responses 这类特有 runtime。
4. 不删除现有 fallback 和 provider 实例绑定能力。

## 9. 测试策略

至少覆盖：

1. 显式 `provider_id` 绑定时，调用计划使用该 provider 实例。
2. 未指定 `provider_id` 时，仍能按现有规则解析 provider 类型并产出计划。
3. ChatGPT OAuth runtime 能被稳定识别为 `chatgpt_oauth_responses`。
4. Anthropic messages / OpenAI-compatible / LiteLLM 路径都能正确产出调用计划。
5. 现有 `test_provider_runtime.py` 与 `test_llm_gateway.py` 不回归。

## 10. 预期收益

1. 新增 provider/runtime 时，不需要继续在多个位置堆分支。
2. provider/model 调用语义更稳定，降低回归概率。
3. 为后续再扩展目录能力、执行 telemetry、工具可视化打基础。

## 11. 最终决策

对 `multi-model-debates` 来说，最佳路线不是“照搬别人的 provider/model 接入方式”，而是：

**继续保留当前 provider 实例优先的主架构，在后端内部新增统一调用计划，逐步消除分流逻辑的散落和语义漂移。**
