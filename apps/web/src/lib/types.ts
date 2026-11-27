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

export interface TeamLive {
  server_time: string;
  local_date: string;
  teams: { id: string; name: string; collectors: number; on_shift: number; offline: number; assigned: number; done: number; open_flags: number }[];
  collectors: LiveCollector[];
}

export interface Team {
  id: string;
  name: string;
  campaign_id: string;
}

export interface Stop {
  id: string;
  started_at: string;
  ended_at: string;
  dwell_s: number;
  lat: number;
  lon: number;
  n_points: number;
  classification: string;
  visit_point_id: string | null;
  visit_point_name: string | null;
  distance_to_point_m: number | null;
  submission_ids: string[];
}

export interface DaySummary {
  distance_m: number;
  n_gps_points: number;
  n_mock_points: number;
  n_stops: number;
  n_stops_at_points: number;
  total_dwell_s: number;
  median_dwell_s: number;
  moving_s: number;
  stationary_s: number;
  n_gaps: number;
  gap_s: number;
  first_activity: string | null;
  last_activity: string | null;
  assigned: number;
  completed: number;
  n_submissions: number;
  risk_score?: number;
}

export interface CollectorDay {
  user: { id: string; full_name: string; username: string };
  date: string;
  summary: DaySummary | null;
  stops: Stop[];
  submissions: { id: string; started_at: string; ended_at: string; duration_s: number; visit_point_id: string | null; lat: number | null; lon: number | null }[];
  events: { type: string; occurred_at: string; details: Record<string, unknown> }[];
  /** [lon, lat, unix seconds] */
  track: [number, number, number][];
  planned: { created_at: string; source: string; distance_m: number; duration_s: number; stop_order: string[]; geometry: [number, number][] } | null;
}

export interface CollectorStats {
  id: string;
  full_name: string;
  username: string;
  team_id: string;
  team_name: string;
  assigned: number;
  done: number;
  coverage_pct: number;
  days_worked: number;
  submissions: number;
  median_interview_s: number | null;
  avg_dwell_at_points_s: number | null;
  distance_km: number;
  flags: number;
  high_flags: number;
  flags_per_100: number | null;
  avg_risk: number;
  max_risk: number;
  spot_checks: number;
  last_spot_check: string | null;
}

export interface DailyRow {
  date: string;
  active_collectors: number;
  submissions: number;
  distance_km: number;
  flags: number;
  avg_risk: number;
}

export interface TeamOverview {
  date_from: string;
  date_to: string;
  collectors: CollectorStats[];
  daily: DailyRow[];
}

export interface Coverage {
  level: string;
  areas: { id: string; name: string; code: string; level: string; points: number; assigned: number; done: number; not_found_or_refused: number; coverage_pct: number }[];
  supervision: { team_id: string; team_name: string; collectors: number; spot_checked: number; never_checked: string[] }[];
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
  updated_at: string;
  actions?: FlagAction[] | null;
}

export interface Area {
  id: string;
  parent_id: string | null;
  level: string;
  name: string;
  code: string;
}
