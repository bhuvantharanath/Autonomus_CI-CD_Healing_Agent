import { create } from "zustand";

// ── Types ───────────────────────────────────────────────────────────

export interface Fix {
  file: string;
  bug_type: string;
  line: number;
  commit_message: string;
  status: string;
  description: string;
  failure_message: string;
}

export interface FailureDetected {
  file: string;
  line: number;
  bug_type: string;
  message: string;
  description: string;
  iteration: number;
}

export interface CITimelineEntry {
  iteration: number;
  status: "PASSED" | "FAILED";
  timestamp: string;
}

export interface Score {
  base: number;
  speed_bonus: number;
  commit_penalty: number;
  total_commits: number;
  final_score: number;
}

export interface GitOperation {
  agent: string;
  status: string;
  message: string;
  timestamp: string;
}

export interface RunStatus {
  run_id: string;
  status: "queued" | "running" | "completed" | "failed";
  repo_url: string;
  branch: string;
  team_name: string;
  leader_name: string;
  current_step: string;
  current_iteration: number;
  latest_message: string;
  iteration_count: number;
  max_iterations: number;
  latest_ci_status: string;
  total_failures_detected: number;
  total_fixes_applied: number;
  runtime_seconds: number;
  current_agent: string;
  created_at: string;
  updated_at: string;
  git_operations: GitOperation[];
}

export interface FinalResults {
  repository_url: string;
  branch: string;
  team_name: string;
  leader_name: string;
  total_failures_detected: number;
  total_fixes_applied: number;
  final_ci_status: string;
  runtime_seconds: number;
  failures_detected: FailureDetected[];
  fixes: Fix[];
  ci_timeline: CITimelineEntry[];
  git_operations: GitOperation[];
  score: Score;
  generated_at: string;
}

// ── Store ───────────────────────────────────────────────────────────

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

interface RunStore {
  // Form
  repoUrl: string;
  teamName: string;
  leaderName: string;
  setRepoUrl: (v: string) => void;
  setTeamName: (v: string) => void;
  setLeaderName: (v: string) => void;

  // Run state
  runId: string | null;
  status: RunStatus | null;
  results: FinalResults | null;
  error: string | null;
  polling: boolean;

  // Actions
  startRun: () => Promise<void>;
  pollStatus: () => Promise<void>;
  fetchResults: () => Promise<void>;
  reset: () => void;

  // Internal
  _pollTimer: ReturnType<typeof setInterval> | null;
  _startPolling: () => void;
  _stopPolling: () => void;
}

export const useRunStore = create<RunStore>((set, get) => ({
  // Form defaults
  repoUrl: "",
  teamName: "",
  leaderName: "",
  setRepoUrl: (v) => set({ repoUrl: v }),
  setTeamName: (v) => set({ teamName: v }),
  setLeaderName: (v) => set({ leaderName: v }),

  // Run state
  runId: null,
  status: null,
  results: null,
  error: null,
  polling: false,
  _pollTimer: null,

  // ── Start a new run ───────────────────────────────────────────

  startRun: async () => {
    const { repoUrl, teamName, leaderName } = get();
    set({ error: null, results: null, status: null, runId: null });

    try {
      const res = await fetch(`${API_BASE}/run-agent`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          repo_url: repoUrl,
          team_name: teamName,
          leader_name: leaderName,
        }),
      });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }

      const data = await res.json();
      set({ runId: data.run_id });
      get()._startPolling();
    } catch (e: unknown) {
      set({ error: (e as Error).message });
    }
  },

  // ── Poll /status/{run_id} ────────────────────────────────────

  pollStatus: async () => {
    const { runId } = get();
    if (!runId) return;

    try {
      const res = await fetch(`${API_BASE}/status/${runId}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: RunStatus = await res.json();
      set({ status: data });

      if (data.status === "completed" || data.status === "failed") {
        get()._stopPolling();
        if (data.status === "completed") {
          await get().fetchResults();
        }
      }
    } catch (e: unknown) {
      set({ error: (e as Error).message });
      get()._stopPolling();
    }
  },

  // ── Fetch final results ──────────────────────────────────────

  fetchResults: async () => {
    const { runId } = get();
    if (!runId) return;

    try {
      const res = await fetch(`${API_BASE}/results/${runId}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: FinalResults = await res.json();
      set({ results: data });
    } catch (e: unknown) {
      set({ error: (e as Error).message });
    }
  },

  // ── Reset ────────────────────────────────────────────────────

  reset: () => {
    get()._stopPolling();
    set({
      runId: null,
      status: null,
      results: null,
      error: null,
      polling: false,
    });
  },

  // ── Polling helpers ──────────────────────────────────────────

  _startPolling: () => {
    get()._stopPolling();
    set({ polling: true });
    // Fire immediately, then every 2 s
    get().pollStatus();
    const timer = setInterval(() => get().pollStatus(), 2000);
    set({ _pollTimer: timer });
  },

  _stopPolling: () => {
    const timer = get()._pollTimer;
    if (timer) clearInterval(timer);
    set({ _pollTimer: null, polling: false });
  },
}));
