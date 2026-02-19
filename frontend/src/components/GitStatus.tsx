import type { GitOperation } from "../store/useRunStore";

// ── Canonical git stages we track ──────────────────────────────────
const GIT_STAGES = [
  { key: "fork", label: "Fork Repository", icon: "🍴", match: /fork/i },
  { key: "clone", label: "Clone Repository", icon: "📥", match: /clon/i },
  { key: "branch", label: "Create Branch", icon: "🌿", match: /branch/i },
  { key: "push", label: "Push Changes", icon: "🚀", match: /push|commit/i },
  { key: "pr", label: "Pull Request", icon: "🔗", match: /pr |pull request/i },
] as const;

type StageStatus = "pending" | "running" | "success" | "warning" | "error";

interface StageInfo {
  key: string;
  label: string;
  icon: string;
  status: StageStatus;
  message: string;
  timestamp: string;
  prUrl?: string;
}

function resolveStages(ops: GitOperation[]): StageInfo[] {
  const stages: StageInfo[] = GIT_STAGES.map((s) => ({
    key: s.key,
    label: s.label,
    icon: s.icon,
    status: "pending" as StageStatus,
    message: "",
    timestamp: "",
  }));

  for (const op of ops) {
    for (const stage of stages) {
      const def = GIT_STAGES.find((s) => s.key === stage.key)!;
      if (def.match.test(op.message)) {
        // Map backend status → visual status
        if (op.status === "started") {
          if (stage.status === "pending") {
            stage.status = "running";
          }
        } else if (op.status === "success") {
          stage.status = "success";
        } else if (op.status === "warning") {
          stage.status = "warning";
        } else if (op.status === "error") {
          stage.status = "error";
        }
        stage.message = op.message;
        stage.timestamp = op.timestamp;

        // Extract PR URL if present
        if (stage.key === "pr" && op.message.includes("http")) {
          const urlMatch = op.message.match(/(https?:\/\/[^\s]+)/);
          if (urlMatch) stage.prUrl = urlMatch[1];
        }
      }
    }
  }

  return stages;
}

// ── Status visual helpers ──────────────────────────────────────────

const STATUS_STYLES: Record<StageStatus, { dot: string; ring: string; text: string; line: string }> = {
  pending: {
    dot: "bg-gray-700",
    ring: "ring-gray-800",
    text: "text-gray-500",
    line: "border-gray-700",
  },
  running: {
    dot: "bg-indigo-500 animate-pulse",
    ring: "ring-indigo-900/60",
    text: "text-indigo-300",
    line: "border-indigo-700",
  },
  success: {
    dot: "bg-green-500",
    ring: "ring-green-900/60",
    text: "text-green-300",
    line: "border-green-700",
  },
  warning: {
    dot: "bg-yellow-500",
    ring: "ring-yellow-900/60",
    text: "text-yellow-300",
    line: "border-yellow-700",
  },
  error: {
    dot: "bg-red-500",
    ring: "ring-red-900/60",
    text: "text-red-300",
    line: "border-red-700",
  },
};

const STATUS_ICONS: Record<StageStatus, string> = {
  pending: "○",
  running: "◉",
  success: "✓",
  warning: "⚠",
  error: "✗",
};

// ── Component ──────────────────────────────────────────────────────

interface GitStatusProps {
  gitOps: GitOperation[];
  branch?: string;
}

export default function GitStatus({ gitOps, branch }: GitStatusProps) {
  const stages = resolveStages(gitOps);

  // Don't render anything if no git operations happened yet
  const hasAnyActivity = stages.some((s) => s.status !== "pending");
  if (!hasAnyActivity && gitOps.length === 0) return null;

  return (
    <div className="rounded-2xl border border-gray-700 bg-gray-900 p-6 shadow-lg">
      {/* Header */}
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white flex items-center gap-2">
          <span className="text-xl">⑂</span> Git Operations
        </h2>
        {branch && (
          <span className="rounded-full bg-indigo-900/40 px-3 py-1 text-xs font-mono text-indigo-300 border border-indigo-700/50">
            🌿 {branch}
          </span>
        )}
      </div>

      {/* Timeline */}
      <div className="relative ml-4">
        {stages.map((stage, idx) => {
          const style = STATUS_STYLES[stage.status];
          const isLast = idx === stages.length - 1;

          return (
            <div key={stage.key} className="relative flex items-start pb-6">
              {/* Vertical connector line */}
              {!isLast && (
                <div
                  className={`absolute left-[9px] top-5 h-full w-px border-l-2 ${style.line}`}
                />
              )}

              {/* Dot */}
              <div
                className={`relative z-10 flex h-5 w-5 flex-shrink-0 items-center justify-center rounded-full ring-4 ${style.dot} ${style.ring}`}
              >
                <span className="text-[10px] font-bold text-white leading-none">
                  {STATUS_ICONS[stage.status]}
                </span>
              </div>

              {/* Content */}
              <div className="ml-4 min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-base">{stage.icon}</span>
                  <span className={`text-sm font-medium ${style.text}`}>
                    {stage.label}
                  </span>
                  {stage.status !== "pending" && (
                    <StatusPill status={stage.status} />
                  )}
                </div>

                {stage.message && (
                  <p className="mt-1 text-xs text-gray-400 font-mono break-all leading-relaxed">
                    {stage.message}
                  </p>
                )}

                {stage.prUrl && (
                  <a
                    href={stage.prUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-1.5 inline-flex items-center gap-1 rounded-md bg-indigo-900/40 px-3 py-1 text-xs font-medium text-indigo-300 hover:bg-indigo-800/50 hover:text-indigo-200 transition border border-indigo-700/40"
                  >
                    <span>🔗</span> Open Pull Request
                  </a>
                )}

                {stage.timestamp && (
                  <time className="mt-0.5 block text-[10px] text-gray-600">
                    {new Date(stage.timestamp).toLocaleTimeString()}
                  </time>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Summary bar */}
      <div className="mt-2 flex items-center gap-4 rounded-lg bg-gray-800/60 px-4 py-2">
        {["success", "running", "warning", "error", "pending"].map((s) => {
          const count = stages.filter((st) => st.status === s).length;
          if (count === 0) return null;
          const style = STATUS_STYLES[s as StageStatus];
          return (
            <div key={s} className="flex items-center gap-1.5 text-xs">
              <span className={`inline-block h-2.5 w-2.5 rounded-full ${style.dot}`} />
              <span className={style.text}>
                {count} {s}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Sub-components ─────────────────────────────────────────────────

function StatusPill({ status }: { status: StageStatus }) {
  const colors: Record<StageStatus, string> = {
    pending: "bg-gray-800 text-gray-500",
    running: "bg-indigo-900 text-indigo-300 animate-pulse",
    success: "bg-green-900/60 text-green-300",
    warning: "bg-yellow-900/60 text-yellow-300",
    error: "bg-red-900/60 text-red-300",
  };

  return (
    <span
      className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${colors[status]}`}
    >
      {status}
    </span>
  );
}
