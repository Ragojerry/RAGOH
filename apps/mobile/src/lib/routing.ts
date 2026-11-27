/**
 * Route suggestions for the collector.
 * Online: road routes and an optimised day plan from the server (OSRM + VROOM).
 * Offline (or if the server can't route): straight-line estimates, clearly labelled.
 */
import { haversineM, planOffline } from "@ragoh/routing";
import { api } from "./api";
import { getMeta, setMeta } from "./db";
import { listAssignments, listCampaigns } from "./repo";
import type { Assignment, Location } from "./types";

export type PlanSource = "vroom" | "heuristic" | "offline";

export interface PlanStop {
  assignmentId: string;
  order: number;
  legDistanceM: number;
  legDurationS: number;
  arrivalOffsetS: number;
  scheduled: boolean;
}

export interface DayPlan {
  source: PlanSource;
  createdAt: string;
  distanceM: number;
  durationS: number;
  stops: PlanStop[];
  geometry: [number, number][]; // [lon, lat]
}

export interface WalkRoute {
  source: "osrm" | "straight_line" | "offline";
  distanceM: number;
  durationS: number;
  geometry: [number, number][];
  steps: { instruction: string; distance_m: number }[];
}

const OPEN = new Set(["pending", "en_route", "arrived"]);

async function routingConfig(): Promise<{ speedKmh: number; serviceS: number }> {
  const c = (await listCampaigns())[0]?.config as { routing?: { walking_speed_kmh?: number; service_s?: number } } | undefined;
  return { speedKmh: c?.routing?.walking_speed_kmh ?? 4, serviceS: c?.routing?.service_s ?? 600 };
}

export async function openAssignments(): Promise<Assignment[]> {
  return (await listAssignments()).filter((a) => OPEN.has(a.status));
}

/** Plan the order of today's open visits from where the collector is now. */
export async function planDay(me: Location): Promise<DayPlan> {
  const open = await openAssignments();
  let plan: DayPlan;
  try {
    const r = await api<{
      source: "vroom" | "heuristic";
      distance_m: number;
      duration_s: number;
      geometry: [number, number][];
      stops: { assignment_id: string; order: number; leg_distance_m: number; leg_duration_s: number; arrival_offset_s: number; scheduled: boolean }[];
    }>("/api/v1/routes/day-plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: me.lat, lon: me.lon, assignment_ids: open.map((a) => a.id) }),
      timeoutMs: 20_000,
    });
    plan = {
      source: r.source,
      createdAt: new Date().toISOString(),
      distanceM: r.distance_m,
      durationS: r.duration_s,
      geometry: r.geometry,
      stops: r.stops.map((s) => ({
        assignmentId: s.assignment_id, order: s.order, legDistanceM: s.leg_distance_m,
        legDurationS: s.leg_duration_s, arrivalOffsetS: s.arrival_offset_s, scheduled: s.scheduled,
      })),
    };
  } catch {
    const cfg = await routingConfig();
    const p = planOffline(
      me,
      open.map((a) => ({ id: a.id, lat: a.visit_point.lat, lon: a.visit_point.lon })),
      { speedKmh: cfg.speedKmh, serviceS: cfg.serviceS },
    );
    plan = {
      source: "offline",
      createdAt: new Date().toISOString(),
      distanceM: p.distanceM,
      durationS: p.durationS,
      geometry: p.geometry,
      stops: p.stops.map((s) => ({
        assignmentId: s.item.id, order: s.order, legDistanceM: s.legDistanceM,
        legDurationS: s.legDurationS, arrivalOffsetS: s.arrivalOffsetS, scheduled: true,
      })),
    };
  }
  await setMeta("day_plan", JSON.stringify(plan));
  return plan;
}

export async function savedPlan(): Promise<DayPlan | null> {
  const raw = await getMeta("day_plan");
  if (!raw) return null;
  const plan = JSON.parse(raw) as DayPlan;
  // Plans are for one day only.
  return new Date(plan.createdAt).toDateString() === new Date().toDateString() ? plan : null;
}

/** A plan is stale when visits were added, reassigned, done or skipped since it was made. */
export function planIsStale(plan: DayPlan, open: Assignment[]): boolean {
  const planned = new Set(plan.stops.map((s) => s.assignmentId));
  const current = new Set(open.map((a) => a.id));
  return [...current].some((id) => !planned.has(id));
}

export async function routeTo(me: Location, a: Assignment): Promise<WalkRoute> {
  try {
    const r = await api<{ source: "osrm" | "straight_line"; distance_m: number; duration_s: number; geometry: [number, number][]; steps: { instruction: string; distance_m: number }[] }>(
      `/api/v1/routes/to?visit_point_id=${a.visit_point.id}&lat=${me.lat}&lon=${me.lon}`,
      { timeoutMs: 15_000 },
    );
    return { source: r.source, distanceM: r.distance_m, durationS: r.duration_s, geometry: r.geometry, steps: r.steps };
  } catch {
    const cfg = await routingConfig();
    const d = haversineM(me, a.visit_point);
    return {
      source: "offline",
      distanceM: Math.round(d),
      durationS: Math.round(d / ((cfg.speedKmh * 1000) / 3600)),
      geometry: [[me.lon, me.lat], [a.visit_point.lon, a.visit_point.lat]],
      steps: [],
    };
  }
}

export function formatDuration(s: number): string {
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}
