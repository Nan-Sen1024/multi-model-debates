import React from "react";

import { previewWorkspace } from "./api";
import type {
  MCPServerConfig,
  ParticipantConfig,
  SessionWorkspaceView,
  WorkspaceCapabilityManifest,
  WorkspaceTreeEntry,
} from "./types";

export interface WorkspaceMCPDraft {
  name: string;
  transport: "stdio" | "streamable_http";
  command: string;
  args: string;
  url: string;
  env: string;
  toolsAllowlist: string;
  enabled: boolean;
}

export interface WorkspaceAgentDraft {
  mode: string;
  maxSteps: string;
  canWrite: boolean;
  allowedSkills: string;
  allowedMcpServers: string;
  memoryScope: string;
}

export interface WorkspaceParticipantOverrideDraft {
  skills: string;
  mcpServers: string;
  agentMode: string;
  agentMaxSteps: string;
  agentCanWrite: boolean;
  agentAllowedSkills: string;
  agentAllowedMcpServers: string;
  agentMemoryScope: string;
}

export interface WorkspaceDraftState {
  rootPath: string;
  displayName: string;
  selectedPaths: string;
  scanExcludes: string;
  skillSources: string;
  mcpServers: WorkspaceMCPDraft[];
  agent: WorkspaceAgentDraft;
  participantOverrides: Record<string, WorkspaceParticipantOverrideDraft>;
}

export type WorkspaceDraftUpdate =
  | Partial<WorkspaceDraftState>
  | ((current: WorkspaceDraftState) => WorkspaceDraftState);

interface WorkspaceCreatePanelProps {
  draft: WorkspaceDraftState;
  aliases: string[];
  onChange: (update: WorkspaceDraftUpdate) => void;
}

interface WorkspaceSessionPanelProps {
  workspace: SessionWorkspaceView | null;
  participants: ParticipantConfig[];
  capabilities?: WorkspaceCapabilityManifest | null;
}

export function createEmptyWorkspaceMCPDraft(): WorkspaceMCPDraft {
  return {
    name: "",
    transport: "stdio",
    command: "",
    args: "",
    url: "",
    env: "",
    toolsAllowlist: "",
    enabled: true,
  };
}

export function createEmptyWorkspaceParticipantOverrideDraft(): WorkspaceParticipantOverrideDraft {
  return {
    skills: "",
    mcpServers: "",
    agentMode: "",
    agentMaxSteps: "6",
    agentCanWrite: false,
    agentAllowedSkills: "",
    agentAllowedMcpServers: "",
    agentMemoryScope: "workspace_shared",
  };
}

function parseTextareaLines(raw: string): string[] {
  return raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

function serializeTextareaLines(lines: Iterable<string>): string {
  return Array.from(lines).sort((left, right) => left.localeCompare(right)).join("\n");
}

function parseEnvLines(raw: string): Record<string, string> {
  const entries: Record<string, string> = {};
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) {
      continue;
    }
    const separator = trimmed.indexOf("=");
    if (separator <= 0) {
      continue;
    }
    const key = trimmed.slice(0, separator).trim();
    const value = trimmed.slice(separator + 1).trim();
    if (!key) {
      continue;
    }
    entries[key] = value;
  }
  return entries;
}

export function buildWorkspaceCapabilitiesFromDraft(
  draft: WorkspaceDraftState,
): WorkspaceCapabilityManifest | undefined {
  const skillSources = parseTextareaLines(draft.skillSources).map((path) => ({
    path,
    source_type: "local",
    label: null,
    recursive: true,
    enabled: true,
  }));
  const mcpServers = draft.mcpServers
    .map((server): MCPServerConfig | null => {
      const name = server.name.trim();
      if (!name) {
        return null;
      }
      if (server.transport === "stdio") {
        const command = server.command.trim();
        if (!command) {
          return null;
        }
        return {
          name,
          transport: server.transport,
          command,
          args: parseTextareaLines(server.args),
          url: null,
          env: parseEnvLines(server.env),
          tools_allowlist: parseTextareaLines(server.toolsAllowlist),
          enabled: server.enabled,
        };
      }
      const url = server.url.trim();
      if (!url) {
        return null;
      }
      return {
        name,
        transport: server.transport,
        command: null,
        args: [],
        url,
        env: parseEnvLines(server.env),
        tools_allowlist: parseTextareaLines(server.toolsAllowlist),
        enabled: server.enabled,
      };
    })
    .filter((server): server is MCPServerConfig => Boolean(server));

  const agentDefaults = {
    mode: draft.agent.mode || "tool_loop",
    max_steps: Math.max(1, Number.parseInt(draft.agent.maxSteps || "6", 10) || 6),
    can_write: draft.agent.canWrite,
    allowed_skills: parseTextareaLines(draft.agent.allowedSkills),
    allowed_mcp_servers: parseTextareaLines(draft.agent.allowedMcpServers),
    memory_scope: draft.agent.memoryScope.trim() || "workspace_shared",
  };

  const hasNonDefaultAgent =
    agentDefaults.mode !== "tool_loop" ||
    agentDefaults.max_steps !== 6 ||
    agentDefaults.can_write ||
    agentDefaults.allowed_skills.length > 0 ||
    agentDefaults.allowed_mcp_servers.length > 0 ||
    agentDefaults.memory_scope !== "workspace_shared";

  if (!skillSources.length && !mcpServers.length && !hasNonDefaultAgent) {
    const participantOverrides = Object.fromEntries(
      Object.entries(draft.participantOverrides)
        .map(([participantId, config]) => {
          const skills = parseTextareaLines(config.skills);
          const serverNames = parseTextareaLines(config.mcpServers);
          const hasAgent = Boolean(config.agentMode.trim());
          const agent = hasAgent
            ? {
                mode: config.agentMode.trim(),
                max_steps: Math.max(1, Number.parseInt(config.agentMaxSteps || "6", 10) || 6),
                can_write: config.agentCanWrite,
                allowed_skills: parseTextareaLines(config.agentAllowedSkills),
                allowed_mcp_servers: parseTextareaLines(config.agentAllowedMcpServers),
                memory_scope: config.agentMemoryScope.trim() || "workspace_shared",
              }
            : null;
          if (!skills.length && !serverNames.length && !agent) {
            return null;
          }
          return [
            participantId,
            {
              skills,
              mcp_servers: serverNames,
              agent,
            },
          ] as const;
        })
        .filter((entry): entry is readonly [string, { skills: string[]; mcp_servers: string[]; agent: {
          mode: string;
          max_steps: number;
          can_write: boolean;
          allowed_skills: string[];
          allowed_mcp_servers: string[];
          memory_scope: string;
        } | null }] => Boolean(entry)),
    );

    if (!Object.keys(participantOverrides).length) {
      return undefined;
    }

    return {
      skill_sources: skillSources,
      mcp_servers: mcpServers,
      agent_defaults: agentDefaults,
      participant_overrides: participantOverrides,
    };
  }

  return {
    skill_sources: skillSources,
    mcp_servers: mcpServers,
    agent_defaults: agentDefaults,
    participant_overrides: Object.fromEntries(
      Object.entries(draft.participantOverrides)
        .map(([participantId, config]) => {
          const skills = parseTextareaLines(config.skills);
          const serverNames = parseTextareaLines(config.mcpServers);
          const hasAgent = Boolean(config.agentMode.trim());
          const agent = hasAgent
            ? {
                mode: config.agentMode.trim(),
                max_steps: Math.max(1, Number.parseInt(config.agentMaxSteps || "6", 10) || 6),
                can_write: config.agentCanWrite,
                allowed_skills: parseTextareaLines(config.agentAllowedSkills),
                allowed_mcp_servers: parseTextareaLines(config.agentAllowedMcpServers),
                memory_scope: config.agentMemoryScope.trim() || "workspace_shared",
              }
            : null;
          if (!skills.length && !serverNames.length && !agent) {
            return null;
          }
          return [
            participantId,
            {
              skills,
              mcp_servers: serverNames,
              agent,
            },
          ] as const;
        })
        .filter((entry): entry is readonly [string, { skills: string[]; mcp_servers: string[]; agent: {
          mode: string;
          max_steps: number;
          can_write: boolean;
          allowed_skills: string[];
          allowed_mcp_servers: string[];
          memory_scope: string;
        } | null }] => Boolean(entry)),
    ),
  };
}

export function WorkspaceCreatePanel({
  draft,
  aliases,
  onChange,
}: WorkspaceCreatePanelProps): JSX.Element {
  const [preview, setPreview] = React.useState<SessionWorkspaceView | null>(null);
  const [previewLoading, setPreviewLoading] = React.useState(false);
  const [previewError, setPreviewError] = React.useState<string | null>(null);
  const selectedPaths = parseTextareaLines(draft.selectedPaths);
  const selectedPathSet = new Set(selectedPaths);

  React.useEffect(() => {
    setPreview(null);
    setPreviewError(null);
  }, [draft.rootPath, draft.scanExcludes]);

  const updateMCPServer = (index: number, patch: Partial<WorkspaceMCPDraft>) => {
    onChange((current) => ({
      ...current,
      mcpServers: current.mcpServers.map((server, serverIndex) =>
        serverIndex === index ? { ...server, ...patch } : server,
      ),
    }));
  };

  const addMCPServer = () => {
    onChange((current) => ({
      ...current,
      mcpServers: [...current.mcpServers, createEmptyWorkspaceMCPDraft()],
    }));
  };

  const removeMCPServer = (index: number) => {
    onChange((current) => ({
      ...current,
      mcpServers:
        current.mcpServers.length > 1
          ? current.mcpServers.filter((_, serverIndex) => serverIndex !== index)
          : [createEmptyWorkspaceMCPDraft()],
    }));
  };

  const updateParticipantOverride = (
    participantId: string,
    patch: Partial<WorkspaceParticipantOverrideDraft>,
  ) => {
    onChange((current) => ({
      ...current,
      participantOverrides: {
        ...current.participantOverrides,
        [participantId]: {
          ...(current.participantOverrides[participantId] || createEmptyWorkspaceParticipantOverrideDraft()),
          ...patch,
        },
      },
    }));
  };

  const copyDefaultAgentToOverride = (participantId: string) => {
    onChange((current) => ({
      ...current,
      participantOverrides: {
        ...current.participantOverrides,
        [participantId]: {
          ...(current.participantOverrides[participantId] || createEmptyWorkspaceParticipantOverrideDraft()),
          agentMode: current.agent.mode,
          agentMaxSteps: current.agent.maxSteps,
          agentCanWrite: current.agent.canWrite,
          agentAllowedSkills: current.agent.allowedSkills,
          agentAllowedMcpServers: current.agent.allowedMcpServers,
          agentMemoryScope: current.agent.memoryScope,
        },
      },
    }));
  };

  const toggleSelectedPath = (path: string) => {
    const next = new Set(parseTextareaLines(draft.selectedPaths));
    if (next.has(path)) {
      next.delete(path);
    } else {
      next.add(path);
    }
    onChange({ selectedPaths: serializeTextareaLines(next) });
  };

  const handlePreviewWorkspace = async () => {
    const rootPath = draft.rootPath.trim();
    if (!rootPath) {
      setPreview(null);
      setPreviewError("请先填写工作区路径。");
      return;
    }
    setPreviewLoading(true);
    setPreviewError(null);
    try {
      const scanPreview = await previewWorkspace({
        root_path: rootPath,
        scan_excludes: parseTextareaLines(draft.scanExcludes),
      });
      setPreview(scanPreview);
    } catch (error) {
      setPreview(null);
      setPreviewError((error as Error).message);
    } finally {
      setPreviewLoading(false);
    }
  };

  return (
    <div className="panel workspace-panel">
      <div className="panel-head">
        <h3 className="section-title" style={{ margin: 0 }}>工作区配置</h3>
        <span className="badge">code_workspace</span>
      </div>

      <div className="workspace-create-grid">
        <label className="field">
          <span>工作区路径</span>
          <input
            value={draft.rootPath}
            onChange={(event) => onChange({ rootPath: event.target.value })}
            placeholder="D:\\game\\mycode\\devPython\\multi-model-debates"
            required
          />
        </label>

        <label className="field">
          <span>显示名（可选）</span>
          <input
            value={draft.displayName}
            onChange={(event) => onChange({ displayName: event.target.value })}
            placeholder="multi-model-debates"
          />
        </label>

        <label className="field">
          <span>扫描排除项（每行一个目录名）</span>
          <textarea
            rows={4}
            value={draft.scanExcludes}
            onChange={(event) => onChange({ scanExcludes: event.target.value })}
            placeholder=".git&#10;node_modules&#10;dist"
          />
        </label>
      </div>

      <section className="workspace-capability-card workspace-preview-card">
        <div className="panel-head compact">
          <div>
            <h4 className="workspace-card-title">文件选择</h4>
            <p className="hint-text" style={{ margin: "4px 0 0" }}>
              先扫描工作区，再勾选目录或文件。勾选目录时，后端会自动展开目录内的源码文件。
            </p>
          </div>
          <div className="row-actions">
            <span className="badge">{selectedPaths.length} 项已选</span>
            <button
              type="button"
              className="ghost-button small"
              onClick={handlePreviewWorkspace}
              disabled={previewLoading}
            >
              {previewLoading ? "扫描中…" : "扫描工作区"}
            </button>
          </div>
        </div>

        {previewError ? <div className="workspace-preview-error">{previewError}</div> : null}

        {preview ? (
          <>
            <div className="workspace-preview-meta">
              <span className="workspace-path">{preview.display_name || preview.root_path}</span>
              <span className="workspace-path">{preview.summary || "扫描完成"}</span>
            </div>

            <div className="workspace-path-list">
              {selectedPaths.length > 0
                ? selectedPaths.map((path) => <span key={path} className="workspace-path">{path}</span>)
                : <span className="muted-text">还没有选中文件或目录。</span>}
            </div>

            <div className="workspace-tree-picker">
              {preview.tree.length > 0 ? (
                <WorkspaceSelectableTreeList
                  entries={preview.tree}
                  selectedPaths={selectedPathSet}
                  onTogglePath={toggleSelectedPath}
                />
              ) : (
                <div className="muted-text">没有扫描到可选择的文件。</div>
              )}
            </div>

            <div className="workspace-path-list">
              {preview.files.slice(0, 12).map((path) => (
                <span key={path} className="workspace-path">{path}</span>
              ))}
            </div>
          </>
        ) : (
          <div className="muted-text">填写工作区路径后点击“扫描工作区”，再从目录树中选择要注入上下文的路径。</div>
        )}

        <label className="field">
          <span>高级路径覆盖（可选，每行一个路径）</span>
          <textarea
            rows={4}
            name="workspace-selected-paths"
            value={draft.selectedPaths}
            onChange={(event) => onChange({ selectedPaths: event.target.value })}
            placeholder="README.md&#10;backend&#10;frontend/src/App.tsx"
          />
        </label>
      </section>

      <div className="workspace-capability-stack">
        <section className="workspace-capability-card">
          <div className="panel-head compact">
            <h4 className="workspace-card-title">Skills</h4>
            <span className="badge">{parseTextareaLines(draft.skillSources).length} 个来源</span>
          </div>
          <label className="field">
            <span>技能来源（每行一个目录）</span>
            <textarea
              rows={4}
              name="workspace-skill-sources"
              value={draft.skillSources}
              onChange={(event) => onChange({ skillSources: event.target.value })}
              placeholder=".codex/skills&#10;C:\\shared-skills"
            />
          </label>
          <p className="hint-text">
            后端会在这些目录中扫描 `SKILL.md` 并把技能摘要注入 `code_workspace` prompt。
          </p>
        </section>

        <section className="workspace-capability-card">
          <div className="panel-head compact">
            <h4 className="workspace-card-title">MCP</h4>
            <div className="row-actions">
              <button type="button" className="ghost-button small" onClick={addMCPServer}>
                ＋ 添加 MCP Server
              </button>
            </div>
          </div>
          <div className="stack">
            {draft.mcpServers.map((server, index) => (
              <div key={`mcp-${index}`} className="workspace-mcp-card">
                <div className="workspace-mcp-head">
                  <strong>MCP Server {index + 1}</strong>
                  <button
                    type="button"
                    className="ghost-button small danger"
                    onClick={() => removeMCPServer(index)}
                  >
                    删除
                  </button>
                </div>
                <div className="form-grid-2">
                  <label className="field">
                    <span>名称</span>
                    <input
                      name={`workspace-mcp-name-${index}`}
                      value={server.name}
                      onChange={(event) => updateMCPServer(index, { name: event.target.value })}
                      placeholder="filesystem"
                    />
                  </label>
                  <label className="field">
                    <span>Transport</span>
                    <select
                      name={`workspace-mcp-transport-${index}`}
                      value={server.transport}
                      onChange={(event) =>
                        updateMCPServer(index, {
                          transport: event.target.value as WorkspaceMCPDraft["transport"],
                        })
                      }
                    >
                      <option value="stdio">stdio</option>
                      <option value="streamable_http">streamable_http</option>
                    </select>
                  </label>
                  {server.transport === "stdio" ? (
                    <>
                      <label className="field">
                        <span>命令</span>
                        <input
                          name={`workspace-mcp-command-${index}`}
                          value={server.command}
                          onChange={(event) => updateMCPServer(index, { command: event.target.value })}
                          placeholder="npx"
                        />
                      </label>
                      <label className="field">
                        <span>Args（每行一个参数）</span>
                        <textarea
                          rows={3}
                          name={`workspace-mcp-args-${index}`}
                          value={server.args}
                          onChange={(event) => updateMCPServer(index, { args: event.target.value })}
                          placeholder="-y&#10;@modelcontextprotocol/server-filesystem"
                        />
                      </label>
                    </>
                  ) : (
                    <label className="field">
                      <span>URL</span>
                      <input
                        name={`workspace-mcp-url-${index}`}
                        value={server.url}
                        onChange={(event) => updateMCPServer(index, { url: event.target.value })}
                        placeholder="http://127.0.0.1:8080/mcp"
                      />
                    </label>
                  )}
                  <label className="field">
                    <span>Env（每行 KEY=VALUE）</span>
                    <textarea
                      rows={3}
                      name={`workspace-mcp-env-${index}`}
                      value={server.env}
                      onChange={(event) => updateMCPServer(index, { env: event.target.value })}
                      placeholder="ROOT_PATH=D:/repo/demo"
                    />
                  </label>
                  <label className="field">
                    <span>允许工具（每行一个）</span>
                    <textarea
                      rows={3}
                      name={`workspace-mcp-tools-${index}`}
                      value={server.toolsAllowlist}
                      onChange={(event) => updateMCPServer(index, { toolsAllowlist: event.target.value })}
                      placeholder="read_file&#10;list_directory"
                    />
                  </label>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="workspace-capability-card">
          <div className="panel-head compact">
            <h4 className="workspace-card-title">Agent</h4>
            <span className="badge">默认运行时</span>
          </div>
          <div className="form-grid-2">
            <label className="field">
              <span>Agent 模式</span>
              <select
                name="workspace-agent-mode"
                value={draft.agent.mode}
                onChange={(event) =>
                  onChange((current) => ({
                    ...current,
                    agent: { ...current.agent, mode: event.target.value },
                  }))
                }
              >
                <option value="disabled">disabled</option>
                <option value="plan_only">plan_only</option>
                <option value="tool_loop">tool_loop</option>
                <option value="full_agent">full_agent</option>
              </select>
            </label>

            <label className="field">
              <span>最大步数</span>
              <input
                type="number"
                min={1}
                name="workspace-agent-max-steps"
                value={draft.agent.maxSteps}
                onChange={(event) =>
                  onChange((current) => ({
                    ...current,
                    agent: { ...current.agent, maxSteps: event.target.value },
                  }))
                }
              />
            </label>

            <label className="field">
              <span>Memory Scope</span>
              <input
                name="workspace-agent-memory-scope"
                value={draft.agent.memoryScope}
                onChange={(event) =>
                  onChange((current) => ({
                    ...current,
                    agent: { ...current.agent, memoryScope: event.target.value },
                  }))
                }
                placeholder="workspace_shared"
              />
            </label>

            <label className="field checkbox-field">
              <span>允许写入工作区</span>
              <input
                type="checkbox"
                name="workspace-agent-can-write"
                checked={draft.agent.canWrite}
                onChange={(event) =>
                  onChange((current) => ({
                    ...current,
                    agent: { ...current.agent, canWrite: event.target.checked },
                  }))
                }
              />
            </label>

            <label className="field">
              <span>允许技能（每行一个）</span>
              <textarea
                rows={3}
                name="workspace-agent-skills"
                value={draft.agent.allowedSkills}
                onChange={(event) =>
                  onChange((current) => ({
                    ...current,
                    agent: { ...current.agent, allowedSkills: event.target.value },
                  }))
                }
                placeholder="repo-review&#10;python-tests"
              />
            </label>

            <label className="field">
              <span>允许 MCP Server（每行一个）</span>
              <textarea
                rows={3}
                name="workspace-agent-mcp-servers"
                value={draft.agent.allowedMcpServers}
                onChange={(event) =>
                  onChange((current) => ({
                    ...current,
                    agent: { ...current.agent, allowedMcpServers: event.target.value },
                  }))
                }
                placeholder="filesystem&#10;git"
              />
            </label>
          </div>
        </section>

        <section className="workspace-capability-card">
          <div className="panel-head compact">
            <h4 className="workspace-card-title">参与者覆盖</h4>
            <span className="badge">{aliases.length} 个 alias</span>
          </div>
          <div className="stack">
            {aliases.length > 0 ? aliases.map((alias) => {
              const override = draft.participantOverrides[alias] || createEmptyWorkspaceParticipantOverrideDraft();
              return (
                <div key={`override-${alias}`} className="workspace-override-card">
                  <div className="workspace-mcp-head">
                    <strong>@{alias}</strong>
                    <div className="row-actions">
                      <span className="muted-text">为空时继承默认配置</span>
                      <button
                        type="button"
                        className="ghost-button small"
                        onClick={() => copyDefaultAgentToOverride(alias)}
                      >
                        复制默认 Agent 到 @{alias}
                      </button>
                    </div>
                  </div>
                  <div className="form-grid-2">
                    <label className="field">
                      <span>Skills（每行一个）</span>
                      <textarea
                        rows={3}
                        name={`workspace-override-skills-${alias}`}
                        value={override.skills}
                        onChange={(event) => updateParticipantOverride(alias, { skills: event.target.value })}
                        placeholder="focused-review"
                      />
                    </label>
                    <label className="field">
                      <span>MCP Server（每行一个）</span>
                      <textarea
                        rows={3}
                        name={`workspace-override-mcp-servers-${alias}`}
                        value={override.mcpServers}
                        onChange={(event) => updateParticipantOverride(alias, { mcpServers: event.target.value })}
                        placeholder="filesystem"
                      />
                    </label>
                    <label className="field">
                      <span>Agent 模式</span>
                      <select
                        name={`workspace-override-agent-mode-${alias}`}
                        value={override.agentMode}
                        onChange={(event) => updateParticipantOverride(alias, { agentMode: event.target.value })}
                      >
                        <option value="">继承默认</option>
                        <option value="disabled">disabled</option>
                        <option value="plan_only">plan_only</option>
                        <option value="tool_loop">tool_loop</option>
                        <option value="full_agent">full_agent</option>
                      </select>
                    </label>
                    <label className="field">
                      <span>Agent 最大步数</span>
                      <input
                        type="number"
                        min={1}
                        name={`workspace-override-agent-max-steps-${alias}`}
                        value={override.agentMaxSteps}
                        onChange={(event) => updateParticipantOverride(alias, { agentMaxSteps: event.target.value })}
                      />
                    </label>
                    <label className="field checkbox-field">
                      <span>允许写入工作区</span>
                      <input
                        type="checkbox"
                        name={`workspace-override-agent-can-write-${alias}`}
                        checked={override.agentCanWrite}
                        onChange={(event) => updateParticipantOverride(alias, { agentCanWrite: event.target.checked })}
                      />
                    </label>
                    <label className="field">
                      <span>Agent Memory Scope</span>
                      <input
                        name={`workspace-override-agent-memory-scope-${alias}`}
                        value={override.agentMemoryScope}
                        onChange={(event) => updateParticipantOverride(alias, { agentMemoryScope: event.target.value })}
                        placeholder="workspace_shared"
                      />
                    </label>
                    <label className="field">
                      <span>Agent 技能（每行一个）</span>
                      <textarea
                        rows={3}
                        name={`workspace-override-agent-skills-${alias}`}
                        value={override.agentAllowedSkills}
                        onChange={(event) => updateParticipantOverride(alias, { agentAllowedSkills: event.target.value })}
                        placeholder="repo-review"
                      />
                    </label>
                    <label className="field">
                      <span>Agent MCP Server（每行一个）</span>
                      <textarea
                        rows={3}
                        name={`workspace-override-agent-mcp-servers-${alias}`}
                        value={override.agentAllowedMcpServers}
                        onChange={(event) => updateParticipantOverride(alias, { agentAllowedMcpServers: event.target.value })}
                        placeholder="filesystem"
                      />
                    </label>
                  </div>
                </div>
              );
            }) : <div className="muted-text">先添加参与者，才能配置单独覆盖。</div>}
          </div>
        </section>
      </div>

      <div className="workspace-hint">
        <div className="muted-text">本地代码工作区会把仓库树、选中文件和模型别名一起送入上下文。</div>
        <strong>别名提示</strong>
        <div className="workspace-chip-row">
          {aliases.length > 0 ? aliases.map((alias) => (
            <span key={alias} className="workspace-chip">@{alias}</span>
          )) : <span className="muted-text">先添加参与者，之后可在输入框里直接 @alias 点名。</span>}
        </div>
      </div>

      <p className="hint-text">
        输入 `@claude`、`@codex` 之类的别名后，后端会按参与者顺序把任务路由给目标模型。
      </p>
    </div>
  );
}

function WorkspaceSelectableTreeList({
  entries,
  selectedPaths,
  onTogglePath,
}: {
  entries: WorkspaceTreeEntry[];
  selectedPaths: Set<string>;
  onTogglePath: (path: string) => void;
}): JSX.Element {
  return (
    <ul className="workspace-tree">
      {entries.map((entry) => (
        <WorkspaceSelectableTreeNode
          key={entry.path}
          entry={entry}
          selectedPaths={selectedPaths}
          onTogglePath={onTogglePath}
        />
      ))}
    </ul>
  );
}

function WorkspaceSelectableTreeNode({
  entry,
  selectedPaths,
  onTogglePath,
}: {
  entry: WorkspaceTreeEntry;
  selectedPaths: Set<string>;
  onTogglePath: (path: string) => void;
}): JSX.Element {
  return (
    <li className={`workspace-tree-node workspace-tree-node-${entry.kind}`}>
      <label className="workspace-tree-label workspace-tree-label-selectable">
        <input
          type="checkbox"
          className="workspace-tree-checkbox"
          data-workspace-select-path={entry.path}
          checked={selectedPaths.has(entry.path)}
          onChange={() => onTogglePath(entry.path)}
        />
        <span className="workspace-tree-kind">{entry.kind === "dir" ? "📁" : "📄"}</span>
        <span>{entry.name}</span>
        <span className="workspace-tree-path">{entry.path}</span>
      </label>
      {entry.children.length > 0 ? (
        <WorkspaceSelectableTreeList
          entries={entry.children}
          selectedPaths={selectedPaths}
          onTogglePath={onTogglePath}
        />
      ) : null}
    </li>
  );
}

export function WorkspaceSessionPanel({
  workspace,
  participants,
  capabilities,
}: WorkspaceSessionPanelProps): JSX.Element {
  if (!workspace) {
    return (
      <div className="workspace-session-panel empty-state">
        未加载到工作区信息。
      </div>
    );
  }

  const capabilityManifest = capabilities || workspace.capabilities || null;

  return (
    <div className="workspace-session-panel stack">
      <div className="workspace-summary-card">
        <div className="panel-head">
          <h3 className="section-title" style={{ margin: 0 }}>工作区</h3>
          <span className="badge">{workspace.index_status}</span>
        </div>
        <div className="workspace-meta">
          <div><span className="status-label">路径</span>{workspace.root_path}</div>
          <div><span className="status-label">名称</span>{workspace.display_name || "未命名"}</div>
          <div><span className="status-label">摘要</span>{workspace.summary || "暂无"}</div>
          <div><span className="status-label">Fingerprint</span>{workspace.repo_fingerprint || "未计算"}</div>
        </div>
      </div>

      <div className="workspace-card">
        <h4 className="workspace-card-title">参与者别名</h4>
        <div className="workspace-chip-row">
          {participants.map((participant) => (
            <span key={participant.custom_id} className="workspace-chip">@{participant.custom_id}</span>
          ))}
        </div>
      </div>

      {capabilityManifest && (
        <>
          <div className="workspace-card">
            <h4 className="workspace-card-title">Skills</h4>
            <div className="workspace-path-list">
              {capabilityManifest.skill_sources.length > 0
                ? capabilityManifest.skill_sources.map((source) => (
                  <span key={source.path} className="workspace-path">{source.path}</span>
                ))
                : <span className="muted-text">未配置技能来源</span>}
            </div>
          </div>

          <div className="workspace-card">
            <h4 className="workspace-card-title">MCP</h4>
            <div className="workspace-path-list">
              {capabilityManifest.mcp_servers.length > 0
                ? capabilityManifest.mcp_servers.map((server) => (
                  <span key={server.name} className="workspace-path">
                    {server.name} · {server.transport}
                  </span>
                ))
                : <span className="muted-text">未配置 MCP Server</span>}
            </div>
          </div>

          <div className="workspace-card">
            <h4 className="workspace-card-title">Agent</h4>
            <div className="workspace-meta">
              <div><span className="status-label">模式</span>{capabilityManifest.agent_defaults.mode}</div>
              <div><span className="status-label">步数</span>{capabilityManifest.agent_defaults.max_steps}</div>
              <div><span className="status-label">技能</span>{capabilityManifest.agent_defaults.allowed_skills.join(", ") || "全部"}</div>
              <div><span className="status-label">MCP</span>{capabilityManifest.agent_defaults.allowed_mcp_servers.join(", ") || "全部"}</div>
            </div>
          </div>

          <div className="workspace-card">
            <h4 className="workspace-card-title">参与者覆盖</h4>
            <div className="workspace-meta">
              {Object.keys(capabilityManifest.participant_overrides).length > 0 ? Object.entries(capabilityManifest.participant_overrides).map(([participantId, override]) => (
                <div key={participantId}>
                  <span className="status-label">@{participantId}</span>
                  {override.agent?.mode || "inherit"} · skills: {override.skills.join(", ") || "inherit"} · mcp: {override.mcp_servers.join(", ") || "inherit"}
                </div>
              )) : <div className="muted-text">未配置参与者覆盖</div>}
            </div>
          </div>
        </>
      )}

      <div className="workspace-card">
        <h4 className="workspace-card-title">选中文件</h4>
        <div className="workspace-path-list">
          {workspace.selected_paths.length > 0
            ? workspace.selected_paths.map((path) => <span key={path} className="workspace-path">{path}</span>)
            : <span className="muted-text">未选中文件</span>}
        </div>
      </div>

      <div className="workspace-card workspace-tree-card">
        <h4 className="workspace-card-title">仓库树</h4>
        {workspace.tree.length > 0
          ? <WorkspaceTreeList entries={workspace.tree} />
          : <div className="muted-text">没有扫描到可展示的文件。</div>}
      </div>

      <div className="workspace-card">
        <h4 className="workspace-card-title">扫描文件</h4>
        <div className="workspace-path-list">
          {workspace.files.length > 0
            ? workspace.files.slice(0, 12).map((path) => <span key={path} className="workspace-path">{path}</span>)
            : <span className="muted-text">没有文件。</span>}
        </div>
      </div>
    </div>
  );
}

function WorkspaceTreeList({ entries }: { entries: WorkspaceTreeEntry[] }): JSX.Element {
  return (
    <ul className="workspace-tree">
      {entries.map((entry) => (
        <WorkspaceTreeNode key={entry.path} entry={entry} />
      ))}
    </ul>
  );
}

function WorkspaceTreeNode({ entry }: { entry: WorkspaceTreeEntry }): JSX.Element {
  return (
    <li className={`workspace-tree-node workspace-tree-node-${entry.kind}`}>
      <div className="workspace-tree-label">
        <span className="workspace-tree-kind">{entry.kind === "dir" ? "📁" : "📄"}</span>
        <span>{entry.name}</span>
      </div>
      {entry.children.length > 0 && <WorkspaceTreeList entries={entry.children} />}
    </li>
  );
}
