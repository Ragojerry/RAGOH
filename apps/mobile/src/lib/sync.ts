import * as Application from "expo-application";
import * as Device from "expo-device";
import { HttpError, SyncEngine, type MediaItem, type PushBody, type PushResult, type Snapshot, type SyncReport, type Transport } from "@ragoh/sync-core";
import { gzipSync, strToU8 } from "fflate";
import { api, apiResponse, apiUrl, countBytes, flushUsage } from "./api";
import { getMeta, setMeta } from "./db";
import { sqliteSyncStore } from "./repo";
import { deviceId, tokens } from "./secure";

const GZIP_FROM = 2048; // bytes; GPS batches shrink ~4-5x

async function push(body: PushBody): Promise<PushResult> {
  const json = JSON.stringify(body);
  if (json.length >= GZIP_FROM && (await getMeta("gzip_upload_off")) !== "1") {
    try {
      return await api<PushResult>("/api/v1/sync/push", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Content-Encoding": "gzip" },
        body: gzipSync(strToU8(json), { level: 6 }) as unknown as BodyInit,
        timeoutMs: 60_000,
      });
    } catch (err) {
      // If compressed uploads ever fail to arrive intact, stop compressing on this phone.
      if (!(err instanceof HttpError && err.status === 400 && /gzip/i.test(err.message))) throw err;
      await setMeta("gzip_upload_off", "1");
    }
  }
  return api<PushResult>("/api/v1/sync/push", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: json,
    timeoutMs: 60_000,
  });
}

/** Snapshot, or null when unchanged since last time (ETag → 304): saves most sync data. */
async function pull(): Promise<Snapshot | null> {
  const etag = await getMeta("pull_etag");
  const res = await apiResponse("/api/v1/sync/pull", { timeoutMs: 60_000, headers: etag ? { "If-None-Match": etag } : {} });
  if (res.status === 304) return null;
  const text = await res.text();
  if (!res.headers.get("content-length")) countBytes(0, text.length);
  const snap = JSON.parse(text) as Snapshot;
  const tag = res.headers.get("etag");
  // Saved only after the snapshot is stored (onSync below), so a crash mid-apply re-downloads.
  pendingEtag = tag;
  return snap;
}
let pendingEtag: string | null = null;

const transport: Transport = {
  push,
  pull,
  async uploadMedia(m: MediaItem) {
    const form = new FormData();
    form.append("field_name", m.fieldName);
    // React Native's fetch streams the file from its uri.
    form.append("file", { uri: m.uri, name: m.uri.split("/").pop() ?? "photo.jpg", type: m.mime } as unknown as Blob);
    await api(`/api/v1/submissions/${m.submissionId}/media`, { method: "POST", body: form, timeoutMs: 120_000 });
  },
};

let engine: SyncEngine | null = null;
const listeners = new Set<(r: SyncReport) => void>();

async function getEngine(): Promise<SyncEngine> {
  if (!engine) {
    engine = new SyncEngine(sqliteSyncStore, transport, {
      id: await deviceId(),
      model: [Device.manufacturer, Device.modelName].filter(Boolean).join(" ") || null,
      android_version: Device.osVersion ?? null,
      app_version: Application.nativeApplicationVersion ?? null,
    });
  }
  return engine;
}

/** Run a sync now (no-op if signed out). Safe to call often: concurrent calls share a run. */
export async function syncNow(): Promise<SyncReport | null> {
  if (!(await tokens.refresh()) || !(await apiUrl())) return null;
  const report = await (await getEngine()).run();
  if (report.pulled && pendingEtag) await setMeta("pull_etag", pendingEtag);
  pendingEtag = null;
  await flushUsage();
  listeners.forEach((l) => l(report));
  return report;
}

export function onSync(listener: (r: SyncReport) => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function friendlySyncError(r: SyncReport | null): string | null {
  if (!r?.error) return null;
  if (r.blocked) return "This phone has been blocked. Contact your supervisor.";
  return r.error;
}

export { HttpError };
