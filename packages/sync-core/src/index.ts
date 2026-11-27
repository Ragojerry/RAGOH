/**
 * Offline-first sync engine, independent of React Native so it can be unit-tested.
 *
 * Everything recorded on the phone goes into an outbox first. A sync run:
 *   1. pushes outbox records in batches (each has a client UUID → retries are safe),
 *   2. uploads photos for submissions the server has accepted,
 *   3. pulls a fresh snapshot (assignments, forms, config), overlaying any local status
 *      changes that are still waiting to be pushed so the phone never "forgets" them.
 * A network failure stops the run and leaves everything queued for next time.
 */

export type OutboxKind =
  | "shift" | "submission" | "assignment_update" | "gps_point" | "device_event" | "message" | "flag_reply"
  /** A sealed bundle carried for another user (supervisor phones only). */
  | "relay";

export interface OutboxItem {
  /** Client UUID of the record (shift id, submission id, status-change id). */
  id: string;
  kind: OutboxKind;
  payload: Record<string, unknown>;
  createdAt: string;
  attempts: number;
}

export interface MediaItem {
  id: string;
  submissionId: string;
  fieldName: string;
  uri: string;
  mime: string;
}

export interface Rejection {
  kind: string;
  id: string;
  error: string;
}

export interface PushBody {
  device: { id: string; model?: string | null; android_version?: string | null; app_version?: string | null };
  device_time: string;
  shifts: Record<string, unknown>[];
  submissions: Record<string, unknown>[];
  assignment_updates: Record<string, unknown>[];
  gps_points: Record<string, unknown>[];
  device_events: Record<string, unknown>[];
  messages: Record<string, unknown>[];
  flag_replies: Record<string, unknown>[];
  relays?: Record<string, unknown>[];
}

export interface PushResult {
  server_time: string;
  accepted: {
    shifts: string[];
    submissions: string[];
    assignment_updates: string[];
    gps_points?: string[];
    device_events?: string[];
    messages?: string[];
    flag_replies?: string[];
    relays?: string[];
  };
  rejected: Rejection[];
}

export interface SnapshotAssignment {
  id: string;
  status: string;
  [key: string]: unknown;
}

export interface Snapshot {
  server_time: string;
  assignments: SnapshotAssignment[];
  [key: string]: unknown;
}

export interface SyncStore {
  /** Oldest first. */
  pending(limit: number): Promise<OutboxItem[]>;
  pendingCount(): Promise<number>;
  markSynced(ids: string[]): Promise<void>;
  /** Permanently refused by the server; kept for review, never retried automatically. */
  markRejected(items: Rejection[]): Promise<void>;
  markAttempt(ids: string[], error: string): Promise<void>;
  /** Media whose submission has been accepted by the server. */
  pendingMedia(limit: number): Promise<MediaItem[]>;
  markMediaUploaded(id: string): Promise<void>;
  markMediaFailed(id: string, error: string): Promise<void>;
  applySnapshot(snapshot: Snapshot): Promise<void>;
  setMeta(key: string, value: string): Promise<void>;
}

export class HttpError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export interface Transport {
  push(body: PushBody): Promise<PushResult>;
  /** null = nothing changed since the last pull (HTTP 304): keep the local copy. */
  pull(): Promise<Snapshot | null>;
  uploadMedia(item: MediaItem): Promise<void>;
}

export interface SyncReport {
  pushed: number;
  rejected: Rejection[];
  mediaUploaded: number;
  pulled: boolean;
  /** The server said nothing changed since the last pull. */
  unchanged?: boolean;
  remaining: number;
  /** Set when the run stopped early (offline, server error, device blocked…). */
  error?: string;
  blocked?: boolean;
}

export interface SyncOptions {
  batchSize?: number;
  mediaBatch?: number;
  now?: () => Date;
}

type PushKey = "shifts" | "submissions" | "assignment_updates" | "gps_points" | "device_events" | "messages" | "flag_replies" | "relays";
const KEY: Record<OutboxKind, PushKey> = {
  shift: "shifts",
  submission: "submissions",
  assignment_update: "assignment_updates",
  gps_point: "gps_points",
  device_event: "device_events",
  message: "messages",
  flag_reply: "flag_replies",
  relay: "relays",
};
/** The server takes at most this many relays per push; the rest wait for the next batch. */
export const MAX_RELAYS_PER_PUSH = 20;

export function buildPushBody(device: PushBody["device"], items: OutboxItem[], now: Date): PushBody {
  const body: PushBody = {
    device, device_time: now.toISOString(), shifts: [], submissions: [], assignment_updates: [],
    gps_points: [], device_events: [], messages: [], flag_replies: [],
  };
  for (const it of items) {
    const k = KEY[it.kind];
    if (k === "relays") (body.relays ??= []).push({ ...it.payload, id: it.id });
    else body[k].push({ ...it.payload, id: it.id });
  }
  return body;
}

/** Local status changes not yet pushed must win over the (older) server snapshot. */
export function overlayPendingStatuses(snapshot: Snapshot, pending: OutboxItem[]): Snapshot {
  const latest = new Map<string, { status: string; at: string }>();
  for (const it of pending) {
    if (it.kind !== "assignment_update") continue;
    const aid = String(it.payload.assignment_id);
    const at = String(it.payload.changed_at);
    const prev = latest.get(aid);
    if (!prev || at >= prev.at) latest.set(aid, { status: String(it.payload.status), at });
  }
  if (!latest.size) return snapshot;
  return {
    ...snapshot,
    assignments: snapshot.assignments.map((a) => (latest.has(a.id) ? { ...a, status: latest.get(a.id)!.status } : a)),
  };
}

function describe(err: unknown): string {
  if (err instanceof HttpError) return `Server error ${err.status}: ${err.message}`;
  if (err instanceof Error) return err.message;
  return String(err);
}

export class SyncEngine {
  private running: Promise<SyncReport> | null = null;

  constructor(
    private store: SyncStore,
    private transport: Transport,
    private device: PushBody["device"],
    private opts: SyncOptions = {},
  ) {}

  /** Runs a sync; concurrent callers share the same run. */
  run(): Promise<SyncReport> {
    if (!this.running) {
      this.running = this.doRun().finally(() => {
        this.running = null;
      });
    }
    return this.running;
  }

  private async doRun(): Promise<SyncReport> {
    const now = this.opts.now ?? (() => new Date());
    let batchSize = this.opts.batchSize ?? 500;
    const report: SyncReport = { pushed: 0, rejected: [], mediaUploaded: 0, pulled: false, remaining: 0 };

    // 1. Push the outbox in batches.
    for (let guard = 0; guard < 1000; guard++) {
      let relays = 0;
      const items = (await this.store.pending(batchSize)).filter((i) => i.kind !== "relay" || ++relays <= MAX_RELAYS_PER_PUSH);
      if (!items.length) break;
      let result: PushResult;
      try {
        result = await this.transport.push(buildPushBody(this.device, items, now()));
      } catch (err) {
        if (err instanceof HttpError && err.status === 413 && batchSize > 1) {
          batchSize = Math.max(1, Math.floor(batchSize / 2));
          continue;
        }
        await this.store.markAttempt(items.map((i) => i.id), describe(err));
        report.error = describe(err);
        report.blocked = err instanceof HttpError && err.status === 423;
        report.remaining = await this.store.pendingCount();
        return report;
      }
      const a = result.accepted;
      const accepted = [
        ...a.shifts, ...a.submissions, ...a.assignment_updates, ...(a.gps_points ?? []), ...(a.device_events ?? []),
        ...(a.messages ?? []), ...(a.flag_replies ?? []), ...(a.relays ?? []),
      ];
      const rejectedIds = new Set(result.rejected.map((r) => r.id));
      // Anything the server neither accepted nor rejected stays queued (defensive).
      await this.store.markSynced(accepted);
      if (result.rejected.length) await this.store.markRejected(result.rejected);
      report.pushed += accepted.length;
      report.rejected.push(...result.rejected);
      if (accepted.length + rejectedIds.size === 0) {
        report.error = "Server accepted nothing; will retry later";
        break;
      }
    }

    // 2. Photos for accepted submissions (a failed photo doesn't block the rest).
    for (let guard = 0; guard < 1000; guard++) {
      const media = await this.store.pendingMedia(this.opts.mediaBatch ?? 10);
      if (!media.length) break;
      let progressed = false;
      for (const m of media) {
        try {
          await this.transport.uploadMedia(m);
          await this.store.markMediaUploaded(m.id);
          report.mediaUploaded++;
          progressed = true;
        } catch (err) {
          await this.store.markMediaFailed(m.id, describe(err));
          if (!(err instanceof HttpError) || err.status >= 500) {
            report.error = report.error ?? describe(err);
            break; // offline or server trouble: try the rest next time
          }
        }
      }
      if (!progressed) break;
    }

    // 3. Pull a fresh snapshot.
    try {
      const snapshot = await this.transport.pull();
      if (snapshot) {
        const stillPending = await this.store.pending(10_000);
        await this.store.applySnapshot(overlayPendingStatuses(snapshot, stillPending));
        await this.store.setMeta("last_pull_at", snapshot.server_time);
      }
      report.pulled = true;
      report.unchanged = snapshot === null;
    } catch (err) {
      report.error = report.error ?? describe(err);
      report.blocked = report.blocked || (err instanceof HttpError && err.status === 423);
    }
    report.remaining = await this.store.pendingCount();
    if (!report.error) await this.store.setMeta("last_sync_ok_at", now().toISOString());
    return report;
  }
}

/** In-memory store, used by tests (and handy for prototyping). */
export class MemoryStore implements SyncStore {
  items = new Map<string, OutboxItem & { state: "pending" | "synced" | "rejected"; error?: string }>();
  media = new Map<string, MediaItem & { uploaded: boolean; error?: string }>();
  snapshot: Snapshot | null = null;
  meta: Record<string, string> = {};
  private seq = 0;

  enqueue(kind: OutboxKind, id: string, payload: Record<string, unknown>): void {
    const existing = this.items.get(id);
    // Re-enqueueing (e.g. a shift that has now ended) replaces the payload and re-queues it.
    this.items.set(id, {
      id, kind, payload, attempts: 0, state: "pending",
      createdAt: existing?.createdAt ?? String(this.seq++).padStart(8, "0"),
    });
  }

  addMedia(m: MediaItem): void {
    this.media.set(m.id, { ...m, uploaded: false });
  }

  async pending(limit: number) {
    return [...this.items.values()]
      .filter((i) => i.state === "pending")
      .sort((a, b) => a.createdAt.localeCompare(b.createdAt))
      .slice(0, limit)
      .map(({ state: _s, error: _e, ...rest }) => rest);
  }
  async pendingCount() {
    return [...this.items.values()].filter((i) => i.state === "pending").length;
  }
  async markSynced(ids: string[]) {
    ids.forEach((id) => { const i = this.items.get(id); if (i) i.state = "synced"; });
  }
  async markRejected(items: Rejection[]) {
    items.forEach((r) => { const i = this.items.get(r.id); if (i) { i.state = "rejected"; i.error = r.error; } });
  }
  async markAttempt(ids: string[], error: string) {
    ids.forEach((id) => { const i = this.items.get(id); if (i) { i.attempts++; i.error = error; } });
  }
  async pendingMedia(limit: number) {
    return [...this.media.values()]
      .filter((m) => !m.uploaded && this.items.get(m.submissionId)?.state === "synced")
      .slice(0, limit);
  }
  async markMediaUploaded(id: string) {
    const m = this.media.get(id);
    if (m) m.uploaded = true;
  }
  async markMediaFailed(id: string, error: string) {
    const m = this.media.get(id);
    if (m) m.error = error;
  }
  async applySnapshot(s: Snapshot) {
    this.snapshot = s;
  }
  async setMeta(k: string, v: string) {
    this.meta[k] = v;
  }
}

export * from "./relay";
