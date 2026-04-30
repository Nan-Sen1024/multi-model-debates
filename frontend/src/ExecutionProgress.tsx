import React, { useEffect, useRef } from "react";

import type { ExecutionEventRecord, StreamState } from "./types";

interface ExecutionProgressPanelProps {
  entries: ExecutionEventRecord[];
  streamState: StreamState;
}

export function ExecutionProgressPanel({
  entries,
  streamState,
}: ExecutionProgressPanelProps): JSX.Element {
  const endRef = useRef<HTMLDivElement | null>(null);
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

  useEffect(() => {
    if (streamState === "streaming") {
      endRef.current?.scrollIntoView({ block: "end" });
    }
  }, [entries.length, streamState]);

  return (
    <div className="workspace-card execution-panel execution-panel-live">
      <div className="panel-head">
        <h4 className="workspace-card-title">实时执行日志</h4>
        <span className={`status-badge status-${statusClass}`}>{statusLabel}</span>
      </div>
      {entries.length === 0 ? (
        <div className="muted-text">
          开始下一轮后，这里会像 Codex 一样逐行显示模型思考、工具调用和命令输出。
        </div>
      ) : (
        <div className="execution-log" role="log" aria-live="polite">
          {entries.map((entry) => (
            <div
              key={entry.id}
              className={`execution-log-row execution-log-row-${entry.status}`}
              data-execution-kind={entry.kind || "unknown"}
              data-execution-event={entry.event}
              data-execution-phase={entry.phase || ""}
            >
              <span className="execution-log-gutter">{iconForExecutionStatus(entry.status)}</span>
              <div className="execution-log-body">
                <div className="execution-log-head">
                  <span className={`execution-kind execution-kind-${entry.kind || "unknown"}`}>
                    {labelForExecutionKind(entry.kind)}
                  </span>
                  {entry.phase ? <code className="execution-phase">{entry.phase}</code> : null}
                  <strong>{entry.summary}</strong>
                  <span className="workspace-path">r{entry.round}</span>
                </div>
                {entry.detail ? (
                  <pre className={entry.kind === "output" ? "execution-log-output" : "execution-log-detail"}>
                    {entry.detail}
                  </pre>
                ) : null}
              </div>
            </div>
          ))}
          <div ref={endRef} />
        </div>
      )}
    </div>
  );
}

function iconForExecutionStatus(status: ExecutionEventRecord["status"]): string {
  if (status === "running") {
    return "◌";
  }
  if (status === "warning") {
    return "⚠";
  }
  if (status === "done") {
    return "✓";
  }
  if (status === "error") {
    return "!";
  }
  return "·";
}

function labelForExecutionKind(kind: ExecutionEventRecord["kind"]): string {
  if (kind === "phase") {
    return "PHASE";
  }
  if (kind === "model") {
    return "MODEL";
  }
  if (kind === "tool") {
    return "TOOL";
  }
  if (kind === "output") {
    return "LOG";
  }
  if (kind === "state") {
    return "STATE";
  }
  if (kind === "turn") {
    return "TURN";
  }
  if (kind === "session") {
    return "SESSION";
  }
  return "NOTE";
}
