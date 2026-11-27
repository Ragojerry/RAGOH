/** Team overview: how collectors compare over a period, and the daily trend. */
import type { ColumnDef } from "@tanstack/react-table";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ColumnChart, StatTile } from "../components/charts";
import { DateRange, TeamSelect } from "../components/Filters";
import { BarCell, DataTable } from "../components/Table";
import { get } from "../lib/api";
import { addDays, duration, isoDay } from "../lib/format";
import { useData, useRoster } from "../lib/hooks";
import type { CollectorStats, TeamOverview } from "../lib/types";

const dayLabel = (d: string) => new Date(`${d}T12:00:00`).toLocaleDateString([], { day: "numeric", month: "short" });
const dayTip = (d: string) => new Date(`${d}T12:00:00`).toLocaleDateString([], { weekday: "short", day: "numeric", month: "short" });

export function TeamPage() {
  const today = isoDay();
  const [from, setFrom] = useState(addDays(today, -6));
  const [to, setTo] = useState(today);
  const [teamId, setTeamId] = useState("");
  const roster = useRoster();
  const navigate = useNavigate();
  const ov = useData(() => get<TeamOverview>("/analytics/team", { date_from: from, date_to: to, team_id: teamId }), [from, to, teamId]);

  const cs = ov.data?.collectors ?? [];
  const totals = useMemo(() => {
    const assigned = cs.reduce((a, c) => a + c.assigned, 0);
    const done = cs.reduce((a, c) => a + c.done, 0);
    const subs = cs.reduce((a, c) => a + c.submissions, 0);
    const flags = cs.reduce((a, c) => a + c.flags, 0);
    const checked = cs.filter((c) => c.spot_checks > 0).length;
    return { assigned, done, subs, flags, checked };
  }, [cs]);

  const columns = useMemo<ColumnDef<CollectorStats, any>[]>(() => [ // eslint-disable-line @typescript-eslint/no-explicit-any
    { header: "Collector", accessorKey: "full_name", cell: (c) => <div><b>{c.row.original.full_name}</b><div className="small muted">{c.row.original.team_name}</div></div> },
    { header: "Coverage", accessorKey: "coverage_pct", cell: (c) => <BarCell pct={c.row.original.coverage_pct} label={`${c.row.original.done}/${c.row.original.assigned}`} /> },
    { header: "Days", accessorKey: "days_worked", meta: { align: "right" } },
    { header: "Surveys", accessorKey: "submissions", meta: { align: "right" } },
    { header: "Median interview", accessorKey: "median_interview_s", meta: { align: "right" }, cell: (c) => duration(c.row.original.median_interview_s), sortUndefined: "last" },
    { header: "Avg time at point", accessorKey: "avg_dwell_at_points_s", meta: { align: "right" }, cell: (c) => duration(c.row.original.avg_dwell_at_points_s) },
    { header: "Distance", accessorKey: "distance_km", meta: { align: "right" }, cell: (c) => `${c.row.original.distance_km.toFixed(1)} km` },
    { header: "Flags", accessorKey: "flags", meta: { align: "right" }, cell: (c) => (c.row.original.high_flags ? <span className="chip sev-high">{c.row.original.flags} ({c.row.original.high_flags} high)</span> : c.row.original.flags) },
    { header: "Flags / 100 surveys", accessorKey: "flags_per_100", meta: { align: "right" }, cell: (c) => (c.row.original.flags_per_100 === null ? "—" : c.row.original.flags_per_100.toFixed(0)) },
    { header: "Risk (max)", accessorKey: "max_risk", meta: { align: "right" }, cell: (c) => <span className={c.row.original.max_risk >= 50 ? "chip sev-high" : ""}>{c.row.original.max_risk}</span> },
    { header: "Spot checks", accessorKey: "spot_checks", meta: { align: "right" }, cell: (c) => (c.row.original.spot_checks ? c.row.original.spot_checks : <span className="chip sev-medium">none</span>) },
  ], []);

  const daily = ov.data?.daily ?? [];
  return (
    <div className="page">
      <div className="filters">
        <h1 style={{ marginRight: 12 }}>Team overview</h1>
        <TeamSelect roster={roster.data} value={teamId} onChange={setTeamId} />
        <DateRange from={from} to={to} max={today} onChange={(f, t) => { setFrom(f); setTo(t); }} />
        <span className="small muted">{ov.loading ? "Loading…" : ""}</span>
      </div>
      {ov.error && <div className="error">{ov.error}</div>}
      <div className="grid tiles">
        <StatTile label="Collectors" value={cs.length} sub={`${cs.filter((c) => c.days_worked > 0).length} worked in this period`} />
        <StatTile label="Coverage" value={totals.assigned ? `${Math.round((100 * totals.done) / totals.assigned)}%` : "—"} sub={`${totals.done} of ${totals.assigned} points done`} />
        <StatTile label="Surveys" value={totals.subs.toLocaleString()} sub="in this period" />
        <StatTile label="Flags" value={totals.flags} sub={totals.subs ? `${((100 * totals.flags) / totals.subs).toFixed(0)} per 100 surveys` : ""} />
        <StatTile label="Spot-checked" value={`${totals.checked}/${cs.length}`} sub="collectors with a spot check" />
      </div>
      <div className="grid two">
        <ColumnChart title="Surveys per day" unit="surveys" data={daily.map((d) => ({ key: d.date, label: dayLabel(d.date), tip: dayTip(d.date), value: d.submissions }))} />
        <ColumnChart title="Flags per day" unit="flags" data={daily.map((d) => ({ key: d.date, label: dayLabel(d.date), tip: dayTip(d.date), value: d.flags }))} />
      </div>
      <div className="card">
        <h2>Collectors</h2>
        <div className="small muted" style={{ marginBottom: 8 }}>Coverage counts all points ever assigned; the other columns cover the selected period. Click a collector to replay their last day.</div>
        <DataTable data={cs} columns={columns} rowId={(r) => r.id} initialSort={[{ id: "max_risk", desc: true }]}
          onRowClick={(r) => navigate(`/replay?collector=${r.id}&date=${to}`)} />
      </div>
    </div>
  );
}
