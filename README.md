# multi-model-debates

一个本地优先的多模型协作控制台。它把 `Provider 管理`、`多模型会话`、`SSE 流式输出`、`代码工作区`、`Skills / MCP / Agent` 放在同一个前后端应用里，适合做模型接入验证、多人格讨论、代码协作、仓库分析和本地 AI 工作台。

这个项目的定位，不是“再做一个聊天页面”，而是：

- 统一管理多家模型 Provider
- 让多个模型围绕同一主题或同一代码仓库协作
- 在前端直接看到流式输出、执行进度、工具调用和工作区文件
- 把会话、Provider、认证状态、快照和历史保存在本地 SQLite

README 的组织方式参考了 `OpenClaw` 和 `Hermes Agent` 的项目首页写法：先讲用途和快速启动，再讲配置，再讲架构和扩展能力。

## 目录

- [1. 这个项目能做什么](#1-这个项目能做什么)
- [2. 核心特性](#2-核心特性)
- [3. 技术栈与架构](#3-技术栈与架构)
- [4. 目录结构](#4-目录结构)
- [5. 快速启动](#5-快速启动)
- [6. 运行后怎么用](#6-运行后怎么用)
- [7. 协作模式说明](#7-协作模式说明)
- [8. Provider 配置详解](#8-provider-配置详解)
- [9. 创建会话详解](#9-创建会话详解)
- [10. `code_workspace` 模式详解](#10-code_workspace-模式详解)
- [11. 流式输出与执行过程](#11-流式输出与执行过程)
- [12. 数据存储与关键 API](#12-数据存储与关键-api)
- [13. 测试与开发命令](#13-测试与开发命令)
- [14. 参考项目与设计来源](#14-参考项目与设计来源)
- [15. 常见问题](#15-常见问题)

## 1. 这个项目能做什么

### 典型使用场景

- 用 `GPT / Claude / Gemini / Grok / DeepSeek / Kimi / Ollama` 做同题对比
- 让多个模型围绕同一主题进行辩论、协作或评审
- 把本地代码仓库挂进会话，让模型基于真实文件上下文分析项目
- 接入 `Skills`、`MCP Server` 和最小 Agent Loop，让模型不仅“说”，还能“调工具”
- 在前端可视化查看：
  - 当前会话历史
  - 流式输出
  - 执行时间线
  - 工作区目录树与文件正文
  - Provider 认证状态

### 这个项目不是什么

- 不是云端托管 SaaS
- 不是多用户权限系统
- 不是只支持单一模型的纯聊天页
- 不是完整 IDE 替代品

它更接近一个本地开发者工作台。

## 2. 核心特性

- 多 Provider 管理：支持 `openai / anthropic / google / groq / mistral / xai / ollama / lm_studio / vllm / openrouter / litellm / gateway / custom`
- 多种认证方式：`API Key`、`Bearer`、`Browser OAuth / PKCE`、`Device Code`、`AWS IAM Identity Center`
- 多模型会话：同一会话支持多个参与者按顺序或按模式协作
- 16 种协作模式：聊天、辩论、代码协作、代码工作区、角色扮演、模拟法庭等
- 代码工作区模式：扫描本地仓库，选目录/文件注入上下文
- 工作区能力层：`Skills`、`MCP`、`Agent defaults`、`participant_overrides`
- 前端流式体验：SSE `chunk` 级输出，执行时间线单独展示
- 会话持久化：刷新页面后可恢复最近会话、历史消息和 Provider 状态
- 工作区文件查看器：右侧面板支持目录展开、文件点击查看正文
- 本地数据库：使用 SQLite 保存会话、消息、压缩摘要、检查点、Provider、认证会话

## 3. 技术栈与架构

### 后端

- `FastAPI`：HTTP API 和 SSE 流式接口
- `aiosqlite`：异步 SQLite 访问
- `httpx`：Provider HTTP 调用、OAuth 交互
- `LiteLLM`：统一多 Provider 模型调用和模型发现
- `sentence-transformers`：上下文压缩/漂移检测相关能力
- `MCP` Python SDK：Model Context Protocol 工具调用

### 前端

- `React 18`
- `TypeScript`
- `react-scripts` / CRA
- 浏览器原生 `EventSource` 处理 SSE

### 存储

- `SQLite`
- 默认数据库文件：`multi_model_debate.db`

### 核心后端模块

- `backend/api.py`
  - FastAPI 入口
  - 会话、Provider、认证、工作区、SSE 接口
- `backend/orchestrator.py`
  - 会话主调度器
  - 负责选下一个参与者、拼 prompt、调模型、写消息、发事件
- `backend/llm_gateway.py`
  - 模型调用网关
  - 负责模型路由、认证恢复、Provider 健康检查、模型发现
- `backend/auth_flow.py`
  - 浏览器登录、Device Code、AWS SSO / IAM 等交互式认证流程
- `backend/workspace_scanner.py`
  - 扫描本地仓库目录树
- `backend/workspace_context.py`
  - 读取选中文件并注入模型上下文
- `backend/workspace_reader.py`
  - 给前端文件查看器读取单文件正文
- `backend/workspace_skills.py`
  - 扫描 `SKILL.md`
- `backend/workspace_mcp.py`
  - MCP Server 运行时
- `backend/workspace_agent.py`
  - 最小 Agent Loop

### 核心前端模块

- `frontend/src/App.tsx`
  - 主控制台
- `frontend/src/api.ts`
  - 前端 API 调用与 SSE 订阅
- `frontend/src/sessionStream.ts`
  - 流式消息状态归并
- `frontend/src/WorkspaceMode.tsx`
  - `code_workspace` 创建面板和右侧工作区浏览器
- `frontend/src/ExecutionProgress.tsx`
  - 执行时间线面板
- `frontend/src/modelCatalog.tsx`
  - 模型下拉、关键字过滤

### 架构概览

```text
React / TypeScript UI
        |
        | HTTP + SSE
        v
FastAPI API Layer
        |
        +--> SessionOrchestrator
        |       |
        |       +--> LLMGatewayClient --> Provider APIs / LiteLLM / httpx
        |       +--> MessageStore / Snapshot / Context Compression
        |       +--> Workspace Scanner / Reader / Skills / MCP / Agent
        |
        +--> AuthFlowManager --> Browser OAuth / Device Code / AWS IAM
        |
        +--> SQLite (sessions, messages, providers, auth_sessions, checkpoints)
```

## 4. 目录结构

```text
multi-model-debates/
├─ backend/                  # FastAPI 后端与调度核心
├─ frontend/                 # React 前端控制台
├─ tests/                    # Python 测试
├─ docs/                     # 设计文档与实施计划
├─ scripts/                  # 辅助脚本
├─ requirements.txt          # Python 依赖
├─ multi_model_debate.db     # 运行后生成的 SQLite 数据库
└─ README.md
```

说明：

- `.worktrees/`、`_codex_backups/`、`.pytest_cache/`、`.hypothesis/` 属于开发或调试遗留目录，不是主运行路径
- 实际运行关注的是 `backend/`、`frontend/`、`tests/` 和根目录数据库文件

## 5. 快速启动

### 运行环境

- Python `3.11+`，当前项目在 `3.12` 上可运行
- Node.js `22+` 推荐
- npm `10+`
- Windows + WSL2 或原生 Linux 都可以

### 1) 安装后端依赖

```bash
cd ./multi-model-debates
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Windows PowerShell：

```powershell
cd .\multi-model-debates
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2) 启动后端

```bash
cd ./multi-model-debates
source .venv/bin/activate
uvicorn backend.api:app --reload --host 127.0.0.1 --port 8000
```

后端默认地址：

- `http://127.0.0.1:8000`

### 3) 安装并启动前端

```bash
cd ./multi-model-debates/frontend
npm install
npm start
```

前端默认地址：

- `http://127.0.0.1:3000`

说明：

- CRA 开发代理默认指向 `http://127.0.0.1:8000`
- SSE 流在开发模式下会优先直连 `8000`，避免开发代理把流式输出缓冲成“一次性返回”

### 4) 打开页面

浏览器访问：

- `http://127.0.0.1:3000`

### 5) 首次使用顺序

1. 先到 `🔌 Provider` 配置模型供应商
2. 再到 `💬 创建会话`
3. 选择模式、参与者和模型
4. 点击 `🚀 创建会话`
5. 在会话详情页点击 `开始下一轮`

## 6. 运行后怎么用

### 页面主流程

#### A. 配置 Provider

- 新增一个或多个 Provider
- 选择认证方式
- 检查健康状态
- 发现模型目录

#### B. 创建会话

- 输入 `Topic`
- 选择 `协作模式`
- 配置至少两个参与者
- 给每个参与者选择模型

#### C. 进入会话详情

- 左侧：历史会话列表
- 中间：聊天消息流
- 右侧：
  - 普通模式显示快照
  - `code_workspace` 显示开发面板、执行过程、工作区文件树和文件正文

#### D. 运行一轮

- 点击 `开始下一轮`
- 前端通过 SSE 订阅 `/api/sessions/{id}/stream`
- 后端按模式调度参与者
- 前端实时显示：
  - `turn_start`
  - `chunk`
  - `agent_plan`
  - `tool_call`
  - `tool_result`
  - `turn_end`
  - `round_end`

### 会话会保存吗

会保存。

- 会话、消息、快照、Provider、认证结果：存 SQLite
- 最近打开的会话 ID、部分前端状态：存浏览器本地存储
- 刷新页面后，前端会尝试恢复最近会话和历史列表

## 7. 协作模式说明

当前项目内置 16 种模式：

| 模式值 | 前端显示 | 用途 |
|---|---|---|
| `chat` | 自由聊天 | 多模型顺序接力式对话 |
| `brainstorm` | 头脑风暴 | 并行产生想法，再汇总 |
| `code_collaboration` | 代码协作 | 面向代码的审查、建议、改进 |
| `code_workspace` | 代码工作区 | 绑定本地仓库，支持文件上下文、Skills、MCP、Agent |
| `data_analysis` | 数据分析 | 结构化发现、风险和建议 |
| `debate` | 辩论 | 对立观点交锋，共识检测 |
| `werewolf` | 狼人杀 | 角色博弈 |
| `murder_mystery` | 剧本杀 | 角色调查与推理 |
| `undercover` | 谁是卧底 | 描述、投票、淘汰 |
| `mock_trial` | 模拟法庭 | 阶段化庭审流程 |
| `role_play` | 角色扮演 | 世界观和剧情推进 |
| `socratic_dialogue` | 苏格拉底问答 | 追问、澄清、洞察 |
| `peer_review` | 多模型评审 | Producer / Reviewer 迭代 |
| `mock_interview` | 模拟面试 | 提问、追问、回答与反馈 |
| `story_chain` | 故事接龙 | 多角色续写 |
| `negotiation` | 模拟谈判 | 立场博弈与协议生成 |

## 8. Provider 配置详解

这一部分最重要。Provider 配置决定：

- 模型请求发往哪里
- 用什么格式调用 API
- 用什么方式认证
- 默认显示哪些模型
- 如果当前 Provider 失败，是否自动切换到其他 Provider

### 8.1 前端表单字段说明

`🔌 Provider` 页面的新增表单包含以下字段：

| 字段 | 对应 API 字段 | 作用 | 是否必填 |
|---|---|---|---|
| `名称` | `name` | Provider 的显示名和唯一名字 | 是 |
| `Provider 类型` | `provider_type` | 决定模型路由逻辑和默认能力分类 | 是 |
| `Base URL` | `base_url` | 自定义 API 根地址，OpenAI-compatible / 本地网关常用 | 否 |
| `API Format` | `api_format` | 请求格式，目前主要是 `openai-completions` 或 `anthropic-messages` | 是 |
| `默认模型` | 存入 `auth_metadata.default_model_ref` | 仅用于保存和回填默认模型，不是独立数据库列 | 否 |
| `Auth Type` | `auth_type` | 认证类型枚举 | 是 |
| `认证方式` | 前端派生字段 | 决定是走浏览器登录、Device Code、API Key 还是 Bearer | 取决于 Provider |
| `Auth Value / API Key` | `auth_value` | 密钥、Bearer Token 或脚本路径 | 某些认证方式需要 |
| `Fallback IDs` | `fallback_ids` | 当前 Provider 失败时可切换的 Provider ID 列表 | 否 |
| `Auth Metadata (JSON)` | `auth_metadata` | 认证和默认模型的扩展元数据 | 取决于场景 |

### 8.2 `provider_type` 代表什么

| `provider_type` | 适合什么场景 |
|---|---|
| `openai` | OpenAI / ChatGPT / Codex |
| `anthropic` | Claude / Anthropic |
| `google` | Gemini |
| `groq` | Groq |
| `mistral` | Mistral |
| `xai` | Grok / xAI |
| `ollama` | 本地 Ollama |
| `lm_studio` | LM Studio OpenAI-compatible 服务 |
| `vllm` | 自建 vLLM OpenAI-compatible 服务 |
| `openrouter` | OpenRouter |
| `litellm` | LiteLLM Proxy |
| `gateway` | 其他网关聚合入口 |
| `custom` | 任何自定义 Provider，尤其是 OpenAI-compatible 厂商或企业网关 |

### 8.3 `api_format` 代表什么

当前项目里主要有两个：

| `api_format` | 含义 | 典型场景 |
|---|---|---|
| `openai-completions` | 走 OpenAI-compatible Chat Completions / 流式路径 | OpenAI、DeepSeek、Kimi、Ollama、OpenRouter、xAI、LM Studio、vLLM |
| `anthropic-messages` | 走 Anthropic Messages API | Claude 直连 |

经验规则：

- `DeepSeek / Kimi / Ollama / LM Studio / vLLM / OpenRouter / xAI` 一般选 `openai-completions`
- `Claude 直连` 一般选 `anthropic-messages`

### 8.4 `auth_type` 代表什么

后端枚举支持：

| `auth_type` | 含义 |
|---|---|
| `api_key` | 直接使用 API Key |
| `oauth` | 使用 OAuth，可能是浏览器 PKCE 或 Device Code |
| `bearer` | 原始 Bearer Token |
| `iam` | AWS IAM / AWS IAM Identity Center 等交互式登录 |
| `adc` | Google Application Default Credentials，属于高级用法 |
| `helper` | 通过脚本或 helper 动态获取密钥，属于高级用法 |

当前前端最常用、最完整的路径是：

- `api_key`
- `bearer`
- `oauth`
- `iam`

### 8.5 “认证方式”下拉的实际含义

这是前端基于 `provider_type + auth_type + auth_metadata` 推导出的交互方式。

| 认证方式 | 典型用途 | 实际后端 flow |
|---|---|---|
| `浏览器登录` | Codex / 通用 PKCE 浏览器 OAuth | `openai_codex` 或 `browser_oauth` |
| `Device Code` | Codex Device Code / 通用 OAuth Device Flow / AWS IAM | `openai_codex`、`generic_oauth`、`aws_iam` |
| `API Key` | OpenAI / Anthropic / DeepSeek / Kimi / OpenRouter 最常见 | `auth_type=api_key` |
| `Bearer Token` | 企业网关、预先拿到 access token 的场景 | `auth_type=bearer` |

### 8.6 `auth_value` 是什么

这个字段的意义取决于 `auth_type`：

| `auth_type` | `auth_value` 的含义 |
|---|---|
| `api_key` | API Key，例如 `sk-...` |
| `bearer` | 原始 Bearer Token |
| `helper` | helper 脚本路径 |
| `oauth` | 通常留空，登录成功后由后端写回 token |
| `iam` | 通常留空，走交互式登录 |

### 8.7 `fallback_ids` 是什么

这是失败切换链。

如果当前 Provider 请求失败，调度层可以尝试切到 `fallback_ids` 指向的其他 Provider。这个字段是 Provider ID 数组，前端表单里用逗号分隔录入。

示例：

```text
openai-main -> openrouter-fallback -> ollama-local
```

### 8.8 `auth_metadata` 每个键的含义

这是最容易混乱的部分。当前项目后端实际会读取以下键：

| 键名 | 用在哪 | 含义 |
|---|---|---|
| `default_model_ref` | Provider 默认模型 | 前端“默认模型”实际保存在这里 |
| `authorization_endpoint` | 浏览器 OAuth | 浏览器登录跳转地址 |
| `token_endpoint` | Browser OAuth / Device Code | 用 `code` 或 `device_code` 换 token 的地址 |
| `device_authorization_endpoint` | Device Code | 可选，单独指定设备授权端点；不填时会尝试从 `token_endpoint` 推导 |
| `client_id` | Browser OAuth / Device Code | OAuth 客户端 ID |
| `client_secret` | Browser OAuth / Device Code | OAuth 客户端密钥，可选但常见 |
| `scope` | Browser OAuth / Device Code | OAuth scope，多个 scope 一般用空格分隔 |
| `sso_start_url` | AWS IAM / SSO | AWS IAM Identity Center Start URL |
| `sso_region` | AWS IAM / SSO | AWS IAM Identity Center 区域 |

### 8.9 常见 Provider 配置模板

#### 8.9.1 OpenAI / GPT / Codex

最常见两种方式：

- `API Key`
- `浏览器登录 / Device Code`

建议：

```json
{
  "name": "openai-main",
  "provider_type": "openai",
  "base_url": "https://api.openai.com/v1",
  "api_format": "openai-completions",
  "auth_type": "api_key",
  "auth_value": "sk-...",
  "auth_metadata": {
    "default_model_ref": "gpt-5"
  },
  "fallback_ids": []
}
```

如果你想用 Codex 登录：

- `provider_type` 仍然可以是 `openai`
- `auth_type` 选 `oauth`
- 认证方式选 `浏览器登录` 或 `Device Code`
- `Auth Value` 留空

说明：

- Codex 的 OpenAI 登录参数由后端内置处理，不需要你自己手填 `authorization_endpoint`

#### 8.9.2 Claude / Anthropic

API Key 直连：

```json
{
  "name": "claude-direct",
  "provider_type": "anthropic",
  "base_url": "https://api.anthropic.com",
  "api_format": "anthropic-messages",
  "auth_type": "api_key",
  "auth_value": "sk-ant-...",
  "auth_metadata": {
    "default_model_ref": "claude-sonnet-4"
  },
  "fallback_ids": []
}
```

如果你使用企业 OAuth 网关，可以把浏览器 / Device Code 所需元数据填进 `auth_metadata`：

```json
{
  "authorization_endpoint": "https://example.com/oauth/authorize",
  "token_endpoint": "https://example.com/oauth/token",
  "client_id": "your-client-id",
  "client_secret": "your-client-secret",
  "scope": "openid profile email",
  "default_model_ref": "claude-sonnet-4"
}
```

#### 8.9.3 Gemini

Gemini 通常使用：

- `provider_type = google`
- 如果走统一兼容网关，也可以用 `custom + openai-completions`

#### 8.9.4 Grok / xAI

建议：

- `provider_type = xai`
- `api_format = openai-completions`

#### 8.9.5 DeepSeek / Kimi

这两个非常适合用 `custom + openai-completions` 接入。

示例思路：

```json
{
  "name": "deepseek",
  "provider_type": "custom",
  "base_url": "https://api.deepseek.com/v1",
  "api_format": "openai-completions",
  "auth_type": "api_key",
  "auth_value": "sk-...",
  "auth_metadata": {
    "default_model_ref": "deepseek-chat"
  },
  "fallback_ids": []
}
```

Kimi 类似：

```json
{
  "name": "kimi",
  "provider_type": "custom",
  "base_url": "https://api.moonshot.cn/v1",
  "api_format": "openai-completions",
  "auth_type": "api_key",
  "auth_value": "sk-...",
  "auth_metadata": {
    "default_model_ref": "moonshot-v1-8k"
  },
  "fallback_ids": []
}
```

#### 8.9.6 Ollama

本地 Ollama：

```json
{
  "name": "ollama-local",
  "provider_type": "ollama",
  "base_url": "http://127.0.0.1:11434/v1",
  "api_format": "openai-completions",
  "auth_type": "api_key",
  "auth_value": "",
  "auth_metadata": {
    "default_model_ref": "qwen2.5-coder:7b"
  },
  "fallback_ids": []
}
```

说明：

- 本地 Ollama 一般不需要实际 API Key
- 前端有本地模型发现能力，会读出本地模型目录

### 8.10 Provider 配置的 API 载荷

后端创建 Provider 的载荷结构：

```json
{
  "name": "openai-main",
  "provider_type": "openai",
  "base_url": "https://api.openai.com/v1",
  "api_format": "openai-completions",
  "auth_type": "api_key",
  "auth_value": "sk-...",
  "auth_metadata": {
    "default_model_ref": "gpt-5"
  },
  "fallback_ids": []
}
```

### 8.11 模型目录发现

前端会调用 `/api/model-catalog/discover` 获取某个 Provider 的可选模型列表，用于：

- Provider 默认模型下拉
- 创建会话时的参与者模型选择
- 模型关键字过滤，例如输入 `gpt` 过滤出所有带 `gpt` 的模型

## 9. 创建会话详解

### 9.1 前端“创建会话”页包含什么

#### 必填

- `讨论主题 Topic`
- `协作模式`
- 至少 2 个参与者

#### 每个参与者包含

| 字段 | 对应数据 | 作用 |
|---|---|---|
| `Custom_ID` | `custom_id` | 会话内别名，例如 `Model_A`、`Claude`、`Codex` |
| `Provider（可选）` | `provider_id` | 显式绑定某个 Provider；留空时自动匹配 |
| `模型选择` | `model_ref` | 实际调用的模型名 |
| `Role` | `role_desc` | 对该参与者的角色说明，例如“正方辩手”“代码审查者” |

### 9.2 什么是 `model_ref`

`model_ref` 有两种常见写法：

- `provider/model`
- 仅写裸模型名，例如 `deepseek-chat`

当前项目的规则是：

- 如果参与者已经明确绑定了 `provider_id`，可以只写裸模型名
- 如果没有绑定 Provider，推荐写成 `provider/model`

### 9.3 创建会话的 API 载荷

基础示例：

```json
{
  "topic": "比较 Redis 和 Memcached 在高并发场景下的优劣",
  "mode": "debate",
  "participants": [
    {
      "custom_id": "Model_A",
      "provider_id": "provider-openai-id",
      "model_ref": "gpt-5",
      "role_desc": "支持 Redis"
    },
    {
      "custom_id": "Model_B",
      "provider_id": "provider-anthropic-id",
      "model_ref": "claude-sonnet-4",
      "role_desc": "支持 Memcached"
    }
  ]
}
```

### 9.4 后端支持但前端默认未暴露的高级参数

`SessionCreatePayload` 还支持以下高级字段，目前前端创建页默认走后端默认值：

| 字段 | 默认值 | 含义 |
|---|---|---|
| `max_rounds` | `20` | 最大轮次 |
| `drift_threshold` | `0.4` | 漂移检测阈值 |
| `retention_window` | `10` | 历史保留窗口 |
| `context_threshold` | `0.7` | 上下文压缩阈值 |
| `summary_model` | `null` | 用于摘要/压缩的模型 |

如果你用 API 创建会话，可以显式传入这些值。

### 9.5 会话创建后的生命周期

1. 会话写入 SQLite
2. 快照初始化
3. 前端跳到会话详情
4. 点击 `开始下一轮`
5. 后端调度当前模式的一轮
6. 前端通过 SSE 持续收到事件
7. 当该轮结束时收到 `round_end`

## 10. `code_workspace` 模式详解

这是本项目区别于普通多模型聊天的关键模式。

### 10.1 这个模式解决什么问题

普通会话只能讨论“文本主题”。  
`code_workspace` 会话可以把本地代码仓库挂进来，让模型围绕真实文件做分析、规划、审查和工具调用。

### 10.2 工作区字段说明

前端工作区创建面板对应的主要字段：

| 字段 | 对应数据 | 含义 |
|---|---|---|
| `工作区路径` | `root_path` | 本地仓库根目录 |
| `显示名` | `display_name` | 前端展示名 |
| `扫描排除项` | `scan_excludes` | 扫描时排除的目录名 |
| `高级路径覆盖` | `selected_paths` | 注入模型上下文的目录/文件路径 |
| `Skills` | `capabilities.skill_sources` | 扫描 `SKILL.md` 的目录来源 |
| `MCP` | `capabilities.mcp_servers` | 可连接的 MCP Server 列表 |
| `Agent` | `capabilities.agent_defaults` | 默认 Agent 配置 |
| `参与者覆盖` | `capabilities.participant_overrides` | 每个 `@alias` 的单独技能 / MCP / Agent 限制 |

### 10.3 仓库代码是怎么给模型看的

不是模型自己直接读本地磁盘，而是后端按以下步骤处理：

1. `workspace_scanner.py` 扫描目录树
2. 你在前端勾选目录或文件
3. 后端把目录展开成具体文件
4. `workspace_context.py` 读取这些文件正文
5. 文件内容以“工作区文件上下文”的形式拼进 prompt
6. 模型基于这些内容回答

所以：

- 前端右侧工作区树是“扫描结果 + 文件查看器”
- 模型看到的文件正文来自后端同一套读取逻辑
- 这两条链路现在已经对齐

### 10.4 工作区文件查看器

右侧面板现在支持：

- 目录树展开
- 点击文件读取正文
- 可拖拽调整目录树和文件正文区域宽度
- 文件正文若过长会截断，并标记 `已截断`

### 10.5 `selected_paths` 的作用

它不是“只是 UI 勾选记录”，而是：

- 直接决定哪些目录 / 文件会被注入模型上下文

建议：

- 只选和当前任务相关的目录
- 不要默认把整个超大仓库全部塞进 prompt

### 10.6 Skills

`skill_sources` 会告诉后端去哪些目录扫描 `SKILL.md`。

结构示例：

```json
{
  "skill_sources": [
    {
      "path": "C:/Users/Nan/.codex/superpowers/skills",
      "source_type": "local",
      "label": null,
      "recursive": true,
      "enabled": true
    }
  ]
}
```

作用：

- 扫描技能来源
- 提取技能摘要
- 把技能信息注入 `code_workspace` prompt

### 10.7 MCP

`mcp_servers` 用来描述可接入的 MCP Server。

字段说明：

| 字段 | 含义 |
|---|---|
| `name` | MCP Server 名 |
| `transport` | `stdio` 或 `streamable_http` |
| `command` | `stdio` 模式下的启动命令 |
| `args` | 命令参数数组 |
| `url` | `streamable_http` 模式下的地址 |
| `env` | 环境变量字典 |
| `tools_allowlist` | 可用工具白名单 |
| `enabled` | 是否启用 |

### 10.8 Agent Defaults

默认 Agent 配置字段：

| 字段 | 含义 |
|---|---|
| `mode` | `disabled` / `plan_only` / `tool_loop` / `full_agent` |
| `max_steps` | 最大 Agent 步数 |
| `can_write` | 是否允许写工作区 |
| `allowed_skills` | 允许使用的技能列表 |
| `allowed_mcp_servers` | 允许调用的 MCP Server 名 |
| `memory_scope` | 记忆共享范围，默认 `workspace_shared` |

### 10.9 `participant_overrides`

这是 `code_workspace` 很有用的一层。  
它允许你给每个参与者单独限制能力。

例如：

- `@architect` 只允许看设计类 skills
- `@coder` 允许 `filesystem` MCP
- `@reviewer` 禁止写入，只能审查

结构示例：

```json
{
  "participant_overrides": {
    "Model_A": {
      "skills": ["focused-review"],
      "mcp_servers": ["filesystem"],
      "agent": {
        "mode": "tool_loop",
        "max_steps": 4,
        "can_write": false,
        "allowed_skills": ["focused-review"],
        "allowed_mcp_servers": ["filesystem"],
        "memory_scope": "workspace_shared"
      }
    }
  }
}
```

## 11. 流式输出与执行过程

### 11.1 现在是怎么流式输出的

前端通过 `EventSource` 连接：

- `/api/sessions/{id}/stream`

后端通过 SSE 推送事件。

### 11.2 主要流事件

| 事件 | 作用 |
|---|---|
| `ping` | keepalive，防止前端空闲超时误判 |
| `turn_start` | 某个参与者开始执行 |
| `chunk` | 文本流式片段 |
| `agent_plan` | Agent 计划 |
| `tool_call` | 工具调用开始 |
| `tool_result` | 工具调用结果 |
| `turn_end` | 某个参与者本轮结束 |
| `round_end` | 一轮结束 |
| `drift_alert` | 检测到话题漂移 |
| `compression` | 触发上下文压缩 |
| `session_end` | 会话结束 |
| `error` | 当前轮出错 |

### 11.3 前端怎么看执行过程

聊天区之外，右侧有 `执行过程` 面板，专门显示：

- 谁开始执行
- 是否进入 agent 模式
- 调用了什么工具
- 工具返回了什么
- 该轮是否完成或失败

这部分设计参考了：

- OpenClaw 的结构化事件思路
- Hermes 的 streaming tool output
- Codex 类工具常见的“消息区 + 执行面板”分离显示

### 11.4 普通模式和 `code_workspace + agent` 的区别

- 普通模式：文本 `chunk` 是流式输出
- `code_workspace + agent`：
  - 计划和工具调用是结构化流事件
  - 最终回答目前仍可能带有一定缓冲后再发的特征，尤其在需要先完成工具阶段时更明显

## 12. 数据存储与关键 API

### 12.1 SQLite 中保存什么

当前数据库表主要包括：

- `collaboration_sessions`
- `model_participants`
- `collaboration_messages`
- `messages_fts`
- `compressed_summaries`
- `checkpoints`
- `provider_configs`
- `auth_sessions`

### 12.2 关键接口总览

#### 会话

| 方法 | 路径 | 作用 |
|---|---|---|
| `POST` | `/api/sessions` | 创建会话 |
| `GET` | `/api/sessions` | 列出历史会话 |
| `GET` | `/api/sessions/{id}` | 获取会话详情 |
| `PATCH` | `/api/sessions/{id}` | 修改会话标题 |
| `DELETE` | `/api/sessions/{id}` | 删除会话 |
| `GET` | `/api/sessions/{id}/messages` | 获取消息历史 |
| `POST` | `/api/sessions/{id}/messages` | 插入用户消息 |
| `GET` | `/api/sessions/{id}/snapshot` | 获取快照 |
| `PATCH` | `/api/sessions/{id}/snapshot` | 修改快照 |
| `GET` | `/api/sessions/{id}/stream` | SSE 流式输出 |

#### 工作区

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/api/sessions/{id}/workspace` | 获取工作区扫描结果 |
| `GET` | `/api/sessions/{id}/workspace/file?path=...` | 读取单文件正文 |
| `POST` | `/api/workspace/preview` | 创建前预览工作区树 |

#### Provider

| 方法 | 路径 | 作用 |
|---|---|---|
| `GET` | `/api/providers` | 列表 |
| `POST` | `/api/providers` | 创建 |
| `PATCH` | `/api/providers/{id}` | 更新 |
| `DELETE` | `/api/providers/{id}` | 删除 |
| `POST` | `/api/providers/{id}/health` | 健康检查 |
| `POST` | `/api/model-catalog/discover` | 模型目录发现 |

#### 认证

| 方法 | 路径 | 作用 |
|---|---|---|
| `POST` | `/api/providers/{id}/auth/start` | 启动认证流程 |
| `GET` | `/api/providers/{id}/auth/status/{auth_session_id}` | 查询认证状态 |
| `POST` | `/api/providers/{id}/auth/cancel/{auth_session_id}` | 取消正在进行的登录 |
| `POST` | `/api/providers/{id}/auth/logout` | 退出登录并清空凭据 |
| `GET` | `/api/providers/{id}/auth/callback` | 浏览器 OAuth 回调 |

## 13. 测试与开发命令

### 后端测试

```bash
cd ./multi-model-debates
source .venv/bin/activate
python -m pytest -q
```

### 前端测试

```bash
cd ./multi-model-debates/frontend
npm test -- --watchAll=false
```

### 前端构建

```bash
cd ./multi-model-debates/frontend
npm run build
```

### 当前依赖文件

- Python：`requirements.txt`
- Frontend：`frontend/package.json`

## 14. 参考项目与设计来源

这个项目不是照搬单一开源项目，而是按当前仓库目标做了混合设计。

### OpenClaw

参考点：

- README 的“先讲价值，再讲 quick start，再讲 docs by goal”的结构
- Provider / Auth / Runtime 分层思想
- 结构化事件和控制面思路
- 本地优先、工作区驱动的设计方向

项目地址：

- https://github.com/openclaw/openclaw

### Hermes Agent

参考点：

- README 的“Quick Install / Getting Started / Configuration / MCP / Architecture”组织方式
- provider-first 接入体验
- streaming tool output 和工具进度可视化思路
- 模型切换、技能和会话持续性的产品形态

项目地址：

- https://github.com/NousResearch/hermes-agent

### 本项目自己的取舍

本项目最终采用的是：

- 架构分层更接近 OpenClaw
- Provider 体验和认证交互更接近 Hermes
- 前端控制台和工作区可视化则贴合当前仓库自身需求

## 15. 常见问题

### 15.1 前端提示 `Could not proxy request /api/... ECONNREFUSED`

说明前端起来了，但后端没在监听 `127.0.0.1:8000`。

检查后端是否有这行：

```text
Uvicorn running on http://127.0.0.1:8000
```

### 15.2 为什么 `npm start` 在 WSL + `/mnt/d/...` 下启动很慢

因为：

- `react-scripts` 本身就比 Vite 慢
- TypeScript 会参与初始编译
- `/mnt/d` 是 Windows 文件系统挂载到 WSL，Node 大量小文件扫描会明显变慢

更快的方式：

- 把项目放到 WSL 原生文件系统
- 或前端改用 Windows 的 Node 在 Windows 侧运行

### 15.3 为什么模型能看到文件内容，但右侧之前看不到正文

原理上一直是：

- 后端会读取选中的文件并拼进 prompt

后来前端才补上：

- 目录树浏览
- 点击文件读取正文

所以“模型能看到、前端一开始看不到”是历史实现阶段差异，不是模型直接读磁盘。

### 15.4 为什么会出现 `SSE 请求超时`

当前前端已经把超时做成“空闲超时”而不是绝对时间超时。如果仍然出现，通常意味着：

- 后端该轮没有持续发事件
- Provider 调用卡住
- 或后端服务已断开

### 15.5 为什么会话刷新后还是丢

正常情况下不会。

需要确认：

- 你是否真的在当前主目录运行，而不是别的 worktree
- 浏览器没有清空本地存储
- 后端数据库 `multi_model_debate.db` 没被删除

### 15.6 `pytest` 结束后有 Windows `PermissionError`

这是当前已知的测试退出阶段清理问题。  
如果测试主体已经输出 `xxx passed`，这个错误通常发生在临时目录清理阶段，不代表功能测试失败。

### 15.7 WSL 下如何配置代理环境变量

示例：

```bash
export HTTP_PROXY=http://代理地址:端口
export HTTPS_PROXY=http://代理地址:端口
export NO_PROXY=127.0.0.1,localhost,::1
```

如果代理运行在 Windows 上，WSL 里要用 Windows 网关地址，而不一定是 `127.0.0.1`。

---

如果你准备继续扩展这个项目，建议先读这几个文件：

- `backend/api.py`
- `backend/orchestrator.py`
- `backend/llm_gateway.py`
- `frontend/src/App.tsx`
- `frontend/src/WorkspaceMode.tsx`

它们基本覆盖了运行入口、调度主线、Provider 路由、前端状态和工作区能力。
