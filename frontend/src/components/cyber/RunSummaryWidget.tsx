// Removed unused React import
import type { FinalResults } from "../../store/useRunStore";
import { CyberWidget } from "../CyberWidget";
import { ArrowUpRight } from "lucide-react";

export default function RunSummaryWidget({ r }: { r: FinalResults }) {
    return (
        <CyberWidget colorTheme="orange" headerTitle="CBRPNK" headerCode="#E0A15E" footerCode="UA 570-B">
            <div className="relative">
                <div className="absolute top-0 right-0 w-24 h-24">
                    <ArrowUpRight strokeWidth={4} className="w-full h-full text-black opacity-80" />
                </div>

                <div className="flex gap-4 mb-6 relative z-10 w-2/3">
                    <div className="flex flex-col gap-[2px]">
                        <div className="h-10 w-2 bg-black/80"></div>
                        <div className="h-4 w-2 bg-red-800/80"></div>
                        <div className="text-[10px] font-tech text-center mt-1">A</div>
                    </div>
                    <div className="flex flex-col gap-[2px]">
                        <div className="h-4 w-2 bg-black/80"></div>
                        <div className="h-10 w-2 bg-red-800/80"></div>
                        <div className="text-[10px] font-tech text-center mt-1">D</div>
                    </div>
                    <div className="flex flex-col gap-[2px]">
                        <div className="h-14 w-2 bg-black/80"></div>
                        <div className="text-[10px] font-tech text-center mt-1">S</div>
                    </div>
                    <div className="flex flex-col gap-[2px]">
                        <div className="h-14 w-2 bg-black/80"></div>
                        <div className="text-[10px] font-tech text-center mt-1">R</div>
                    </div>
                    <div className="ml-4 border-l border-black pl-4 flex-1">
                        <div className="font-tech text-[10px] uppercase opacity-70 mb-2 max-w-[150px]">
                            The legacy of autonomous systems remains strong in contemporary infra, inspiring new generation.
                        </div>
                    </div>
                </div>

                <div className="border border-black p-3 space-y-2 mt-4">
                    <div className="flex items-center justify-between border-b border-black/20 pb-1">
                        <span className="font-tech text-xs uppercase w-24">REPO</span>
                        <div className="flex items-center gap-2 min-w-0 flex-1 justify-end">
                            <span className="text-[10px]">▶</span>
                            <span className="font-mono border border-black px-2 text-xs truncate max-w-[220px]" title={r.repository_url}>{r.repository_url}</span>
                        </div>
                    </div>

                    <div className="flex items-center justify-between border-b border-black/20 pb-1">
                        <span className="font-tech text-xs uppercase w-24">TEAM</span>
                        <div className="flex items-center gap-2">
                            <span className="text-[10px]">▶</span>
                            <span className="font-mono border border-black px-2">{r.team_name}</span>
                        </div>
                    </div>

                    <div className="flex items-center justify-between border-b border-black/20 pb-1">
                        <span className="font-tech text-xs uppercase w-24">LEADER</span>
                        <div className="flex items-center gap-2">
                            <span className="text-[10px]">▶</span>
                            <span className="font-mono border border-black px-2">{r.leader_name}</span>
                        </div>
                    </div>

                    <div className="flex items-center justify-between border-b border-black/20 pb-1">
                        <span className="font-tech text-xs uppercase w-24">BRANCH</span>
                        <div className="flex items-center gap-2">
                            <span className="text-[10px]">▶</span>
                            <span className="font-mono border border-black px-2">{r.branch}</span>
                        </div>
                    </div>

                    <div className="flex items-center justify-between border-b border-black/20 pb-1">
                        <span className="font-tech text-xs uppercase w-24">BUGS FIXED</span>
                        <div className="flex items-center gap-2">
                            <span className="text-[10px]">▶</span>
                            <span className="font-mono border border-black px-2">{r.total_fixes_applied} / {r.total_failures_detected}</span>
                        </div>
                    </div>

                    <div className="flex items-center justify-between border-b border-black/20 pb-1">
                        <span className="font-tech text-xs uppercase w-24">TIME (secs)</span>
                        <div className="flex items-center gap-2">
                            <span className="text-[10px]">▶</span>
                            <span className="font-mono border border-black px-2">{r.runtime_seconds.toFixed(2)}</span>
                        </div>
                    </div>

                    <div className="flex items-center justify-between pt-1">
                        <span className="font-tech text-xs uppercase w-24">FINAL CI</span>
                        <div className="flex items-center gap-2">
                            <span className="text-[10px]">▶</span>
                            <span className={`font-mono border px-2 ${r.final_ci_status === "PASSED" ? "border-green-800 text-green-900 bg-green-500/20" : "border-red-800 text-red-900 bg-red-500/20"}`}>
                                {r.final_ci_status}
                            </span>
                        </div>
                    </div>
                </div>
            </div>
        </CyberWidget>
    );
}
