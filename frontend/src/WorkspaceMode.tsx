import React from "react";

import type {
  ParticipantConfig,
  SessionWorkspaceView,
  WorkspaceTreeEntry,
} from "./types";

export interface WorkspaceDraftState {
  rootPath: string;
  displayName: string;
  selectedPaths: string;
  scanExcludes: string;
}

interface WorkspaceCreatePanelProps {
  draft: WorkspaceDraftState;
  aliases: string[];
  onChange: (patch: Partial<WorkspaceDraftState>) => void;
}

interface WorkspaceSessionPanelProps {
  workspace: SessionWorkspaceView | null;
  participants: ParticipantConfig[];
}

export function WorkspaceCreatePanel({
  draft,
  aliases,
  onChange,
}: WorkspaceCreatePanelProps): JSX.Element {
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
          <span>选中文件（每行一个路径）</span>
          <textarea
            rows={5}
            value={draft.selectedPaths}
            onChange={(event) => onChange({ selectedPaths: event.target.value })}
            placeholder="README.md&#10;backend/orchestrator.py&#10;frontend/src/App.tsx"
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

export function WorkspaceSessionPanel({
  workspace,
  participants,
}: WorkspaceSessionPanelProps): JSX.Element {
  if (!workspace) {
    return (
      <div className="workspace-session-panel empty-state">
        未加载到工作区信息。
      </div>
    );
  }

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
