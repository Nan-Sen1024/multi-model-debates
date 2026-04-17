# 统一 Provider/Auth Runtime 重构设计

日期：2026-04-17

## 1. 背景

当前项目已经具备基础的多模型辩论能力，但模型接入、认证流程、运行时请求构造和前端授权体验仍然耦合过深，导致两个直接问题：

1. 新模型家族和新认证方式只能继续堆在 `backend/auth_flow.py`、`backend/llm_gateway.py` 和 `frontend/src/App.tsx` 的分支逻辑里，扩展成本快速上升。
2. 现有高优先级问题已经影响可用性和正确性：
   - 认证元数据无法稳定序列化/反序列化，企业级认证和 AWS 路径不可用。
   - 上下文压缩没有真正减少发给模型的历史。
   - `validate_model_ref()` 过度放宽，已打坏测试。
   - 前端 SSE 结束后会话详情、快照和轮询定时器处理不完整。
   - `requirements.txt` 不能支持干净环境自举。

本次设计目标不是一次性做出完整插件平台，而是在当前代码结构中引入足够清晰的边界，让后续可以稳定接入 `Codex`、`Claude`、`Grok`、`Gemini`、`DeepSeek`、`Kimi` 以及企业级 `Bedrock`、`Vertex`、`Copilot BYOK` 等路径。

## 2. 需求

### 2.1 功能需求

1. 系统必须支持统一管理 provider 的静态能力，包括默认 `base_url`、支持的认证方式、支持的 transport、模型前缀和前端字段提示。
2. 系统必须支持统一管理认证方式，包括：
   - `api_key`
   - `bearer`
   - `oauth_device_code`
   - `oauth_pkce_browser`
   - `aws_sso_device_code`
   - `aws_sso_pkce`
   - `adc`
   - `service_account`
   - `helper`
   - `mtls`
3. 系统必须支持统一管理运行时 transport，包括：
   - `openai_compatible`
   - `anthropic`
   - `copilot`
   - `bedrock`
   - `vertex`
   - `xai_native`
4. 必须接入以下模型家族，且每个家族至少具备一条官方或稳定兼容路径：
   - `Codex`
   - `Claude`
   - `Grok`
   - `Gemini`
   - `DeepSeek`
   - `Kimi`
5. 必须支持浏览器认证和设备码认证，且认证完成后凭据能持久化，并能在运行时恢复。
6. 必须保留当前会话、参与者、SSE 推流、策略调度的主流程，不重写 orchestrator。
7. 必须修复现有 review 中的高优先级问题，并为每个修复补回归测试。

### 2.2 非功能需求

1. 所有认证配置必须可序列化、可反序列化、可安全恢复，不允许运行时丢失关键 metadata。
2. 上下文压缩必须真的减少 prompt 历史体积，而不是只在数据库里做标记。
3. 新架构必须允许继续扩展 provider，而不是继续往现有大文件追加分支。
4. 第一批改造必须以最小侵入为主，不做完整外部插件 SDK，不引入额外服务进程。
5. 必须保留现有 SQLite 存储模式，仅做兼容性扩展。

## 3. 参考结论

### 3.1 采用 OpenClaw 式后端边界

公开资料里，`OpenClaw` 最值得借鉴的是 provider 自己拥有 `auth / catalog / transport normalize / config normalize`，宿主只保留通用推理循环。这种结构更适合持续接入多家模型和企业级认证，而不是在宿主里堆分支。

参考：
- OpenClaw provider 文档：https://github.com/openclaw/openclaw/blob/main/docs/concepts/model-providers.md

### 3.2 采用 Hermes 式统一 onboarding 体验

`Hermes` 作为参考仓库并不是 LLM provider 平台，但它的统一配置和浏览器登录体验值得借用到前端。结论是：后端边界借 OpenClaw，前端交互借统一 onboarding 思路，而不是复制某一个项目的全部实现。

## 4. 接入矩阵

### 4.1 首批稳定路径

| 模型家族 | Transport | Auth | 备注 |
| --- | --- | --- | --- |
| Codex | `copilot` | `github_device_code` / `github_token_env` | 交互式优先 device flow |
| Claude | `anthropic` | `api_key` | 先保证直连 |
| Gemini | `google_ai` | `api_key` | 先保证 AI Studio key |
| Grok | `xai_native` | `api_key` | 企业增强另走 mTLS |
| DeepSeek | `openai_compatible` | `api_key` | 官方兼容 OpenAI API |
| Kimi | `openai_compatible` | `api_key` | 官方兼容 OpenAI API |

### 4.2 首批企业路径

| 模型家族 | Transport | Auth | 备注 |
| --- | --- | --- | --- |
| Claude | `bedrock` | `aws_iam` / `aws_sso_*` | Bedrock 走 AWS 凭证 |
| Claude / Gemini | `vertex` | `adc` / `service_account` | Vertex 走 GCP 凭证 |
| Codex / Claude / Gemini / Grok | `copilot` | `github_device_code` / `BYOK` | Copilot 作为统一入口之一 |
| Grok | `xai_native` | `api_key + mtls` | mTLS 是企业增强层 |

参考：
- GitHub Copilot CLI 认证：https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/authenticate-copilot-cli
- GitHub Copilot BYOK：https://docs.github.com/copilot/how-tos/copilot-cli/customize-copilot/use-byok-models
- Anthropic 企业部署：https://docs.anthropic.com/en/docs/claude-code/bedrock-vertex-proxies
- Gemini API Key：https://ai.google.dev/gemini-api/docs/api-key
- Gemini OAuth：https://ai.google.dev/gemini-api/docs/oauth
- Google ADC：https://cloud.google.com/docs/authentication/application-default-credentials
- xAI mTLS：https://docs.x.ai/developers/advanced-api-usage/mtls
- DeepSeek API：https://api-docs.deepseek.com/
- Kimi API：https://platform.moonshot.ai/docs/guide/kimi-k2-5-quickstart

## 5. 目标架构

### 5.1 设计原则

1. 先做“注册表 + 适配器”，不做“外部插件平台”。
2. provider 负责描述能力，auth 负责拿凭据，transport 负责发请求，runtime resolver 负责组装。
3. orchestrator 继续只关心“调用哪个 provider 配置和 model_ref”，不感知每家认证细节。
4. 前端继续单页应用，但 provider 表单改成统一的“选择 provider -> 选择认证方式 -> 完成认证/填写凭据”的流。

### 5.2 后端模块边界

新增或重构如下模块：

- `backend/provider_profiles.py`
  - 定义 `ProviderProfile`
  - 静态注册 provider 能力、默认值和 UI 可见字段

- `backend/auth_profiles.py`
  - 定义认证方式枚举和认证输入/输出 schema
  - 统一处理 metadata 的存取和字段校验

- `backend/transport_adapters.py`
  - 定义 transport adapter 接口
  - 首批实现 `openai_compatible`、`anthropic`、`copilot`、`bedrock`、`vertex`、`xai_native`

- `backend/runtime_resolver.py`
  - 输入：`ProviderConfig`、`AuthConfig`、`model_ref`
  - 输出：规范化的 `ResolvedRuntimeConfig`
  - 负责决定最终 `base_url`、请求头、认证物料、transport 选择

保留并收敛职责的模块：

- `backend/llm_gateway.py`
  - 保留通用入口和 LiteLLM/httpx 调用外壳
  - 删除与 provider 绑定过紧的分支逻辑
  - 调用 `RuntimeResolver` 和 `TransportAdapter`

- `backend/auth_flow.py`
  - 只管理交互式认证会话与状态机
  - 不再直接写 provider 运行时特例

- `backend/api.py`
  - 保留 REST/SSE 接口
  - 新增 provider profile 和 auth profile 所需接口

### 5.3 数据模型和存储

#### `AuthConfig`

继续使用 `backend/models.py` 中的 `AuthConfig`，但约束改为：

- `metadata` 作为运行时扩展字段必须完整序列化和反序列化。
- 企业级通道都通过 `metadata` 承载额外字段，例如：
  - Bedrock：`region`、`role_arn`、`access_key_id`、`secret_access_key`、`session_token`
  - Vertex：`project_id`、`location`、`use_adc`、`service_account_path`
  - Copilot：`host`、`token_source`
  - xAI mTLS：`cert_path`、`key_path`

#### `auth_sessions`

现有 `auth_sessions` 表不再继续复用字段语义。新增一个兼容列：

- `context_json`

用于保存 PKCE/device flow/OAuth 的会话上下文，避免继续把不同语义塞进 `device_code`、`client_id`、`client_secret` 这些字段。保留旧字段用于兼容已有数据。

#### `provider_configs`

继续保留 `auth_config JSON` 存储，不新增新表。首批重构以内聚字段和运行时恢复为目标，不做拆表。

## 6. 核心数据流

### 6.1 Provider 配置

1. 前端拉取 provider profile 列表。
2. 用户选择 provider 和认证方式。
3. 如果是静态凭据，前端直接提交配置。
4. 如果是交互式认证，前端调用 `/auth/start`，进入 device code 或 browser PKCE。
5. 认证完成后，后端把标准化 `AuthConfig` 写回 `provider_configs.auth_config`。

### 6.2 运行时调用

1. orchestrator 仍按 `provider_id + model_ref` 找 provider。
2. `RuntimeResolver` 解析：
   - provider profile
   - auth config
   - transport
   - model id
3. `TransportAdapter` 发起请求。
4. `LLMGatewayClient` 统一处理 streaming、错误映射和 fallback。

### 6.3 上下文压缩

1. `context_compressor.py` 继续负责生成摘要和写 `compressed_summaries`。
2. `message_store.py` 在构造历史时必须读取已压缩区间的摘要，而不是继续把原始消息内容直接发给模型。
3. `orchestrator.py` 保持调用点不变，但历史来源变为“未压缩消息 + 摘要节点”的拼接结果。

## 7. 现有问题修复优先级

### P0

1. 修复 `deserialize_auth_config()` 对 `metadata` 的遗漏。
2. 修复 IAM/企业认证路径运行时无法消费 metadata 的问题。
3. 修复上下文压缩无效。
4. 修复 `requirements.txt` 无法自举。

### P1

1. 修复 `validate_model_ref()` 对 `no-slash` 和多斜杠值的错误放行。
2. 修复前端 SSE 结束后不刷新 session/snapshot。
3. 修复前端认证和流式轮询 timer 清理不完整。

### P2

1. 把 provider/auth UI 改成统一 onboarding。
2. 为企业路径增加字段校验和错误提示。

## 8. 测试策略

### 8.1 后端

- 扩展 `tests/test_llm_gateway.py`
  - `metadata` round-trip
  - `validate_model_ref` 合法/非法矩阵
  - runtime resolver 对不同 transport 的解析

- 扩展 `tests/test_auth_flow.py`
  - PKCE 会话上下文持久化/恢复
  - AWS SSO PKCE 回调成功/失败状态流转
  - 认证完成后 provider config 回写

- 扩展 `tests/test_message_store.py`
  - 已压缩消息区间应输出摘要而不是原文

### 8.2 前端

如果现有前端测试基础不足，第一批至少通过最小单元或集成方式验证：

- SSE `turn_end` 后自动刷新会话详情
- 认证轮询结束后清理 timer
- 交互式授权路径能正确打开浏览器链接

## 9. 非目标

以下内容不在第一批范围：

1. 外部第三方 provider 插件 SDK
2. 完整的可视化 provider 向导设计系统
3. 多账户自动 failover
4. 对所有供应商做完整的模型目录同步

## 10. 实施顺序

1. 修补数据模型和认证配置序列化问题。
2. 引入 provider profile / runtime resolver / transport adapter 基础层。
3. 修复上下文压缩和 `validate_model_ref`。
4. 修复前端 SSE 和认证轮询状态。
5. 接入首批稳定路径。
6. 接入企业路径。
7. 完成依赖、测试和运行文档收口。
