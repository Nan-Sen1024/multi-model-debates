import React, {
  FormEvent,
  startTransition,
  useDeferredValue,
  useEffect,
  useRef,
  useState,
} from "react";

import {
  bindAwsRole,
  createProvider,
  createSession,
  deleteProvider,
  exportSessionHistory,
  getAuthStatus,
  getSession,
  getSnapshot,
  healthCheckProvider,
  listProviders,
  openSessionStream,
  patchSnapshot,
  sendUserMessage,
  startAuthFlow,
  updateProvider,
} from "./api";
import { API_FORMATS, AUTH_TYPES, MODE_OPTIONS, PROVIDER_TYPES } from "./modeOptions";
import {
  AuthFlowState,
  ChatMessage,
  CollaborationMode,
  ParticipantConfig,
  ProviderRecord,
  SessionDetail,
  SessionSnapshot,
  StreamPayload,
} from "./types";

// ─── Mode icons ──────────────────────────────────────────────────────────────
const MODE_ICONS: Record<string, string> = {
  chat: "💬", brainstorm: "🧠", code_collaboration: "💻", data_analysis: "📊",
  debate: "⚔️", werewolf: "🐺", murder_mystery: "🔍", undercover: "🕵️",
  mock_trial: "⚖️", role_play: "🎭", socratic_dialogue: "🏛️", peer_review: "📝",
  mock_interview: "🎤", story_chain: "📖", negotiation: "🤝",
};

// ─── Toast ────────────────────────────────────────────────────────────────────
interface Toast { id: number; message: string; kind: "success" | "error" | "info" }

let toastCounter = 0;

function useToasts() {
  const [toasts, setToasts] = useState<Toast[]>([]);
  function push(message: string, kind: Toast["kind"] = "info") {
    const id = ++toastCounter;
    setToasts((t) => [...t, { id, message, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3000);
  }
  return { toasts, push };
}

// ─── Initial state ────────────────────────────────────────────────────────────
const initialParticipants: ParticipantConfig[] = [
  { custom_id: "Model_A", model_ref: "openai/gpt-4o", role_desc: "" },
  { custom_id: "Model_B", model_ref: "anthropic/claude-3-5-sonnet-20241022", role_desc: "" },
];

const initialSnapshot: SessionSnapshot = {
  topic: "", mode: "chat", participant_summaries: {}, consensus_list: [], key_events: [],
};

// ─── App ──────────────────────────────────────────────────────────────────────
export default function App(): JSX.Element {
  const [activeTab, setActiveTab] = useState<0 | 1 | 2 | 3>(0);

  // Session state
  const [topic, setTopic] = useState("比较两种缓存失效策略在高并发接口中的优劣");
  const [mode, setMode] = useState<CollaborationMode>("debate");
  const [participants, setParticipants] = useState<ParticipantConfig[]>(initialParticipants);
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [snapshot, setSnapshot] = useState<SessionSnapshot>(initialSnapshot);
  const [snapshotOpen, setSnapshotOpen] = useState(true);
  const [historyExport, setHistoryExport] = useState("");
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  // Provider state
  const [providers, setProviders] = useState<ProviderRecord[]>([]);
  const [providerHealth, setProviderHealth] = useState<Record<string, boolean | null>>({});
  const [providerDraft, setProviderDraft] = useState({
    name: "", provider_type: "openai", base_url: "", api_format: API_FORMATS[0],
    auth_type: "api_key", auth_value: "", fallback_ids: "",
  });

  // Auth flows
  const [authFlows, setAuthFlows] = useState<Record<string, AuthFlowState>>({});
  const [awsRoleSelection, setAwsRoleSelection] = useState<Record<string, { accountId: string; roleName: string }>>({});
  const pollTimers = useRef<Record<string, ReturnType<typeof setInterval>>>({});

  const { toasts, push } = useToasts();
  const deferredMessages = useDeferredValue(messages);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => { void reloadProviders(); }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [deferredMessages]);

  // ── Providers ──────────────────────────────────────────────────────────────
  async function reloadProviders() {
    try {
      const list = await listProviders();
      setProviders(Array.isArray(list) ? list : []);
    } catch (err) {
      push((err as Error).message, "error");
      setProviders([]);
    }
  }

  async function handleCreateProvider(e: FormEvent) {
    e.preventDefault();
    try {
      await createProvider({
        ...providerDraft,
        fallback_ids: providerDraft.fallback_ids.split(",").map((s) => s.trim()).filter(Boolean),
      });
      setProviderDraft({ name: "", provider_type: "openai", base_url: "", api_format: API_FORMATS[0], auth_type: "api_key", auth_value: "", fallback_ids: "" });
      await reloadProviders();
      push("Provider 已创建", "success");
    } catch (err) {
      push((err as Error).message, "error");
    }
  }

  async function handleProviderHealth(providerId: string) {
    setProviderHealth((h) => ({ ...h, [providerId]: null }));
    try {
      const result = await healthCheckProvider(providerId);
      setProviderHealth((h) => ({ ...h, [providerId]: result.healthy }));
    } catch {
      setProviderHealth((h) => ({ ...h, [providerId]: false }));
    }
  }

  async function handleDeleteProvider(providerId: string) {
    if (!window.confirm("确认删除该 Provider？")) return;
    try {
      await deleteProvider(providerId);
      setAuthFlows((prev) => { const next = { ...prev }; delete next[providerId]; return next; });
      await reloadProviders();
      push("Provider 已删除", "success");
    } catch (err) { push((err as Error).message, "error"); }
  }

  async function handleUpdateProvider(providerId: string, draft: typeof providerDraft) {
    try {
      await updateProvider(providerId, {
        ...draft,
        fallback_ids: draft.fallback_ids.split(",").map((s) => s.trim()).filter(Boolean),
      });
      // 清掉旧的认证流状态，让登录按钮重新出现
      setAuthFlows((prev) => { const next = { ...prev }; delete next[providerId]; return next; });
      await reloadProviders();
      push("Provider 已更新", "success");
    } catch (err) { push((err as Error).message, "error"); }
  }

  // ── Auth flows ─────────────────────────────────────────────────────────────
  async function handleStartAuthFlow(
    providerId: string,
    flowType: "aws_iam" | "openai_codex" | "generic_oauth",
    extra: Record<string, string> = {},
  ) {
    try {
      const result = await startAuthFlow(providerId, { flow_type: flowType, ...extra } as Parameters<typeof startAuthFlow>[1]);
      const flowState: AuthFlowState = {
        authSessionId: result.auth_session_id, verificationUri: result.verification_uri,
        userCode: result.user_code, expiresIn: result.expires_in, status: "pending", flowType: result.flow_type,
      };
      setAuthFlows((prev) => ({ ...prev, [providerId]: flowState }));
      const interval = result.interval * 1000;
      const timer = setInterval(async () => {
        try {
          const status = await getAuthStatus(providerId, result.auth_session_id);
          setAuthFlows((prev) => ({ ...prev, [providerId]: { ...prev[providerId], status: status.status, accounts: status.accounts, errorMessage: status.error_message } }));
          if (status.status !== "pending") {
            clearInterval(pollTimers.current[providerId]);
            delete pollTimers.current[providerId];
            if (status.status === "completed") { push(`Provider ${providerId} 认证完成！`, "success"); await reloadProviders(); }
          }
        } catch { /* ignore */ }
      }, interval);
      pollTimers.current[providerId] = timer;
    } catch (err) { push((err as Error).message, "error"); }
  }

  async function handleBindAwsRole(providerId: string) {
    const flow = authFlows[providerId];
    const sel = awsRoleSelection[providerId];
    if (!flow || !sel?.accountId || !sel?.roleName) { push("请先选择账号和角色", "error"); return; }
    try {
      await bindAwsRole(providerId, flow.authSessionId, sel.accountId, sel.roleName);
      setAuthFlows((prev) => ({ ...prev, [providerId]: { ...prev[providerId], status: "completed" } }));
      push(`AWS 角色绑定成功：${sel.accountId} / ${sel.roleName}`, "success");
      await reloadProviders();
    } catch (err) { push((err as Error).message, "error"); }
  }

  // ── Session ────────────────────────────────────────────────────────────────
  async function handleCreateSession(e: FormEvent) {
    e.preventDefault();
    setLoading(true);
    try {
      const created = await createSession({ topic, mode, participants });
      const [detail, snap] = await Promise.all([getSession(created.id), getSnapshot(created.id)]);
      setSession(detail);
      setSnapshot(snap);
      setMessages([{ id: `sys-${Date.now()}`, senderId: "system", type: "system", content: `会话 ${created.id} 已创建，模式：${created.mode}`, round: 0, status: "done" }]);
      setHistoryExport("");
      setActiveTab(3);
      push("会话创建成功，已跳转到会话详情", "success");
    } catch (err) {
      push((err as Error).message, "error");
    } finally {
      setLoading(false);
    }
  }

  async function handleSendMessage(e: FormEvent) {
    e.preventDefault();
    if (!session || !input.trim()) return;
    const content = input.trim();
    setInput("");
    setMessages((cur) => [...cur, { id: `user-${Date.now()}`, senderId: "[用户]", type: "user", content, round: session.current_round, status: "done" }]);
    try {
      await sendUserMessage(session.id, content);
      const [detail, snap] = await Promise.all([getSession(session.id), getSnapshot(session.id)]);
      setSession(detail);
      setSnapshot(snap);
    } catch (err) { push((err as Error).message, "error"); }
  }

  async function handleSaveSnapshot() {
    if (!session) return;
    try {
      const updated = await patchSnapshot(session.id, snapshot);
      setSnapshot(updated);
      push("快照已保存", "success");
    } catch (err) { push((err as Error).message, "error"); }
  }

  async function handleExportHistory() {
    if (!session) return;
    try {
      const data = await exportSessionHistory(session.id);
      setHistoryExport(data.content);
      push("历史已导出", "success");
    } catch (err) { push((err as Error).message, "error"); }
  }

  // ── Stream events ──────────────────────────────────────────────────────────
  function handleStreamEvent(eventName: string, payload: StreamPayload) {
    if (eventName === "chunk") {
      setMessages((cur) => {
        const senderId = payload.participant_id || "Unknown";
        const last = cur[cur.length - 1];
        if (last && last.type === "model" && last.senderId === senderId && last.status === "streaming") {
          return [...cur.slice(0, -1), { ...last, content: `${last.content}${payload.content || ""}` }];
        }
        return [...cur, { id: `${senderId}-${Date.now()}`, senderId, type: "model", content: payload.content || "", round: payload.round || 0, status: "streaming" }];
      });
      return;
    }
    if (eventName === "turn_end") {
      setMessages((cur) => cur.map((m, i) => i === cur.length - 1 ? { ...m, status: "done" } : m));
      return;
    }
    if (eventName === "drift_alert") {
      setMessages((cur) => cur.map((m) => m.senderId === payload.participant_id ? { ...m, driftScore: payload.score, status: "warning" } : m));
      push(`检测到 ${payload.participant_id} 可能偏题，分数 ${payload.score?.toFixed(2) ?? "未知"}`, "info");
      return;
    }
    if (eventName === "compression") { push(`上下文压缩：${payload.action || "unknown"}`, "info"); return; }
    if (eventName === "session_end") {
      setMessages((cur) => [...cur, { id: `end-${Date.now()}`, senderId: "system", type: "system", content: `会话结束：${payload.reason || "unknown"}\n${payload.summary || ""}`, round: payload.round || 0, status: "done" }]);
      return;
    }
    if (eventName === "error") {
      setMessages((cur) => [...cur, { id: `err-${Date.now()}`, senderId: payload.participant_id || "system", type: "system", content: payload.message || "调度异常", round: payload.round || 0, status: "error" }]);
    }
  }

  // ── Participants ───────────────────────────────────────────────────────────
  function updateParticipant(index: number, patch: Partial<ParticipantConfig>) {
    setParticipants((cur) => cur.map((p, i) => i === index ? { ...p, ...patch } : p));
  }
  function addParticipant() {
    setParticipants((cur) => [...cur, { custom_id: `Model_${cur.length + 1}`, model_ref: "openai/gpt-4o", role_desc: "" }]);
  }
  function removeParticipant(index: number) {
    setParticipants((cur) => cur.filter((_, i) => i !== index));
  }

  // ── Render ─────────────────────────────────────────────────────────────────
  const tabs: Array<{ label: string; index: 0 | 1 | 2 | 3; disabled?: boolean }> = [
    { label: "🚀 快速开始", index: 0 },
    { label: "⚙️ Provider 配置", index: 1 },
    { label: "💬 创建会话", index: 2 },
    { label: "📊 会话详情", index: 3, disabled: !session },
  ];

  return (
    <div className="app-shell">
      {/* Header */}
      <header className="hero">
        <div>
          <p className="eyebrow">Multi-Model Debate Console</p>
          <h1>多模型协作控制台</h1>
          <p className="subtitle">配置 Provider、创建会话、实时 SSE 对话，一站完成。</p>
        </div>

      </header>

      {/* Tab nav */}
      <nav className="tab-nav">
        {tabs.map((t) => (
          <button
            key={t.index}
            className={`tab-btn${activeTab === t.index ? " tab-btn-active" : ""}${t.disabled ? " tab-btn-disabled" : ""}`}
            onClick={() => !t.disabled && setActiveTab(t.index as 0 | 1 | 2 | 3)}
            disabled={t.disabled}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {/* Tab 0 – Quick Start */}
      {activeTab === 0 && <TabQuickStart onNavigate={setActiveTab} />}

      {/* Tab 1 – Provider Config */}
      {activeTab === 1 && (
        <TabProviders
          providers={providers}
          providerDraft={providerDraft}
          setProviderDraft={setProviderDraft}
          providerHealth={providerHealth}
          authFlows={authFlows}
          awsRoleSelection={awsRoleSelection}
          setAwsRoleSelection={setAwsRoleSelection}
          onCreateProvider={handleCreateProvider}
          onHealthCheck={handleProviderHealth}
          onStartAuthFlow={handleStartAuthFlow}
          onBindAwsRole={handleBindAwsRole}
          onDeleteProvider={handleDeleteProvider}
          onUpdateProvider={handleUpdateProvider}
        />
      )}

      {/* Tab 2 – Create Session */}
      {activeTab === 2 && (
        <TabCreateSession
          topic={topic} setTopic={setTopic}
          mode={mode} setMode={setMode}
          participants={participants}
          providers={providers}
          loading={loading}
          onUpdateParticipant={updateParticipant}
          onAddParticipant={addParticipant}
          onRemoveParticipant={removeParticipant}
          onSubmit={handleCreateSession}
        />
      )}

      {/* Tab 3 – Session Detail */}
      {activeTab === 3 && session && (
        <TabSessionDetail
          session={session}
          messages={deferredMessages}
          snapshot={snapshot}
          setSnapshot={setSnapshot}
          snapshotOpen={snapshotOpen}
          setSnapshotOpen={setSnapshotOpen}
          historyExport={historyExport}
          input={input}
          setInput={setInput}
          messagesEndRef={messagesEndRef}
          onSendMessage={handleSendMessage}
          onSaveSnapshot={handleSaveSnapshot}
          onExportHistory={handleExportHistory}
          onStreamEvent={handleStreamEvent}
        />
      )}

      {/* Toast container */}
      <div className="toast-container">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`}>{t.message}</div>
        ))}
      </div>
    </div>
  );
}

// ─── Tab 0: Quick Start ───────────────────────────────────────────────────────
function TabQuickStart({ onNavigate }: { onNavigate: (tab: 0 | 1 | 2 | 3) => void }) {
  return (
    <div className="tab-content">
      <div className="quickstart-grid">
        <div className="qs-steps">
          <h2 className="section-title">使用流程</h2>
          <div className="step-list">
            <div className="step-card">
              <div className="step-num">1</div>
              <div className="step-body">
                <h3>配置 Provider</h3>
                <p>点击 <strong>⚙️ Provider 配置</strong> Tab，填写名称、类型、Base URL 和 API Key，点击"保存 Provider"。可点击"健康检查"验证连通性。</p>
                <button className="step-link" onClick={() => onNavigate(1)}>前往 Provider 配置 →</button>
              </div>
            </div>
            <div className="step-card">
              <div className="step-num">2</div>
              <div className="step-body">
                <h3>创建会话</h3>
                <p>点击 <strong>💬 创建会话</strong> Tab，填写 Topic，选择协作模式（15 种可选），配置至少 2 个参与者（每个指定 Model_Ref），点击"创建会话"。</p>
                <button className="step-link" onClick={() => onNavigate(2)}>前往创建会话 →</button>
              </div>
            </div>
            <div className="step-card">
              <div className="step-num">3</div>
              <div className="step-body">
                <h3>开始对话</h3>
                <p>会话创建后自动跳转到 <strong>📊 会话详情</strong> Tab。点击"▶ 开始下一轮"触发模型发言，在底部输入框发送用户消息，实时查看 SSE 流式输出。</p>
              </div>
            </div>
          </div>
        </div>

        <div className="qs-sidebar">
          <div className="info-card">
            <h3>常见 Model_Ref 示例</h3>
            <ul className="model-ref-list">
              {[
                { ref: "openai/gpt-4o", note: "OpenAI" },
                { ref: "anthropic/claude-3-5-sonnet-20241022", note: "Anthropic" },
                { ref: "ollama/llama3.3", note: "本地，无需 API Key" },
                { ref: "google/gemini-pro", note: "Google" },
                { ref: "groq/llama-3.1-70b-versatile", note: "Groq" },
              ].map(({ ref, note }) => (
                <li key={ref}>
                  <code>{ref}</code>
                  <span>{note}</span>
                </li>
              ))}
            </ul>
          </div>

          <div className="info-card">
            <h3>本地 Ollama 提示</h3>
            <p>无需填写 API Key，Base URL 填：</p>
            <code className="url-code">http://127.0.0.1:11434/v1</code>
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Tab 1: Provider Config ───────────────────────────────────────────────────
interface TabProvidersProps {
  providers: ProviderRecord[];
  providerDraft: { name: string; provider_type: string; base_url: string; api_format: string; auth_type: string; auth_value: string; fallback_ids: string };
  setProviderDraft: React.Dispatch<React.SetStateAction<TabProvidersProps["providerDraft"]>>;
  providerHealth: Record<string, boolean | null>;
  authFlows: Record<string, AuthFlowState>;
  awsRoleSelection: Record<string, { accountId: string; roleName: string }>;
  setAwsRoleSelection: React.Dispatch<React.SetStateAction<Record<string, { accountId: string; roleName: string }>>>;
  onCreateProvider: (e: FormEvent) => void;
  onHealthCheck: (id: string) => void;
  onStartAuthFlow: (id: string, type: "aws_iam" | "openai_codex" | "generic_oauth", extra?: Record<string, string>) => void;
  onBindAwsRole: (id: string) => void;
  onDeleteProvider: (id: string) => void;
  onUpdateProvider: (id: string, draft: { name: string; provider_type: string; base_url: string; api_format: string; auth_type: string; auth_value: string; fallback_ids: string }) => void;
}

function TabProviders({
  providers, providerDraft, setProviderDraft, providerHealth,
  authFlows, awsRoleSelection, setAwsRoleSelection,
  onCreateProvider, onHealthCheck, onStartAuthFlow, onBindAwsRole,
  onDeleteProvider, onUpdateProvider,
}: TabProvidersProps) {
  const isApiKey = providerDraft.auth_type === "api_key";
  const isIam = providerDraft.auth_type === "iam";
  const isOauth = providerDraft.auth_type === "oauth";

  // 编辑状态：provider_id -> draft
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editDraft, setEditDraft] = useState<TabProvidersProps["providerDraft"] | null>(null);

  function startEdit(p: ProviderRecord) {
    setEditingId(p.id);
    setEditDraft({ name: p.name, provider_type: p.provider_type, base_url: p.base_url || "", api_format: p.api_format, auth_type: p.auth_type, auth_value: "", fallback_ids: p.fallback_ids.join(",") });
  }

  function cancelEdit() { setEditingId(null); setEditDraft(null); }

  function saveEdit(id: string) {
    if (editDraft) { onUpdateProvider(id, editDraft); setEditingId(null); setEditDraft(null); }
  }

  return (
    <div className="tab-content">
      <div className="provider-layout">
        {/* Left: form */}
        <div className="panel">
          <div className="panel-head">
            <h2>添加 Provider</h2>
          </div>
          <form className="stack" onSubmit={onCreateProvider}>
            <div className="form-grid-2">
              <label className="field">
                <span>名称</span>
                <input value={providerDraft.name} onChange={(e) => setProviderDraft((d) => ({ ...d, name: e.target.value }))} placeholder="my-openai" required />
              </label>
              <label className="field">
                <span>Provider 类型</span>
                <select value={providerDraft.provider_type} onChange={(e) => setProviderDraft((d) => ({ ...d, provider_type: e.target.value }))}>
                  {PROVIDER_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                </select>
              </label>
              <label className="field">
                <span>Base URL</span>
                <input value={providerDraft.base_url} onChange={(e) => setProviderDraft((d) => ({ ...d, base_url: e.target.value }))} placeholder="https://api.openai.com/v1" />
              </label>
              <label className="field">
                <span>API Format</span>
                <select value={providerDraft.api_format} onChange={(e) => setProviderDraft((d) => ({ ...d, api_format: e.target.value }))}>
                  {API_FORMATS.map((f) => <option key={f} value={f}>{f}</option>)}
                </select>
              </label>
              <label className="field">
                <span>Auth Type</span>
                <select value={providerDraft.auth_type} onChange={(e) => setProviderDraft((d) => ({ ...d, auth_type: e.target.value }))}>
                  {AUTH_TYPES.map((a) => <option key={a} value={a}>{a}</option>)}
                </select>
              </label>
              <label className="field">
                <span>Auth Value / API Key</span>
                <input
                  value={providerDraft.auth_value}
                  onChange={(e) => setProviderDraft((d) => ({ ...d, auth_value: e.target.value }))}
                  type={isApiKey ? "password" : "text"}
                  placeholder={isApiKey ? "sk-..." : isIam ? "（IAM 登录后自动填充）" : isOauth ? "（OAuth 登录后自动填充）" : "bearer token / helper path"}
                  disabled={isIam || isOauth}
                />
              </label>
            </div>
            <label className="field">
              <span>Fallback IDs（逗号分隔）</span>
              <input value={providerDraft.fallback_ids} onChange={(e) => setProviderDraft((d) => ({ ...d, fallback_ids: e.target.value }))} placeholder="id-1,id-2" />
            </label>
            <p className="hint-text">💡 本地 Ollama 无需填写 API Key，Base URL 填 http://127.0.0.1:11434/v1</p>
            <div className="row-actions">
              <button type="submit" className="primary-button">保存 Provider</button>
            </div>
          </form>
        </div>

        {/* Right: list */}
        <div className="panel">
          <div className="panel-head">
            <h2>已配置 Provider</h2>
            <span className="badge">{providers.length}</span>
          </div>
          {providers.length === 0
            ? <div className="empty-state">还没有 Provider，请在左侧添加。</div>
            : (
              <div className="provider-cards">
                {providers.map((p) => {
                  const health = providerHealth[p.id];
                  const flow = authFlows[p.id];
                  return (
                    <div className="provider-card" key={p.id}>
                      <div className="provider-card-head">
                        <div>
                          <strong>{p.name}</strong>
                          <span className="tag">{p.provider_type}</span>
                        </div>
                        <div className="provider-card-actions">
                          {health === true && <span className="health-ok">✓ 可用</span>}
                          {health === false && <span className="health-fail">✗ 不可用</span>}
                          {health === null && <span className="health-checking">检查中…</span>}
                          <button className="ghost-button small" onClick={() => onHealthCheck(p.id)}>健康检查</button>
                          <button className="ghost-button small" onClick={() => startEdit(p)}>编辑</button>
                          <button className="ghost-button small danger" onClick={() => onDeleteProvider(p.id)}>删除</button>
                          {p.auth_type === "iam" && (!flow || flow.status === "failed" || flow.status === "expired") && (
                            <button className="ghost-button small" onClick={() => {
                              const url = prompt("SSO Start URL") || "";
                              const region = prompt("SSO Region", "us-east-1") || "us-east-1";
                              if (url) onStartAuthFlow(p.id, "aws_iam", { sso_start_url: url, sso_region: region });
                            }}>AWS 登录</button>
                          )}
                          {p.auth_type === "oauth" && (!flow || flow.status === "failed" || flow.status === "expired") && (
                            <button className="ghost-button small" onClick={() => onStartAuthFlow(p.id, "openai_codex")}>Codex 登录</button>
                          )}
                        </div>
                      </div>

                      {/* 编辑模式 */}
                      {editingId === p.id && editDraft ? (
                        <div className="edit-form">
                          <div className="form-grid-2">
                            <label className="field"><span>名称</span><input value={editDraft.name} onChange={(e) => setEditDraft((d) => d && ({ ...d, name: e.target.value }))} /></label>
                            <label className="field"><span>Provider 类型</span><select value={editDraft.provider_type} onChange={(e) => setEditDraft((d) => d && ({ ...d, provider_type: e.target.value }))}>{PROVIDER_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}</select></label>
                            <label className="field"><span>Base URL</span><input value={editDraft.base_url} onChange={(e) => setEditDraft((d) => d && ({ ...d, base_url: e.target.value }))} /></label>
                            <label className="field"><span>API Format</span><select value={editDraft.api_format} onChange={(e) => setEditDraft((d) => d && ({ ...d, api_format: e.target.value }))}>{API_FORMATS.map((f) => <option key={f} value={f}>{f}</option>)}</select></label>
                            <label className="field"><span>Auth Type</span><select value={editDraft.auth_type} onChange={(e) => setEditDraft((d) => d && ({ ...d, auth_type: e.target.value }))}>{AUTH_TYPES.map((a) => <option key={a} value={a}>{a}</option>)}</select></label>
                            <label className="field"><span>Auth Value</span><input type={editDraft.auth_type === "api_key" ? "password" : "text"} value={editDraft.auth_value} onChange={(e) => setEditDraft((d) => d && ({ ...d, auth_value: e.target.value }))} placeholder="留空则不修改" /></label>
                          </div>
                          <div className="row-actions" style={{ marginTop: 8 }}>
                            <button className="ghost-button small" onClick={cancelEdit}>取消</button>
                            <button className="primary-button small" onClick={() => saveEdit(p.id)}>保存修改</button>
                          </div>
                        </div>
                      ) : (
                        <div className="provider-meta-row">
                          <span>{p.api_format}</span>
                          <span>{p.auth_type}</span>
                          <span className="muted-text">{p.base_url || "default endpoint"}</span>
                        </div>
                      )}
                      {flow && flow.status === "pending" && (
                        <div className="auth-flow-box">
                          <p>🔐 请在浏览器完成授权：</p>
                          <a href={flow.verificationUri} target="_blank" rel="noreferrer" className="auth-link">{flow.verificationUri}</a>
                          <p>设备码：<code>{flow.userCode}</code></p>
                          <p className="muted-text">轮询中，请稍候…</p>
                        </div>
                      )}
                      {flow && flow.status === "awaiting_role" && (
                        <div className="auth-flow-box">
                          <p>✅ 授权完成，请选择 AWS 账号和角色：</p>
                          <select value={(awsRoleSelection[p.id] || {}).accountId || ""} onChange={(e) => setAwsRoleSelection((prev) => ({ ...prev, [p.id]: { ...(prev[p.id] || { roleName: "" }), accountId: e.target.value } }))}>
                            <option value="">选择账号</option>
                            {(flow.accounts || []).map((acc) => <option key={acc.accountId} value={acc.accountId}>{acc.accountName} ({acc.accountId})</option>)}
                          </select>
                          <input placeholder="角色名称，如 AdministratorAccess" value={(awsRoleSelection[p.id] || {}).roleName || ""} onChange={(e) => setAwsRoleSelection((prev) => ({ ...prev, [p.id]: { ...(prev[p.id] || { accountId: "" }), roleName: e.target.value } }))} />
                          <button className="primary-button small" onClick={() => onBindAwsRole(p.id)}>绑定角色</button>
                        </div>
                      )}
                      {flow && flow.status === "completed" && <div className="auth-success">✅ 认证完成</div>}
                      {flow && flow.status === "failed" && <div className="auth-error">❌ 认证失败：{flow.errorMessage}</div>}
                    </div>
                  );
                })}
              </div>
            )}
        </div>
      </div>
    </div>
  );
}

// ─── Tab 2: Create Session ────────────────────────────────────────────────────
interface TabCreateSessionProps {
  topic: string; setTopic: (v: string) => void;
  mode: CollaborationMode; setMode: (v: CollaborationMode) => void;
  participants: ParticipantConfig[];
  providers: ProviderRecord[];
  loading: boolean;
  onUpdateParticipant: (i: number, p: Partial<ParticipantConfig>) => void;
  onAddParticipant: () => void;
  onRemoveParticipant: (i: number) => void;
  onSubmit: (e: FormEvent) => void;
}

function TabCreateSession({
  topic, setTopic, mode, setMode, participants, providers, loading,
  onUpdateParticipant, onAddParticipant, onRemoveParticipant, onSubmit,
}: TabCreateSessionProps) {
  return (
    <div className="tab-content">
      <form className="stack" onSubmit={onSubmit}>
        {/* Topic */}
        <div className="panel">
          <label className="field">
            <span className="field-label">讨论主题 Topic</span>
            <textarea
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              rows={3}
              placeholder="例：比较 Redis 和 Memcached 在高并发场景下的优劣，或：请扮演一场关于 AI 监管的辩论"
              required
            />
          </label>
        </div>

        {/* Mode */}
        <div className="panel">
          <h3 className="section-title">协作模式</h3>
          <div className="mode-grid">
            {MODE_OPTIONS.map((opt) => (
              <button
                type="button"
                key={opt.value}
                className={`mode-card${opt.value === mode ? " mode-card-active" : ""}`}
                onClick={() => setMode(opt.value)}
              >
                <span className="mode-icon">{MODE_ICONS[opt.value] || "🤖"}</span>
                <strong>{opt.label}</strong>
                <span>{opt.blurb}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Participants */}
        <div className="panel">
          <div className="panel-head">
            <h3 className="section-title" style={{ margin: 0 }}>参与者配置</h3>
            <span className="badge">{participants.length} 个</span>
          </div>
          <div className="participant-list">
            {participants.map((p, i) => (
              <div className="participant-card" key={`${p.custom_id}-${i}`}>
                <div className="participant-card-head">
                  <strong>参与者 {i + 1}</strong>
                  <button type="button" className="ghost-button small danger" onClick={() => onRemoveParticipant(i)} disabled={participants.length <= 2}>删除</button>
                </div>
                <div className="form-grid-2">
                  <label className="field">
                    <span>Custom_ID</span>
                    <input value={p.custom_id} onChange={(e) => onUpdateParticipant(i, { custom_id: e.target.value })} placeholder="Model_A" />
                  </label>
                  <label className="field">
                    <span>Provider（可选）</span>
                    <select value={p.provider_id || ""} onChange={(e) => onUpdateParticipant(i, { provider_id: e.target.value || undefined })}>
                      <option value="">自动匹配（从 Model_Ref 推断）</option>
                      {providers.map((pv) => <option key={pv.id} value={pv.id}>{pv.name} ({pv.provider_type})</option>)}
                    </select>
                  </label>
                  <label className="field">
                    <span>Model_Ref</span>
                    <input value={p.model_ref} onChange={(e) => onUpdateParticipant(i, { model_ref: e.target.value })} placeholder="openai/gpt-4o" />
                  </label>
                  <label className="field">
                    <span>Role（可选角色描述）</span>
                    <input value={p.role_desc || ""} onChange={(e) => onUpdateParticipant(i, { role_desc: e.target.value })} placeholder="正方辩手 / 代码审查者 / 侦探…" />
                  </label>
                </div>
              </div>
            ))}
          </div>
          <div className="row-actions" style={{ marginTop: 12 }}>
            <button type="button" className="ghost-button" onClick={onAddParticipant}>＋ 添加参与者</button>
            <button type="submit" className="primary-button" disabled={loading}>
              {loading ? "创建中…" : "🚀 创建会话"}
            </button>
          </div>
        </div>
      </form>
    </div>
  );
}

// ─── Tab 3: Session Detail ────────────────────────────────────────────────────
interface TabSessionDetailProps {
  session: SessionDetail;
  messages: ChatMessage[];
  snapshot: SessionSnapshot;
  setSnapshot: React.Dispatch<React.SetStateAction<SessionSnapshot>>;
  snapshotOpen: boolean;
  setSnapshotOpen: (v: boolean) => void;
  historyExport: string;
  input: string;
  setInput: (v: string) => void;
  messagesEndRef: React.RefObject<HTMLDivElement>;
  onSendMessage: (e: FormEvent) => void;
  onSaveSnapshot: () => void;
  onExportHistory: () => void;
  onStreamEvent: (eventName: string, payload: StreamPayload) => void;
}

function TabSessionDetail({
  session, messages, snapshot, setSnapshot, snapshotOpen, setSnapshotOpen,
  historyExport, input, setInput, messagesEndRef,
  onSendMessage, onSaveSnapshot, onExportHistory, onStreamEvent,
}: TabSessionDetailProps) {
  const [streaming, setStreaming] = useState(false);
  const closeStreamRef = useRef<(() => void) | null>(null);

  function handleNextRound() {
    if (streaming) {
      closeStreamRef.current?.();
      setStreaming(false);
      return;
    }
    setStreaming(true);
    // 每次调用 GET /stream 都会触发后端 dispatch_next 调度一轮
    const close = openSessionStream(session.id, (eventName, payload) => {
      startTransition(() => onStreamEvent(eventName, payload));
      // 一轮结束后自动停止 streaming 状态
      if (eventName === "turn_end" || eventName === "session_end") {
        setStreaming(false);
      }
    });
    closeStreamRef.current = close;
    // 60秒超时保底
    setTimeout(() => {
      close();
      setStreaming(false);
    }, 60000);
  }

  return (
    <div className="tab-content">
      <div className="session-layout">
        {/* Left: chat */}
        <div className="panel chat-panel-new">
          {/* Status bar */}
          <div className="session-status-bar">
            <span className="status-item"><span className="status-label">ID</span>{session.id.slice(0, 12)}…</span>
            <span className="status-item"><span className="status-label">模式</span>{session.mode}</span>
            <span className="status-item"><span className="status-label">轮次</span>{session.current_round}</span>
            <span className={`status-badge status-${session.status}`}>{session.status}</span>
          </div>

          {/* Messages */}
          <div className="message-stream">
            {messages.length === 0
              ? <div className="empty-state">点击"▶ 开始下一轮"触发模型发言，或在下方输入用户消息。</div>
              : messages.map((msg) => (
                <div key={msg.id} className={`bubble bubble-${msg.type}${msg.status === "warning" ? " bubble-warning" : ""}${msg.status === "error" ? " bubble-error" : ""}`}>
                  {msg.type !== "system" && (
                    <div className="bubble-meta">
                      <strong>{msg.senderId}</strong>
                      <span>Round {msg.round}</span>
                    </div>
                  )}
                  <div className="bubble-content">
                    {msg.content}
                    {msg.status === "streaming" && <span className="cursor-blink">▌</span>}
                  </div>
                  {typeof msg.driftScore === "number" && (
                    <div className="drift-flag">⚠ 偏题告警：{msg.driftScore.toFixed(2)}</div>
                  )}
                </div>
              ))
            }
            <div ref={messagesEndRef} />
          </div>

          {/* Actions */}
          <div className="chat-actions">
            <button
              type="button"
              className={streaming ? "ghost-button stop-btn" : "primary-button"}
              onClick={handleNextRound}
            >
              {streaming ? "⏹ 停止" : "▶ 开始下一轮"}
            </button>
            <button type="button" className="ghost-button" onClick={onExportHistory}>📥 导出历史</button>
          </div>

          {/* Composer */}
          <form className="composer" onSubmit={onSendMessage}>
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              rows={2}
              placeholder="输入用户消息，发送后由后端接力调度…"
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); onSendMessage(e as unknown as FormEvent); } }}
            />
            <button type="submit" className="primary-button send-btn" disabled={!input.trim()}>发送</button>
          </form>

          {/* Export */}
          {historyExport && (
            <div className="export-block">
              <div className="panel-head compact"><h3>导出历史</h3></div>
              <pre>{historyExport}</pre>
            </div>
          )}
        </div>

        {/* Right: snapshot */}
        <div className="panel snapshot-panel-new">
          <div className="panel-head">
            <h3>快照面板</h3>
            <button type="button" className="ghost-button small" onClick={() => setSnapshotOpen(!snapshotOpen)}>
              {snapshotOpen ? "收起" : "展开"}
            </button>
          </div>
          {snapshotOpen && (
            <div className="stack">
              <label className="field">
                <span>Topic</span>
                <textarea rows={2} value={snapshot.topic} onChange={(e) => setSnapshot((s) => ({ ...s, topic: e.target.value }))} />
              </label>
              <label className="field">
                <span>Mode</span>
                <select value={snapshot.mode} onChange={(e) => setSnapshot((s) => ({ ...s, mode: e.target.value as CollaborationMode }))}>
                  {MODE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
              <label className="field">
                <span>参与者摘要（每行 id: 摘要）</span>
                <textarea rows={5} value={Object.entries(snapshot.participant_summaries).map(([k, v]) => `${k}: ${v}`).join("\n")} onChange={(e) => setSnapshot((s) => ({ ...s, participant_summaries: e.target.value.split("\n").filter(Boolean).reduce<Record<string, string>>((acc, line) => { const [k, ...rest] = line.split(":"); if (k) acc[k.trim()] = rest.join(":").trim(); return acc; }, {}) }))} />
              </label>
              <label className="field">
                <span>共识列表（每行一条）</span>
                <textarea rows={4} value={snapshot.consensus_list.join("\n")} onChange={(e) => setSnapshot((s) => ({ ...s, consensus_list: e.target.value.split("\n").filter(Boolean) }))} />
              </label>
              <label className="field">
                <span>关键事件（每行一条）</span>
                <textarea rows={4} value={snapshot.key_events.join("\n")} onChange={(e) => setSnapshot((s) => ({ ...s, key_events: e.target.value.split("\n").filter(Boolean) }))} />
              </label>
              <button type="button" className="primary-button" onClick={onSaveSnapshot}>💾 保存快照</button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
