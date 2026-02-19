import { useCallback, useEffect, useRef, useState } from "react";

/* ── Types ─────────────────────────────────────────────────────────── */

interface StatusPayload {
  run_id: string;
  status: string;
  current_step: string;
  current_iteration: number;
  latest_message: string;
  latest_ci_status: string;
  iteration_count: number;
  max_iterations: number;
  total_failures_detected: number;
  total_fixes_applied: number;
  runtime_seconds: number;
}

const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

/* ── Component ─────────────────────────────────────────────────────── */

export default function DebugRunner() {
  /* form state */
  const [repoUrl, setRepoUrl] = useState("");
  const [teamName, setTeamName] = useState("");
  const [leaderName, setLeaderName] = useState("");

  /* run state */
  const [runId, setRunId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [status, setStatus] = useState<StatusPayload | null>(null);
  const [results, setResults] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  /* ── Start run ───────────────────────────────────────────────────── */
  const startRun = useCallback(async () => {
    setError(null);
    setResults(null);
    setStatus(null);
    setRunId(null);
    setStarting(true);

    try {
      const res = await fetch(`${API}/run-agent`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          repo_url: repoUrl,
          team_name: teamName,
          leader_name: leaderName,
        }),
      });

      if (!res.ok) {
        const txt = await res.text();
        throw new Error(`POST /run-agent ${res.status}: ${txt}`);
      }

      const data = await res.json();
      setRunId(data.run_id);
    } catch (err: unknown) {
      setError(String(err));
    } finally {
      setStarting(false);
    }
  }, [repoUrl, teamName, leaderName]);

  /* ── Poll status every 2 s ──────────────────────────────────────── */
  useEffect(() => {
    if (!runId) return;

    const poll = async () => {
      try {
        const res = await fetch(`${API}/status/${runId}`);
        if (!res.ok) return;
        const data: StatusPayload = await res.json();
        setStatus(data);

        if (data.status === "completed" || data.status === "failed") {
          // stop polling & fetch results
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          fetchResults(runId);
        }
      } catch {
        /* network hiccup — retry on next tick */
      }
    };

    poll(); // immediate first call
    pollRef.current = setInterval(poll, 2000);

    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
     
  }, [runId]);

  /* ── Fetch final results ────────────────────────────────────────── */
  const fetchResults = async (id: string) => {
    try {
      const res = await fetch(`${API}/results/${id}`);
      if (res.status === 409 || res.status === 204) return; // still running or empty
      if (!res.ok) {
        const txt = await res.text();
        setError(`GET /results/${id} ${res.status}: ${txt}`);
        return;
      }
      setResults(await res.json());
    } catch (err: unknown) {
      setError(String(err));
    }
  };

  /* ── Reset ──────────────────────────────────────────────────────── */
  const reset = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    setRunId(null);
    setStatus(null);
    setResults(null);
    setError(null);
  };

  /* ── Render helpers ─────────────────────────────────────────────── */
  const badge = (text: string, color: string) => (
    <span
      className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${color}`}
    >
      {text}
    </span>
  );

  const statusColor = (s: string) => {
    switch (s) {
      case "completed":
        return "bg-green-900/60 text-green-300";
      case "failed":
        return "bg-red-900/60 text-red-300";
      case "running":
        return "bg-blue-900/60 text-blue-300";
      default:
        return "bg-gray-800 text-gray-400";
    }
  };

  /* ── UI ──────────────────────────────────────────────────────────── */
  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 p-6">
      <div className="mx-auto max-w-3xl space-y-8">
        {/* Title */}
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-bold tracking-tight">
            <span className="text-amber-400">Debug</span> Runner
          </h1>
          {runId && (
            <button
              onClick={reset}
              className="rounded-lg border border-gray-700 px-3 py-1.5 text-xs text-gray-400 hover:border-gray-500 hover:text-white transition"
            >
              Reset
            </button>
          )}
        </div>

        {/* ── 1) Input fields ──────────────────────────────────────── */}
        {!runId && (
          <section className="rounded-xl border border-gray-800 bg-gray-900 p-6 space-y-4">
            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
              Configuration
            </h2>

            <label className="block">
              <span className="text-xs text-gray-500">Repository URL</span>
              <input
                type="text"
                value={repoUrl}
                onChange={(e) => setRepoUrl(e.target.value)}
                placeholder="https://github.com/org/repo.git"
                className="mt-1 block w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 placeholder-gray-600 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 outline-none"
              />
            </label>

            <div className="grid grid-cols-2 gap-4">
              <label className="block">
                <span className="text-xs text-gray-500">Team Name</span>
                <input
                  type="text"
                  value={teamName}
                  onChange={(e) => setTeamName(e.target.value)}
                  placeholder="team-alpha"
                  className="mt-1 block w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 placeholder-gray-600 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 outline-none"
                />
              </label>

              <label className="block">
                <span className="text-xs text-gray-500">Leader Name</span>
                <input
                  type="text"
                  value={leaderName}
                  onChange={(e) => setLeaderName(e.target.value)}
                  placeholder="Jane Doe"
                  className="mt-1 block w-full rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm text-gray-100 placeholder-gray-600 focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 outline-none"
                />
              </label>
            </div>

            {/* ── 2) Start button ──────────────────────────────────── */}
            <button
              onClick={startRun}
              disabled={starting || !repoUrl.trim()}
              className="w-full rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-indigo-500 disabled:opacity-40 disabled:cursor-not-allowed transition"
            >
              {starting ? "Starting…" : "Start Agent"}
            </button>
          </section>
        )}

        {/* ── Error banner ─────────────────────────────────────────── */}
        {error && (
          <div className="rounded-lg border border-red-800 bg-red-950/60 p-4 text-sm text-red-300">
            {error}
          </div>
        )}

        {/* ── Run ID badge ─────────────────────────────────────────── */}
        {runId && (
          <div className="flex items-center gap-3 text-sm">
            <span className="text-gray-500">run_id</span>
            <code className="rounded bg-gray-800 px-2 py-0.5 font-mono text-xs text-indigo-300">
              {runId}
            </code>
            {status && badge(status.status, statusColor(status.status))}
          </div>
        )}

        {/* ── 3) Live status panel ─────────────────────────────────── */}
        {status && !results && (
          <section className="rounded-xl border border-gray-800 bg-gray-900 p-6 space-y-4">
            <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
              Live Status
              <span className="ml-2 inline-block h-2 w-2 rounded-full bg-blue-400 animate-pulse" />
            </h2>

            <div className="grid grid-cols-2 gap-y-3 gap-x-8 text-sm">
              <Row label="Current Step" value={status.current_step || "—"} />
              <Row
                label="Iteration"
                value={`${status.current_iteration} / ${status.max_iterations || "?"}`}
              />
              <Row label="CI Status" value={status.latest_ci_status || "pending"} />
              <Row
                label="Runtime"
                value={`${status.runtime_seconds.toFixed(1)} s`}
              />
              <Row
                label="Failures"
                value={String(status.total_failures_detected)}
              />
              <Row
                label="Fixes Applied"
                value={String(status.total_fixes_applied)}
              />
            </div>

            {status.latest_message && (
              <div className="rounded-lg bg-gray-800 p-3 text-xs text-gray-300 font-mono whitespace-pre-wrap break-words max-h-40 overflow-y-auto">
                {status.latest_message}
              </div>
            )}
          </section>
        )}

        {/* ── 4) Final results ─────────────────────────────────────── */}
        {results && (
          <section className="space-y-6">
            {/* Failures Detected */}
            {Array.isArray((results as Record<string, unknown>).failures_detected) &&
              (results.failures_detected as Array<{
                file: string;
                line: number;
                bug_type: string;
                message: string;
                description: string;
              }>).length > 0 && (
              <div className="rounded-xl border border-gray-800 bg-gray-900 p-6 space-y-3">
                <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
                  Errors Detected
                </h2>
                <div className="divide-y divide-gray-800">
                  {(
                    results.failures_detected as Array<{
                      file: string;
                      line: number;
                      bug_type: string;
                      message: string;
                      description: string;
                    }>
                  ).map((f, i) => (
                    <div key={i} className="py-3 space-y-1">
                      <div className="flex items-center gap-2 text-sm">
                        <span className="text-red-400">●</span>
                        <span className="font-mono text-indigo-300">
                          {f.file}:{f.line}
                        </span>
                        {badge(f.bug_type, "bg-amber-900/60 text-amber-300")}
                      </div>
                      <p className="text-xs text-gray-300 ml-5">
                        {f.description}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Fixes summary */}
            {Array.isArray((results as Record<string, unknown>).fixes) && (
              <div className="rounded-xl border border-gray-800 bg-gray-900 p-6 space-y-3">
                <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
                  Fixes
                </h2>
                <div className="divide-y divide-gray-800">
                  {(
                    results.fixes as Array<{
                      file: string;
                      bug_type: string;
                      line: number;
                      commit_message: string;
                      status: string;
                      description: string;
                    }>
                  ).map((fix, i) => (
                    <div key={i} className="py-3 space-y-1">
                      <div className="flex items-center gap-2 text-sm">
                        <span className="font-mono text-indigo-300">
                          {fix.file}:{fix.line}
                        </span>
                        {badge(
                          fix.bug_type,
                          "bg-amber-900/60 text-amber-300"
                        )}
                        {badge(
                          fix.status === "applied" || fix.status === "verified"
                            ? "✓ Fixed"
                            : "✗ Failed",
                          fix.status === "applied" || fix.status === "verified"
                            ? "bg-green-900/60 text-green-300"
                            : "bg-red-900/60 text-red-300"
                        )}
                      </div>
                      {/* Canonical test-case format */}
                      <p className="text-xs font-mono text-amber-200 ml-2">
                        {fix.description || `${fix.bug_type} error in ${fix.file} line ${fix.line} → Fix: ${fix.commit_message}`}
                      </p>
                      <p className="text-xs text-gray-500 ml-2">
                        {fix.commit_message}
                      </p>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Pretty-print results.json */}
            <div className="rounded-xl border border-gray-800 bg-gray-900 p-6 space-y-3">
              <h2 className="text-sm font-semibold text-gray-400 uppercase tracking-wider">
                results.json
              </h2>
              <pre className="rounded-lg bg-gray-950 p-4 text-xs text-gray-300 font-mono overflow-x-auto max-h-[32rem] overflow-y-auto">
                {JSON.stringify(results, null, 2)}
              </pre>
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

/* ── Tiny helper ───────────────────────────────────────────────────── */

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span className="text-gray-500">{label}</span>
      <span className="ml-2 font-medium text-gray-200">{value}</span>
    </div>
  );
}
