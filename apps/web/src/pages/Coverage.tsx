/** Coverage: visited vs not-visited points by area (map + table), and supervision coverage. */
import type { ColumnDef } from "@tanstack/react-table";
import * as maplibregl from "maplibre-gl";
import type { Map as MlMap } from "maplibre-gl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { StatTile } from "../components/charts";
import { DateRange, TeamSelect } from "../components/Filters";
import { EMPTY, MapView, fitTo, setData } from "../components/MapView";
import { BarCell, DataTable } from "../components/Table";
import { get } from "../lib/api";
import { addDays, isoDay } from "../lib/format";
import { useData, useRoster } from "../lib/hooks";
import type { Coverage } from "../lib/types";
import { escapeHtml } from "./Live";

type AreaRow = Coverage["areas"][number];

// Sequential blue, light → dark (0% → 100%). Areas without points stay unfilled.
const STEPS: [number, string][] = [[0, "#cce3fd"], [20, "#9dc8f7"], [40, "#6aabf1"], [60, "#338ced"], [80, "#2370cd"], [100, "#0f4a96"]];

export function CoveragePage() {
  const today = isoDay();
  const [from, setFrom] = useState(addDays(today, -29));
  const [to, setTo] = useState(today);
  const [level, setLevel] = useState<"ward" | "lga">("ward");
  const [teamId, setTeamId] = useState("");
  const roster = useRoster();
  const cov = useData(() => get<Coverage>("/analytics/coverage", { date_from: from, date_to: to, level, team_id: teamId }), [from, to, level, teamId]);
  // No ward boundaries loaded yet (e.g. only area councils imported): show LGAs instead.
  const fellBack = useRef(false);
  useEffect(() => {
    if (level === "ward" && cov.data && cov.data.areas.length === 0 && !fellBack.current) {
      fellBack.current = true;
      setLevel("lga");
    }
  }, [cov.data, level]);
  const shapes = useData(() => get<GeoJSON.FeatureCollection>("/map/areas.geojson", { level }), [level]);
  const mapRef = useRef<MlMap | null>(null);
  const [ready, setReady] = useState(false);

  const onReady = useCallback((map: MlMap) => {
    mapRef.current = map;
    setData(map, "cov", EMPTY);
    map.addLayer({
      id: "cov-fill", type: "fill", source: "cov",
      paint: {
        "fill-color": ["case", ["==", ["get", "points"], 0], "rgba(0,0,0,0)",
          ["step", ["get", "pct"], STEPS[0]![1], ...STEPS.slice(1).flatMap(([v, c]) => [v, c])]],
        "fill-opacity": 0.75,
      },
    });
    map.addLayer({ id: "cov-line", type: "line", source: "cov", paint: { "line-color": "#ffffff", "line-width": 1.5 } });
    map.on("click", "cov-fill", (e) => {
      const p = e.features?.[0]?.properties as { name: string; points: number; done: number; pct: number } | undefined;
      if (!p) return;
      new maplibregl.Popup({ closeButton: false }).setLngLat(e.lngLat)
        .setHTML(`<b>${escapeHtml(p.name)}</b><br/>${p.points ? `${p.done} of ${p.points} points done (${p.pct}%)` : "No visit points"}`).addTo(map);
    });
    setReady(true);
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready || !shapes.data) return;
    const by = new Map((cov.data?.areas ?? []).map((a) => [a.id, a]));
    const features = shapes.data.features.map((f) => {
      const a = by.get(String(f.properties?.id));
      return { ...f, properties: { ...f.properties, points: a?.points ?? 0, done: a?.done ?? 0, pct: a?.coverage_pct ?? 0 } };
    });
    setData(map, "cov", { type: "FeatureCollection", features });
    const coords: [number, number][] = [];
    for (const f of features) {
      const g = f.geometry;
      const polys = g.type === "MultiPolygon" ? g.coordinates : g.type === "Polygon" ? [g.coordinates] : [];
      for (const poly of polys) for (const [x, y] of poly[0] ?? []) coords.push([x!, y!]);
    }
    fitTo(map, coords, 13);
  }, [ready, shapes.data, cov.data]);

  const areas = cov.data?.areas ?? [];
  const tot = useMemo(() => ({
    points: areas.reduce((a, x) => a + x.points, 0),
    done: areas.reduce((a, x) => a + x.done, 0),
    nf: areas.reduce((a, x) => a + x.not_found_or_refused, 0),
  }), [areas]);
  const sup = cov.data?.supervision ?? [];
  const supTotals = { n: sup.reduce((a, s) => a + s.collectors, 0), checked: sup.reduce((a, s) => a + s.spot_checked, 0) };

  const columns = useMemo<ColumnDef<AreaRow, any>[]>(() => [ // eslint-disable-line @typescript-eslint/no-explicit-any
    { header: level === "ward" ? "Ward" : "LGA", accessorKey: "name" },
    { header: "Points", accessorKey: "points", meta: { align: "right" } },
    { header: "Done", accessorKey: "done", meta: { align: "right" } },
    { header: "Not found / refused", accessorKey: "not_found_or_refused", meta: { align: "right" } },
    { header: "Not visited", id: "left", accessorFn: (r) => r.points - r.done, meta: { align: "right" } },
    { header: "Coverage", accessorKey: "coverage_pct", cell: (c) => <BarCell pct={c.row.original.coverage_pct} /> },
  ], [level]);

  return (
    <div className="page">
      <div className="filters">
        <h1 style={{ marginRight: 12 }}>Coverage</h1>
        <label>
          Areas
          <select value={level} onChange={(e) => setLevel(e.target.value as "ward" | "lga")}>
            <option value="ward">Wards</option>
            <option value="lga">LGAs / area councils</option>
          </select>
        </label>
        <TeamSelect roster={roster.data} value={teamId} onChange={setTeamId} />
        <DateRange from={from} to={to} max={today} onChange={(f, t) => { setFrom(f); setTo(t); }} />
      </div>
      {cov.error && <div className="error">{cov.error}</div>}
      <div className="grid tiles">
        <StatTile label="Points visited" value={tot.points ? `${Math.round((100 * tot.done) / tot.points)}%` : "—"} sub={`${tot.done} of ${tot.points}`} />
        <StatTile label="Not found or refused" value={tot.nf} sub="counted as visited" />
        <StatTile label="Still to visit" value={tot.points - tot.done} />
        <StatTile label="Collectors spot-checked" value={`${supTotals.checked}/${supTotals.n}`} sub="in the selected period" />
      </div>
      <div className="grid two">
        <div className="card" style={{ padding: 0, height: 420, position: "relative", overflow: "hidden" }}>
          <MapView onReady={onReady} />
          <div className="legend">
            <div className="small" style={{ fontWeight: 600 }}>Points visited</div>
            <div className="row">
              {STEPS.map(([v, c]) => <span key={v} title={`${v}%+`} style={{ width: 22, height: 10, background: c }} />)}
            </div>
            <div className="row small muted" style={{ justifyContent: "space-between" }}><span>0%</span><span>100%</span></div>
          </div>
        </div>
        <div className="card">
          <h2>Supervision coverage</h2>
          {sup.map((s) => (
            <div key={s.team_id} style={{ padding: "6px 0", borderBottom: "1px solid var(--grid)" }}>
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <b>{s.team_name}</b>
                <BarCell pct={s.collectors ? (100 * s.spot_checked) / s.collectors : 0} label={`${s.spot_checked}/${s.collectors}`} />
              </div>
              {s.never_checked.length > 0 && <div className="small muted">No spot check yet: {s.never_checked.join(", ")}</div>}
            </div>
          ))}
          {!sup.length && <div className="muted">No teams.</div>}
        </div>
      </div>
      <div className="card">
        <DataTable data={areas} columns={columns} rowId={(r) => r.id} initialSort={[{ id: "coverage_pct", desc: false }]} empty="No visit points in your areas." />
      </div>
    </div>
  );
}
