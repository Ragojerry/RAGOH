import { describe, expect, it } from "vitest";
import {
  HttpError,
  MemoryStore,
  overlayPendingStatuses,
  SyncEngine,
  type MediaItem,
  type PushBody,
  type PushResult,
  type Snapshot,
  type Transport,
} from "../src/index";

const device = { id: "dev-1", model: "Tecno", android_version: "13", app_version: "0.2.0" };

/** Fake server that behaves like the real API: idempotent by id. */
class FakeServer implements Transport {
  stored = new Map<string, Record<string, unknown>>();
  pushes: PushBody[] = [];
  media: string[] = [];
  online = true;
  failNextPush: HttpError | Error | null = null;
  maxBatch = Infinity;
  reject = new Set<string>();
  snapshot: Snapshot = { server_time: "2026-11-26T10:00:00Z", assignments: [{ id: "a1", status: "pending" }, { id: "a2", status: "pending" }] };

  async push(body: PushBody): Promise<PushResult> {
    if (!this.online) throw new TypeError("Network request failed");
    if (this.failNextPush) { const e = this.failNextPush; this.failNextPush = null; throw e; }
    const n = body.shifts.length + body.submissions.length + body.assignment_updates.length;
    if (n > this.maxBatch) throw new HttpError(413, "too many");
    this.pushes.push(body);
    const result: PushResult = { server_time: "", accepted: { shifts: [], submissions: [], assignment_updates: [] }, rejected: [] };
    result.accepted.gps_points = [];
    result.accepted.device_events = [];
    for (const [key, list] of [["shifts", body.shifts], ["submissions", body.submissions], ["assignment_updates", body.assignment_updates], ["gps_points", body.gps_points], ["device_events", body.device_events]] as const) {
      for (const rec of list) {
        const id = String(rec.id);
        if (this.reject.has(id)) { result.rejected.push({ kind: key, id, error: "assignment is not yours" }); continue; }
        if (!this.stored.has(id) || key === "shifts") this.stored.set(id, rec);
        result.accepted[key]!.push(id);
      }
    }
    return result;
  }
  async pull() {
    if (!this.online) throw new TypeError("Network request failed");
    return this.snapshot;
  }
  async uploadMedia(m: MediaItem) {
    if (!this.online) throw new TypeError("Network request failed");
    if (!this.stored.has(m.submissionId)) throw new HttpError(404, "Submission not found");
    this.media.push(m.id);
  }
}

const sub = (id: string, extra: Record<string, unknown> = {}) => ({ form_version_id: "fv", started_at: "t0", ended_at: "t1", data: { q: 1 }, ...extra, id });

describe("sync engine", () => {
  it("queues work offline and delivers it exactly once when back online", async () => {
    const store = new MemoryStore();
    const server = new FakeServer();
    const engine = new SyncEngine(store, server, device);

    server.online = false;
    store.enqueue("shift", "s1", { started_at: "t0" });
    store.enqueue("submission", "sub1", sub("sub1"));
    store.enqueue("assignment_update", "u1", { assignment_id: "a1", status: "completed", changed_at: "t2" });

    const offline = await engine.run();
    expect(offline.error).toMatch(/Network/);
    expect(offline.remaining).toBe(3);
    expect(server.stored.size).toBe(0);

    server.online = true;
    const online = await engine.run();
    expect(online).toMatchObject({ pushed: 3, remaining: 0, pulled: true });
    expect(online.error).toBeUndefined();
    expect(server.stored.size).toBe(3);

    // Nothing is sent again on the next run.
    await engine.run();
    expect(server.pushes).toHaveLength(1);
    expect(store.meta.last_sync_ok_at).toBeDefined();
  });

  it("is safe when a push succeeds on the server but the reply is lost", async () => {
    const store = new MemoryStore();
    const server = new FakeServer();
    const engine = new SyncEngine(store, server, device);
    store.enqueue("submission", "sub1", sub("sub1"));

    // Server stores it, but the phone never hears back.
    const realPush = server.push.bind(server);
    server.push = async (b) => { await realPush(b); throw new TypeError("Network request failed"); };
    expect((await engine.run()).remaining).toBe(1);

    server.push = realPush;
    expect((await engine.run()).remaining).toBe(0);
    expect(server.stored.size).toBe(1); // still one record: ids make retries idempotent
  });

  it("re-sends a shift when it ends after the start was already synced", async () => {
    const store = new MemoryStore();
    const server = new FakeServer();
    const engine = new SyncEngine(store, server, device);
    store.enqueue("shift", "s1", { started_at: "t0" });
    await engine.run();
    store.enqueue("shift", "s1", { started_at: "t0", ended_at: "t9" });
    await engine.run();
    expect(server.stored.get("s1")).toMatchObject({ ended_at: "t9" });
  });

  it("pushes in batches and halves the batch when the server says it's too large", async () => {
    const store = new MemoryStore();
    const server = new FakeServer();
    server.maxBatch = 3;
    for (let i = 0; i < 10; i++) store.enqueue("submission", `s${i}`, sub(`s${i}`));
    const report = await new SyncEngine(store, server, device, { batchSize: 8 }).run();
    expect(report.pushed).toBe(10);
    expect(server.pushes.every((p) => p.submissions.length <= 3)).toBe(true);
  });

  it("keeps rejected records out of the queue and reports them", async () => {
    const store = new MemoryStore();
    const server = new FakeServer();
    server.reject.add("bad");
    store.enqueue("submission", "bad", sub("bad"));
    store.enqueue("submission", "good", sub("good"));
    const report = await new SyncEngine(store, server, device).run();
    expect(report.rejected).toEqual([{ kind: "submissions", id: "bad", error: "assignment is not yours" }]);
    expect(report.remaining).toBe(0);
    expect(store.items.get("bad")?.state).toBe("rejected");
  });

  it("uploads photos only after their submission is accepted", async () => {
    const store = new MemoryStore();
    const server = new FakeServer();
    const engine = new SyncEngine(store, server, device);
    store.enqueue("submission", "sub1", sub("sub1"));
    store.addMedia({ id: "m1", submissionId: "sub1", fieldName: "photo", uri: "file:///p.jpg", mime: "image/jpeg" });
    server.online = false;
    await engine.run();
    expect(server.media).toEqual([]);
    server.online = true;
    const r = await engine.run();
    expect(r.mediaUploaded).toBe(1);
    expect(server.media).toEqual(["m1"]);
  });

  it("does not pull over local changes when the push failed", async () => {
    const store = new MemoryStore();
    const server = new FakeServer();
    server.failNextPush = new HttpError(500, "boom");
    store.enqueue("assignment_update", "u1", { assignment_id: "a2", status: "refused", changed_at: "t5" });
    const report = await new SyncEngine(store, server, device).run();
    expect(report.error).toMatch(/500/);
    expect(report.pulled).toBe(false);
    expect(store.snapshot).toBeNull();
    expect(report.remaining).toBe(1);
  });

  it("overlays queued status changes on a snapshot (latest change wins)", () => {
    const snapshot: Snapshot = { server_time: "", assignments: [{ id: "a1", status: "pending" }, { id: "a2", status: "pending" }] };
    const pending = [
      { id: "u1", kind: "assignment_update" as const, payload: { assignment_id: "a2", status: "refused", changed_at: "t5" }, createdAt: "1", attempts: 0 },
      { id: "u2", kind: "assignment_update" as const, payload: { assignment_id: "a2", status: "completed", changed_at: "t6" }, createdAt: "2", attempts: 0 },
    ];
    const merged = overlayPendingStatuses(snapshot, pending);
    expect(merged.assignments).toEqual([{ id: "a1", status: "pending" }, { id: "a2", status: "completed" }]);
  });

  it("reports a blocked device", async () => {
    const store = new MemoryStore();
    const server = new FakeServer();
    server.failNextPush = new HttpError(423, "This device has been blocked");
    store.enqueue("submission", "sub1", sub("sub1"));
    const r = await new SyncEngine(store, server, device).run();
    expect(r.blocked).toBe(true);
  });

  it("sends GPS points and phone events with the rest of the outbox", async () => {
    const store = new MemoryStore();
    const server = new FakeServer();
    for (let i = 0; i < 1200; i++) store.enqueue("gps_point", `g${i}`, { recorded_at: `t${i}`, lat: 9, lon: 7 });
    store.enqueue("device_event", "e1", { type: "gps_off", occurred_at: "t5" });
    const report = await new SyncEngine(store, server, device).run();
    expect(report.pushed).toBe(1201);
    expect(server.pushes.length).toBe(3); // default batch of 500
    expect(server.pushes.flatMap((p) => p.device_events)).toHaveLength(1);
    expect(report.remaining).toBe(0);
  });

  it("shares one run between concurrent callers", async () => {
    const store = new MemoryStore();
    const server = new FakeServer();
    store.enqueue("submission", "sub1", sub("sub1"));
    const engine = new SyncEngine(store, server, device);
    const [a, b] = await Promise.all([engine.run(), engine.run()]);
    expect(a).toBe(b);
    expect(server.pushes).toHaveLength(1);
  });
});
