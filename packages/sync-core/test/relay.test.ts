import { describe, expect, it } from "vitest";
import {
  buildPushBody,
  FrameCollector,
  fromBase64,
  MAX_RELAYS_PER_PUSH,
  MemoryStore,
  SyncEngine,
  type PushBody,
  openRelay,
  relayBody,
  sealRelay,
  toBase64,
  toFrames,
  uuidBytes,
  type OutboxItem,
} from "../src/index";

const key = new Uint8Array(32).map((_, i) => i);
const nonce = new Uint8Array(12).map((_, i) => 100 + i);
const collectorId = "6f1c2a9e-4b7d-4c1e-9a3f-2d8e5b6a7c90";
const device = { id: "0b9c2f5e-1d2a-4e3b-8c4d-5e6f7a8b9c0d" };

function items(n: number): OutboxItem[] {
  const out: OutboxItem[] = [];
  for (let i = 0; i < n; i++) {
    out.push({
      id: `00000000-0000-4000-8000-${String(i).padStart(12, "0")}`,
      kind: i % 3 === 0 ? "submission" : i % 3 === 1 ? "assignment_update" : "gps_point",
      payload: { answers: { hh_size: i, note: "x".repeat(40) }, changed_at: "2026-11-27T08:00:00Z" },
      createdAt: "2026-11-27T08:00:00Z",
      attempts: 0,
    });
  }
  return out;
}

describe("base64 and uuid helpers", () => {
  it("round-trips every length", () => {
    for (let n = 0; n < 40; n++) {
      const bytes = new Uint8Array(n).map((_, i) => (i * 37 + n) & 0xff);
      expect(toBase64(bytes)).toBe(Buffer.from(bytes).toString("base64"));
      expect(fromBase64(toBase64(bytes))).toEqual(bytes);
    }
  });

  it("matches Python's uuid.bytes order", () => {
    expect(Buffer.from(uuidBytes(collectorId)).toString("hex")).toBe("6f1c2a9e4b7d4c1e9a3f2d8e5b6a7c90");
    expect(() => uuidBytes("not-a-uuid")).toThrow();
  });
});

describe("sealing", () => {
  it("leaves GPS points out and round-trips the rest", () => {
    const body = relayBody(device, items(9), new Date("2026-11-27T09:00:00Z"));
    expect(body.gps_points).toEqual([]);
    expect(body.submissions).toHaveLength(3);
    const env = sealRelay({ id: "aaaaaaaa-0000-4000-8000-000000000001", collectorId, key, nonce, body });
    expect(env.count).toBe(6);
    expect(env.ciphertext).not.toContain("hh_size"); // the carrier can't read it
    expect(openRelay(env, key)).toEqual(body);
  });

  it("detects any change and the wrong collector", () => {
    const body = relayBody(device, items(3), new Date());
    const env = sealRelay({ id: "aaaaaaaa-0000-4000-8000-000000000002", collectorId, key, nonce, body });
    const ct = fromBase64(env.ciphertext);
    ct[3]! ^= 1;
    expect(() => openRelay({ ...env, ciphertext: toBase64(ct) }, key)).toThrow();
    expect(() => openRelay({ ...env, collector_id: "6f1c2a9e-4b7d-4c1e-9a3f-2d8e5b6a7c91" }, key)).toThrow();
  });
});

describe("QR frames", () => {
  const body = relayBody(device, items(60), new Date());
  const env = sealRelay({ id: "bbbbbbbb-0000-4000-8000-000000000003", collectorId, key, nonce, body });

  it("reassemble in any order, with repeats and noise", () => {
    const frames = toFrames(env, 300);
    expect(frames.length).toBeGreaterThan(2);
    const c = new FrameCollector();
    expect(c.add("https://example.com")).toBeNull();
    const shuffled = [...frames].reverse();
    let last = null;
    for (const f of [shuffled[0]!, ...shuffled]) last = c.add(f);
    expect(last?.have).toBe(frames.length);
    expect(last?.envelope).toEqual(env);
  });

  it("starts over when a different bundle is scanned", () => {
    const other = sealRelay({ id: "cccccccc-0000-4000-8000-000000000004", collectorId, key, nonce, body });
    const c = new FrameCollector();
    c.add(toFrames(env, 300)[0]!);
    const p = c.add(toFrames(other, 300)[1]!);
    expect(p?.have).toBe(1);
    expect(p?.session).toBe("cccccccc");
  });
});

describe("relays in a push", () => {
  it("go in their own list, at most MAX_RELAYS_PER_PUSH at a time is enforced by the engine", () => {
    const relay: OutboxItem = { id: "r1", kind: "relay", payload: { collector_id: collectorId }, createdAt: "", attempts: 0 };
    const body = buildPushBody(device, [relay], new Date());
    expect(body.relays).toEqual([{ collector_id: collectorId, id: "r1" }]);
    expect(MAX_RELAYS_PER_PUSH).toBe(20);
  });

  it("25 relays go out over two pushes", async () => {
    const store = new MemoryStore();
    for (let i = 0; i < 25; i++) store.enqueue("relay", `r${i}`, { collector_id: collectorId });
    store.enqueue("message", "m1", { body: "hi" });
    const sizes: number[] = [];
    const engine = new SyncEngine(store, {
      async push(b: PushBody) {
        sizes.push(b.relays?.length ?? 0);
        return { server_time: "", accepted: { shifts: [], submissions: [], assignment_updates: [], messages: b.messages.map((m) => String(m.id)), relays: (b.relays ?? []).map((r) => String(r.id)) }, rejected: [] };
      },
      async pull() { return { server_time: "", assignments: [] }; },
      async uploadMedia() {},
    }, device);
    const r = await engine.run();
    expect(sizes).toEqual([20, 5]);
    expect(r.pushed).toBe(26);
    expect(r.remaining).toBe(0);
  });
});
