/** Audit log: sign-ins, who viewed or exported tracking data, blocked phones, changes. */
import type { ColumnDef } from "@tanstack/react-table";
import { useMemo, useState } from "react";
import { DataTable } from "../components/Table";
import { get } from "../lib/api";
import { addDays, isoDay } from "../lib/format";
import { useData } from "../lib/hooks";

interface Entry {
  id: string;
  created_at: string;
  action: string;
  actor_name: string | null;
  entity: string | null;
  entity_id: string | null;
  entity_name: string | null;
  details: Record<string, unknown>;
  ip: string | null;
}

const LABEL: Record<string, string> = {
  track_view: "Viewed a route", export: "Exported data", login: "Signed in", login_failed: "Failed sign-in",
  device_block: "Blocked a phone", device_unblock: "Unblocked a phone", device_wiped: "Phone erased", consent: "Accepted privacy notice",
  relay_received: "Received data phone-to-phone", login_blocked_device: "Sign-in from a blocked phone",
};
const PAGE = 100;

export function AuditPage() {
  const [privacyOnly, setPrivacyOnly] = useState(true);
  const [action, setAction] = useState("");
  const [from, setFrom] = useState(addDays(isoDay(), -30));
  const [page, setPage] = useState(0);
  const log = useData(
    () => get<{ total: number; items: Entry[]; actions: string[] }>("/audit-logs", { privacy_only: privacyOnly, action, date_from: from, limit: PAGE, offset: page * PAGE }),
    [privacyOnly, action, from, page],
  );
  const cols = useMemo<ColumnDef<Entry, any>[]>(() => [ // eslint-disable-line @typescript-eslint/no-explicit-any
    { header: "When", accessorKey: "created_at", cell: (c) => new Date(c.row.original.created_at).toLocaleString() },
    { header: "Who", accessorKey: "actor_name", cell: (c) => c.row.original.actor_name ?? <span className="muted">system / phone</span> },
    { header: "What", accessorKey: "action", cell: (c) => LABEL[c.row.original.action] ?? c.row.original.action.replace(/_/g, " ") },
    { header: "About", id: "about", accessorFn: (r) => r.entity_name ?? r.entity_id, cell: (c) => c.row.original.entity_name ?? (c.row.original.entity ? `${c.row.original.entity} ${c.row.original.entity_id ?? ""}` : "") },
    { header: "Details", id: "details", enableSorting: false, cell: (c) => <span className="small muted">{Object.entries(c.row.original.details).filter(([, v]) => v !== null && v !== "").map(([k, v]) => `${k.replace(/_/g, " ")}: ${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join(" · ")}</span> },
  ], []);
  const total = log.data?.total ?? 0;
  return (
    <div className="page">
      <div className="filters">
        <h1 style={{ marginRight: 12 }}>Audit log</h1>
        <label>
          Show
          <select value={privacyOnly ? "privacy" : "all"} onChange={(e) => { setPrivacyOnly(e.target.value === "privacy"); setPage(0); }}>
            <option value="privacy">Access to personal data</option>
            <option value="all">Everything</option>
          </select>
        </label>
        <label>
          Action
          <select value={action} onChange={(e) => { setAction(e.target.value); setPage(0); }}>
            <option value="">Any</option>
            {(log.data?.actions ?? []).map((a) => <option key={a} value={a}>{LABEL[a] ?? a}</option>)}
          </select>
        </label>
        <label>
          Since
          <input type="date" value={from} max={isoDay()} onChange={(e) => { if (e.target.value) { setFrom(e.target.value); setPage(0); } }} />
        </label>
        <span className="small muted">{total} entries</span>
      </div>
      {log.error && <div className="error">{log.error}</div>}
      <div className="card">
        <DataTable data={log.data?.items ?? []} columns={cols} rowId={(r) => r.id} empty="Nothing recorded." />
        {total > PAGE && (
          <div style={{ display: "flex", gap: 8, marginTop: 10, alignItems: "center" }}>
            <button className="secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>Newer</button>
            <span className="small muted">Page {page + 1} of {Math.ceil(total / PAGE)}</span>
            <button className="secondary" disabled={(page + 1) * PAGE >= total} onClick={() => setPage(page + 1)}>Older</button>
          </div>
        )}
      </div>
    </div>
  );
}
