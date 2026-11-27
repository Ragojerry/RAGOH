/** Flags inbox with filters, and a side drawer with the evidence, history and actions. */
import type { ColumnDef } from "@tanstack/react-table";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { DataTable } from "../components/Table";
import { ApiError, currentUser, get, post } from "../lib/api";
import { addDays, isoDay } from "../lib/format";
import { useData, useRoster } from "../lib/hooks";
import type { Flag } from "../lib/types";

const OPEN = ["new", "acknowledged", "under_review"];
const STATUS_TEXT: Record<string, string> = { new: "New", acknowledged: "Acknowledged", under_review: "Under review", resolved: "Resolved", dismissed: "Dismissed" };
const ACTION_TEXT: Record<string, string> = {
  acknowledge: "Acknowledged", note: "Note", call: "Called", message: "Messaged", request_explanation: "Asked for an explanation",
  reply: "Collector replied", revisit: "Revisit requested", backcheck: "Back-check assigned", escalate: "Escalated",
  resolve: "Resolved", dismiss: "Dismissed", reopen: "Reopened",
};
const NEEDS_COMMENT = new Set(["note", "message", "request_explanation", "resolve", "dismiss", "revisit"]);

function Severity({ s }: { s: Flag["severity"] }) {
  return <span className={`chip sev-${s}`}>{s}</span>;
}

export function FlagsPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const roster = useRoster();
  const [state, setState] = useState<"open" | "closed" | "all">("open");
  const [severity, setSeverity] = useState("");
  const [from, setFrom] = useState(addDays(isoDay(), -30));
  const collector = params.get("collector") ?? "";

  const list = useData(async () => {
    const q = new URLSearchParams();
    for (const st of state === "open" ? OPEN : state === "closed" ? ["resolved", "dismissed"] : []) q.append("status", st);
    if (severity) q.append("severity", severity);
    if (collector) q.set("collector_id", collector);
    q.set("date_from", from);
    q.set("limit", "500");
    return get<{ total: number; items: Flag[] }>(`/flags?${q.toString()}`);
  }, [state, severity, collector, from], 60_000);

  const columns = useMemo<ColumnDef<Flag, any>[]>(() => [ // eslint-disable-line @typescript-eslint/no-explicit-any
    { header: "Severity", accessorKey: "severity", sortingFn: (a, b) => rank(a.original.severity) - rank(b.original.severity), cell: (c) => <Severity s={c.row.original.severity} /> },
    { header: "Date", accessorKey: "local_date" },
    { header: "Collector", accessorKey: "collector_name" },
    { header: "What was found", accessorKey: "title", cell: (c) => <span>{c.row.original.title} <span className="small muted">({c.row.original.rule_id})</span></span> },
    { header: "Status", accessorKey: "status", cell: (c) => <span>{STATUS_TEXT[c.row.original.status]}{c.row.original.escalated ? " · escalated" : ""}</span> },
  ], []);

  const setCollector = (v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set("collector", v);
    else next.delete("collector");
    setParams(next, { replace: true });
  };

  return (
    <div className="page">
      <div className="filters">
        <h1 style={{ marginRight: 12 }}>Flags</h1>
        <label>
          Status
          <select value={state} onChange={(e) => setState(e.target.value as typeof state)}>
            <option value="open">Open</option>
            <option value="closed">Resolved / dismissed</option>
            <option value="all">All</option>
          </select>
        </label>
        <label>
          Severity
          <select value={severity} onChange={(e) => setSeverity(e.target.value)}>
            <option value="">Any</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </label>
        <label>
          Collector
          <select value={collector} onChange={(e) => setCollector(e.target.value)}>
            <option value="">Everyone</option>
            {(roster.data?.collectors ?? []).map((c) => <option key={c.id} value={c.id}>{c.full_name}</option>)}
          </select>
        </label>
        <label>
          Since
          <input type="date" value={from} max={isoDay()} onChange={(e) => e.target.value && setFrom(e.target.value)} />
        </label>
        <span className="small muted">{list.data ? `${list.data.total} flag(s)` : list.loading ? "Loading…" : ""}</span>
      </div>
      {list.error && <div className="error">{list.error}</div>}
      <div className="card" style={{ marginRight: id ? 470 : 0 }}>
        <DataTable
          data={list.data?.items ?? []} columns={columns} rowId={(r) => r.id} selectedId={id}
          initialSort={[{ id: "severity", desc: true }]}
          onRowClick={(r) => navigate(`/flags/${r.id}${collector ? `?collector=${collector}` : ""}`)}
          empty="No flags match."
        />
      </div>
      {id && <FlagDrawer id={id} onClose={() => navigate(`/flags${collector ? `?collector=${collector}` : ""}`)} onChanged={list.reload} />}
    </div>
  );
}

const rank = (s: string) => ({ low: 0, medium: 1, high: 2 })[s] ?? 0;

function FlagDrawer({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const flag = useData(() => get<Flag>(`/flags/${id}`), [id]);
  const roster = useRoster();
  const [comment, setComment] = useState("");
  const [assignee, setAssignee] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const f = flag.data;
  const me = currentUser();

  const act = async (action: string, extra: Record<string, unknown> = {}) => {
    setBusy(action);
    setError(null);
    try {
      flag.setData(await post<Flag>(`/flags/${id}/actions`, { action, comment: comment.trim() || undefined, ...extra }));
      setComment("");
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  if (!f) return <aside className="drawer">{flag.error ? <div className="error">{flag.error}</div> : <div className="muted">Loading…</div>}</aside>;
  const closed = f.status === "resolved" || f.status === "dismissed";
  const off = (a: string) => busy !== null || (NEEDS_COMMENT.has(a) && !comment.trim());
  const teammates = (roster.data?.collectors ?? []).filter((c) => c.id !== f.collector_id);
  const pointFlag = f.subject_type === "assignment" || f.subject_type === "submission";

  return (
    <aside className="drawer" aria-label="Flag details">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Severity s={f.severity} />
        <button className="secondary" onClick={onClose}>Close</button>
      </div>
      <h1>{f.title}</h1>
      <div className="muted">{f.collector_name} · {f.local_date} · rule {f.rule_id} · {STATUS_TEXT[f.status]}{f.escalated ? " · escalated" : ""}</div>
      <div className="card">
        <h2>Evidence</h2>
        <table className="data"><tbody>
          {Object.entries(f.details).map(([k, v]) => (
            <tr key={k}><td className="muted">{k.replace(/_/g, " ")}</td><td className="num">{typeof v === "object" ? JSON.stringify(v) : typeof v === "number" && !Number.isInteger(v) ? v.toFixed(1) : String(v)}</td></tr>
          ))}
        </tbody></table>
        <div style={{ marginTop: 8 }}><Link to={`/replay?collector=${f.collector_id}&date=${f.local_date}`}>Replay this day →</Link></div>
      </div>

      <div className="card" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <h2>Follow up</h2>
        <textarea rows={3} placeholder="Comment — needed for notes, messages, revisits and closing" value={comment} onChange={(e) => setComment(e.target.value)} />
        {error && <div className="error">{error}</div>}
        {closed ? (
          <button className="secondary" disabled={busy !== null} onClick={() => act("reopen")}>Reopen</button>
        ) : (
          <>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {f.status === "new" && <button disabled={busy !== null} onClick={() => act("acknowledge")}>Acknowledge</button>}
              <button className="secondary" disabled={off("request_explanation")} onClick={() => act("request_explanation")}>Ask collector to explain</button>
              <button className="secondary" disabled={off("message")} onClick={() => act("message")}>Send message</button>
              <button className="secondary" disabled={off("note")} onClick={() => act("note")}>Add note</button>
              <button className="secondary" disabled={busy !== null} onClick={() => act("call")}>Log a call</button>
              {pointFlag && <button className="secondary" disabled={off("revisit")} onClick={() => act("revisit")}>Request revisit</button>}
              {!f.escalated && <button className="secondary" disabled={busy !== null} onClick={() => act("escalate")}>Escalate</button>}
            </div>
            {pointFlag && (
              <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                <select value={assignee} onChange={(e) => setAssignee(e.target.value)} style={{ flex: 1, minWidth: 0 }}>
                  <option value="">Back-check by me ({me?.full_name})</option>
                  {teammates.map((c) => <option key={c.id} value={c.id}>Back-check by {c.full_name}</option>)}
                </select>
                <button className="secondary" style={{ whiteSpace: "nowrap" }} disabled={busy !== null} onClick={() => act("backcheck", assignee ? { assignee_id: assignee } : {})}>Assign back-check</button>
              </div>
            )}
            <div style={{ display: "flex", gap: 6 }}>
              <button disabled={off("resolve")} onClick={() => act("resolve")}>Resolve</button>
              <button className="danger" disabled={off("dismiss")} onClick={() => act("dismiss")}>Dismiss (false alarm)</button>
            </div>
          </>
        )}
        {f.resolution_comment && <div className="small">Closing comment: {f.resolution_comment}</div>}
      </div>

      <div className="card">
        <h2>History</h2>
        {(f.actions ?? []).map((a) => (
          <div key={a.id} style={{ padding: "5px 0", borderBottom: "1px solid var(--grid)" }}>
            <div><b>{ACTION_TEXT[a.action] ?? a.action}</b> <span className="small muted">· {a.actor_name ?? "system"} · {new Date(a.created_at).toLocaleString()}</span></div>
            {a.comment && <div className="small">{a.comment}</div>}
          </div>
        ))}
        {!f.actions?.length && <div className="muted">Nothing done yet.</div>}
      </div>
    </aside>
  );
}
