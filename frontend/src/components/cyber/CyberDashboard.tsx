// Removed unused React import
import { useRunStore } from "../../store/useRunStore";
import RunFormWidget from "./RunFormWidget";
import RunSummaryWidget from "./RunSummaryWidget";
import ScoreWidget from "./ScoreWidget";
import TimelineWidget from "./TimelineWidget";
import FixesTableWidget from "./FixesTableWidget";
import { ShieldAlert, AlertTriangle, Loader2 } from "lucide-react";

export default function CyberDashboard() {
    const results = useRunStore((s) => s.results);
    const status = useRunStore((s) => s.status);
    const error = useRunStore((s) => s.error);
    const polling = useRunStore((s) => s.polling);
    const reset = useRunStore((s) => s.reset);

    return (
        <div className="min-h-screen bg-(--color-cyber-dark) text-white font-cyber p-4 md:p-8 overflow-x-hidden relative flex flex-col items-center">
            {/* Background Decorative Grid */}
            <div className="fixed inset-0 pointer-events-none opacity-5"
                style={{ backgroundImage: 'linear-gradient(rgba(255,255,255,1) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,1) 1px, transparent 1px)', backgroundSize: '40px 40px' }}
            />

            {/* Top Bar Area */}
            <header className="w-full max-w-7xl relative z-20 mb-6 flex justify-between items-end border-b-2 border-white/20 pb-4">
                <div className="flex gap-4 items-center">
                    <ShieldAlert className="text-red-500 animate-pulse" size={32} />
                    <div>
                        <h1 className="text-2xl md:text-4xl font-tech font-bold tracking-widest uppercase">
                            Self-Healing <span className="text-red-500">Node</span>
                        </h1>
                        <p className="font-mono text-xs opacity-50 tracking-widest mt-1">SYS_VER 2.4.0 // ACTIVE</p>
                    </div>
                </div>

                {/* Reset / New Run Button */}
                {(status || results) && (
                    <button
                        onClick={reset}
                        className="border-2 border-white px-6 py-2 uppercase font-tech font-bold text-sm tracking-widest hover:bg-white hover:text-black transition-colors"
                    >
                        Reboot Interface
                    </button>
                )}
            </header>

            {/* Main Dashboard Layout */}
            <main className="w-full max-w-7xl relative z-10 flex flex-col gap-6">

                {/* ── ROW 1: Run Form (always visible, full width) ─── */}
                <div className="w-full">
                    <RunFormWidget />
                </div>

                {/* Error Banner */}
                {error && (
                    <div className="w-full flex items-center gap-3 border-2 border-red-500 bg-red-500/10 rounded-[16px] p-4 text-red-400 font-mono text-sm">
                        <AlertTriangle size={20} className="flex-shrink-0" />
                        <span>{error}</span>
                    </div>
                )}

                {/* Live Progress Panel - shown while running */}
                {!results && (polling || status?.status === "running" || status?.status === "queued") && (
                    <div className="w-full border-2 border-white/20 rounded-[24px] bg-white/5 p-4 sm:p-6 space-y-4 relative overflow-hidden">
                        <div className="bg-noise absolute inset-0 pointer-events-none opacity-10 mix-blend-overlay" />
                        <div className="relative z-10">
                            <div className="flex items-center justify-between mb-4">
                                <h3 className="font-tech text-base sm:text-lg font-bold uppercase tracking-widest flex items-center gap-2">
                                    <Loader2 size={18} className="animate-spin text-red-500" />
                                    Live Status
                                </h3>
                                <span className="font-mono text-xs border border-white/20 px-3 py-1 rounded-sm uppercase tracking-widest animate-pulse">
                                    {status?.status ?? "connecting..."}
                                </span>
                            </div>

                            {status && (
                                <>
                                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-center font-mono text-sm mb-4">
                                        <div className="border border-white/10 p-3 rounded-sm">
                                            <div className="text-xl sm:text-2xl font-bold text-white">{status.current_iteration}/{status.max_iterations || "?"}</div>
                                            <div className="text-xs text-white/50 uppercase mt-1">Iteration</div>
                                        </div>
                                        <div className="border border-white/10 p-3 rounded-sm">
                                            <div className="text-xl sm:text-2xl font-bold text-white">{status.total_failures_detected}</div>
                                            <div className="text-xs text-white/50 uppercase mt-1">Failures</div>
                                        </div>
                                        <div className="border border-white/10 p-3 rounded-sm">
                                            <div className="text-xl sm:text-2xl font-bold text-white">{status.total_fixes_applied}</div>
                                            <div className="text-xs text-white/50 uppercase mt-1">Fixes</div>
                                        </div>
                                        <div className="border border-white/10 p-3 rounded-sm">
                                            <div className="text-xl sm:text-2xl font-bold text-white">{status.runtime_seconds.toFixed(1)}s</div>
                                            <div className="text-xs text-white/50 uppercase mt-1">Runtime</div>
                                        </div>
                                    </div>

                                    {status.current_step && (
                                        <div className="font-mono text-xs text-white/60 border-t border-white/10 pt-3">
                                            <span className="text-red-400">STEP &gt;</span> {status.current_step}
                                        </div>
                                    )}

                                    {status.latest_message && (
                                        <div className="mt-2 font-mono text-xs text-white/40 bg-black/20 p-2 rounded-sm max-h-24 overflow-y-auto break-all">
                                            {status.latest_message}
                                        </div>
                                    )}
                                </>
                            )}
                        </div>
                    </div>
                )}

                {/* Awaiting-input placeholder when no results yet */}
                {!results && !polling && status?.status !== "running" && status?.status !== "queued" && (
                    <div className="w-full min-h-[200px] flex flex-col items-center justify-center border-2 border-dashed border-white/10 rounded-[24px] bg-white/5 relative overflow-hidden group">
                        <div className="absolute inset-0 bg-noise opacity-20 pointer-events-none mix-blend-overlay"></div>
                        <div className="text-center font-mono opacity-30 group-hover:opacity-50 transition-opacity">
                            <div className="text-4xl mb-2">[-_-]</div>
                            <div className="uppercase tracking-widest">Awaiting Input...</div>
                            <div className="text-[10px] mt-4 max-w-xs mx-auto">
                                INITIALIZE LINK SEQUENCE TO COMMENCE SYSTEM SCAN AND HEALING PROCEDURES
                            </div>
                        </div>
                    </div>
                )}

                {/* ── ROW 2: Summary + Score side-by-side ─────────── */}
                {results && (
                    <div className="w-full grid grid-cols-1 md:grid-cols-2 gap-6">
                        {/* Run Summary (Orange) */}
                        <RunSummaryWidget r={results} />

                        {/* Score Widget (Green) */}
                        <ScoreWidget score={results.score} />
                    </div>
                )}

                {/* ── ROW 3: Fixes Table (FIX DB) — taller ───────── */}
                {results && (
                    <div className="w-full min-h-[480px] sm:min-h-[540px]">
                        <FixesTableWidget fixes={results.fixes} />
                    </div>
                )}

                {/* ── ROW 4: Timeline (Iteration Matrix) — full width */}
                {results && (
                    <div className="w-full">
                        <TimelineWidget fixes={results.fixes} timeline={results.ci_timeline} maxIterations={status?.max_iterations} />
                    </div>
                )}

            </main>
        </div>
    );
}
