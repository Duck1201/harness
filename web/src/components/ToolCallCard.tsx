import {
  AlertTriangle,
  Check,
  ChevronDown,
  Clock3,
  LoaderCircle,
  Wrench,
} from "lucide-react";
import type { ToolCall } from "../types";

interface ToolCallCardProps {
  tool: ToolCall;
}

const statusLabel = {
  success: "Concluída",
  error: "Erro",
  running: "Em execução",
} as const;

export function ToolCallCard({ tool }: ToolCallCardProps) {
  const StatusIcon =
    tool.status === "success"
      ? Check
      : tool.status === "error"
        ? AlertTriangle
        : LoaderCircle;

  return (
    <details
      className={`tool-card tool-card--${tool.status}`}
      open={tool.status === "error" ? true : undefined}
    >
      <summary>
        <span className="tool-icon" aria-hidden="true">
          <Wrench size={15} />
        </span>
        <span className="tool-heading">
          <span className="tool-name">{tool.name}</span>
          <span className="tool-label">{tool.label}</span>
        </span>
        <span className={`tool-status tool-status--${tool.status}`}>
          <StatusIcon
            size={13}
            className={tool.status === "running" ? "spin" : undefined}
          />
          {statusLabel[tool.status]}
        </span>
        {tool.durationMs !== undefined && (
          <span className="tool-duration">
            <Clock3 size={12} /> {tool.durationMs} ms
          </span>
        )}
        <ChevronDown className="summary-chevron" size={16} aria-hidden="true" />
      </summary>
      <div className="tool-body">
        <div className="tool-arguments">
          <span className="eyebrow">Argumentos</span>
          <dl>
            {Object.entries(tool.arguments).map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{formatValue(value)}</dd>
              </div>
            ))}
          </dl>
        </div>
        {(tool.error || tool.result || tool.summary) && <div className="tool-result">
          <span className="eyebrow">
            {tool.status === "error" ? "Erro retornado" : "Resultado"}
          </span>
          <p>{tool.error ?? tool.result ?? tool.summary}</p>
        </div>}
        {tool.diff && <UnifiedDiff diff={tool.diff} />}
      </div>
    </details>
  );
}

export function UnifiedDiff({ diff }: { diff: string }) {
  return (
    <div className="diff-block">
      <div className="diff-heading">
        <span>Unified diff</span>
        <span>diff</span>
      </div>
      <pre aria-label="Unified diff">
        {diff.split("\n").map((line, index) => {
          const className = line.startsWith("+")
            ? "diff-add"
            : line.startsWith("-")
              ? "diff-remove"
              : line.startsWith("@@")
                ? "diff-hunk"
                : "";

          return (
            <span className={className} key={`${index}-${line}`}>
              {line || " "}
              {"\n"}
            </span>
          );
        })}
      </pre>
    </div>
  );
}

function formatValue(value: unknown) {
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
