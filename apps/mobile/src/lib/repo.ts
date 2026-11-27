/** Reads and writes of the app's local data, plus the SQLite implementation of SyncStore. */
import * as Crypto from "expo-crypto";
import type { Answers } from "@ragoh/form-engine";
import { RELAY_KINDS, type MediaItem, type OutboxItem, type OutboxKind, type RelayEnvelope, type Rejection, type Snapshot as CoreSnapshot, type SyncStore } from "@ragoh/sync-core";
import { getDb, setMeta } from "./db";
import type { Assignment, AssignmentStatus, Campaign, Contact, FlagToExplain, FormInfo, InboxMessage, Location, Shift, Snapshot } from "./types";

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
    "SELECT SUM(state = 'pending' AND kind NOT IN ('gps_point', 'device_event', 'message', 'flag_reply')) AS pending, SUM(state = 'rejected') AS rejected FROM outbox",
  );
  const m = await db.getFirstAsync<{ n: number }>("SELECT COUNT(*) AS n FROM media WHERE uploaded = 0");
  return { pending: r?.pending ?? 0, rejected: r?.rejected ?? 0, media: m?.n ?? 0 };
}

/** Synced GPS points and events aren't shown anywhere; drop them after a day to save space. */
export async function pruneOutbox(): Promise<void> {
  const db = await getDb();
  const cutoff = new Date(Date.now() - 24 * 3600_000).toISOString();
  await db.runAsync("DELETE FROM outbox WHERE state = 'synced' AND kind IN ('gps_point', 'device_event', 'message', 'flag_reply', 'relay') AND created_at < ?", [cutoff]);
  await db.runAsync("DELETE FROM meta WHERE key LIKE 'gps_count_%' AND key < ?", [`gps_count_${cutoff.slice(0, 10)}`]);
}

// --- Inbox: supervisor messages and flags to explain ---------------------------------
export async function inbox(): Promise<{ messages: InboxMessage[]; flags: FlagToExplain[]; supervisors: Contact[] }> {
  const db = await getDb();
  const read = async (key: string) => {
    const r = await db.getFirstAsync<{ value: string }>("SELECT value FROM meta WHERE key = ?", [key]);
    return r ? JSON.parse(r.value) : [];
  };
  const pending = await db.getAllAsync<{ id: string; payload: string }>(
    "SELECT id, payload FROM outbox WHERE kind IN ('message', 'flag_reply') AND state = 'pending' ORDER BY created_at",
  );
  const me = await db.getFirstAsync<{ value: string }>("SELECT value FROM meta WHERE key = 'user_id'");
  const outgoing: InboxMessage[] = pending.map((p) => {
    const d = JSON.parse(p.payload) as { body?: string; comment?: string; recipient_id?: string; flag_id?: string; sent_at: string };
    return { id: p.id, sender_id: me?.value ?? "", sender_name: "You", recipient_id: d.recipient_id ?? null, flag_id: d.flag_id ?? null,
      body: d.body ?? d.comment ?? "", sent_at: d.sent_at, pending: true };
  });
  const messages = [...((await read("inbox_messages")) as InboxMessage[]), ...outgoing].sort((a, b) => b.sent_at.localeCompare(a.sent_at));
  // A supervisor writes to their collectors (and to their own supervisors, if any).
  const team = ((await read("team_members")) as Contact[]).map(({ id, full_name, phone }) => ({ id, full_name, phone }));
  const contacts = [...((await read("inbox_supervisors")) as Contact[]), ...team];
  return { messages, flags: (await read("inbox_flags")) as FlagToExplain[], supervisors: contacts };
}

export async function queueMessage(recipientId: string, body: string): Promise<void> {
  await enqueue("message", newId(), { recipient_id: recipientId, body: body.trim(), sent_at: now() });
}

export async function queueFlagReply(flagId: string, comment: string): Promise<void> {
  await enqueue("flag_reply", newId(), { flag_id: flagId, comment: comment.trim(), sent_at: now() });
}

export async function unreadCount(): Promise<number> {
  const { messages, flags } = await inbox();
  const seen = (await (await getDb()).getFirstAsync<{ value: string }>("SELECT value FROM meta WHERE key = 'inbox_seen_at'"))?.value ?? "";
  const me = (await (await getDb()).getFirstAsync<{ value: string }>("SELECT value FROM meta WHERE key = 'user_id'"))?.value;
  return messages.filter((m) => !m.pending && m.sender_id !== me && m.sent_at > seen).length + flags.length;
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
      const put = (key: string, value: unknown) =>
        db.runAsync("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", [key, JSON.stringify(value)]);
      await put("inbox_messages", s.messages ?? []);
      await put("inbox_flags", s.my_flags ?? []);
      await put("inbox_supervisors", s.supervisors ?? []);
      await put("team_members", s.team_members ?? []);
      await db.runAsync("INSERT OR REPLACE INTO meta (key, value) VALUES ('user_id', ?)", [s.user.id]);
    });
  },
  async setMeta(key, value) {
    await setMeta(key, value);
  },
};

// --- Device-to-device relay ---------------------------------------------------------
/** Collector side: unsynced work records that can be handed to a supervisor. */
export async function relayableItems(): Promise<OutboxItem[]> {
  const db = await getDb();
  const kinds = RELAY_KINDS.map((k) => `'${k}'`).join(", ");
  const rows = await db.getAllAsync<{ id: string; kind: OutboxKind; payload: string; created_at: string; attempts: number }>(
    `SELECT id, kind, payload, created_at, attempts FROM outbox WHERE state = 'pending' AND kind IN (${kinds}) ORDER BY created_at LIMIT 1000`,
  );
  return rows.map((r) => ({ id: r.id, kind: r.kind, payload: JSON.parse(r.payload), createdAt: r.created_at, attempts: r.attempts }));
}

export interface ReceivedRelay {
  id: string;
  collector_id: string;
  collector_name: string;
  count: number;
  made_at: string;
  scanned_at: string;
}

/** Supervisor side: keep a scanned bundle; it goes to the server with the next sync. */
export async function queueRelay(env: RelayEnvelope, collectorName: string): Promise<"queued" | "duplicate"> {
  const db = await getDb();
  const scannedAt = now();
  const r = await db.runAsync(
    "INSERT OR IGNORE INTO outbox (id, kind, payload, created_at, state) VALUES (?, 'relay', ?, ?, 'pending')",
    [env.id, JSON.stringify({ collector_id: env.collector_id, nonce: env.nonce, ciphertext: env.ciphertext, scanned_at: scannedAt }), scannedAt],
  );
  if (!r.changes) return "duplicate";
  const list = JSON.parse((await db.getFirstAsync<{ value: string }>("SELECT value FROM meta WHERE key = 'relays_received'"))?.value ?? "[]") as ReceivedRelay[];
  list.unshift({ id: env.id, collector_id: env.collector_id, collector_name: collectorName, count: env.count, made_at: env.made_at, scanned_at: scannedAt });
  await setMeta("relays_received", JSON.stringify(list.slice(0, 50)));
  return "queued";
}

export async function receivedRelays(): Promise<(ReceivedRelay & { state: string; error: string | null })[]> {
  const db = await getDb();
  const list = JSON.parse((await db.getFirstAsync<{ value: string }>("SELECT value FROM meta WHERE key = 'relays_received'"))?.value ?? "[]") as ReceivedRelay[];
  const out = [];
  for (const r of list) {
    const row = await db.getFirstAsync<{ state: string; error: string | null }>("SELECT state, error FROM outbox WHERE id = ?", [r.id]);
    out.push({ ...r, state: row?.state ?? "synced", error: row?.error ?? null });
  }
  return out;
}
