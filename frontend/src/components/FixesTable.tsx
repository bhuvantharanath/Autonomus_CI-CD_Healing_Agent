import type { Fix, FailureDetected } from "../store/useRunStore";

interface FixesTableProps {
  fixes: Fix[];
  failuresDetected?: FailureDetected[];
}

export default function FixesTable({ fixes, failuresDetected = [] }: FixesTableProps) {
  const hasContent = fixes.length > 0 || failuresDetected.length > 0;

  if (!hasContent) {
    return (
      <div className="border-2 border-black bg-white p-6 text-center text-sm font-bold text-black shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
        No fixes applied.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* ── Failures Detected ────────────────────────────────────── */}
      {failuresDetected.length > 0 && (
        <div className="border-2 border-black bg-white p-6 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
          <h2 className="mb-4 text-xl font-bold text-black border-b-2 border-black pb-2">
            Errors Detected
            <span className="ml-2 text-sm font-bold text-gray-500">
              ({failuresDetected.length})
            </span>
          </h2>
          <div className="space-y-4">
            {failuresDetected.map((f, i) => (
              <div
                key={i}
                className="border-2 border-black bg-white p-3 shadow-[2px_2px_0px_0px_rgba(0,0,0,1)]"
              >
                {/* Detection line: file — Line N: message */}
                <div className="flex items-start gap-2">
                  <span className="mt-0.5 text-black">●</span>
                  <div className="flex-1">
                    <p className="text-sm text-black font-medium">
                      <span className="font-mono font-bold">{f.file}</span>
                      <span className="text-gray-600"> — Line {f.line}: </span>
                      <span className="text-gray-900">{f.message}</span>
                    </p>
                    {/* Canonical description in bold */}
                    <p className="mt-1 text-xs font-bold text-black">
                      {f.description}
                    </p>
                  </div>
                  <BugTypeBadge type={f.bug_type} />
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Fixes Applied ────────────────────────────────────────── */}
      {fixes.length > 0 && (
        <div className="border-2 border-black bg-white p-6 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
          <h2 className="mb-4 text-xl font-bold text-black border-b-2 border-black pb-2">
            Fixes Applied
            <span className="ml-2 text-sm font-bold text-gray-500">
              ({fixes.length})
            </span>
          </h2>

          {/* Table */}
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead>
                <tr className="border-b-2 border-black text-xs uppercase text-black font-bold">
                  <th className="pb-2 pr-4">File</th>
                  <th className="pb-2 pr-4">Bug Type</th>
                  <th className="pb-2 pr-4 text-right">Line Number</th>
                  <th className="pb-2 pr-4">Commit Message</th>
                  <th className="pb-2">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y-2 divide-gray-200">
                {fixes.map((f, i) => (
                  <tr key={i} className="text-black hover:bg-yellow-50 font-medium">
                    <td className="py-3 pr-4 font-mono text-xs">{f.file}</td>
                    <td className="py-3 pr-4">
                      <BugTypeBadge type={f.bug_type} />
                    </td>
                    <td className="py-3 pr-4 text-right font-mono">{f.line}</td>
                    <td className="max-w-xs truncate py-3 pr-4 text-xs font-bold">
                      {f.commit_message}
                    </td>
                    <td className="py-3">
                      <StatusPill status={f.status} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {/* Canonical description lines (exact test-case format) */}
          <div className="mt-6 space-y-2 border-t-2 border-black pt-4">
            <h3 className="text-xs font-bold uppercase text-black">
              Test Case Output
            </h3>
            {fixes.map((f, i) => (
              <div
                key={i}
                className="flex items-center gap-2 border-2 border-black bg-gray-50 p-2.5 font-mono text-xs font-bold"
              >
                <StatusPill status={f.status} />
                <span className="text-black">
                  {f.description || `${f.bug_type} error in ${f.file} line ${f.line} → Fix: ${f.commit_message}`}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function BugTypeBadge({ type }: { type: string }) {
  const colorMap: Record<string, string> = {
    LINTING: "bg-yellow-200 text-black border-2 border-black",
    SYNTAX: "bg-red-200 text-black border-2 border-black",
    LOGIC: "bg-purple-200 text-black border-2 border-black",
    TYPE_ERROR: "bg-orange-200 text-black border-2 border-black",
    IMPORT: "bg-blue-200 text-black border-2 border-black",
    INDENTATION: "bg-cyan-200 text-black border-2 border-black",
  };
  const color = colorMap[type] || "bg-gray-200 text-black border-2 border-black";
  return (
    <span className={`rounded-none px-2 py-0.5 text-xs font-bold ${color}`}>
      {type}
    </span>
  );
}

function StatusPill({ status }: { status: string }) {
  const isFixed = status === "verified" || status === "applied";
  return (
    <span
      className={`rounded-none border-2 border-black px-2 py-0.5 text-xs font-bold ${isFixed ? "bg-green-400 text-black" : "bg-red-400 text-white"
        }`}
    >
      {isFixed ? "✓ Fixed" : "✗ Failed"}
    </span>
  );
}
