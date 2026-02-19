import { useRunStore } from "../store/useRunStore";

export default function RunForm() {
  const repoUrl = useRunStore((s) => s.repoUrl);
  const teamName = useRunStore((s) => s.teamName);
  const leaderName = useRunStore((s) => s.leaderName);
  const setRepoUrl = useRunStore((s) => s.setRepoUrl);
  const setTeamName = useRunStore((s) => s.setTeamName);
  const setLeaderName = useRunStore((s) => s.setLeaderName);
  const startRun = useRunStore((s) => s.startRun);
  const polling = useRunStore((s) => s.polling);
  const status = useRunStore((s) => s.status);

  const busy =
    polling || status?.status === "running" || status?.status === "queued";

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        startRun();
      }}
      className="border-2 border-black bg-white p-6 shadow-[4px_4px_0px_0px_rgba(0,0,0,1)]"
    >
      <h2 className="mb-4 text-xl font-bold text-black border-b-2 border-black pb-2">
        Analyze Repository
      </h2>

      <div className="space-y-4 font-medium">
        <div>
          <label className="block text-sm font-bold text-black mb-1">GitHub repository URL</label>
          <input
            type="url"
            required
            placeholder="https://github.com/..."
            value={repoUrl}
            onChange={(e) => setRepoUrl(e.target.value)}
            className="w-full rounded-none border-2 border-black bg-white px-4 py-2 text-sm text-black placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-yellow-400"
          />
        </div>
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-bold text-black mb-1">Team Name</label>
            <input
              required
              placeholder='e.g., "RIFT ORGANISERS"'
              value={teamName}
              onChange={(e) => setTeamName(e.target.value)}
              className="w-full rounded-none border-2 border-black bg-white px-4 py-2 text-sm text-black placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-yellow-400"
            />
          </div>
          <div>
            <label className="block text-sm font-bold text-black mb-1">Team Leader Name</label>
            <input
              required
              placeholder='e.g., "Saiyam Kumar"'
              value={leaderName}
              onChange={(e) => setLeaderName(e.target.value)}
              className="w-full rounded-none border-2 border-black bg-white px-4 py-2 text-sm text-black placeholder-gray-500 focus:outline-none focus:ring-2 focus:ring-yellow-400"
            />
          </div>
        </div>
      </div>

      <button
        type="submit"
        disabled={busy}
        className="mt-6 w-full rounded-none border-2 border-black bg-yellow-400 px-4 py-3 text-sm font-bold text-black transition hover:bg-yellow-300 disabled:cursor-not-allowed disabled:opacity-50 shadow-[2px_2px_0px_0px_rgba(0,0,0,1)] hover:translate-x-[2px] hover:translate-y-[2px] hover:shadow-none"
      >
        {busy ? "Running..." : "Run Agent"}
      </button>
    </form>
  );
}
