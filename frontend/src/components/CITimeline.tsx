import type { CITimelineEntry } from "../store/useRunStore";

export default function CITimeline({ timeline, maxIterations }: { timeline: CITimelineEntry[]; maxIterations?: number }) {
  if (timeline.length === 0) {
    return (
      <div className="border-2 border-black bg-white p-6 text-center text-sm font-bold text-black shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
        No CI runs recorded.
      </div>
    );
  }

  return (
    <div className="border-2 border-black bg-white p-6 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
      <div className="mb-6 flex items-center justify-between border-b-2 border-black pb-2">
        <h2 className="text-xl font-bold text-black">CI Timeline</h2>
        {maxIterations && (
          <span className="rounded-none border-2 border-black bg-yellow-400 px-3 py-1 text-xs font-bold text-black">
            {timeline.length}/{maxIterations} iterations
          </span>
        )}
      </div>

      {/* Horizontal bar visualization */}
      <div className="mb-8 flex items-end gap-2 border-b-2 border-black pb-4">
        {timeline.map((entry, i) => (
          <div key={i} className="flex flex-1 flex-col items-center gap-2">
            {/* Bar */}
            <div
              className={`w-full rounded-none border-2 border-black border-b-0 transition-all duration-500 ${entry.status === "PASSED" ? "bg-green-400" : "bg-red-400"
                }`}
              style={{ height: entry.status === "PASSED" ? "64px" : "40px" }}
            />
            {/* Label */}
            <span className="text-xs font-bold text-black">
              #{entry.iteration}
            </span>
          </div>
        ))}
      </div>

      {/* Detail rows */}
      <ol className="relative border-l-2 border-black ml-3 space-y-6">
        {timeline.map((entry, i) => {
          const passed = entry.status === "PASSED";
          return (
            <li key={i} className="ml-6">
              {/* Dot */}
              <span
                className={`absolute -left-[11px] flex h-5 w-5 items-center justify-center rounded-full border-2 border-black ring-4 ring-white ${passed ? "bg-green-400" : "bg-red-400"
                  }`}
              />
              <div className="flex items-center gap-3">
                <span className="text-sm font-bold text-black">
                  Iteration {entry.iteration}
                </span>
                <span
                  className={`rounded-none border-2 border-black px-2 py-0.5 text-xs font-bold ${passed
                    ? "bg-green-400 text-black"
                    : "bg-red-400 text-white"
                    }`}
                >
                  {entry.status}
                </span>
              </div>
              <time className="mt-1 block text-xs font-bold text-gray-500">
                {formatTime(entry.timestamp)}
              </time>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function formatTime(iso: string): string {
  if (!iso) return "–";
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}
