// Removed unused React import
import { useRunStore } from "../../store/useRunStore";
import { CyberWidget, GridLines } from "../CyberWidget";
import { Play } from "lucide-react";

export default function RunFormWidget() {
    const repoUrl = useRunStore((s) => s.repoUrl);
    const teamName = useRunStore((s) => s.teamName);
    const leaderName = useRunStore((s) => s.leaderName);
    const setRepoUrl = useRunStore((s) => s.setRepoUrl);
    const setTeamName = useRunStore((s) => s.setTeamName);
    const setLeaderName = useRunStore((s) => s.setLeaderName);
    const startRun = useRunStore((s) => s.startRun);
    const polling = useRunStore((s) => s.polling);
    const status = useRunStore((s) => s.status);

    const busy = polling || status?.status === "running" || status?.status === "queued";

    return (
        <CyberWidget colorTheme="red" headerTitle="01" headerCode="Cyberpunk Tech 2025" className="h-full">
            <div className="flex h-full flex-col lg:flex-row gap-6">
                {/* Decorative Side Panel resembling the cybergirl illustration side */}
                <div className="hidden sm:flex lg:w-1/3 relative flex-col items-center justify-between border-b-2 lg:border-b-0 lg:border-r-2 border-black/20 pb-6 lg:pb-0 lg:pr-6">
                    <div className="absolute inset-0 bg-black/10 mix-blend-multiply rounded-xl z-0 overflow-hidden">
                        <GridLines />
                    </div>

                    <div className="relative z-10 w-full flex-1 min-h-[150px] border-2 border-black bg-[#1a1a1a] rounded-lg p-2 mb-4 flex flex-col justify-center items-center shadow-[4px_4px_0_0_rgba(0,0,0,1)]">
                        <div className="text-red-500 font-tech text-xl tracking-widest text-center">SYSTEM</div>
                        <div className="text-white font-mono text-xs opacity-50 mt-1">NO SIGNAL</div>
                        <div className="absolute top-2 right-2 flex gap-1">
                            <span className="w-1.5 h-1.5 bg-red-500 rounded-full animate-ping"></span>
                            <span className="w-1.5 h-1.5 bg-red-500 rounded-full"></span>
                        </div>
                    </div>

                    <div className="relative z-10 font-tech text-6xl font-bold tracking-tighter self-start transform -rotate-90 origin-top-left translate-y-[100px] lg:translate-x-4 opacity-80 mix-blend-color-burn">
                        独創
                    </div>

                    <div className="relative z-10 mt-auto w-full text-center">
                        <div className="h-1 bg-black/50 w-full mb-1"></div>
                        <div className="h-0.5 bg-black/50 w-2/3 mx-auto"></div>
                    </div>
                </div>

                {/* Form Panel */}
                <div className="lg:w-2/3 flex flex-col justify-center">
                    <form
                        onSubmit={(e) => {
                            e.preventDefault();
                            startRun();
                        }}
                        className="space-y-6"
                    >
                        <div className="font-tech text-lg sm:text-2xl font-bold tracking-wider uppercase border-l-4 border-black pl-3 mb-4 sm:mb-6">
                            Initiate <br /> Link Sequence
                        </div>

                        <div className="space-y-4 font-mono font-bold text-sm">
                            <div className="relative group">
                                <label className="block text-black mb-1.5 uppercase tracking-wide">Target Repository</label>
                                <div className="flex">
                                    <span className="bg-black text-white px-3 py-2 flex items-center justify-center font-tech">URL</span>
                                    <input
                                        type="url"
                                        required
                                        placeholder="https://github.com/..."
                                        value={repoUrl}
                                        onChange={(e) => setRepoUrl(e.target.value)}
                                        className="flex-1 rounded-none border-2 border-black border-l-0 bg-white/60 backdrop-blur-sm px-4 py-2 text-black placeholder-black/40 focus:outline-none focus:bg-white transition-colors uppercase"
                                    />
                                </div>
                            </div>

                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <div className="relative">
                                    <label className="block text-black mb-1.5 uppercase tracking-wide">Squadron Div.</label>
                                    <input
                                        required
                                        placeholder="TEAM NAME"
                                        value={teamName}
                                        onChange={(e) => setTeamName(e.target.value)}
                                        className="w-full rounded-none border-b-2 border-black bg-transparent px-2 py-2 text-black placeholder-black/40 focus:outline-none focus:bg-white/50 transition-colors uppercase focus:border-white"
                                    />
                                </div>
                                <div className="relative">
                                    <label className="block text-black mb-1.5 uppercase tracking-wide">Commander</label>
                                    <input
                                        required
                                        placeholder="LEADER IDENT"
                                        value={leaderName}
                                        onChange={(e) => setLeaderName(e.target.value)}
                                        className="w-full rounded-none border-b-2 border-black bg-transparent px-2 py-2 text-black placeholder-black/40 focus:outline-none focus:bg-white/50 transition-colors uppercase focus:border-white"
                                    />
                                </div>
                            </div>
                        </div>

                        <button
                            type="submit"
                            disabled={busy}
                            className="mt-8 overflow-hidden relative group w-full rounded-sm border-2 border-black bg-black px-4 py-4 text-white uppercase font-tech font-bold tracking-[0.2em] transition-all hover:bg-white hover:text-black disabled:cursor-not-allowed disabled:opacity-50 flex items-center justify-center gap-3"
                        >
                            <span className="relative z-10 flex items-center gap-2">
                                {busy ? "Establishing Link..." : "Deploy Agent"}
                                <Play fill="currentColor" size={16} />
                            </span>
                            <div className="absolute inset-0 bg-[url('data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSI0IiBoZWlnaHQ9IjQiPjxyZWN0IHdpZHRoPSI0IiBoZWlnaHQ9IjQiIGZpbGw9IiNmZmYiIGZpbGwtb3BhY2l0eT0iMC4xIi8+PC9zdmc+')] opacity-0 group-hover:opacity-100 transition-opacity"></div>
                        </button>

                        {busy && (
                            <div className="flex gap-1 mt-2 justify-center">
                                <div className="h-1 w-4 bg-black animate-pulse"></div>
                                <div className="h-1 w-4 bg-black animate-pulse delay-75"></div>
                                <div className="h-1 w-4 bg-black animate-pulse delay-150"></div>
                                <div className="h-1 w-4 bg-black animate-pulse delay-300"></div>
                            </div>
                        )}
                    </form>
                </div>
            </div>
        </CyberWidget>
    );
}
