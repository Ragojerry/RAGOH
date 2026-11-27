/** Live map: each collector's last position by status, their points, area boundaries. */
import type { ColumnDef } from "@tanstack/react-table";
import * as maplibregl from "maplibre-gl";
import type { Map as MlMap } from "maplibre-gl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { TeamSelect } from "../components/Filters";
import { EMPTY, MapView, fitTo, labelFont, setData } from "../components/MapView";
import { DataTable } from "../components/Table";
import { get } from "../lib/api";
import { ago, km, STATUS_TEXT } from "../lib/format";
import { useData } from "../lib/hooks";
import type { LiveCollector, LiveStatus, TeamLive } from "../lib/types";

export const STATUS_VAR: Record<LiveStatus, string> = {
  moving: "--st-moving",
  at_point: "--st-at-point",
  idle: "--st-idle",
  offline: "--st-offline",
  off_shift: "--st-neutral",
  never_seen: "--st-neutral",
};
const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();

export function StatusChip({ s }: { s: LiveStatus }) {
  return (
    <span className="chip">
      <span className="dot" style={{ background: `var(${STATUS_VAR[s]})` }} />
      {STATUS_TEXT[s]}
    </span>
  );
}

function collectorsGeo(cs: LiveCollector[]): GeoJSON.FeatureCollection {
  return {
    type: "FeatureCollection",
    features: cs
      .filter((c) => c.lat !== null && c.lon !== null)
      .map((c) => ({
        type: "Feature",
        id: c.id,
        geometry: { type: "Point", coordinates: [c.lon!, c.lat!] },
        properties: { id: c.id, name: c.full_name, status: c.status, color: css(STATUS_VAR[c.status]), high: c.open_high_flags > 0 },
      })),
  };
}

export function LivePage() {
  const [teamId, setTeamId] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const live = useData(() => get<TeamLive>("/team/live", { team_id: teamId }), [teamId], 30_000);
  const points = useData(() => get<GeoJSON.FeatureCollection>("/map/points.geojson", { team_id: teamId }), [teamId], 120_000);
  const areas = useData(() => get<GeoJSON.FeatureCollection>("/map/areas.geojson", { level: "ward" }), []);
  const mapRef = useRef<MlMap | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const fitted = useRef(false);
  const popup = useRef<maplibregl.Popup | null>(null);
  const navigate = useNavigate();

  const onReady = useCallback((map: MlMap) => {
    mapRef.current = map;
    labelFont().then((font) => {
      setData(map, "areas", EMPTY);
      map.addLayer({ id: "areas-line", type: "line", source: "areas", paint: { "line-color": "#5b615e", "line-width": 1, "line-opacity": 0.6 } });
      setData(map, "points", EMPTY);
      // Points: shape/fill encode state, so they never compete with the collector status hues.
      map.addLayer({
        id: "points", type: "circle", source: "points",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["zoom"], 10, 2.5, 16, 6],
          "circle-color": ["match", ["get", "status"], "completed", "#0f5132", "not_found", "#5b615e", "refused", "#5b615e", "#ffffff"],
          "circle-stroke-color": ["match", ["get", "status"], "completed", "#ffffff", "#5b615e"],
          "circle-stroke-width": 1.5,
        },
      });
      setData(map, "collectors", EMPTY);
      map.addLayer({
        id: "collectors", type: "circle", source: "collectors",
        paint: { "circle-radius": 8, "circle-color": ["get", "color"], "circle-stroke-color": "#ffffff", "circle-stroke-width": 2 },
      });
      map.addLayer({
        id: "collector-labels", type: "symbol", source: "collectors",
        layout: { "text-field": ["concat", ["get", "name"], ["case", ["get", "high"], " ⚠", ""]], "text-font": font, "text-size": 12, "text-offset": [0, 1.3], "text-anchor": "top" },
        paint: { "text-color": "#191919", "text-halo-color": "#ffffff", "text-halo-width": 1.5 },
      });
      map.on("click", "collectors", (e) => {
        const id = e.features?.[0]?.properties?.id as string | undefined;
        if (id) setSelected(id);
      });
      map.on("click", "points", (e) => {
        const p = e.features?.[0]?.properties as { name: string; external_id: string; status: string; collector: string | null } | undefined;
        if (!p) return;
        new maplibregl.Popup({ closeButton: false }).setLngLat(e.lngLat)
          .setHTML(`<b>${escapeHtml(p.name)}</b> (${escapeHtml(p.external_id)})<br/>${escapeHtml(p.status.replace("_", " "))}${p.collector ? ` · ${escapeHtml(p.collector)}` : ""}`)
          .addTo(map);
      });
      for (const l of ["collectors", "points"]) {
        map.on("mouseenter", l, () => (map.getCanvas().style.cursor = "pointer"));
        map.on("mouseleave", l, () => (map.getCanvas().style.cursor = ""));
      }
      setMapReady(true);
    });
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    if (areas.data) setData(map, "areas", areas.data);
    if (points.data) setData(map, "points", points.data);
    if (live.data) {
      setData(map, "collectors", collectorsGeo(live.data.collectors));
      if (!fitted.current) {
        const coords: [number, number][] = live.data.collectors.filter((c) => c.lon !== null).map((c) => [c.lon!, c.lat!]);
        const pts = (points.data?.features ?? []).map((f) => (f.geometry as GeoJSON.Point).coordinates as [number, number]);
        if (coords.length || pts.length) {
          fitTo(map, [...coords, ...pts], 15);
          fitted.current = true;
        }
      }
    }
  }, [mapReady, live.data, points.data, areas.data]);

  const sel = live.data?.collectors.find((c) => c.id === selected) ?? null;
  useEffect(() => {
    const map = mapRef.current;
    popup.current?.remove();
    if (!map || !sel || sel.lat === null || sel.lon === null) return;
    map.easeTo({ center: [sel.lon, sel.lat], zoom: Math.max(map.getZoom(), 15) });
    popup.current = new maplibregl.Popup({ offset: 14 }).setLngLat([sel.lon, sel.lat])
      .setHTML(`<b>${escapeHtml(sel.full_name)}</b><br/>${STATUS_TEXT[sel.status]} · seen ${ago(sel.last_seen_at)}${sel.at_point_name ? `<br/>At ${escapeHtml(sel.at_point_name)}` : ""}<br/>${sel.done_today}/${sel.assigned_today} visits · ${km(sel.distance_m)}${sel.battery_pct !== null ? ` · battery ${sel.battery_pct}%` : ""}`)
      .addTo(map);
  }, [sel?.id, sel?.lat, sel?.lon]); // eslint-disable-line react-hooks/exhaustive-deps

  const columns = useMemo<ColumnDef<LiveCollector, any>[]>(() => [ // eslint-disable-line @typescript-eslint/no-explicit-any
    { header: "Collector", accessorKey: "full_name", cell: (c) => <div><b>{c.row.original.full_name}</b><div className="small muted">{c.row.original.team_name}</div></div> },
    { header: "Status", accessorKey: "status", cell: (c) => <StatusChip s={c.row.original.status} /> },
    { header: "Seen", accessorKey: "last_seen_at", cell: (c) => <span className="small">{ago(c.row.original.last_seen_at)}</span> },
    { header: "Visits", id: "visits", accessorFn: (r) => r.done_today, meta: { align: "right" }, cell: (c) => `${c.row.original.done_today}/${c.row.original.assigned_today}` },
    { header: "Flags", accessorKey: "open_flags", meta: { align: "right" }, cell: (c) => (c.row.original.open_high_flags ? <span className="chip sev-high">{c.row.original.open_flags}</span> : c.row.original.open_flags || "") },
  ], []);

  const d = live.data;
  const onShift = d?.collectors.filter((c) => c.on_shift).length ?? 0;
  const offline = d?.collectors.filter((c) => c.status === "offline").length ?? 0;

  return (
    <div className="page full">
      <aside className="side">
        <div className="head">
          <div className="filters"><TeamSelect roster={d} value={teamId} onChange={setTeamId} /></div>
          <div className="small muted">
            {d ? `${d.collectors.length} collectors · ${onShift} on shift · ${offline} no signal · updated ${ago(d.server_time)}` : live.loading ? "Loading…" : ""}
          </div>
          {live.error && <div className="error">{live.error}</div>}
        </div>
        <div className="body">
          <DataTable
            data={d?.collectors ?? []} columns={columns} rowId={(r) => r.id} selectedId={selected}
            onRowClick={(r) => setSelected(r.id)} empty="No collectors in your teams."
          />
          {sel && (
            <div className="card" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <h2>{sel.full_name}</h2>
              <div><StatusChip s={sel.status} /> <span className="small muted">seen {ago(sel.last_seen_at)}{sel.accuracy_m ? ` · ±${Math.round(sel.accuracy_m)} m` : ""}</span></div>
              <div className="small">{sel.on_shift ? "On shift" : "Not on shift"} · {sel.done_today}/{sel.assigned_today} visits · {km(sel.distance_m)} · risk {sel.risk_score}</div>
              {sel.pending_sync > 0 && <div className="small muted">{sel.pending_sync} record(s) on the phone not synced yet</div>}
              <div style={{ display: "flex", gap: 8 }}>
                <button onClick={() => navigate(`/replay?collector=${sel.id}`)}>Replay today</button>
                {sel.open_flags > 0 && <Link to={`/flags?collector=${sel.id}`}><button className="secondary">Open flags ({sel.open_flags})</button></Link>}
                {sel.phone && <a href={`tel:${sel.phone}`}><button className="secondary">Call</button></a>}
              </div>
            </div>
          )}
        </div>
      </aside>
      <div className="mapwrap">
        <MapView onReady={onReady} />
        <div className="legend" aria-label="Legend">
          {(["moving", "at_point", "idle", "offline", "off_shift"] as LiveStatus[]).map((s) => (
            <div className="row" key={s}><span className="dot" style={{ background: `var(${STATUS_VAR[s]})`, border: "2px solid #fff", boxShadow: "0 0 0 1px #ccc" }} />{STATUS_TEXT[s]}</div>
          ))}
          <div className="row"><span className="dot" style={{ background: "#fff", border: "1.5px solid #5b615e" }} />Point to visit</div>
          <div className="row"><span className="dot" style={{ background: "#0f5132" }} />Point done</div>
          <div className="row"><span className="dot" style={{ background: "#5b615e" }} />Not found / refused</div>
        </div>
      </div>
    </div>
  );
}

export function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}
