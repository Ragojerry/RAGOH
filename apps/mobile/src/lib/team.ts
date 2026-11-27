/**
 * Supervisor-mode data. Everything is fetched from the server when online and cached
 * on the phone, so screens still show the last known picture (labelled "as of HH:MM")
 * when the network drops in the field.
 */
import { api } from "./api";
import { getMeta, setMeta } from "./db";
import { localNotify, pushConfigured } from "./live";
import type { Location } from "./types";

export type LiveStatus = "moving" | "at_point" | "idle" | "off_shift" | "offline" | "never_seen";

export interface LiveCollector {
  id: string;
  username: string;
  full_name: string;
  phone: string | null;
  team_id: string;
  team_name: string;
  status: LiveStatus;
  last_seen_at: string | null;
  lat: number | null;
  lon: number | null;
  accuracy_m: number | null;
  battery_pct: number | null;
  on_shift: boolean;
  shift_started_at: string | null;
  pending_sync: number;
  at_point_name: string | null;
  assigned_today: number;
  done_today: number;
  open_flags: number;
  open_high_flags: number;
  risk_score: number;
  distance_m: number;
}

export interface TeamSummary {
  id: string;
  name: string;
  collectors: number;
  on_shift: number;
  offline: number;
  assigned: number;
  done: number;
  open_flags: number;
}

export interface TeamLive {
  server_time: string;
  local_date: string;
  teams: TeamSummary[];
  collectors: LiveCollector[];
}

export interface FlagAction {
  id: string;
  action: string;
  comment: string | null;
  data: Record<string, unknown>;
  actor_name: string | null;
  created_at: string;
}

export interface Flag {
  id: string;
  rule_id: string;
  title: string;
  severity: "low" | "medium" | "high";
  status: "new" | "acknowledged" | "under_review" | "resolved" | "dismissed";
  escalated: boolean;
  collector_id: string;
  collector_name: string;
  local_date: string;
  subject_type: string;
  subject_id: string;
  details: Record<string, unknown>;
  resolution_comment: string | null;
  created_at: string;
  actions?: FlagAction[] | null;
}

export interface Cached<T> {
  data: T;
  fetchedAt: string;
  /** true when the server couldn't be reached and this is the saved copy */
  stale: boolean;
  error?: string;
}

async function cached<T>(key: string, fetcher: () => Promise<T>): Promise<Cached<T> | null> {
  try {
    const data = await fetcher();
    const fetchedAt = new Date().toISOString();
    await setMeta(`cache_${key}`, JSON.stringify({ data, fetchedAt }));
    return { data, fetchedAt, stale: false };
  } catch (err) {
    const raw = await getMeta(`cache_${key}`);
    if (!raw) return null;
    const saved = JSON.parse(raw) as { data: T; fetchedAt: string };
    return { ...saved, stale: true, error: err instanceof Error ? err.message : String(err) };
  }
}

export const STATUS_TEXT: Record<LiveStatus, string> = {
  moving: "Moving",
  at_point: "At a point",
  idle: "Idle",
  off_shift: "Off shift",
  offline: "No signal",
  never_seen: "Not seen today",
};

export function statusTone(s: LiveStatus): "ok" | "warn" | "danger" | "neutral" {
  if (s === "moving" || s === "at_point") return "ok";
  if (s === "idle") return "warn";
  if (s === "offline") return "danger";
  return "neutral";
}

export function teamLive(): Promise<Cached<TeamLive> | null> {
  return cached("team_live", () => api<TeamLive>("/api/v1/team/live", { timeoutMs: 20_000 }));
}

export async function openFlags(collectorId?: string): Promise<Cached<Flag[]> | null> {
  const q = new URLSearchParams();
  for (const s of ["new", "acknowledged", "under_review"]) q.append("status", s);
  if (collectorId) q.set("collector_id", collectorId);
  q.set("limit", "200");
  const res = await cached(`flags_${collectorId ?? "all"}`, async () =>
    (await api<{ items: Flag[] }>(`/api/v1/flags?${q.toString()}`, { timeoutMs: 20_000 })).items,
  );
  if (res && !res.stale && !collectorId) await alertNewHighFlags(res.data);
  return res;
}

/**
 * Without remote push, still raise a phone notification for high flags we haven't
 * announced yet (while the app is open or refreshing in the background).
 */
async function alertNewHighFlags(flags: Flag[]): Promise<void> {
  if (pushConfigured()) return; // the server sends these
  const known = new Set<string>(JSON.parse((await getMeta("announced_flags")) ?? "[]") as string[]);
  const first = known.size === 0 && (await getMeta("announced_flags")) === null;
  const fresh = flags.filter((f) => f.severity === "high" && f.status === "new" && !known.has(f.id));
  for (const f of fresh) known.add(f.id);
  await setMeta("announced_flags", JSON.stringify([...known].slice(-500)));
  if (first) return; // don't flood on first use
  for (const f of fresh.slice(0, 3)) {
    await localNotify(`High-priority flag: ${f.collector_name}`, f.title, { kind: "flag", flag_id: f.id, collector_id: f.collector_id });
  }
}

export function getFlag(id: string): Promise<Flag> {
  return api<Flag>(`/api/v1/flags/${id}`, { timeoutMs: 20_000 });
}

export type FlagActionName =
  | "acknowledge" | "note" | "call" | "message" | "request_explanation" | "revisit"
  | "backcheck" | "escalate" | "resolve" | "dismiss" | "reopen";

export function actOnFlag(
  id: string,
  body: { action: FlagActionName; comment?: string; phone?: string; visit_point_id?: string; assignee_id?: string },
): Promise<Flag> {
  return api<Flag>(`/api/v1/flags/${id}/actions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    timeoutMs: 20_000,
  });
}

export interface DayStop {
  id: string;
  started_at: string;
  ended_at: string;
  dwell_s: number;
  lat: number;
  lon: number;
  classification: string;
  visit_point_name: string | null;
}

export interface CollectorDay {
  user: { id: string; full_name: string; username: string };
  date: string;
  summary: { distance_m: number; n_stops: number; total_dwell_s: number; n_gaps: number; gap_s: number; completed: number; assigned: number } | null;
  stops: DayStop[];
  submissions: { id: string; started_at: string; ended_at: string; lat: number | null; lon: number | null }[];
  /** [lon, lat, unix seconds] */
  track: [number, number, number][];
}

export function collectorDay(id: string, date: string): Promise<Cached<CollectorDay> | null> {
  return cached(`day_${id}_${date}`, () => api<CollectorDay>(`/api/v1/collectors/${id}/day?date=${date}`, { timeoutMs: 30_000 }));
}

export interface TeamAssignment {
  id: string;
  collector_id: string;
  status: string;
  kind: string;
  visit_point: { id: string; name: string; external_id: string; lat: number; lon: number };
}

export function teamAssignments(collectorId: string): Promise<TeamAssignment[]> {
  return api<TeamAssignment[]>(`/api/v1/team/assignments?collector_id=${collectorId}`, { timeoutMs: 20_000 });
}

export function reassign(ids: string[], to: string, reason?: string): Promise<{ moved: string[]; skipped: { id: string; reason: string }[] }> {
  return api("/api/v1/assignments/reassign", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ assignment_ids: ids, to_collector_id: to, reason: reason || null }),
    timeoutMs: 20_000,
  });
}

export interface Route {
  source: string;
  distance_m: number;
  duration_s: number;
  /** [lon, lat] */
  geometry: [number, number][];
}

export function routeBetween(from: Location, to: { lat: number; lon: number }): Promise<Route> {
  const q = new URLSearchParams({ lat: String(from.lat), lon: String(from.lon), to_lat: String(to.lat), to_lon: String(to.lon) });
  return api<Route>(`/api/v1/routes/between?${q.toString()}`, { timeoutMs: 20_000 });
}

/** Team roster saved at sync (works offline). */
export interface TeamMember {
  id: string;
  username: string;
  full_name: string;
  phone: string | null;
  team_id: string;
  team_name: string;
}

export async function roster(): Promise<TeamMember[]> {
  return JSON.parse((await getMeta("team_members")) ?? "[]") as TeamMember[];
}

export function asOf(iso: string): string {
  const d = new Date(iso);
  const sameDay = d.toDateString() === new Date().toDateString();
  return sameDay ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : d.toLocaleString();
}

export function ago(iso: string | null): string {
  if (!iso) return "never";
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const h = Math.floor(mins / 60);
  return h < 24 ? `${h} h ${mins % 60} min ago` : new Date(iso).toLocaleDateString();
}
