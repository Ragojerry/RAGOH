/**
 * Staying in touch with the supervisor in real time:
 * - heartbeat: a tiny "I'm alive" ping (position, battery, unsynced count) every few
 *   minutes on shift, so the supervisor's live map is current even between syncs;
 * - push notifications (when the build has Firebase/Expo credentials);
 * - the relay key used to seal data handed to a supervisor's phone.
 */
import * as Battery from "expo-battery";
import Constants from "expo-constants";
import * as Notifications from "expo-notifications";
import * as SecureStore from "expo-secure-store";
import { router } from "expo-router";
import { Platform } from "react-native";
import { api } from "./api";
import { getDb, getMeta, setMeta } from "./db";
import { activeShift, outboxCounts } from "./repo";
import { deviceId } from "./secure";

const DEFAULT_EVERY_S = 180;

// --- Heartbeat ------------------------------------------------------------------
async function lastFix(): Promise<{ lat: number; lon: number; accuracy_m: number | null; recorded_at: string } | null> {
  const db = await getDb();
  const row = await db.getFirstAsync<{ payload: string }>(
    "SELECT payload FROM outbox WHERE kind = 'gps_point' ORDER BY created_at DESC LIMIT 1",
  );
  return row ? (JSON.parse(row.payload) as { lat: number; lon: number; accuracy_m: number | null; recorded_at: string }) : null;
}

/** Send a heartbeat if one is due (or `force`). Never throws: being offline is normal. */
export async function heartbeat(force = false): Promise<boolean> {
  try {
    const every = Number((await getMeta("heartbeat_every_s")) ?? DEFAULT_EVERY_S);
    const last = Number((await getMeta("heartbeat_at")) ?? 0);
    if (!force && Date.now() - last < every * 1000 - 5000) return false;
    // A sync that just succeeded already told the server where we are (GPS points carry
    // position and battery): skip the heartbeat to save data.
    const synced = await getMeta("last_sync_ok_at");
    if (!force && synced && Date.now() - new Date(synced).getTime() < every * 1000) return false;
    const [shift, fix, counts, level] = await Promise.all([
      activeShift(),
      lastFix(),
      outboxCounts(),
      Battery.getBatteryLevelAsync().catch(() => -1),
    ]);
    const fresh = fix && Date.now() - new Date(fix.recorded_at).getTime() < 15 * 60_000 ? fix : null;
    const r = await api<{ heartbeat_every_s: number }>("/api/v1/heartbeat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      timeoutMs: 15_000,
      body: JSON.stringify({
        device_id: await deviceId(),
        recorded_at: new Date().toISOString(),
        lat: fresh?.lat ?? null,
        lon: fresh?.lon ?? null,
        accuracy_m: fresh?.accuracy_m ?? null,
        battery_pct: level >= 0 ? Math.round(level * 100) : null,
        shift_active: !!shift,
        pending_count: counts.pending,
        app_version: Constants.expoConfig?.version ?? null,
      }),
    });
    await setMeta("heartbeat_at", String(Date.now()));
    await setMeta("heartbeat_every_s", String(r.heartbeat_every_s));
    return true;
  } catch {
    return false;
  }
}

// --- Relay key ------------------------------------------------------------------
const relayKeyName = (userId: string) => `ragoh.relay_key.${userId.replace(/-/g, "")}`;

/** Fetch and keep the key once, while online; afterwards handover works offline. */
export async function ensureRelayKey(userId: string): Promise<void> {
  if (await SecureStore.getItemAsync(relayKeyName(userId))) return;
  try {
    const r = await api<{ key: string }>("/api/v1/me/relay-key", { timeoutMs: 15_000 });
    await SecureStore.setItemAsync(relayKeyName(userId), r.key);
  } catch {
    // try again at the next sync
  }
}

export async function relayKey(userId: string): Promise<string | null> {
  return SecureStore.getItemAsync(relayKeyName(userId));
}

// --- Push notifications ---------------------------------------------------------
Notifications.setNotificationHandler({
  handleNotification: async () => ({
    shouldShowBanner: true,
    shouldShowList: true,
    shouldPlaySound: true,
    shouldSetBadge: false,
  }),
});

let channelsReady = false;
async function ensureChannels(): Promise<void> {
  if (channelsReady || Platform.OS !== "android") return;
  await Notifications.setNotificationChannelAsync("alerts", {
    name: "Team alerts",
    description: "High-priority flags and collectors offline during a shift",
    importance: Notifications.AndroidImportance.HIGH,
    vibrationPattern: [0, 250, 250, 250],
    lightColor: "#B42318",
  });
  await Notifications.setNotificationChannelAsync("default", {
    name: "Messages and tasks",
    importance: Notifications.AndroidImportance.DEFAULT,
  });
  channelsReady = true;
}

/** True when this build can receive remote push (see docs/MOBILE.md → Push). */
export function pushConfigured(): boolean {
  const extra = Constants.expoConfig?.extra as { eas?: { projectId?: string } } | undefined;
  return !!extra?.eas?.projectId;
}

/** Ask permission, get the Expo token and tell the server. Silent no-op if not set up. */
export async function registerPush(): Promise<"ok" | "not_configured" | "denied" | "failed"> {
  try {
    await ensureChannels();
    let perm = await Notifications.getPermissionsAsync();
    if (!perm.granted && perm.canAskAgain) perm = await Notifications.requestPermissionsAsync();
    if (!perm.granted) return "denied";
    if (!pushConfigured()) return "not_configured";
    const extra = Constants.expoConfig?.extra as { eas: { projectId: string } };
    const { data: token } = await Notifications.getExpoPushTokenAsync({ projectId: extra.eas.projectId });
    if ((await getMeta("push_token_sent")) === token) return "ok";
    await api("/api/v1/devices/push-token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_id: await deviceId(), token }),
    });
    await setMeta("push_token_sent", token);
    return "ok";
  } catch {
    return "failed";
  }
}

/** Show a notification from inside the app (used when remote push isn't set up). */
export async function localNotify(title: string, body: string, data: Record<string, unknown>): Promise<void> {
  try {
    await ensureChannels();
    const perm = await Notifications.getPermissionsAsync();
    if (!perm.granted) return;
    await Notifications.scheduleNotificationAsync({
      content: { title, body, data, sound: "default" },
      trigger: Platform.OS === "android" ? { channelId: "alerts" } : null,
    });
  } catch {
    // best effort
  }
}

/** Where tapping a notification takes you. */
export function openFromNotification(data: Record<string, unknown> | undefined): void {
  const kind = String(data?.kind ?? "");
  const flagId = data?.flag_id ? String(data.flag_id) : null;
  const collectorId = data?.collector_id ? String(data.collector_id) : null;
  if (kind === "flag" && flagId) router.push({ pathname: "/team/flag/[id]", params: { id: flagId } });
  else if (kind.startsWith("alert") && collectorId) router.push({ pathname: "/team/[id]", params: { id: collectorId } });
  else if (kind === "message") router.push("/messages");
  else if (kind === "reassign" || kind === "backcheck") router.push("/points");
}

export function listenForTaps(): () => void {
  Notifications.getLastNotificationResponseAsync()
    .then((r) => r && openFromNotification(r.notification.request.content.data as Record<string, unknown>))
    .catch(() => undefined);
  const sub = Notifications.addNotificationResponseReceivedListener((r) =>
    openFromNotification(r.notification.request.content.data as Record<string, unknown>),
  );
  return () => sub.remove();
}
