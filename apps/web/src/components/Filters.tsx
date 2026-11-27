import type { TeamLive } from "../lib/types";

export function TeamSelect({ roster, value, onChange }: { roster: TeamLive | null; value: string; onChange: (v: string) => void }) {
  const teams = roster?.teams ?? [];
  return (
    <label>
      Team
      <select value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">{teams.length > 1 ? "All my teams" : "My team"}</option>
        {teams.map((t) => (
          <option key={t.id} value={t.id}>{t.name}</option>
        ))}
      </select>
    </label>
  );
}

export function DateRange({ from, to, onChange, max }: { from: string; to: string; onChange: (from: string, to: string) => void; max: string }) {
  return (
    <>
      <label>
        From
        <input type="date" value={from} max={to} onChange={(e) => e.target.value && onChange(e.target.value, to)} />
      </label>
      <label>
        To
        <input type="date" value={to} min={from} max={max} onChange={(e) => e.target.value && onChange(from, e.target.value)} />
      </label>
    </>
  );
}
