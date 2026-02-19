import { useRunStore } from "../store/useRunStore";

const PHASE_LABELS: Record<string, string> = {
  RUN_TESTS: "Running Tests",
  CLASSIFY: "Classifying Failures",
  PLAN_FIX: "Planning Fixes",
  APPLY_PATCH: "Applying Patches",
  COMMIT_PUSH: "Committing & Pushing",
  WAIT_FOR_CI: "Waiting for CI",
  FETCH_CI_RESULTS: "Fetching CI Results",
  VERIFY: "Verifying",
};

const PHASES = Object.keys(PHASE_LABELS);

export default function LiveProgress() {
  const status = useRunStore((s) => s.status);
  const error = useRunStore((s) => s.error);

  if (!status && !error) return null;

  const activeIdx = PHASES.indexOf(status?.current_step ?? "");

  return (
    <div className="rounded-2xl border border-gray-700 bg-gray-900 p-6 shadow-lg">
      <div className="mb-4 flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white">Live Progress</h2>
        <StatusBadge value={status?.status ?? "unknown"} />
      </div>

      {error && (
        <div className="mb-4 rounded-lg bg-red-900/40 px-4 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      {status && (
        <>
          {/* Iteration + step */}
          <div className="mb-2 text-sm text-gray-400">
            Iteration{" "}
            <span className="font-mono text-white">
              {status.current_iteration}
            </span>{" "}
            / {status.max_iterations || "–"}
          </div>

          {/* Phase pipeline */}
          <div className="mb-4 flex items-center gap-1 overflow-x-auto">
            {PHASES.map((p, i) => (
              <div
                key={p}
                className={`flex-shrink-0 rounded-md px-2 py-1 text-xs font-medium transition ${
                  i < activeIdx
                    ? "bg-green-800 text-green-200"
                    : i === activeIdx
                      ? "bg-indigo-600 text-white animate-pulse"
                      : "bg-gray-800 text-gray-500"
                }`}
              >
                {PHASE_LABELS[p]}
              </div>
            ))}
          </div>

          {/* Latest message */}
          {status.latest_message && (
            <p className="rounded-lg bg-gray-800 px-4 py-2 text-sm text-gray-300 font-mono break-all">
              {status.latest_message}
            </p>
          )}

          {/* Quick stats */}
          <div className="mt-4 grid grid-cols-4 gap-3 text-center">
            <Stat label="Failures" value={status.total_failures_detected} />
            <Stat label="Fixes" value={status.total_fixes_applied} />
            <Stat
              label="CI"
              value={status.latest_ci_status || "–"}
              color={
                status.latest_ci_status === "PASSED"
                  ? "text-green-400"
                  : status.latest_ci_status === "FAILED"
                    ? "text-red-400"
                    : "text-gray-400"
              }
            />
            <Stat
              label="Runtime"
              value={`${status.runtime_seconds.toFixed(1)}s`}
            />
          </div>
        </>
      )}
    </div>
  );
}

function StatusBadge({ value }: { value: string }) {
  const colors: Record<string, string> = {
    queued: "bg-yellow-800 text-yellow-200",
    running: "bg-blue-800 text-blue-200",
    completed: "bg-green-800 text-green-200",
    failed: "bg-red-800 text-red-200",
  };
  return (
    <span
      className={`rounded-full px-3 py-0.5 text-xs font-semibold uppercase ${colors[value] ?? "bg-gray-700 text-gray-300"}`}
    >
      {value}
    </span>
  );
}

function Stat({
  label,
  value,
  color = "text-white",
}: {
  label: string;
  value: string | number;
  color?: string;
}) {
  return (
    <div>
      <div className={`text-xl font-bold ${color}`}>{value}</div>
      <div className="text-xs text-gray-500">{label}</div>
    </div>
  );
}
