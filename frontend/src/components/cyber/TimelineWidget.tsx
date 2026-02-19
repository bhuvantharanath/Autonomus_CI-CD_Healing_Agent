// Removed unused React import
import type { Fix, CITimelineEntry } from "../../store/useRunStore";
import { CyberWidget } from "../CyberWidget";

export default function TimelineWidget({
    fixes,
    timeline,
    maxIterations,
}: {
    fixes?: Fix[];
    timeline?: CITimelineEntry[];
    maxIterations?: number;
}) {
    return (
        <CyberWidget colorTheme="grey" headerTitle="25" headerCode="Retro AI Technology" className="w-full">
            <div className="absolute top-6 right-6 hidden sm:flex flex-col gap-1">
                <div className="flex gap-1 justify-end">
                    <div className="w-3 h-3 bg-black"></div>
                    <div className="w-3 h-3 bg-black"></div>
                    <div className="w-3 h-3 bg-black opacity-30"></div>
                </div>
                <div className="flex gap-1 justify-end">
                    <div className="w-3 h-3 bg-black opacity-30"></div>
                    <div className="w-3 h-3 bg-black"></div>
                    <div className="w-3 h-3 bg-black"></div>
                </div>
            </div>

            <div className="relative pt-4 sm:pt-8">
                {/* ── Iteration Matrix ─────────────────────────── */}
                <div className="flex items-center justify-between border-b-2 border-black pb-1 mb-4">
                    <h3 className="font-tech font-bold uppercase text-sm sm:text-base">Iteration Matrix</h3>
                    {maxIterations && timeline && (
                        <span className="font-mono text-xs border border-black px-2 py-0.5 font-bold">
                            {timeline.length} / {maxIterations} iterations
                        </span>
                    )}
                </div>

                {timeline && timeline.length > 0 && (
                    <div className="mb-6 overflow-x-auto -mx-2 sm:mx-0">
                        <table className="w-full font-mono text-sm border-collapse min-w-[400px]">
                            <thead>
                                <tr className="border-b-2 border-black/30 font-tech text-[10px] sm:text-xs uppercase tracking-wider">
                                    <th className="p-2 sm:p-3 text-left">Iteration</th>
                                    <th className="p-2 sm:p-3 text-left">Timestamp</th>
                                    <th className="p-2 sm:p-3 text-right">Status</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-black/10">
                                {timeline.map((st, i) => (
                                    <tr key={i} className="hover:bg-black/5 transition-colors">
                                        <td className="p-2 sm:p-3 font-tech text-xs">
                                            ITER {st.iteration}
                                        </td>
                                        <td className="p-2 sm:p-3 text-xs">
                                            {new Date(st.timestamp).toLocaleString()}
                                        </td>
                                        <td className="p-2 sm:p-3 text-right">
                                            <span className={`px-2 py-0.5 text-xs font-bold inline-block ${st.status === "PASSED" ? "bg-green-600/20 text-green-900 border border-green-800" : "bg-red-600/20 text-red-900 border border-red-800"}`}>
                                                {st.status}
                                            </span>
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}

                {/* ── Patch Deployment ────────────────────────── */}
                <h3 className="font-tech font-bold uppercase text-sm sm:text-base border-b-2 border-black pb-1 mb-4 mt-6">Patch Deployment</h3>

                {fixes && fixes.length > 0 ? (
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2 sm:gap-3 max-h-[400px] overflow-y-auto pr-1 custom-scrollbar">
                        {fixes.map((f, i) => (
                            <div key={i} className="border-2 border-black bg-white/20 p-2 sm:p-3 text-xs font-mono hover:bg-black hover:text-(--color-cyber-grey) transition-colors group">
                                <div className="flex justify-between items-center mb-1">
                                    <span className="font-bold flex items-center gap-1">
                                        <span className="group-hover:text-red-400">&gt;</span> ERROR:
                                    </span>
                                    <span className="bg-black text-(--color-cyber-grey) group-hover:bg-(--color-cyber-grey) group-hover:text-black px-1 py-0.5 text-[10px]">L{f.line ?? "?"}</span>
                                </div>
                                <div className="truncate opacity-80 mb-2">{f.failure_message || "Unknown error"}</div>
                                <div className="font-bold border-t border-black/20 group-hover:border-white/20 pt-1 mt-1 text-(--color-cyber-dark)">
                                    <span className="group-hover:text-green-400">+</span> FIX APPLIED <span className="text-[10px] font-tech text-black/50 group-hover:text-white/50">{f.bug_type}</span>
                                </div>
                            </div>
                        ))}
                    </div>
                ) : (
                    <div className="p-4 border border-black/20 text-center font-mono text-xs opacity-60 italic">
                        No patches deployed
                    </div>
                )}
            </div>

        </CyberWidget>
    );
}
