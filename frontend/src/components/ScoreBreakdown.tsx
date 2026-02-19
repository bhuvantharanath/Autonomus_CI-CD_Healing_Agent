import type { Score } from "../store/useRunStore";

export default function ScoreBreakdown({ score }: { score: Score }) {
  const pct = Math.min(score.final_score, 110);

  return (
    <div className="border-2 border-black bg-white p-6 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
      <h2 className="mb-4 text-xl font-bold text-black border-b-2 border-black pb-2">
        Score Breakdown
      </h2>

      {/* Big score */}
      <div className="mb-4 flex items-end gap-2">
        <span className="text-5xl font-extrabold text-black">
          {score.final_score}
        </span>
        <span className="mb-1 text-sm font-bold text-gray-500">/ 110</span>
      </div>

      {/* Bar */}
      <div className="mb-4 h-4 w-full border-2 border-black bg-gray-100">
        <div
          className="h-full bg-yellow-400 transition-all duration-700 border-r-2 border-black"
          style={{ width: `${(pct / 110) * 100}%` }}
        />
      </div>

      {/* Breakdown */}
      <ul className="space-y-2 text-sm">
        <li className="flex justify-between">
          <span className="font-bold text-gray-700">Base score</span>
          <span className="font-mono font-bold text-black">{score.base}</span>
        </li>
        <li className="flex justify-between">
          <span className="font-bold text-gray-700">Speed bonus (&lt; 5 minutes)</span>
          <span
            className={`font-mono font-bold ${score.speed_bonus > 0 ? "text-green-600" : "text-gray-500"}`}
          >
            +{score.speed_bonus}
          </span>
        </li>
        <li className="flex justify-between">
          <span className="font-bold text-gray-700">
            Efficiency penalty ({score.total_commits} commits)
          </span>
          <span
            className={`font-mono font-bold ${score.commit_penalty < 0 ? "text-red-600" : "text-gray-500"}`}
          >
            {score.commit_penalty}
          </span>
        </li>
      </ul>
    </div>
  );
}
