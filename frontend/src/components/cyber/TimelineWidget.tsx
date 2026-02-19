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
        <CyberWidget colorTheme="grey" headerTitle="25" headerCode="Retro AI Technology" className="row-span-2">
            <div className="absolute top-6 right-6 flex flex-col gap-1">
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

            <div className="relative pt-8">
                <div className="flex items-center justify-between border-b-2 border-black pb-1 mb-4">
                    <h3 className="font-tech font-bold uppercase">Iteration Matrix</h3>
                    {maxIterations && timeline && (
                        <span className="font-mono text-xs border border-black px-2 py-0.5 font-bold">
                            {timeline.length} / {maxIterations} iterations
                        </span>
                    )}
                </div>

                {timeline && timeline.length > 0 && (
                    <div className="mb-6 border-l-4 border-black pl-4 py-2 space-y-4 font-mono text-sm max-h-48 overflow-y-auto">
                        {timeline.map((st, i) => (
                            <div key={i} className="flex flex-col border-b border-black/10 pb-2">
                                <div className="flex items-center justify-between">
                                    <span className="font-tech text-xs opacity-70">ITER {st.iteration}</span>
                                    <span className={`px-2 text-xs font-bold ${st.status === "PASSED" ? "bg-green-600/20 text-green-900 border border-green-800" : "bg-red-600/20 text-red-900 border border-red-800"}`}>
                                        {st.status}
                                    </span>
                                </div>
                                <div className="text-xs truncate max-w-[250px] mt-1">{new Date(st.timestamp).toLocaleString()}</div>
                            </div>
                        ))}
                    </div>
                )}

                <h3 className="font-tech font-bold uppercase border-b-2 border-black pb-1 mb-4 mt-6">Patch Deployment</h3>

                {fixes && fixes.length > 0 ? (
                    <div className="space-y-2 max-h-64 overflow-y-auto pr-2 custom-scrollbar">
                        {fixes.map((f, i) => (
                            <div key={i} className="border-2 border-black bg-white/20 p-2 text-xs font-mono hover:bg-black hover:text-(--color-cyber-grey) transition-colors group">
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
