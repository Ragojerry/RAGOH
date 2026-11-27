/**
 * Route replay: a collector's day on a timeline. The actual track draws up to the play
 * head; stops are circles sized by time spent; the planned route is a dashed line.
 */
import * as maplibregl from "maplibre-gl";
import type { Map as MlMap } from "maplibre-gl";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { StatTile } from "../components/charts";
import { EMPTY, MapView, fitTo, setData } from "../components/MapView";
import { get } from "../lib/api";
import { addDays, duration, isoDay, km, time } from "../lib/format";
import { useData, useRoster } from "../lib/hooks";
import type { CollectorDay, Flag, Stop } from "../lib/types";
import { escapeHtml } from "./Live";

const SPEEDS = [1, 5, 15, 60]; // minutes of the day per second
const STOP_FILL: Record<string, string> = {
  at_assigned_point: "#0f5132",
  near_assigned_point: "#0f5132",
  unassigned_location: "#b54708",
  base: "#8a8f98",
};
const STOP_TEXT: Record<string, string> = {
  at_assigned_point: "At an assigned point",
  near_assigned_point: "Near an assigned point",
  unassigned_location: "Somewhere else",
  base: "Home / base",
};

const stopRadius = (dwell_s: number) => Math.min(24, 5 + Math.sqrt(dwell_s / 60) * 2.6);

function line(coords: [number, number][]): GeoJSON.Feature {
  return { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: coords } };
}

export function ReplayPage() {
  const [params, setParams] = useSearchParams();
  const roster = useRoster();
  const collectorId = params.get("collector") ?? "";
  const date = params.get("date") ?? isoDay();
  const day = useData(
    () => (collectorId ? get<CollectorDay>(`/collectors/${collectorId}/day`, { date }) : Promise.resolve(null)),
    [collectorId, date],
  );
  const flags = useData(
    () =>
      collectorId
        ? get<{ items: Flag[] }>("/flags", { collector_id: collectorId, date_from: date, date_to: date, limit: 200 }).then((r) => r.items)
        : Promise.resolve([] as Flag[]),
    [collectorId, date],
  );
  const risk = useData(
    () => (collectorId ? get<{ collector_id: string; risk_score: number }[]>("/risk", { date }) : Promise.resolve([])),
    [collectorId, date],
  );

  const mapRef = useRef<MlMap | null>(null);
  const [mapReady, setMapReady] = useState(false);
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(5);
  const [stopSel, setStopSel] = useState<Stop | null>(null);

  const track = day.data?.track ?? [];
  const t0 = track[0]?.[2] ?? 0;
  const t1 = track[track.length - 1]?.[2] ?? 0;
  useEffect(() => {
    setT(t1);
    setPlaying(false);
    setStopSel(null);
  }, [day.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const onReady = useCallback((map: MlMap) => {
    mapRef.current = map;
    setData(map, "planned", EMPTY);
    map.addLayer({ id: "planned", type: "line", source: "planned", paint: { "line-color": "#5b615e", "line-width": 2, "line-dasharray": [2, 2] } });
    setData(map, "track-all", EMPTY);
    map.addLayer({ id: "track-all", type: "line", source: "track-all", paint: { "line-color": "#257de0", "line-width": 2, "line-opacity": 0.25 } });
    setData(map, "track", EMPTY);
    map.addLayer({ id: "track", type: "line", source: "track", layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": "#257de0", "line-width": 3 } });
    setData(map, "stops", EMPTY);
    map.addLayer({
      id: "stops", type: "circle", source: "stops",
      paint: {
        "circle-radius": ["get", "r"], "circle-color": ["get", "fill"], "circle-opacity": 0.35,
        "circle-stroke-color": ["get", "fill"], "circle-stroke-width": 2,
      },
    });
    setData(map, "head", EMPTY);
    map.addLayer({ id: "head", type: "circle", source: "head", paint: { "circle-radius": 7, "circle-color": "#191919", "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
    map.on("click", "stops", (e) => {
      const id = e.features?.[0]?.properties?.id as string | undefined;
      if (id) setStopSel((cur) => (cur?.id === id ? null : ({ id } as Stop)));
    });
    map.on("mouseenter", "stops", () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", "stops", () => (map.getCanvas().style.cursor = ""));
    setMapReady(true);
  }, []);

  // Static layers when the day changes.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    const d = day.data;
    setData(map, "planned", d?.planned?.geometry.length ? line(d.planned.geometry) : EMPTY);
    setData(map, "track-all", track.length > 1 ? line(track.map(([x, y]) => [x, y])) : EMPTY);
    const all: [number, number][] = [...track.map(([x, y]) => [x, y] as [number, number]), ...(d?.planned?.geometry ?? [])];
    fitTo(map, all, 16);
  }, [mapReady, day.data]); // eslint-disable-line react-hooks/exhaustive-deps

  // Everything that moves with the play head.
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    let i = 0;
    while (i < track.length - 1 && track[i + 1]![2] <= t) i++;
    const shown = track.slice(0, i + 1).map(([x, y]) => [x, y] as [number, number]);
    setData(map, "track", shown.length > 1 ? line(shown) : EMPTY);
    setData(map, "head", track[i] ? { type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [track[i]![0], track[i]![1]] } } : EMPTY);
    const stops = (day.data?.stops ?? []).filter((s) => new Date(s.started_at).getTime() / 1000 <= t);
    setData(map, "stops", {
      type: "FeatureCollection",
      features: stops.map((s) => ({
        type: "Feature",
        properties: { id: s.id, r: stopRadius(s.dwell_s), fill: STOP_FILL[s.classification] ?? "#8a8f98" },
        geometry: { type: "Point", coordinates: [s.lon, s.lat] },
      })),
    });
  }, [mapReady, t, day.data]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!playing) return;
    const timer = setInterval(() => {
      setT((cur) => {
        const next = cur + (speed * 60) / 10;
        if (next >= t1) {
          setPlaying(false);
          return t1;
        }
        return next;
      });
    }, 100);
    return () => clearInterval(timer);
  }, [playing, speed, t1]);

  const stop = useMemo(() => day.data?.stops.find((s) => s.id === stopSel?.id) ?? null, [stopSel, day.data]);
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !stop) return;
    const subs = day.data?.submissions.filter((s) => stop.submission_ids.includes(s.id)) ?? [];
    const p = new maplibregl.Popup({ offset: 10 }).setLngLat([stop.lon, stop.lat]).setHTML(
      `<b>${time(stop.started_at)}–${time(stop.ended_at)}</b> · ${duration(stop.dwell_s)}<br/>${escapeHtml(STOP_TEXT[stop.classification] ?? stop.classification)}` +
        (stop.visit_point_name ? `<br/>${escapeHtml(stop.visit_point_name)}${stop.distance_to_point_m !== null ? ` (${Math.round(stop.distance_to_point_m)} m away)` : ""}` : "") +
        `<br/>${subs.length ? subs.map((s) => `Survey ${time(s.started_at)}–${time(s.ended_at)} (${duration(s.duration_s)})`).join("<br/>") : "No survey during this stop"}`,
    ).addTo(map);
    p.on("close", () => setStopSel(null));
    return () => {
      p.remove();
    };
  }, [stop]); // eslint-disable-line react-hooks/exhaustive-deps

  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    next.set(k, v);
    setParams(next, { replace: true });
  };
  const s = day.data?.summary;
  const atPoints = (day.data?.stops ?? []).filter((x) => x.classification === "at_assigned_point").reduce((a, x) => a + x.dwell_s, 0);
  const riskScore = risk.data?.find((r) => r.collector_id === collectorId)?.risk_score;
  const collectors = roster.data?.collectors ?? [];
  const fmtClock = (u: number) => (u ? new Date(u * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—");

  return (
    <div className="page full">
      <aside className="side">
        <div className="head">
          <div className="filters">
            <label>
              Collector
              <select value={collectorId} onChange={(e) => set("collector", e.target.value)}>
                <option value="">Choose…</option>
                {collectors.map((c) => <option key={c.id} value={c.id}>{c.full_name} — {c.team_name}</option>)}
              </select>
            </label>
            <label>
              Date
              <div style={{ display: "flex", gap: 4 }}>
                <button className="secondary" onClick={() => set("date", addDays(date, -1))} aria-label="Previous day">‹</button>
                <input type="date" value={date} max={isoDay()} onChange={(e) => e.target.value && set("date", e.target.value)} />
                <button className="secondary" disabled={date >= isoDay()} onClick={() => set("date", addDays(date, 1))} aria-label="Next day">›</button>
              </div>
            </label>
          </div>
          {day.error && <div className="error">{day.error}</div>}
        </div>
        <div className="body">
          {!collectorId && <div className="muted">Choose a collector to replay their day.</div>}
          {day.data && (
            <>
              <div className="grid tiles" style={{ gridTemplateColumns: "1fr 1fr" }}>
                <StatTile label="Distance walked" value={s ? km(s.distance_m) : "—"} sub={day.data.planned ? `planned ${km(day.data.planned.distance_m)}` : "no plan made"} />
                <StatTile label="Stops" value={s?.n_stops ?? 0} sub={`${s?.n_stops_at_points ?? 0} at assigned points`} />
                <StatTile label="Time at points" value={duration(atPoints)} sub={`of ${duration(s?.total_dwell_s ?? 0)} stopped`} />
                <StatTile label="Visits done" value={s ? `${s.completed}/${s.assigned}` : "—"} sub={`${s?.n_submissions ?? 0} surveys`} />
                <StatTile label="Flags" value={flags.data?.length ?? 0} sub={flags.data?.some((f) => f.severity === "high") ? "includes high" : ""} />
                <StatTile label="Risk score" value={riskScore ?? 0} sub="0–100, from the flags" />
              </div>
              {s && (s.n_gaps > 0 || s.n_mock_points > 0) && (
                <div className="small muted">
                  {s.n_gaps > 0 && `${s.n_gaps} gap(s) without signal, ${duration(s.gap_s)} in total. `}
                  {s.n_mock_points > 0 && `${s.n_mock_points} fake-location points were ignored.`}
                </div>
              )}
              {track.length > 1 ? (
                <div className="card" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  <div className="timeline">
                    <button onClick={() => { if (t >= t1) setT(t0); setPlaying((p) => !p); }} style={{ minWidth: 70 }}>{playing ? "Pause" : t >= t1 ? "Replay" : "Play"}</button>
                    <input type="range" min={t0} max={t1} step={30} value={t} onChange={(e) => { setPlaying(false); setT(Number(e.target.value)); }} aria-label="Time of day" />
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <b className="num">{fmtClock(t)}</b>
                    <label className="small muted">
                      Speed{" "}
                      <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
                        {SPEEDS.map((v) => <option key={v} value={v}>{v} min / s</option>)}
                      </select>
                    </label>
                    <span className="small muted">{fmtClock(t0)}–{fmtClock(t1)}</span>
                  </div>
                </div>
              ) : (
                <div className="muted">No synced GPS track for this day.</div>
              )}
              <div className="card">
                <h2>Stops</h2>
                <table className="data">
                  <tbody>
                    {day.data.stops.map((st, i) => (
                      <tr key={st.id} className={`clickable ${stop?.id === st.id ? "selected" : ""}`} onClick={() => { setPlaying(false); setT(Math.max(t, new Date(st.ended_at).getTime() / 1000)); setStopSel(st); }}>
                        <td className="num">{i + 1}</td>
                        <td className="num">{time(st.started_at)}</td>
                        <td className="num">{duration(st.dwell_s)}</td>
                        <td>
                          <span className="dot" style={{ background: STOP_FILL[st.classification] ?? "#8a8f98" }} /> {st.visit_point_name ?? STOP_TEXT[st.classification] ?? st.classification}
                        </td>
                        <td className="num">{st.submission_ids.length ? `${st.submission_ids.length} survey` : ""}</td>
                      </tr>
                    ))}
                    {!day.data.stops.length && <tr><td className="muted">No stops.</td></tr>}
                  </tbody>
                </table>
              </div>
              {!!flags.data?.length && (
                <div className="card">
                  <h2>Flags this day</h2>
                  {flags.data.map((f) => (
                    <div key={f.id} style={{ padding: "3px 0" }}>
                      <span className={`chip sev-${f.severity}`}>{f.severity}</span> <Link to={`/flags/${f.id}`}>{f.title}</Link>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </aside>
      <div className="mapwrap">
        <MapView onReady={onReady} />
        <div className="legend">
          <div className="row"><span style={{ width: 22, borderTop: "3px solid #257de0" }} />Actual route</div>
          <div className="row"><span style={{ width: 22, borderTop: "2px dashed #5b615e" }} />Planned route</div>
          {Object.entries({ at_assigned_point: "Stop at a point", unassigned_location: "Stop elsewhere", base: "Home / base" }).map(([k, v]) => (
            <div className="row" key={k}><span className="dot" style={{ background: STOP_FILL[k], opacity: 0.6, border: `2px solid ${STOP_FILL[k]}` }} />{v}</div>
          ))}
          <div className="small muted">Circle size = time spent</div>
        </div>
      </div>
    </div>
  );
}
