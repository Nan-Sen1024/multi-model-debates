import React, { useEffect, useRef } from "react";

import type { ExecutionEventRecord, StreamState } from "./types";

type ExecutionFilter = "all" | "failures" | "commands" | "files";

interface ExecutionSummary {
  files: string[];
  commands: string[];
  validation: string;
  blockers: string[];
}

type RoundStatus = "running" | "passed" | "failed" | "blocked";

interface TimelineEntryView {
  id: string;
  summary: string;
  detail?: string;
  kind?: ExecutionEventRecord["kind"];
  status: ExecutionEventRecord["status"];
  phase?: string;
  round: number;
}

interface ExecutionProgressPanelProps {
  entries: ExecutionEventRecord[];
  streamState: StreamState;
}

export function ExecutionProgressPanel({
  entries,
  streamState,
}: ExecutionProgressPanelProps): JSX.Element {
  const endRef = useRef<HTMLDivElement | null>(null);
  const [expandedEntryIds, setExpandedEntryIds] = React.useState<string[]>([]);
  const [activeFilter, setActiveFilter] = React.useState<ExecutionFilter>("all");
  const [showRawLog, setShowRawLog] = React.useState(false);
  const [selectedRound, setSelectedRound] = React.useState<number | null>(null);
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
        ? "failed"
        : streamState === "idle"
          ? "idle"
       : "active";
  const surfaceTone =
    streamState === "completed"
      ? "finished"
      : streamState === "failed"
        ? "failed"
        : streamState === "streaming"
          ? "active"
          : streamState === "connecting"
            ? "connecting"
            : "idle";
  const stateHeadline =
    streamState === "completed"
      ? "本轮执行已完成"
      : streamState === "failed"
        ? "执行流已中断"
        : streamState === "streaming"
          ? "正在持续写入执行进展"
          : streamState === "connecting"
            ? "正在建立实时连接"
            : "等待下一轮开始";
  const stateDescription =
    streamState === "completed"
      ? "可以回看本轮工具调用、验证结果和关键文件变更。"
      : streamState === "failed"
        ? "请优先检查连接、Provider 健康状态和最近一条阻塞信息。"
        : streamState === "streaming"
          ? "这里会持续刷新模型输出、工具活动、命令执行和验证状态。"
          : streamState === "connecting"
            ? "连接建立后会开始逐行显示当前 Run 的执行轨迹。"
            : "运行状态已就绪，开始任务后会持续显示模型、工具和验证进展。";

  useEffect(() => {
    if (streamState === "streaming" && showRawLog) {
      endRef.current?.scrollIntoView({ block: "end" });
    }
  }, [entries.length, showRawLog, streamState]);

  const rounds = React.useMemo(
    () =>
      Array.from(new Set(entries.map((entry) => entry.round)))
        .filter((round) => typeof round === "number" && round > 0)
        .sort((left, right) => right - left),
    [entries],
  );
  const roundStatuses = React.useMemo(
    () => buildRoundStatuses(entries),
    [entries],
  );

  useEffect(() => {
    if (rounds.length === 0) {
      setSelectedRound(null);
      return;
    }
    setSelectedRound((current) => (current && rounds.includes(current) ? current : rounds[0]));
  }, [rounds]);

  const roundScopedEntries = React.useMemo(
    () =>
      selectedRound == null
        ? entries
        : entries.filter((entry) => entry.round === selectedRound),
    [entries, selectedRound],
  );

  const filterOptions = React.useMemo(
    () =>
      [
        { key: "all", label: "全部" },
        { key: "failures", label: "失败" },
        { key: "commands", label: "命令" },
        { key: "files", label: "文件" },
      ].map((item) => ({
        ...item,
        count:
          item.key === "all"
            ? roundScopedEntries.length
            : roundScopedEntries.filter((entry) => matchesExecutionFilter(entry, item.key as ExecutionFilter)).length,
      })),
    [roundScopedEntries],
  );
  const visibleEntries = React.useMemo(
    () =>
      activeFilter === "all"
        ? roundScopedEntries
        : roundScopedEntries.filter((entry) => matchesExecutionFilter(entry, activeFilter)),
    [activeFilter, roundScopedEntries],
  );
  const timelineEntries = React.useMemo(
    () => buildTimelineEntries(roundScopedEntries, activeFilter),
    [activeFilter, roundScopedEntries],
  );
  const summary = React.useMemo(() => summarizeExecution(roundScopedEntries), [roundScopedEntries]);
  const hasEntries = entries.length > 0;
  const validationTone = toneForValidation(summary.validation);
  const filesTone = toneForPresence(summary.files.length);
  const commandsTone = toneForPresence(summary.commands.length);
  const blockersTone = toneForBlockers(summary.blockers.length);

  function toggleExpanded(entryId: string) {
    setExpandedEntryIds((current) =>
      current.includes(entryId)
        ? current.filter((value) => value !== entryId)
        : [...current, entryId],
    );
  }

  return (
    <div
      className="workspace-card execution-panel execution-panel-live"
      data-stream-state={streamState}
      data-has-entries={hasEntries ? "true" : "false"}
      data-validation-tone={validationTone}
      data-surface-tone={surfaceTone}
    >
      <div className="panel-head">
        <h4 className="workspace-card-title">实时执行日志</h4>
        <span className={`status-badge status-${statusClass}`}>{statusLabel}</span>
      </div>
      <div className="execution-state-banner" data-surface-tone={surfaceTone}>
        <strong>{stateHeadline}</strong>
        <span>{stateDescription}</span>
      </div>
      {rounds.length > 0 ? (
        <div className="execution-round-bar">
          <span className="execution-round-label">当前轮次</span>
          <div className="execution-round-list">
            {rounds.map((round) => (
              <button
                key={round}
                type="button"
                className={`execution-round-chip execution-round-chip-${roundStatuses[round] || "running"}${
                  selectedRound === round ? " execution-round-chip-active" : ""
                }`}
                onClick={() => setSelectedRound(round)}
                data-execution-round-chip={round}
                data-round-status={roundStatuses[round] || "running"}
              >
                {`第 ${round} 轮 · ${labelForRoundStatus(roundStatuses[round] || "running")}`}
              </button>
            ))}
          </div>
        </div>
      ) : null}
      <div className="execution-filter-bar" role="tablist" aria-label="执行日志筛选">
        {filterOptions.map((option) => (
          <button
            key={option.key}
            type="button"
            className={`execution-filter-chip${activeFilter === option.key ? " execution-filter-chip-active" : ""}`}
            aria-pressed={activeFilter === option.key}
            onClick={() => setActiveFilter(option.key as ExecutionFilter)}
          >
            <span>{option.label}</span>
            <strong>{option.count}</strong>
          </button>
        ))}
      </div>
      <div className="execution-summary-card">
        <div className="execution-summary-head">
          <strong>本轮摘要</strong>
          <button
            type="button"
            className="execution-log-toggle"
            onClick={() => setShowRawLog((current) => !current)}
          >
            {showRawLog ? "隐藏原始事件" : "查看原始事件"}
          </button>
        </div>
        <div className="execution-summary-grid">
          <div
            className="execution-summary-item"
            data-summary-field="validation"
            data-summary-tone={validationTone}
          >
            <span>验证状态</span>
            <strong>{summary.validation}</strong>
          </div>
          <div
            className="execution-summary-item"
            data-summary-field="files"
            data-summary-tone={filesTone}
          >
            <span>涉及文件</span>
            <strong>{summary.files.length > 0 ? summary.files.slice(0, 2).join("、") : "暂无"}</strong>
          </div>
          <div
            className="execution-summary-item"
            data-summary-field="commands"
            data-summary-tone={commandsTone}
          >
            <span>执行命令</span>
            <strong>{summary.commands.length > 0 ? summary.commands.slice(0, 2).join("、") : "暂无"}</strong>
          </div>
          <div
            className="execution-summary-item"
            data-summary-field="blockers"
            data-summary-tone={blockersTone}
          >
            <span>阻塞与告警</span>
            <strong>{summary.blockers.length > 0 ? `${summary.blockers.length} 项` : "无"}</strong>
          </div>
        </div>
      </div>
      <div className="execution-timeline-card">
        <div className="execution-summary-head">
          <strong>关键进展</strong>
          <span className="execution-timeline-caption">
            {timelineEntries.length > 0
              ? `${timelineEntries.length} 条里程碑`
              : hasEntries
                ? "当前筛选下暂无关键进展"
                : "等待本轮开始"}
          </span>
        </div>
        {timelineEntries.length > 0 ? (
          <div className="execution-timeline" aria-live="polite">
            {timelineEntries.map((entry) => (
              <ExecutionTimelineRow key={entry.id} entry={entry} />
            ))}
          </div>
        ) : (
          <div className="execution-log-empty">
            {hasEntries ? "当前筛选下没有关键进展。" : "等待开始下一轮后，这里会显示关键进展。"}
          </div>
        )}
      </div>
      {hasEntries && showRawLog ? (
        <div className="execution-log" role="log" aria-live="polite">
          {visibleEntries.map((entry) => (
            <ExecutionLogRow
              key={entry.id}
              entry={entry}
              expanded={expandedEntryIds.includes(entry.id)}
              onToggleExpanded={() => toggleExpanded(entry.id)}
            />
          ))}
          {visibleEntries.length === 0 ? (
            <div className="execution-log-empty">当前筛选下没有执行记录。</div>
          ) : null}
          <div ref={endRef} />
        </div>
      ) : (
        <div className="execution-log" role="log" aria-live="polite">
          <div className="execution-log-empty">
            {hasEntries ? "原始事件已收起，可按需展开查看完整协议日志。" : "原始事件尚未产生，开始下一轮后可展开查看完整协议日志。"}
          </div>
          <div ref={endRef} />
        </div>
      )}
    </div>
  );
}

interface ExecutionTimelineRowProps {
  entry: TimelineEntryView;
}

function ExecutionTimelineRow({ entry }: ExecutionTimelineRowProps): JSX.Element {
  return (
    <div
      className={`execution-timeline-row execution-timeline-row-${entry.status}`}
      data-execution-kind={entry.kind || "unknown"}
      data-execution-timeline-kind={entry.kind || "unknown"}
      data-execution-event={entry.summary}
    >
      <span className="execution-timeline-gutter">{iconForExecutionStatus(entry.status)}</span>
      <div className="execution-timeline-body">
        <div className="execution-timeline-head">
          <span className={`execution-kind execution-kind-${entry.kind || "unknown"}`}>
            {labelForTimelineKind(entry.kind)}
          </span>
          {entry.phase ? <code className="execution-phase">{entry.phase}</code> : null}
          <strong>{entry.summary}</strong>
          <span className="workspace-path">r{entry.round}</span>
        </div>
        {entry.detail ? <div className="execution-timeline-detail">{entry.detail}</div> : null}
      </div>
    </div>
  );
}

interface ExecutionLogRowProps {
  entry: ExecutionEventRecord;
  expanded: boolean;
  onToggleExpanded: () => void;
}

function ExecutionLogRow({
  entry,
  expanded,
  onToggleExpanded,
}: ExecutionLogRowProps): JSX.Element {
  const detail = entry.detail || "";
  const preview = summarizeDetailPreview(detail);
  const isCollapsible = detail.split(/\r?\n/).length > 4 || detail.length > 240;
  const visibleDetail = isCollapsible && !expanded ? preview : detail;

  return (
    <div
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
        {visibleDetail ? (
          <pre className={entry.kind === "output" ? "execution-log-output" : "execution-log-detail"}>
            {visibleDetail}
          </pre>
        ) : null}
        {isCollapsible ? (
          <button type="button" className="execution-log-toggle" onClick={onToggleExpanded}>
            {expanded ? "收起详情" : "展开详情"}
          </button>
        ) : null}
      </div>
    </div>
  );
}

function summarizeDetailPreview(detail: string): string {
  const lines = detail.split(/\r?\n/);
  if (lines.length <= 4 && detail.length <= 240) {
    return detail;
  }
  const preview = lines.slice(0, 4).join("\n");
  return `${preview}\n…`;
}

function joinDetailLines(lines: Array<string | null | undefined>): string | undefined {
  const filtered = lines.map((line) => line?.trim()).filter(Boolean) as string[];
  return filtered.length > 0 ? filtered.join("\n") : undefined;
}

function summarizeTimelinePreview(detail: string, maxLines = 2, maxLength = 180): string {
  const lines = detail
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const preview = lines.slice(0, maxLines).join("\n");
  if (preview.length <= maxLength && lines.length <= maxLines) {
    return preview;
  }
  return `${preview.slice(0, maxLength)}…`;
}

function matchesExecutionFilter(entry: ExecutionEventRecord, filter: ExecutionFilter): boolean {
  if (filter === "failures") {
    return entry.status === "warning" || entry.status === "error";
  }
  if (filter === "commands") {
    return isCommandEntry(entry);
  }
  if (filter === "files") {
    return isFileEntry(entry);
  }
  return true;
}

function isCommandEntry(entry: ExecutionEventRecord): boolean {
  const metadata = (entry.metadata || {}) as Record<string, unknown>;
  return metadata.tool_name === "run_command";
}

function isFileEntry(entry: ExecutionEventRecord): boolean {
  const metadata = (entry.metadata || {}) as Record<string, unknown>;
  return (
    metadata.tool_name === "read_file" ||
    metadata.tool_name === "write_file" ||
    metadata.tool_name === "list_files"
  );
}

function summarizeExecution(entries: ExecutionEventRecord[]): ExecutionSummary {
  const files = new Set<string>();
  const commands = new Set<string>();
  const blockers: string[] = [];
  let validation = "等待执行";

  for (const entry of entries) {
    const path = pathFromExecutionEntry(entry);
    const command = commandFromExecutionEntry(entry);
    const detail = entry.detail || "";

    if (path) {
      files.add(path);
    }
    const localizedPathMatch = detail.match(/路径：([^\n\r]+)/);
    if (localizedPathMatch?.[1]) {
      files.add(localizedPathMatch[1].trim());
    }

    if (command) {
      commands.add(command);
    }
    const localizedCommandMatch = detail.match(/命令：([^\n\r]+)/);
    if (localizedCommandMatch?.[1]) {
      commands.add(localizedCommandMatch[1].trim());
    }

    if (/exit_code=0/.test(detail) || /退出码：0/.test(detail)) {
      validation = "验证通过";
    } else if (/exit_code=\d+/.test(detail) || /退出码：-?\d+/.test(detail)) {
      validation = "验证未通过";
    }

    if (entry.status === "warning" || entry.status === "error") {
      blockers.push(entry.detail || entry.summary);
      if (validation === "等待执行") {
        validation = "存在阻塞";
      }
    }
  }

  return {
    files: Array.from(files),
    commands: Array.from(commands),
    validation,
    blockers,
  };
}

function buildTimelineEntries(
  entries: ExecutionEventRecord[],
  filter: ExecutionFilter,
): TimelineEntryView[] {
  const source =
    filter === "all"
      ? entries
      : entries.filter((entry) => matchesExecutionFilter(entry, filter));
  return source
    .filter((entry) => shouldShowInTimeline(entry))
    .map((entry) => ({
      id: entry.id,
      summary: summarizeTimelineEntry(entry),
      detail: summarizeTimelineDetail(entry),
      kind: entry.kind,
      status: entry.status,
      phase: entry.phase,
      round: entry.round,
    }));
}

function shouldShowInTimeline(entry: ExecutionEventRecord): boolean {
  if (entry.event === "state_write") {
    return false;
  }
  if (entry.event === "model_response") {
    return false;
  }
  if (entry.event === "model_output") {
    return Boolean(entry.detail) && entry.summary !== "模型准备调用工具";
  }
  if (entry.event === "tool_output") {
    return false;
  }
  return true;
}

function summarizeTimelineEntry(entry: ExecutionEventRecord): string {
  const metadata = (entry.metadata || {}) as Record<string, unknown>;
  const toolName = typeof metadata.tool_name === "string" ? metadata.tool_name : "";
  const path = pathFromExecutionEntry(entry);
  const command = commandFromExecutionEntry(entry);

  if (entry.event === "tool_result" && (toolName === "read_file" || toolName === "list_files" || toolName === "write_file") && path) {
    return `${summarizeFileTimelineAction(toolName, entry)} ${path}`;
  }
  if ((entry.event === "tool_call" || entry.event === "tool_result") && toolName === "run_command" && command) {
    return entry.event === "tool_call" ? `执行命令 ${command}` : `${entry.summary} · ${command}`;
  }
  return entry.summary;
}

function summarizeFileTimelineAction(toolName: string, entry: ExecutionEventRecord): string {
  const detail = entry.detail || "";
  if (toolName === "read_file") {
    return "已读取文件";
  }
  if (toolName === "list_files") {
    return "已列出目录";
  }
  if (toolName === "write_file") {
    if (/覆盖|overwrite|updated /i.test(detail)) {
      return "覆盖写入";
    }
    if (/新建|create(?:d)?|new file/i.test(detail)) {
      return "新建文件";
    }
    return "已写入文件";
  }
  return entry.summary;
}

function summarizeTimelineDetail(entry: ExecutionEventRecord): string | undefined {
  const metadata = (entry.metadata || {}) as Record<string, unknown>;
  const toolName = typeof metadata.tool_name === "string" ? metadata.tool_name : "";
  const detail = entry.detail || "";
  if (!detail) {
    return undefined;
  }

  if (toolName === "read_file" || toolName === "list_files" || toolName === "write_file") {
    const path = pathFromExecutionEntry(entry);
    if (toolName === "read_file") {
      return summarizeTimelinePreview(
        joinDetailLines([
          path ? `路径：${path}` : undefined,
          detail,
        ]) || detail,
        3,
        220,
      );
    }
    if (toolName === "write_file") {
      if (/覆盖|overwrite|updated /i.test(detail)) {
        return joinDetailLines([
          "已覆盖",
          path ? `路径：${path}` : undefined,
        ]);
      }
      if (/新建|create(?:d)?|new file/i.test(detail)) {
        return joinDetailLines([
          "新建完成",
          path ? `路径：${path}` : undefined,
        ]);
      }
    }
    return path ? `路径：${path}` : undefined;
  }

  if (toolName === "run_command") {
    const importantLines = detail
      .split(/\r?\n/)
      .filter((line) =>
        line.startsWith("命令：")
        || line.startsWith("目录：")
        || line.startsWith("退出码：")
        || line.startsWith("标准错误："),
      );
    if (importantLines.length > 0) {
      return summarizeTimelinePreview(importantLines.join("\n"), 3, 220);
    }
  }

  if (entry.event === "provider_fallback") {
    const fallbackLine = detail
      .split(/\r?\n/)
      .find((line) => line.startsWith("Fallback Provider：") || line.startsWith("主 Provider："));
    if (fallbackLine) {
      return fallbackLine;
    }
  }

  return summarizeTimelinePreview(detail);
}

function pathFromExecutionEntry(entry: ExecutionEventRecord): string | null {
  const metadata = (entry.metadata || {}) as Record<string, unknown>;
  const argumentsValue = metadata.arguments as Record<string, unknown> | undefined;
  const path = typeof argumentsValue?.path === "string" ? argumentsValue.path.trim() : "";
  if (path) {
    return path;
  }
  const detail = entry.detail || "";
  const localizedMatch = detail.match(/路径：([^\n\r]+)/);
  if (localizedMatch?.[1]) {
    return localizedMatch[1].trim();
  }
  const pathMatch = detail.match(/path[:=]\s*([^\n\r]+)/i);
  if (pathMatch?.[1]) {
    return pathMatch[1].trim();
  }
  const writeMatch = detail.match(/(?:written to|updated|Wrote \d+ characters to)\s+([^\n\r]+)/i);
  return writeMatch?.[1]?.trim() || null;
}

function commandFromExecutionEntry(entry: ExecutionEventRecord): string | null {
  const metadata = (entry.metadata || {}) as Record<string, unknown>;
  const argumentsValue = metadata.arguments as Record<string, unknown> | undefined;
  const command = typeof argumentsValue?.command === "string" ? argumentsValue.command.trim() : "";
  const candidateArgs = argumentsValue?.args;
  const rawArgs = Array.isArray(candidateArgs) ? candidateArgs : [];
  const args = rawArgs.filter((item): item is string => typeof item === "string");
  if (command) {
    return [command, ...args].join(" ").trim();
  }
  const detail = entry.detail || "";
  const match = detail.match(/命令：([^\n\r]+)/);
  return match?.[1]?.trim() || null;
}

function buildRoundStatuses(entries: ExecutionEventRecord[]): Record<number, RoundStatus> {
  const statuses: Record<number, RoundStatus> = {};
  const byRound = new Map<number, ExecutionEventRecord[]>();

  for (const entry of entries) {
    if (!byRound.has(entry.round)) {
      byRound.set(entry.round, []);
    }
    byRound.get(entry.round)?.push(entry);
  }

  byRound.forEach((roundEntries, round) => {
    const summary = summarizeExecution(roundEntries);
    const hasRoundEnd = roundEntries.some((entry) => entry.event === "round_end");
    if (summary.validation === "验证未通过") {
      statuses[round] = "failed";
      return;
    }
    if (summary.blockers.length > 0) {
      statuses[round] = "blocked";
      return;
    }
    if (summary.validation === "验证通过" || hasRoundEnd) {
      statuses[round] = "passed";
      return;
    }
    statuses[round] = "running";
  });

  return statuses;
}

function toneForValidation(validation: string): string {
  if (validation === "验证通过") {
    return "success";
  }
  if (validation === "验证未通过") {
    return "danger";
  }
  if (validation === "存在阻塞") {
    return "warning";
  }
  return "working";
}

function toneForPresence(count: number): string {
  return count > 0 ? "active" : "empty";
}

function toneForBlockers(count: number): string {
  return count > 0 ? "warning" : "clear";
}

function labelForRoundStatus(status: RoundStatus): string {
  if (status === "passed") {
    return "通过";
  }
  if (status === "failed") {
    return "失败";
  }
  if (status === "blocked") {
    return "阻塞";
  }
  return "进行中";
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
    return "阶段";
  }
  if (kind === "model") {
    return "模型";
  }
  if (kind === "tool") {
    return "工具";
  }
  if (kind === "output") {
    return "输出";
  }
  if (kind === "state") {
    return "更新";
  }
  if (kind === "turn") {
    return "本轮";
  }
  if (kind === "session") {
    return "运行";
  }
  return "说明";
}

function labelForTimelineKind(kind: ExecutionEventRecord["kind"]): string {
  return labelForExecutionKind(kind);
}
