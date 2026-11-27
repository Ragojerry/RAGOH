/** Phones: block a lost or stolen phone (and erase it remotely), crashes, battery and data. */
import type { ColumnDef } from "@tanstack/react-table";
import { useMemo, useState, type ReactNode } from "react";
import { StatTile } from "../components/charts";
import { DateRange, TeamSelect } from "../components/Filters";
import { DataTable } from "../components/Table";
import { ApiError, get, post } from "../lib/api";
import { addDays, ago, isoDay } from "../lib/format";
import { useData, useRoster } from "../lib/hooks";

interface DeviceRow {
  id: string;
  user_name: string | null;
  username: string | null;
  model: string | null;
  android_version: string | null;
  app_version: string | null;
  last_seen_at: string | null;
  push_enabled: boolean;
  is_blocked: boolean;
  blocked_at: string | null;
  block_reason: string | null;
  wipe_requested: boolean;
  wiped_at: string | null;
  app_errors_7d: number;
}

interface UsageRow {
  id: string;
  full_name: string;
  team_name: string;
  phone_models: string[];
  shifts: number;
  hours: number;
  battery_per_hour: number | null;
  battery_8h: number | null;
  mb_per_day: number | null;
  gps_points_per_hour: number | null;
}

interface Usage {
  collectors: UsageRow[];
  overall_battery_8h: number | null;
  overall_mb_per_day: number | null;
  target_battery_8h: number;
}

function BlockDialog({ device, onDone, onCancel }: { device: DeviceRow; onDone: () => void; onCancel: () => void }) {
  const [reason, setReason] = useState("");
  const [wipe, setWipe] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    try {
      await post(`/devices/${device.id}/block`, { reason: reason.trim(), wipe });
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setBusy(false);
    }
  };
  return (
    <aside className="drawer" aria-label="Block phone">
      <h1>Block {device.user_name}'s phone</h1>
      <div className="muted">{device.model ?? "Unknown phone"} · last seen {ago(device.last_seen_at)}</div>
      <div className="card" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <div>The phone is signed out at once and can no longer sync or sign in.</div>
        <label style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
          <input type="checkbox" checked={wipe} onChange={(e) => setWipe(e.target.checked)} />
          <span><b>Also erase it</b> — the next time the phone has internet, RAGOH deletes everything on it: unsynced surveys, GPS, photos, the offline map and the sign-in. Use this when the phone is lost or stolen.</span>
        </label>
        {wipe && <div className="error small">Unsynced work on that phone will be lost.</div>}
        <label className="small muted">
          Reason (recorded in the audit log)
          <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Reported stolen on 27 Nov" style={{ width: "100%" }} />
        </label>
        {error && <div className="error">{error}</div>}
        <div style={{ display: "flex", gap: 8 }}>
          <button className="danger" disabled={busy || reason.trim().length < 3} onClick={submit}>{wipe ? "Block and erase" : "Block"}</button>
          <button className="secondary" onClick={onCancel}>Cancel</button>
        </div>
      </div>
    </aside>
  );
}

function state(d: DeviceRow): ReactNode {
  if (d.wiped_at) return <span className="chip">Erased {new Date(d.wiped_at).toLocaleDateString()}</span>;
  if (d.is_blocked && d.wipe_requested) return <span className="chip sev-high">Blocked · erase pending</span>;
  if (d.is_blocked) return <span className="chip sev-high">Blocked</span>;
  return <span className="chip">Active</span>;
}

export function DevicesPage() {
  const today = isoDay();
  const [from, setFrom] = useState(addDays(today, -13));
  const [to, setTo] = useState(today);
  const [teamId, setTeamId] = useState("");
  const [blocking, setBlocking] = useState<DeviceRow | null>(null);
  const roster = useRoster();
  const devices = useData(() => get<DeviceRow[]>("/devices"), []);
  const usage = useData(() => get<Usage>("/analytics/device-usage", { date_from: from, date_to: to, team_id: teamId }), [from, to, teamId]);

  const unblock = async (d: DeviceRow) => {
    await post(`/devices/${d.id}/unblock`, {});
    devices.reload();
  };

  const deviceCols = useMemo<ColumnDef<DeviceRow, any>[]>(() => [ // eslint-disable-line @typescript-eslint/no-explicit-any
    { header: "User", accessorKey: "user_name", cell: (c) => <div><b>{c.row.original.user_name}</b><div className="small muted">{c.row.original.username}</div></div> },
    { header: "Phone", accessorKey: "model", cell: (c) => <div>{c.row.original.model ?? "—"}<div className="small muted">Android {c.row.original.android_version ?? "?"}</div></div> },
    { header: "App", accessorKey: "app_version" },
    { header: "Last seen", accessorKey: "last_seen_at", cell: (c) => ago(c.row.original.last_seen_at) },
    { header: "Push", accessorKey: "push_enabled", cell: (c) => (c.row.original.push_enabled ? "On" : "—") },
    { header: "Crashes (7 days)", accessorKey: "app_errors_7d", meta: { align: "right" }, cell: (c) => (c.row.original.app_errors_7d ? <span className="chip sev-medium">{c.row.original.app_errors_7d}</span> : 0) },
    { header: "State", id: "state", accessorFn: (r) => (r.is_blocked ? 1 : 0), cell: (c) => <div>{state(c.row.original)}{c.row.original.block_reason && <div className="small muted">{c.row.original.block_reason}</div>}</div> },
    {
      header: "", id: "act", enableSorting: false,
      cell: (c) => (c.row.original.is_blocked
        ? <button className="secondary" onClick={() => unblock(c.row.original)}>Unblock</button>
        : <button className="secondary" onClick={() => setBlocking(c.row.original)}>Block…</button>),
    },
  ], []); // eslint-disable-line react-hooks/exhaustive-deps

  const usageCols = useMemo<ColumnDef<UsageRow, any>[]>(() => [ // eslint-disable-line @typescript-eslint/no-explicit-any
    { header: "Collector", accessorKey: "full_name", cell: (c) => <div><b>{c.row.original.full_name}</b><div className="small muted">{c.row.original.team_name}</div></div> },
    { header: "Phone", id: "models", accessorFn: (r) => r.phone_models.join(", ") },
    { header: "Shifts", accessorKey: "shifts", meta: { align: "right" } },
    { header: "Hours", accessorKey: "hours", meta: { align: "right" } },
    { header: "Battery / hour", accessorKey: "battery_per_hour", meta: { align: "right" }, cell: (c) => (c.row.original.battery_per_hour === null ? "—" : `${c.row.original.battery_per_hour}%`) },
    { header: "8-hour shift", accessorKey: "battery_8h", meta: { align: "right" }, cell: (c) => { const v = c.row.original.battery_8h; return v === null ? "—" : <span className={v > 25 ? "chip sev-high" : ""}>{v}%</span>; } },
    { header: "Data / day", accessorKey: "mb_per_day", meta: { align: "right" }, cell: (c) => (c.row.original.mb_per_day === null ? "—" : `${c.row.original.mb_per_day.toFixed(1)} MB`) },
    { header: "GPS points / hour", accessorKey: "gps_points_per_hour", meta: { align: "right" }, cell: (c) => c.row.original.gps_points_per_hour ?? "—" },
  ], []);

  const u = usage.data;
  return (
    <div className="page">
      <div className="filters"><h1>Phones</h1></div>
      {devices.error && <div className="error">{devices.error}</div>}
      <div className="card">
        <h2>Phones in your teams</h2>
        <div className="small muted" style={{ marginBottom: 8 }}>If a phone is lost or stolen, block it — and erase it if it had work on it. Every block and erase is recorded in the audit log.</div>
        <DataTable data={devices.data ?? []} columns={deviceCols} rowId={(r) => r.id} empty="No phones have synced yet." />
      </div>
      <div className="filters">
        <h2 style={{ margin: 0 }}>Battery and mobile data</h2>
        <TeamSelect roster={roster.data} value={teamId} onChange={setTeamId} />
        <DateRange from={from} to={to} max={today} onChange={(f, t) => { setFrom(f); setTo(t); }} />
      </div>
      <div className="grid tiles">
        <StatTile label="Battery for an 8-hour shift" value={u?.overall_battery_8h != null ? `${u.overall_battery_8h}%` : "—"} sub={`target ≤ ${u?.target_battery_8h ?? 25}%, shifts without charging`} />
        <StatTile label="Mobile data per collector per day" value={u?.overall_mb_per_day != null ? `${u.overall_mb_per_day.toFixed(1)} MB` : "—"} sub="estimated by the app" />
      </div>
      <div className="card">
        <DataTable data={u?.collectors ?? []} columns={usageCols} rowId={(r) => r.id} empty="No shift reports yet (phones send one when a shift ends)." />
        <div className="small muted" style={{ marginTop: 8 }}>Figures come from each phone's end-of-shift report: battery at start and end (shifts on the charger are left out), and the data the app sent and received. Phones with app version 0.8 or later.</div>
      </div>
      {blocking && <BlockDialog device={blocking} onCancel={() => setBlocking(null)} onDone={() => { setBlocking(null); devices.reload(); }} />}
    </div>
  );
}
