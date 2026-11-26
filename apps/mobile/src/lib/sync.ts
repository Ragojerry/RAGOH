import * as Application from "expo-application";
import * as Device from "expo-device";
import { HttpError, SyncEngine, type MediaItem, type PushBody, type PushResult, type Snapshot, type SyncReport, type Transport } from "@ragoh/sync-core";
import { api, apiUrl } from "./api";
import { sqliteSyncStore } from "./repo";
import { deviceId, tokens } from "./secure";

const transport: Transport = {
  push: (body: PushBody) => api<PushResult>("/api/v1/sync/push", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    timeoutMs: 60_000,
  }),
  pull: () => api<Snapshot>("/api/v1/sync/pull", { timeoutMs: 60_000 }),
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
