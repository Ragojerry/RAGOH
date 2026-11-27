/** Reads and writes of the app's local data, plus the SQLite implementation of SyncStore. */
import * as Crypto from "expo-crypto";
import type { Answers } from "@ragoh/form-engine";
import type { MediaItem, OutboxItem, OutboxKind, Rejection, Snapshot as CoreSnapshot, SyncStore } from "@ragoh/sync-core";
import { getDb, setMeta } from "./db";
import type { Assignment, AssignmentStatus, Campaign, FormInfo, Location, Shift, Snapshot } from "./types";

const now = () => new Date().toISOString();
export const newId = () => Crypto.randomUUID();

async function enqueue(kind: OutboxKind, id: string, payload: Record<string, unknown>): Promise<void> {
  const db = await getDb();
  // Re-enqueueing the same id (e.g. a shift that has ended) replaces it and queues it again.
  await db.runAsync(
    `INSERT INTO outbox (id, kind, payload, created_at, state) VALUES (?, ?, ?, ?, 'pending')
     ON CONFLICT(id) DO UPDATE SET payload = excluded.payload, state = 'pending', error = NULL`,
    [id, kind, JSON.stringify(payload), now()],
  );
}

// --- Snapshot data -------------------------------------------------------------
export async function listAssignments(): Promise<Assignment[]> {
  const db = await getDb();
  const rows = await db.getAllAsync<{ json: string; status: string }>("SELECT json, status FROM assignments ORDER BY sort");
  return rows.map((r) => ({ ...(JSON.parse(r.json) as Assignment), status: r.status as AssignmentStatus }));
}

export async function getAssignment(id: string): Promise<Assignment | null> {
  const db = await getDb();
  const r = await db.getFirstAsync<{ json: string; status: string }>("SELECT json, status FROM assignments WHERE id = ?", [id]);
  return r ? { ...(JSON.parse(r.json) as Assignment), status: r.status as AssignmentStatus } : null;
}

export async function listForms(kind?: FormInfo["kind"]): Promise<FormInfo[]> {
  const db = await getDb();
  const rows = kind
    ? await db.getAllAsync<{ json: string }>("SELECT json FROM forms WHERE kind = ? ORDER BY name", [kind])
    : await db.getAllAsync<{ json: string }>("SELECT json FROM forms ORDER BY kind, name");
  return rows.map((r) => JSON.parse(r.json) as FormInfo);
}

export async function getForm(versionId: string): Promise<FormInfo | null> {
  const db = await getDb();
  const r = await db.getFirstAsync<{ json: string }>("SELECT json FROM forms WHERE version_id = ?", [versionId]);
  return r ? (JSON.parse(r.json) as FormInfo) : null;
}

export async function listCampaigns(): Promise<Campaign[]> {
  const db = await getDb();
  const rows = await db.getAllAsync<{ json: string }>("SELECT json FROM campaigns");
  return rows.map((r) => JSON.parse(r.json) as Campaign);
}

// --- Assignment status -----------------------------------------------------------
export async function setAssignmentStatus(assignmentId: string, status: AssignmentStatus, reason?: string): Promise<void> {
  const db = await getDb();
  const changedAt = now();
  await db.withTransactionAsync(async () => {
    await db.runAsync("UPDATE assignments SET status = ? WHERE id = ?", [status, assignmentId]);
    await enqueue("assignment_update", newId(), {
      assignment_id: assignmentId, status, reason: reason ?? null, changed_at: changedAt,
    });
  });
}

// --- Shifts -----------------------------------------------------------------------
export async function activeShift(): Promise<Shift | null> {
  const db = await getDb();
  const r = await db.getFirstAsync<{ id: string; started_at: string }>(
    "SELECT id, started_at FROM shifts WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 1",
  );
  return r ? { id: r.id, started_at: r.started_at, ended_at: null } : null;
}

export async function startShift(loc: Location | null): Promise<Shift> {
  const db = await getDb();
  const shift: Shift = { id: newId(), started_at: now(), ended_at: null };
  const payload = { started_at: shift.started_at, start_location: loc };
  await db.withTransactionAsync(async () => {
    await db.runAsync("INSERT INTO shifts (id, started_at, json) VALUES (?, ?, ?)", [shift.id, shift.started_at, JSON.stringify(payload)]);
    await enqueue("shift", shift.id, payload);
  });
  return shift;
}

export async function endShift(id: string, loc: Location | null, reason = "manual"): Promise<void> {
  const db = await getDb();
  const row = await db.getFirstAsync<{ json: string }>("SELECT json FROM shifts WHERE id = ?", [id]);
  if (!row) return;
  const payload = { ...(JSON.parse(row.json) as object), ended_at: now(), end_reason: reason, end_location: loc };
  await db.withTransactionAsync(async () => {
    await db.runAsync("UPDATE shifts SET ended_at = ?, json = ? WHERE id = ?", [payload.ended_at, JSON.stringify(payload), id]);
    await enqueue("shift", id, payload);
  });
}

// --- Drafts & submissions ------------------------------------------------------------
export interface Draft {
  id: string;
  formVersionId: string;
  assignmentId: string | null;
  startedAt: string;
  startLocation: Location | null;
  answers: Answers;
  /** field path → local photo uri */
  media: Record<string, string>;
}

export async function saveDraft(d: Draft): Promise<void> {
  const db = await getDb();
  await db.runAsync(
    `INSERT OR REPLACE INTO drafts (id, form_version_id, assignment_id, started_at, updated_at, json)
     VALUES (?, ?, ?, ?, ?, ?)`,
    [d.id, d.formVersionId, d.assignmentId, d.startedAt, now(), JSON.stringify(d)],
  );
}

export async function findDraft(formVersionId: string, assignmentId: string | null): Promise<Draft | null> {
  const db = await getDb();
  const r = assignmentId
    ? await db.getFirstAsync<{ json: string }>("SELECT json FROM drafts WHERE form_version_id = ? AND assignment_id = ?", [formVersionId, assignmentId])
    : await db.getFirstAsync<{ json: string }>("SELECT json FROM drafts WHERE form_version_id = ? AND assignment_id IS NULL", [formVersionId]);
  return r ? (JSON.parse(r.json) as Draft) : null;
}

export async function deleteDraft(id: string): Promise<void> {
  const db = await getDb();
  await db.runAsync("DELETE FROM drafts WHERE id = ?", [id]);
}

/** Save a finished survey: local log + outbox + photos, all in one transaction. */
export async function finishSubmission(args: {
  draft: Draft;
  form: FormInfo;
  answers: Answers;
  endLocation: Location | null;
  shiftId: string | null;
  pointName: string | null;
  visitPointId: string | null;
}): Promise<void> {
  const { draft, form, answers } = args;
  const db = await getDb();
  const endedAt = now();
  await db.withTransactionAsync(async () => {
    await enqueue("submission", draft.id, {
      form_version_id: form.version_id,
      shift_id: args.shiftId,
      assignment_id: draft.assignmentId,
      visit_point_id: args.visitPointId,
      started_at: draft.startedAt,
      ended_at: endedAt,
      start_location: draft.startLocation,
      end_location: args.endLocation,
      data: answers,
    });
    await db.runAsync(
      "INSERT OR REPLACE INTO submissions (id, form_version_id, form_name, assignment_id, point_name, ended_at) VALUES (?, ?, ?, ?, ?, ?)",
      [draft.id, form.version_id, form.name, draft.assignmentId, args.pointName, endedAt],
    );
    for (const [field, uri] of Object.entries(draft.media)) {
      if (!(field in answers)) continue; // photo question became hidden
      await db.runAsync("INSERT OR REPLACE INTO media (id, submission_id, field_name, uri, mime) VALUES (?, ?, ?, ?, ?)", [
        `${draft.id}:${field}`, draft.id, field, uri, "image/jpeg",
      ]);
    }
    if (draft.assignmentId) {
      await db.runAsync("UPDATE assignments SET status = 'completed' WHERE id = ?", [draft.assignmentId]);
      await enqueue("assignment_update", newId(), {
        assignment_id: draft.assignmentId, status: "completed", reason: null, changed_at: endedAt,
      });
    }
    await db.runAsync("DELETE FROM drafts WHERE id = ?", [draft.id]);
  });
}

export interface LogEntry {
  id: string;
  form_name: string;
  point_name: string | null;
  ended_at: string;
  state: "pending" | "synced" | "rejected";
  error: string | null;
}

export async function submissionLog(sinceIso: string): Promise<LogEntry[]> {
  const db = await getDb();
  return db.getAllAsync<LogEntry>(
    `SELECT s.id, s.form_name, s.point_name, s.ended_at, COALESCE(o.state, 'synced') AS state, o.error
     FROM submissions s LEFT JOIN outbox o ON o.id = s.id
     WHERE s.ended_at >= ? ORDER BY s.ended_at DESC`,
    [sinceIso],
  );
}

export async function outboxCounts(): Promise<{ pending: number; rejected: number; media: number }> {
  const db = await getDb();
  // GPS points are counted separately so "3 waiting" means work the collector did, not fixes.
  const r = await db.getFirstAsync<{ pending: number; rejected: number }>(
    "SELECT SUM(state = 'pending' AND kind NOT IN ('gps_point', 'device_event')) AS pending, SUM(state = 'rejected') AS rejected FROM outbox",
  );
  const m = await db.getFirstAsync<{ n: number }>("SELECT COUNT(*) AS n FROM media WHERE uploaded = 0");
  return { pending: r?.pending ?? 0, rejected: r?.rejected ?? 0, media: m?.n ?? 0 };
}

/** Synced GPS points and events aren't shown anywhere; drop them after a day to save space. */
export async function pruneOutbox(): Promise<void> {
  const db = await getDb();
  const cutoff = new Date(Date.now() - 24 * 3600_000).toISOString();
  await db.runAsync("DELETE FROM outbox WHERE state = 'synced' AND kind IN ('gps_point', 'device_event') AND created_at < ?", [cutoff]);
  await db.runAsync("DELETE FROM meta WHERE key LIKE 'gps_count_%' AND key < ?", [`gps_count_${cutoff.slice(0, 10)}`]);
}

// --- SyncStore over SQLite ------------------------------------------------------------
export const sqliteSyncStore: SyncStore = {
  async pending(limit) {
    const db = await getDb();
    const rows = await db.getAllAsync<{ id: string; kind: OutboxKind; payload: string; created_at: string; attempts: number }>(
      "SELECT id, kind, payload, created_at, attempts FROM outbox WHERE state = 'pending' ORDER BY created_at LIMIT ?",
      [limit],
    );
    return rows.map((r): OutboxItem => ({ id: r.id, kind: r.kind, payload: JSON.parse(r.payload), createdAt: r.created_at, attempts: r.attempts }));
  },
  async pendingCount() {
    const db = await getDb();
    const r = await db.getFirstAsync<{ n: number }>("SELECT COUNT(*) AS n FROM outbox WHERE state = 'pending'");
    return r?.n ?? 0;
  },
  async markSynced(ids) {
    if (!ids.length) return;
    const db = await getDb();
    await db.withTransactionAsync(async () => {
      for (const id of ids) await db.runAsync("UPDATE outbox SET state = 'synced', error = NULL WHERE id = ?", [id]);
    });
  },
  async markRejected(items: Rejection[]) {
    const db = await getDb();
    await db.withTransactionAsync(async () => {
      for (const r of items) await db.runAsync("UPDATE outbox SET state = 'rejected', error = ? WHERE id = ?", [r.error, r.id]);
    });
  },
  async markAttempt(ids, error) {
    const db = await getDb();
    await db.withTransactionAsync(async () => {
      for (const id of ids) await db.runAsync("UPDATE outbox SET attempts = attempts + 1, error = ? WHERE id = ?", [error, id]);
    });
  },
  async pendingMedia(limit) {
    const db = await getDb();
    const rows = await db.getAllAsync<{ id: string; submission_id: string; field_name: string; uri: string; mime: string }>(
      `SELECT m.id, m.submission_id, m.field_name, m.uri, m.mime FROM media m
       JOIN outbox o ON o.id = m.submission_id AND o.state = 'synced'
       WHERE m.uploaded = 0 ORDER BY m.id LIMIT ?`,
      [limit],
    );
    return rows.map((r): MediaItem => ({ id: r.id, submissionId: r.submission_id, fieldName: r.field_name, uri: r.uri, mime: r.mime }));
  },
  async markMediaUploaded(id) {
    const db = await getDb();
    await db.runAsync("UPDATE media SET uploaded = 1, error = NULL WHERE id = ?", [id]);
  },
  async markMediaFailed(id, error) {
    const db = await getDb();
    await db.runAsync("UPDATE media SET error = ? WHERE id = ?", [error, id]);
  },
  async applySnapshot(core: CoreSnapshot) {
    const s = core as unknown as Snapshot;
    const db = await getDb();
    await db.withTransactionAsync(async () => {
      await db.runAsync("DELETE FROM campaigns");
      for (const c of s.campaigns) await db.runAsync("INSERT INTO campaigns (id, json) VALUES (?, ?)", [c.id, JSON.stringify(c)]);
      await db.runAsync("DELETE FROM forms");
      for (const f of s.forms) {
        await db.runAsync("INSERT INTO forms (version_id, form_id, campaign_id, kind, name, json) VALUES (?, ?, ?, ?, ?, ?)", [
          f.version_id, f.form_id, f.campaign_id, f.kind, f.name, JSON.stringify(f),
        ]);
      }
      await db.runAsync("DELETE FROM assignments");
      let i = 0;
      for (const a of s.assignments) {
        await db.runAsync("INSERT INTO assignments (id, status, sort, json) VALUES (?, ?, ?, ?)", [a.id, a.status, i++, JSON.stringify(a)]);
      }
    });
  },
  async setMeta(key, value) {
    await setMeta(key, value);
  },
};
