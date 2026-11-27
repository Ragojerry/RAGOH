/** Integrations (admins): Elasticsearch, DHIS2 and DIGIT HCM connectors — status, run now,
 * settings, and a preview of the DHIS2 numbers before anything is sent. */
import { useEffect, useState } from "react";
import { ApiError, get, post, put } from "../lib/api";
import { ago, isoDay } from "../lib/format";
import { useData } from "../lib/hooks";

interface Status {
  name: "elasticsearch" | "dhis2" | "digit";
  campaign_id: string;
  campaign: string;
  enabled: boolean;
  target: string | null;
  secret_set: boolean;
  every_min: number;
  last_run_at: string | null;
  last_ok_at: string | null;
  last_status: string | null;
  last_error: string | null;
  last_stats: Record<string, unknown>;
}

interface PreviewRow {
  area_code: string;
  area: string;
  org_unit: string | null;
  values: Record<string, number>;
}

const TITLE = { elasticsearch: "Elasticsearch / Kibana", dhis2: "DHIS2", digit: "DIGIT HCM" } as const;
const WHAT = {
  elasticsearch: "Surveys, stops, daily summaries and flags as documents, every few minutes.",
  dhis2: "Daily indicators per ward/LGA (visits, coverage, flags, spot checks…), sent to a data set.",
  digit: "Visit outcomes, supervision results and closed flags, sent as event batches to your HCM instance.",
} as const;
const SECRET = { elasticsearch: "ES_API_KEY", dhis2: "DHIS2_PASSWORD", digit: "DIGIT_PASSWORD (or WEBHOOK_SECRET)" } as const;

export function IntegrationsPage() {
  const list = useData(() => get<Status[]>("/integrations"), []);
  const campaigns = [...new Map((list.data ?? []).map((s) => [s.campaign_id, s.campaign])).entries()];
  const [campaignId, setCampaignId] = useState("");
  useEffect(() => {
    if (!campaignId && campaigns[0]) setCampaignId(campaigns[0][0]);
  }, [campaigns, campaignId]);
  const rows = (list.data ?? []).filter((s) => s.campaign_id === campaignId);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  const runNow = async (name: string) => {
    setBusy(name);
    setMsg(null);
    try {
      const r = await post<Record<string, unknown>[]>(`/integrations/${name}/run?campaign_id=${campaignId}`, {});
      setMsg(`${TITLE[name as keyof typeof TITLE]}: ${r[0]?.status === "ok" ? "done" : `failed — ${String(r[0]?.error ?? "")}`}`);
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
      list.reload();
    }
  };

  return (
    <div className="page" style={{ maxWidth: 1100 }}>
      <div className="filters">
        <h1 style={{ marginRight: 12 }}>Integrations</h1>
        {campaigns.length > 1 && (
          <label>
            Campaign
            <select value={campaignId} onChange={(e) => setCampaignId(e.target.value)}>
              {campaigns.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
            </select>
          </label>
        )}
      </div>
      <div className="muted">Connectors run on the server every few minutes, outside the field app: if a system is down, RAGOH keeps working and tries again later. Passwords and keys are set on the server (Parameter Store), never here.</div>
      {list.error && <div className="error">{list.error}</div>}
      {msg && <div className="card">{msg}</div>}
      <div className="grid two">
        {rows.map((s) => (
          <div className="card" key={s.name} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h2 style={{ margin: 0 }}>{TITLE[s.name]}</h2>
              <span className={`chip ${s.enabled ? (s.last_status === "error" ? "sev-high" : "") : "sev-low"}`}>
                {!s.enabled ? "Off" : s.last_status === "error" ? "Error" : s.last_status === "ok" ? "Working" : "On, not run yet"}
              </span>
            </div>
            <div className="small muted">{WHAT[s.name]}</div>
            <div className="small">Sends to: <b>{s.target ?? "not set"}</b> · every {s.every_min} min · secret {s.secret_set ? "set" : <span className="error">missing ({SECRET[s.name]})</span>}</div>
            <div className="small">Last run {ago(s.last_run_at)}{s.last_ok_at && s.last_status === "error" ? ` · last success ${ago(s.last_ok_at)}` : ""}</div>
            {Object.keys(s.last_stats).length > 0 && (
              <div className="small muted">{Object.entries(s.last_stats).map(([k, v]) => `${k.replace(/_/g, " ")}: ${String(v)}`).join(" · ")}</div>
            )}
            {s.last_error && <pre className="small error" style={{ whiteSpace: "pre-wrap", margin: 0 }}>{s.last_error}</pre>}
            <div><button disabled={!s.enabled || busy !== null} onClick={() => runNow(s.name)}>{busy === s.name ? "Running…" : "Run now"}</button></div>
          </div>
        ))}
      </div>
      {campaignId && <Settings campaignId={campaignId} onSaved={() => { list.reload(); setVersion((v) => v + 1); }} />}
      {campaignId && <Dhis2Preview campaignId={campaignId} version={version} />}
    </div>
  );
}

function Settings({ campaignId, onSaved }: { campaignId: string; onSaved: () => void }) {
  const cfg = useData(() => get<{ overrides: Record<string, unknown>; effective: { integrations: unknown } }>(`/campaigns/${campaignId}/config`), [campaignId]);
  const [text, setText] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    if (cfg.data) setText(JSON.stringify(cfg.data.effective.integrations, null, 2));
  }, [cfg.data]);
  const save = async () => {
    setError(null);
    setSaved(false);
    let parsed: unknown;
    try {
      parsed = JSON.parse(text);
    } catch (e) {
      setError(`Not valid JSON: ${e instanceof Error ? e.message : String(e)}`);
      return;
    }
    try {
      await put(`/campaigns/${campaignId}/config`, { ...(cfg.data?.overrides ?? {}), integrations: parsed });
      setSaved(true);
      cfg.reload();
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    }
  };
  return (
    <div className="card" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <h2>Settings</h2>
      <div className="small muted">Switch connectors on and set where they send (see docs/INTEGRATIONS.md). Saving is recorded in the audit log.</div>
      <textarea rows={18} spellCheck={false} value={text} onChange={(e) => setText(e.target.value)} style={{ fontFamily: "ui-monospace, monospace", fontSize: 12 }} />
      {error && <div className="error">{error}</div>}
      {saved && <div className="small">Saved.</div>}
      <div><button onClick={save}>Save settings</button></div>
    </div>
  );
}

function Dhis2Preview({ campaignId, version }: { campaignId: string; version: number }) {
  const [day, setDay] = useState(isoDay());
  const p = useData(() => get<PreviewRow[]>("/integrations/dhis2/preview", { campaign_id: campaignId, date: day }), [campaignId, day, version]);
  const indicators = Object.keys(p.data?.[0]?.values ?? {});
  return (
    <div className="card">
      <div className="filters" style={{ marginBottom: 8 }}>
        <h2 style={{ margin: 0 }}>DHIS2 preview</h2>
        <label>
          Day
          <input type="date" value={day} max={isoDay()} onChange={(e) => e.target.value && setDay(e.target.value)} />
        </label>
      </div>
      <div className="small muted" style={{ marginBottom: 8 }}>The values RAGOH would send for this day (mapped areas, or all LGAs before any mapping). Only mapped indicators are actually sent.</div>
      {p.error && <div className="error">{p.error}</div>}
      <div style={{ overflowX: "auto" }}>
        <table className="data">
          <thead><tr><th>Area</th><th>Org unit</th>{indicators.map((i) => <th key={i} className="right">{i.replace(/_/g, " ")}</th>)}</tr></thead>
          <tbody>
            {(p.data ?? []).map((r) => (
              <tr key={r.area_code}>
                <td>{r.area} <span className="small muted">{r.area_code}</span></td>
                <td>{r.org_unit ?? <span className="muted">not mapped</span>}</td>
                {indicators.map((i) => <td key={i} className="right num">{r.values[i]}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
