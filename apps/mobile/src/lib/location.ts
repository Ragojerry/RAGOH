import * as Location from "expo-location";
import type { Location as Loc } from "./types";

export async function ensureLocationPermission(): Promise<boolean> {
  const { status } = await Location.requestForegroundPermissionsAsync();
  return status === "granted";
}

/**
 * Best current position within `timeoutMs`; falls back to the last known position
 * (common indoors or right after the phone wakes). Returns null if nothing is available.
 */
export async function currentLocation(timeoutMs = 15_000): Promise<Loc | null> {
  if (!(await ensureLocationPermission())) return null;
  const toLoc = (p: Location.LocationObject): Loc => ({
    lat: p.coords.latitude,
    lon: p.coords.longitude,
    accuracy_m: p.coords.accuracy ?? undefined,
  });
  try {
    const fresh = await Promise.race([
      Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.High }),
      new Promise<null>((resolve) => setTimeout(() => resolve(null), timeoutMs)),
    ]);
    if (fresh) return toLoc(fresh);
  } catch {
    // fall through to last known
  }
  try {
    const last = await Location.getLastKnownPositionAsync({ maxAge: 10 * 60_000 });
    return last ? toLoc(last) : null;
  } catch {
    return null;
  }
}
