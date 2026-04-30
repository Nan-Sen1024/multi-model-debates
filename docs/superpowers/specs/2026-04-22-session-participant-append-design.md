# Existing Session Participant Append Design
日期：2026-04-22

## 1. 背景

当前项目已经支持：
- 创建会话时配置多个参与者
- 通过已认证 Provider 和自动同步模型目录来选择模型
- 在会话详情页重命名、删除会话

但当前不支持：
- 在现有会话中追加新的已认证模型参与者

这会带来两个直接问题：
1. 用户发现某个 Provider 认证成功后，无法把该模型加入正在使用的会话，只能新建会话。
2. `code_workspace` 模式里用户希望随着任务演进动态增加 reviewer / tester / coder 等角色，但当前参与者集合是创建时固定的。

## 2. 目标

1. 支持在现有 `active` 会话中追加新参与者。
2. 新参与者必须复用现有 Provider 绑定、认证状态和模型下拉，不引入手填模型名的额外路径。
3. 新参与者从下一轮开始参与，不插入当前正在生成中的半轮。
4. 普通模式和 `code_workspace` 模式都要支持。
5. 追加完成后，前端会话详情能立即刷新并展示新增参与者。

## 3. 非目标

1. 本次不支持删除会话内已有参与者。
2. 本次不支持修改已有参与者的模型、角色或顺序。
3. 本次不支持在流式生成过程中热插入到当前轮。
4. 本次不做批量编辑整份参与者列表的 UI。

## 4. 设计决策

### 4.1 API 采用 append-only

新增接口：

- `POST /api/sessions/{session_id}/participants`

请求体沿用创建会话时的参与者字段：
- `model_ref`
- `provider_id`
- `custom_id`
- `role_desc`

返回值沿用当前 `GET /api/sessions/{session_id}` 的 SessionDetail 结构，减少前端适配成本。

原因：
- 追加参与者是低风险增量能力；
- 不需要让前端一次性提交整份参与者清单；
- 能避免覆盖已有参与者状态。

### 4.2 运行时约束

只允许对 `active` 会话追加参与者。

如果该会话当前正在生成：
- 返回 409 类错误语义；
- 消息明确提示“请等待当前轮结束后再添加参与者”。

原因：
- 当前调度依赖 `next_speaker_index` 与当前参与者列表长度；
- 运行中修改列表会让当前轮边界和下一位发言人推断变得不稳定。

### 4.3 参与者校验

追加时沿用创建会话已有规则：
- `model_ref` 必须合法；
- `provider_id` 如果指定，必须存在；
- `custom_id` 必须在当前会话内唯一；
- 顺序号 `sequence_order` 为当前最大值 + 1。

### 4.4 code_workspace 兼容

`code_workspace` 模式下追加参与者时：
- 该参与者默认加入会话参与者列表；
- 如果 workspace capabilities 存在 `participant_overrides`，则不强制立即创建 override；
- 让新增参与者先走全局默认 agent / skill / mcp 配置。

原因：
- 这样能保持最小实现；
- 用户后续仍可在 workspace capability UI 里单独调整。

## 5. 数据流

1. 前端会话详情页点击“添加模型”
2. 选择 Provider、模型、别名、角色描述
3. 前端调用 `POST /api/sessions/{id}/participants`
4. 后端加锁加载 session，检查运行状态与唯一性
5. 后端写入 `model_participants`
6. 后端返回最新 SessionDetail
7. 前端用返回值替换当前 `session`
8. 前端在会话状态栏 / 右侧面板 / workspace panel 中展示新增参与者

## 6. 前端交互

会话详情页新增一个轻量追加面板：
- 默认折叠
- 字段与“创建会话 > 参与者配置”一致，但仅配置单个参与者
- 仅展示已加载的 Provider 与模型下拉
- 提交后清空表单并刷新当前会话

按钮行为：
- 非流式时可点
- 流式中禁用
- 无 session 时隐藏

## 7. 错误处理

后端返回明确错误消息：
- 会话不存在
- 会话不是 active
- 当前轮正在生成
- `custom_id` 重复
- `provider_id` 无效
- `model_ref` 无效

前端直接 toast 展示后端错误，不额外包装模糊文案。

## 8. 测试

后端：
- 新增参与者接口成功写入并返回更新后的会话
- 生成中追加参与者被拒绝
- `custom_id` 重复被拒绝
- `code_workspace` 会话追加参与者成功

前端：
- 会话详情页能打开追加面板并提交
- 成功后当前 session 参与者列表刷新
- 流式生成时按钮禁用

## 9. 影响文件

- `backend/api.py`
- `backend/orchestrator.py`
- `frontend/src/api.ts`
- `frontend/src/types.ts`
- `frontend/src/App.tsx`
- `tests/` 下新增或扩展 session append 相关后端测试
- `frontend/src/App.sessionManagement.test.tsx`
