/** Exports for M&E. Each download is recorded in the audit log. */
import { useEffect, useState } from "react";
import { DateRange, TeamSelect } from "../components/Filters";
import { ApiError, download, get } from "../lib/api";
import { addDays, isoDay } from "../lib/format";
import { useRoster } from "../lib/hooks";
import type { Area } from "../lib/types";

const KINDS = [
  ["submissions", "Surveys and checklists", "One row per submission, with every answer as a column, timing, GPS and app version."],
  ["stops", "Stops", "Every place a collector stayed: when, how long, where, and which point it matched."],
  ["flags", "Flags", "Every flag with its evidence, status and how it was closed."],
  ["points", "Visit points", "Each point with its area, status and collector (coverage)."],
  ["tracks", "GPS tracks", "One line per collector per day (simplified to ~10 m), for GIS tools. At most 31 days."],
] as const;

export function ExportsPage() {
  const today = isoDay();
  const roster = useRoster();
  const [kind, setKind] = useState<(typeof KINDS)[number][0]>("submissions");
  const [format, setFormat] = useState<"csv" | "xlsx">("xlsx");
  const [from, setFrom] = useState(addDays(today, -6));
  const [to, setTo] = useState(today);
  const [teamId, setTeamId] = useState("");
  const [areaId, setAreaId] = useState("");
  const [areas, setAreas] = useState<Area[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    get<Area[]>("/admin-areas").then((a) => setAreas(a.filter((x) => x.level === "lga" || x.level === "ward"))).catch(() => setAreas([]));
  }, []);

  const go = async () => {
    setBusy(true);
    setMsg(null);
    try {
      await download(`/exports/${kind}`, {
        date_from: from, date_to: to, team_id: teamId || null, area_id: areaId || null, format: kind === "tracks" ? "geojson" : format,
      });
      setMsg({ ok: true, text: "Downloaded. This export has been recorded in the audit log." });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page" style={{ maxWidth: 820 }}>
      <h1>Exports</h1>
      <div className="card" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <h2>What</h2>
        {KINDS.map(([k, label, help]) => (
          <label key={k} style={{ display: "flex", gap: 8, alignItems: "flex-start", cursor: "pointer" }}>
            <input type="radio" name="kind" checked={kind === k} onChange={() => setKind(k)} />
            <span><b>{label}</b><br /><span className="small muted">{help}</span></span>
          </label>
        ))}
      </div>
      <div className="card">
        <h2>Filters</h2>
        <div className="filters">
          <DateRange from={from} to={to} max={today} onChange={(f, t) => { setFrom(f); setTo(t); }} />
          <TeamSelect roster={roster.data} value={teamId} onChange={setTeamId} />
          <label>
            Area
            <select value={areaId} onChange={(e) => setAreaId(e.target.value)}>
              <option value="">All my areas</option>
              {areas.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.level})</option>)}
            </select>
          </label>
          {kind !== "tracks" && (
            <label>
              Format
              <select value={format} onChange={(e) => setFormat(e.target.value as "csv" | "xlsx")}>
                <option value="xlsx">Excel (.xlsx)</option>
                <option value="csv">CSV</option>
              </select>
            </label>
          )}
          <button onClick={go} disabled={busy}>{busy ? "Preparing…" : "Download"}</button>
        </div>
        {msg && <div className={msg.ok ? "small" : "error"} style={{ marginTop: 10 }}>{msg.text}</div>}
      </div>
      <div className="small muted">Exports contain personal data (names, locations). Share them only with people who need them; who exported what is logged.</div>
    </div>
  );
}
