/**
 * Background location tracking — ONLY while a shift is active.
 *
 * Runs as an Android foreground service (persistent notification) so the phone keeps
 * recording when the app is in the background. Every fix goes straight into the encrypted
 * outbox and is synced like everything else.
 *
 * Adaptive sampling saves battery: every ~10 s while walking, every ~90 s while standing
 * still. The mode switches automatically from the fixes themselves.
 */
import * as Battery from "expo-battery";
import * as IntentLauncher from "expo-intent-launcher";
import * as Location from "expo-location";
import * as Network from "expo-network";
import * as TaskManager from "expo-task-manager";
import { getDb, getMeta, setMeta } from "./db";
import { haversineM } from "./geo";
import { activeShift, endShift, newId } from "./repo";

export const LOCATION_TASK = "ragoh-location-tracking";

// Defaults; the campaign config sent by the server overrides these (tracking section).
const DEFAULTS = {
  moving_interval_s: 10,
  stationary_interval_s: 90,
  max_shift_hours: 12,
};
const STILL_RADIUS_M = 30; // movement smaller than this is GPS noise
const STILL_AFTER_S = 180; // this long within the radius → stationary mode
const MOVE_RADIUS_M = 40; // leaving this radius → moving mode
const BACKGROUND_SYNC_EVERY_S = 10 * 60;

type Mode = "moving" | "stationary";

async function trackingConfig(): Promise<typeof DEFAULTS> {
  const db = await getDb();
  const row = await db.getFirstAsync<{ json: string }>("SELECT json FROM campaigns LIMIT 1");
  const tracking = row ? ((JSON.parse(row.json) as { config?: { tracking?: Partial<typeof DEFAULTS> } }).config?.tracking ?? {}) : {};
  return { ...DEFAULTS, ...tracking };
}

function taskOptions(mode: Mode, cfg: typeof DEFAULTS): Location.LocationTaskOptions {
  const moving = mode === "moving";
  return {
    accuracy: moving ? Location.Accuracy.High : Location.Accuracy.Balanced,
    timeInterval: (moving ? cfg.moving_interval_s : cfg.stationary_interval_s) * 1000,
    distanceInterval: moving ? 5 : 0,
    pausesUpdatesAutomatically: false,
    mayShowUserSettingsDialog: true,
    foregroundService: {
      notificationTitle: "RAGOH shift in progress",
      notificationBody: "Your location is recorded until you end your shift.",
      notificationColor: "#0F5132",
      killServiceOnDestroy: false,
    },
  };
}

export async function addDeviceEvent(type: string, details: Record<string, unknown> = {}, shiftId?: string | null): Promise<void> {
  const db = await getDb();
  const id = newId();
  await db.runAsync("INSERT INTO outbox (id, kind, payload, created_at, state) VALUES (?, 'device_event', ?, ?, 'pending')", [
    id,
    JSON.stringify({ type, occurred_at: new Date().toISOString(), shift_id: shiftId ?? null, details }),
    new Date().toISOString(),
  ]);
}

async function networkType(): Promise<string | null> {
  try {
    const st = await Network.getNetworkStateAsync();
    return st.isConnected ? String(st.type ?? "unknown").toLowerCase() : "none";
  } catch {
    return null;
  }
}

async function batteryPct(): Promise<number | null> {
  try {
    const level = await Battery.getBatteryLevelAsync();
    return level >= 0 ? Math.round(level * 100) : null;
  } catch {
    return null;
  }
}

async function switchMode(next: Mode): Promise<void> {
  await setMeta("tracking_mode", next);
  await setMeta("tracking_anchor", null);
  await Location.startLocationUpdatesAsync(LOCATION_TASK, taskOptions(next, await trackingConfig()));
}

/** Decide moving/stationary from the latest fix. */
async function updateMode(lat: number, lon: number, t: number): Promise<void> {
  const mode = ((await getMeta("tracking_mode")) as Mode | null) ?? "moving";
  const rawAnchor = await getMeta("tracking_anchor");
  const anchor = rawAnchor ? (JSON.parse(rawAnchor) as { lat: number; lon: number; t: number }) : null;
  if (!anchor) {
    await setMeta("tracking_anchor", JSON.stringify({ lat, lon, t }));
    return;
  }
  const d = haversineM(anchor, { lat, lon });
  if (mode === "moving") {
    if (d > STILL_RADIUS_M) await setMeta("tracking_anchor", JSON.stringify({ lat, lon, t }));
    else if ((t - anchor.t) / 1000 >= STILL_AFTER_S) await switchMode("stationary");
  } else if (d > MOVE_RADIUS_M) {
    await switchMode("moving");
  }
}

// Must be defined at startup (imported from the root layout) so Android can run it headless.
TaskManager.defineTask<{ locations: Location.LocationObject[] }>(LOCATION_TASK, async ({ data, error }) => {
  if (error) {
    await addDeviceEvent("tracking_error", { message: error.message }).catch(() => undefined);
    return;
  }
  const locations = data?.locations ?? [];
  if (!locations.length) return;
  const shift = await activeShift();
  if (!shift) {
    // Never track outside a shift.
    await Location.stopLocationUpdatesAsync(LOCATION_TASK).catch(() => undefined);
    return;
  }
  const cfg = await trackingConfig();
  if (Date.now() - new Date(shift.started_at).getTime() > cfg.max_shift_hours * 3600_000) {
    await endShift(shift.id, null, "auto_max");
    await addDeviceEvent("shift_auto_ended", { max_hours: cfg.max_shift_hours }, shift.id);
    await Location.stopLocationUpdatesAsync(LOCATION_TASK).catch(() => undefined);
    return;
  }

  const [battery, network] = await Promise.all([batteryPct(), networkType()]);
  const db = await getDb();
  let mocked = false;
  await db.withTransactionAsync(async () => {
    for (const loc of locations) {
      mocked = mocked || !!loc.mocked;
      const payload = {
        recorded_at: new Date(loc.timestamp).toISOString(),
        lat: loc.coords.latitude,
        lon: loc.coords.longitude,
        accuracy_m: loc.coords.accuracy ?? null,
        altitude_m: loc.coords.altitude ?? null,
        speed_mps: loc.coords.speed !== null && loc.coords.speed >= 0 ? loc.coords.speed : null,
        bearing: loc.coords.heading !== null && loc.coords.heading >= 0 ? loc.coords.heading : null,
        battery_pct: battery,
        is_mock: !!loc.mocked,
        network_type: network,
        shift_id: shift.id,
      };
      await db.runAsync("INSERT OR IGNORE INTO outbox (id, kind, payload, created_at, state) VALUES (?, 'gps_point', ?, ?, 'pending')", [
        newId(),
        JSON.stringify(payload),
        payload.recorded_at,
      ]);
    }
  });
  const last = locations[locations.length - 1]!;
  await setMeta("last_fix_at", new Date(last.timestamp).toISOString());
  const countKey = `gps_count_${localDay()}`;
  await setMeta(countKey, String(Number((await getMeta(countKey)) ?? 0) + locations.length));

  if (mocked && (await getMeta("mock_reported_shift")) !== shift.id) {
    await addDeviceEvent("mock_location", { provider: "android" }, shift.id);
    await setMeta("mock_reported_shift", shift.id);
  }
  await updateMode(last.coords.latitude, last.coords.longitude, last.timestamp);

  // Tell the supervisor's live map where we are (throttled; cheap when offline).
  import("./live").then((m) => m.heartbeat()).catch(() => undefined);

  // Keep supervisors' data fresh while the app is in the background.
  const lastSync = Number((await getMeta("bg_sync_at")) ?? 0);
  if (Date.now() - lastSync > BACKGROUND_SYNC_EVERY_S * 1000) {
    await setMeta("bg_sync_at", String(Date.now()));
    const { syncNow } = await import("./sync");
    syncNow().catch(() => undefined);
  }
});

export type StartResult = "ok" | "no_permission" | "no_background_permission" | "gps_off";

export async function startTracking(shiftId: string): Promise<StartResult> {
  if (!(await Location.hasServicesEnabledAsync())) return "gps_off";
  const fg = await Location.requestForegroundPermissionsAsync();
  if (fg.status !== "granted") return "no_permission";
  const bg = await Location.requestBackgroundPermissionsAsync();
  if (bg.status !== "granted") return "no_background_permission";
  await setMeta("tracking_mode", "moving");
  await setMeta("tracking_anchor", null);
  await Location.startLocationUpdatesAsync(LOCATION_TASK, taskOptions("moving", await trackingConfig()));
  await addDeviceEvent("tracking_started", {}, shiftId);
  return "ok";
}

export async function stopTracking(): Promise<void> {
  if (await Location.hasStartedLocationUpdatesAsync(LOCATION_TASK).catch(() => false)) {
    await Location.stopLocationUpdatesAsync(LOCATION_TASK);
  }
}

/**
 * Called whenever the app opens: if a shift is active but tracking isn't running (app was
 * killed, phone restarted), restart it and record that it had stopped.
 */
export async function ensureTracking(): Promise<"running" | "restarted" | "off" | StartResult> {
  const shift = await activeShift();
  if (!shift) {
    await stopTracking();
    return "off";
  }
  if (!(await Location.hasServicesEnabledAsync())) {
    if ((await getMeta("gps_off_reported")) !== shift.id) {
      await addDeviceEvent("gps_off", {}, shift.id);
      await setMeta("gps_off_reported", shift.id);
    }
    return "gps_off";
  }
  await setMeta("gps_off_reported", null);
  if (await Location.hasStartedLocationUpdatesAsync(LOCATION_TASK).catch(() => false)) return "running";
  const lastFix = await getMeta("last_fix_at");
  const r = await startTracking(shift.id);
  if (r === "ok") {
    await addDeviceEvent("tracking_restarted", { last_fix_at: lastFix }, shift.id);
    return "restarted";
  }
  return r;
}

function localDay(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export async function trackingStats(): Promise<{ running: boolean; lastFixAt: string | null; pointsToday: number }> {
  return {
    running: await Location.hasStartedLocationUpdatesAsync(LOCATION_TASK).catch(() => false),
    lastFixAt: await getMeta("last_fix_at"),
    pointsToday: Number((await getMeta(`gps_count_${localDay()}`)) ?? 0),
  };
}

/** Opens Android's battery optimisation list so the user can exempt RAGOH. */
export async function openBatterySettings(): Promise<void> {
  await IntentLauncher.startActivityAsync(IntentLauncher.ActivityAction.IGNORE_BATTERY_OPTIMIZATION_SETTINGS).catch(() => undefined);
}

/** Start a shift and its tracking together. */
export async function beginShift(loc: Parameters<typeof import("./repo").startShift>[0]) {
  const { startShift } = await import("./repo");
  const shift = await startShift(loc);
  const tracking = await startTracking(shift.id);
  return { shift, tracking };
}

/** End a shift and stop tracking. */
export async function finishShift(shiftId: string, loc: Parameters<typeof import("./repo").endShift>[1]): Promise<void> {
  await endShift(shiftId, loc);
  await stopTracking();
}
