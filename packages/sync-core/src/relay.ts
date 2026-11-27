/**
 * Device-to-device relay: a collector with no network hands their unsynced records to a
 * supervisor's phone, which forwards them to the server at its next sync.
 *
 * The collector's phone seals the records with its own relay key (fetched once from the
 * server while online): ChaCha20-Poly1305 over zlib(JSON), with the collector's id as
 * associated data. The supervisor's phone carries the bundle but cannot read or change
 * it; the server opens it and stores the records as the collector's — exactly as if the
 * collector had synced. Records stay queued on the collector's phone too, so nothing is
 * lost if the supervisor's phone is; a later direct sync is acknowledged as a duplicate.
 *
 * Transport is a short sequence of QR codes shown on one screen and scanned by the other
 * camera ("animated QR"): no pairing, no Bluetooth/Wi-Fi permissions, works on any phone.
 */
import { chacha20poly1305 } from "@noble/ciphers/chacha.js";
import { strFromU8, strToU8, unzlibSync, zlibSync } from "fflate";
import type { OutboxItem, OutboxKind, PushBody } from "./index";

export const FRAME_PREFIX = "RAGOH1";
/** Characters of envelope per QR frame: small enough for cheap cameras to read quickly. */
export const DEFAULT_CHUNK = 600;

/** Records worth relaying. GPS points are left for the collector's own sync (too bulky). */
export const RELAY_KINDS: OutboxKind[] = ["shift", "submission", "assignment_update", "device_event", "message", "flag_reply"];

export interface RelayEnvelope {
  v: 1;
  id: string;
  collector_id: string;
  nonce: string;
  ciphertext: string;
  /** Collector phone time when sealed (informational). */
  made_at: string;
  /** Number of records inside, shown to the supervisor (the content itself is sealed). */
  count: number;
}

// --- base64 / uuid helpers (no Buffer/atob reliance: works in Hermes and Node) -----
const B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
const B64_INDEX = new Map([...B64].map((c, i) => [c, i]));

export function toBase64(bytes: Uint8Array): string {
  let out = "";
  for (let i = 0; i < bytes.length; i += 3) {
    const n = (bytes[i]! << 16) | ((bytes[i + 1] ?? 0) << 8) | (bytes[i + 2] ?? 0);
    out += B64[(n >> 18) & 63]! + B64[(n >> 12) & 63]!;
    out += i + 1 < bytes.length ? B64[(n >> 6) & 63]! : "=";
    out += i + 2 < bytes.length ? B64[n & 63]! : "=";
  }
  return out;
}

export function fromBase64(s: string): Uint8Array {
  const clean = s.replace(/=+$/, "");
  if (!/^[A-Za-z0-9+/]*$/.test(clean)) throw new Error("invalid base64");
  const out = new Uint8Array(Math.floor((clean.length * 3) / 4));
  let bits = 0;
  let acc = 0;
  let j = 0;
  for (const c of clean) {
    acc = (acc << 6) | B64_INDEX.get(c)!;
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      out[j++] = (acc >> bits) & 0xff;
    }
  }
  return out;
}

export function uuidBytes(id: string): Uint8Array {
  const hex = id.replace(/-/g, "");
  if (!/^[0-9a-fA-F]{32}$/.test(hex)) throw new Error(`not a uuid: ${id}`);
  const out = new Uint8Array(16);
  for (let i = 0; i < 16; i++) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return out;
}

// --- Sealing ------------------------------------------------------------------------
export function relayBody(device: PushBody["device"], items: OutboxItem[], now: Date): PushBody {
  const body: PushBody = {
    device, device_time: now.toISOString(), shifts: [], submissions: [], assignment_updates: [],
    gps_points: [], device_events: [], messages: [], flag_replies: [],
  };
  const key: Partial<Record<OutboxKind, keyof PushBody>> = {
    shift: "shifts", submission: "submissions", assignment_update: "assignment_updates",
    device_event: "device_events", message: "messages", flag_reply: "flag_replies",
  };
  for (const it of items) {
    const k = key[it.kind];
    if (k) (body[k] as Record<string, unknown>[]).push({ ...it.payload, id: it.id });
  }
  return body;
}

export function sealRelay(args: {
  id: string;
  collectorId: string;
  key: Uint8Array;
  nonce: Uint8Array;
  body: PushBody;
}): RelayEnvelope {
  if (args.key.length !== 32) throw new Error("relay key must be 32 bytes");
  if (args.nonce.length !== 12) throw new Error("nonce must be 12 bytes");
  const plain = zlibSync(strToU8(JSON.stringify(args.body)), { level: 9 });
  const ct = chacha20poly1305(args.key, args.nonce, uuidBytes(args.collectorId)).encrypt(plain);
  const count = (Object.keys(args.body) as (keyof PushBody)[])
    .filter((k) => Array.isArray(args.body[k]))
    .reduce((n, k) => n + (args.body[k] as unknown[]).length, 0);
  return {
    v: 1, id: args.id, collector_id: args.collectorId, nonce: toBase64(args.nonce),
    ciphertext: toBase64(ct), made_at: args.body.device_time, count,
  };
}

/** Inverse of sealRelay (used by tests; the server does this in production). */
export function openRelay(env: RelayEnvelope, key: Uint8Array): PushBody {
  const plain = chacha20poly1305(key, fromBase64(env.nonce), uuidBytes(env.collector_id)).decrypt(fromBase64(env.ciphertext));
  return JSON.parse(strFromU8(unzlibSync(plain))) as PushBody;
}

// --- QR frames ------------------------------------------------------------------------
/** "RAGOH1|<session>|<index>|<total>|<text>" — session = first 8 chars of the relay id. */
export function toFrames(env: RelayEnvelope, chunk = DEFAULT_CHUNK): string[] {
  const text = JSON.stringify(env);
  const n = Math.max(1, Math.ceil(text.length / chunk));
  const session = env.id.replace(/-/g, "").slice(0, 8);
  return Array.from({ length: n }, (_, i) => `${FRAME_PREFIX}|${session}|${i}|${n}|${text.slice(i * chunk, (i + 1) * chunk)}`);
}

export interface FrameProgress {
  session: string;
  have: number;
  total: number;
  envelope?: RelayEnvelope;
}

/** Collects frames in any order (the camera misses and repeats some); one bundle at a time. */
export class FrameCollector {
  private session: string | null = null;
  private parts: (string | undefined)[] = [];

  /** Returns progress, or null if the text isn't a RAGOH frame. */
  add(frame: string): FrameProgress | null {
    const m = /^RAGOH1\|([0-9a-f]{8})\|(\d+)\|(\d+)\|([\s\S]*)$/.exec(frame);
    if (!m) return null;
    const [, session, iStr, nStr, text] = m;
    const i = Number(iStr);
    const n = Number(nStr);
    if (!(n > 0 && n <= 2000 && i < n)) return null;
    if (session !== this.session || this.parts.length !== n) {
      this.session = session!;
      this.parts = new Array<string | undefined>(n).fill(undefined);
    }
    this.parts[i] = text;
    const have = this.parts.filter((p) => p !== undefined).length;
    const progress: FrameProgress = { session: session!, have, total: n };
    if (have === n) {
      const env = JSON.parse(this.parts.join("")) as RelayEnvelope;
      if (env.v !== 1 || !env.id || !env.collector_id || !env.ciphertext || !env.nonce) {
        throw new Error("Not a RAGOH data bundle");
      }
      progress.envelope = env;
    }
    return progress;
  }

  reset(): void {
    this.session = null;
    this.parts = [];
  }
}
