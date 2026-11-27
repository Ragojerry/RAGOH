import type { FormSchema } from "@ragoh/form-engine";

export type Role = "collector" | "supervisor" | "manager" | "admin";

export interface User {
  id: string;
  username: string;
  full_name: string;
  role: Role;
  language: string;
}

export type AssignmentStatus =
  | "pending" | "en_route" | "arrived" | "in_progress" | "completed" | "not_found" | "refused" | "skipped";

export interface VisitPoint {
  id: string;
  campaign_id: string;
  external_id: string;
  name: string;
  type: string;
  lat: number;
  lon: number;
  geofence_m: number;
  priority: number;
  attributes: Record<string, unknown>;
}

export interface Assignment {
  id: string;
  collector_id: string;
  planned_date: string | null;
  status: AssignmentStatus;
  visit_point: VisitPoint;
  /** "backcheck" = re-interview to verify someone else's visit */
  kind?: "visit" | "backcheck";
  /** e.g. "Revisit requested: …" from the supervisor */
  note?: string | null;
}

export interface InboxMessage {
  id: string;
  sender_id: string;
  sender_name: string;
  recipient_id: string | null;
  flag_id: string | null;
  body: string;
  sent_at: string;
  /** true for messages written on this phone and not yet sent */
  pending?: boolean;
}

export interface FlagToExplain {
  id: string;
  title: string;
  local_date: string;
  status: string;
  details: Record<string, unknown>;
}

export interface Contact {
  id: string;
  full_name: string;
  phone: string | null;
}

export interface FormInfo {
  form_id: string;
  version_id: string;
  campaign_id: string;
  id_string: string;
  name: string;
  kind: "survey" | "supervision" | "backcheck";
  version: string;
  schema: FormSchema;
}

export interface Campaign {
  id: string;
  name: string;
  config: Record<string, unknown>;
}

export interface Snapshot {
  server_time: string;
  user: User;
  campaigns: Campaign[];
  forms: FormInfo[];
  assignments: Assignment[];
  messages?: InboxMessage[];
  my_flags?: FlagToExplain[];
  supervisors?: Contact[];
  /** supervisors only: the collectors they supervise */
  team_members?: { id: string; username: string; full_name: string; phone: string | null; team_id: string; team_name: string }[];
}

export interface Location {
  lat: number;
  lon: number;
  accuracy_m?: number;
}

export interface Shift {
  id: string;
  started_at: string;
  ended_at: string | null;
}

export const STATUS_LABEL: Record<AssignmentStatus, string> = {
  pending: "To visit",
  en_route: "On the way",
  arrived: "Arrived",
  in_progress: "In progress",
  completed: "Done",
  not_found: "Not found",
  refused: "Refused",
  skipped: "Skipped",
};

export const DONE_STATUSES: AssignmentStatus[] = ["completed", "not_found", "refused", "skipped"];
