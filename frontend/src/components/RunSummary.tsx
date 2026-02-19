import type { FinalResults } from "../store/useRunStore";

export default function RunSummary({ r }: { r: FinalResults }) {
  return (
    <div className="border-2 border-black bg-white p-6 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]">
      <h2 className="mb-4 text-xl font-bold text-black border-b-2 border-black pb-2">
        Run Summary
      </h2>

      <dl className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
        <Row label="Repository" value={r.repository_url} mono />
        <Row label="Branch" value={r.branch} mono />
        <Row label="Team" value={r.team_name} />
        <Row label="Leader" value={r.leader_name} />
        <Row label="Failures Detected" value={r.total_failures_detected} />
        <Row label="Fixes Applied" value={r.total_fixes_applied} />
        <Row
          label="Final CI"
          value={r.final_ci_status}
          color={
            r.final_ci_status === "PASSED" ? "text-green-600 bg-green-100 px-2 py-0.5 border border-green-600 rounded-sm" : "text-red-600 bg-red-100 px-2 py-0.5 border border-red-600 rounded-sm"
          }
        />
        <Row label="Runtime" value={`${r.runtime_seconds.toFixed(1)}s`} />
      </dl>
    </div>
  );
}

function Row({
  label,
  value,
  mono = false,
  color = "text-black",
}: {
  label: string;
  value: string | number;
  mono?: boolean;
  color?: string;
}) {
  return (
    <>
      <dt className="text-gray-700 font-bold">{label}</dt>
      <dd
        className={`truncate font-medium ${color} ${mono ? "font-mono text-xs leading-6" : ""}`}
      >
        {value}
      </dd>
    </>
  );
}
