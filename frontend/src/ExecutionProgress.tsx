import React from "react";

import type { ExecutionEventRecord, StreamState } from "./types";

interface ExecutionProgressPanelProps {
  entries: ExecutionEventRecord[];
  streamState: StreamState;
}

export function ExecutionProgressPanel({
  entries,
  streamState,
}: ExecutionProgressPanelProps): JSX.Element {
  const statusLabel =
    streamState === "connecting"
      ? "连接中"
      : streamState === "streaming"
        ? "执行中"
        : streamState === "completed"
          ? "已完成"
          : streamState === "failed"
            ? "失败"
            : "空闲";
  const statusClass =
    streamState === "completed"
      ? "finished"
      : streamState === "failed"
        ? "idle"
        : streamState === "idle"
          ? "idle"
          : "active";

  return (
    <div className="workspace-card execution-panel">
      <div className="panel-head">
        <h4 className="workspace-card-title">执行过程</h4>
        <span className={`status-badge status-${statusClass}`}>
          {statusLabel}
        </span>
      </div>
      {entries.length === 0 ? (
        <div className="muted-text">开始下一轮后，这里会显示计划、工具调用、阶段完成和错误信息。</div>
      ) : (
        <div className="execution-timeline">
          {entries.map((entry) => (
            <div
              key={entry.id}
              className={`execution-entry execution-entry-${entry.status}`}
            >
              <div className="execution-entry-head">
                <span className="execution-entry-icon">{iconForExecutionStatus(entry.status)}</span>
                <strong>{entry.summary}</strong>
                <span className="workspace-path">Round {entry.round}</span>
              </div>
              {entry.detail ? <pre className="execution-entry-detail">{entry.detail}</pre> : null}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function iconForExecutionStatus(status: ExecutionEventRecord["status"]): string {
  if (status === "running") {
    return "●";
  }
  if (status === "done") {
    return "✓";
  }
  if (status === "error") {
    return "!";
  }
  return "·";
}
