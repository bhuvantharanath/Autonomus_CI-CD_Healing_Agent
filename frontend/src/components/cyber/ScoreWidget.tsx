// Removed initial backticks
// Removed unused React import
import type { FinalResults } from "../../store/useRunStore";
import { CyberWidget } from "../CyberWidget";
import { Target } from "lucide-react";

export default function ScoreWidget({ score }: { score: FinalResults["score"] }) {
    if (!score) return null;

    return (
        <CyberWidget colorTheme="green" headerTitle="DPM SYSTM" footerCode="TS26" headerCode="©2023">
            <div className="flex items-start justify-between border-b-2 border-black/20 pb-4 mb-4">
                <div className="font-tech text-xs uppercase tracking-widest max-w-[120px] leading-tight">
                    SORT BEFORE SENDING
                </div>
                <div className="flex items-center gap-2 border-2 border-black rounded-full px-2 py-0.5">
                    <Target size={14} />
                    <span className="font-tech text-xs font-bold uppercase tracking-widest">Score</span>
                </div>
            </div>

            <div className="space-y-4">
                {/* Total Score */}
                <div className="flex items-center justify-between bg-black text-(--color-cyber-green) p-3 rounded-sm font-tech">
                    <span className="text-sm">EVALUATION</span>
                    <span className="text-4xl font-bold">{score.final_score}</span>
                </div>

                {/* Visual Progress Bar */}
                <div>
                    <div className="flex justify-between text-xs font-mono mb-1">
                        <span>0</span>
                        <span>{score.final_score} / 110</span>
                    </div>
                    <div className="h-3 w-full border-2 border-black bg-white/20 rounded-sm overflow-hidden">
                        <div
                            className="h-full bg-black transition-all duration-700"
                            style={{ width: `${(Math.min(score.final_score, 110) / 110) * 100}%` }}
                        />
                    </div>
                </div>

                {/* Breakdown */}
                <div className="grid grid-cols-2 gap-2 text-xs font-mono">
                    <ScoreItemCard label="BASE" value={score.base} />
                    <ScoreItemCard label="SPEED BONUS" value={score.speed_bonus} />
                    <ScoreItemCard label="CMT PENALTY" value={score.commit_penalty} />
                    <ScoreItemCard label="COMMITS" value={score.total_commits} />
                </div>
            </div>
        </CyberWidget>
    );
}

function ScoreItemCard({ label, value }: { label: string; value: number }) {
    return (
        <div className="border border-black p-2 flex flex-col justify-between h-full bg-white/10 relative group hover:bg-black hover:text-(--color-cyber-green) transition-colors">
            <div className="font-tech uppercase mb-2 group-hover:opacity-80 flex items-center justify-between">
                {label}
                <div className="w-1.5 h-1.5 bg-black group-hover:bg-(--color-cyber-green) rounded-full"></div>
            </div>
            <div className="flex items-end justify-end">
                <div className="pl-2 border-l border-black/20 group-hover:border-(--color-cyber-green)/50 min-w-16 text-right font-bold text-lg">
                    {value}
                </div>
            </div>
        </div>
    );
}
// EOF
