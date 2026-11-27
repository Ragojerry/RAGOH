/**
 * Map style and offline map download.
 *
 * The server publishes a style for the self-hosted FCT map (vector tiles) once the
 * "Routing data" workflow has run. MapLibre can then download the team's area for offline
 * use. Until then the app uses OpenStreetMap's online tiles (no offline download — their
 * usage policy forbids bulk downloads).
 */
import { OfflineManager } from "@maplibre/maplibre-react-native";
import { apiUrl } from "./api";
import { getMeta, setMeta } from "./db";
import { listAssignments } from "./repo";

const PACK_NAME = "ragoh-area";
const MARGIN_DEG = 0.01; // ~1 km around the furthest points
const MIN_ZOOM = 11;
const MAX_ZOOM = 16;

/** URL of the server's map style, or null if the server has no offline map yet. */
export async function mapStyleUrl(refresh = false): Promise<string | null> {
  const base = await apiUrl();
  if (!base) return null;
  const url = `${base}/api/v1/map/style.json`;
  const cached = await getMeta("map_style_available");
  if (!refresh && cached !== null) return cached === "1" ? url : null;
  try {
    const r = await fetch(url, { method: "GET" });
    await setMeta("map_style_available", r.ok ? "1" : "0");
    return r.ok ? url : null;
  } catch {
    return cached === "1" ? url : null; // offline: trust what we knew
  }
}

export async function areaBounds(): Promise<[number, number, number, number] | null> {
  const pts = (await listAssignments()).map((a) => a.visit_point);
  if (!pts.length) return null;
  const lons = pts.map((p) => p.lon);
  const lats = pts.map((p) => p.lat);
  return [Math.min(...lons) - MARGIN_DEG, Math.min(...lats) - MARGIN_DEG, Math.max(...lons) + MARGIN_DEG, Math.max(...lats) + MARGIN_DEG];
}

export interface PackInfo {
  percentage: number;
  state: "inactive" | "active" | "complete";
  sizeMb: number;
}

export async function currentPack(): Promise<PackInfo | null> {
  const packs = await OfflineManager.getPacks();
  const pack = packs.find((p) => (p.metadata as { name?: string })?.name === PACK_NAME);
  if (!pack) return null;
  const st = await pack.status();
  return { percentage: Math.round(st.percentage), state: st.state, sizeMb: Math.round(st.completedResourceSize / 1e6) };
}

export async function deletePack(): Promise<void> {
  const packs = await OfflineManager.getPacks();
  for (const p of packs) if ((p.metadata as { name?: string })?.name === PACK_NAME) await OfflineManager.deletePack(p.id);
}

export async function downloadArea(onProgress: (p: PackInfo) => void, onError: (msg: string) => void): Promise<"started" | "no_style" | "no_points"> {
  const style = await mapStyleUrl(true);
  if (!style) return "no_style";
  const bounds = await areaBounds();
  if (!bounds) return "no_points";
  await deletePack();
  OfflineManager.setTileCountLimit(50_000);
  await OfflineManager.createPack(
    { mapStyle: style, bounds, minZoom: MIN_ZOOM, maxZoom: MAX_ZOOM, metadata: { name: PACK_NAME, created: new Date().toISOString() } },
    (_pack, st) => onProgress({ percentage: Math.round(st.percentage), state: st.state, sizeMb: Math.round(st.completedResourceSize / 1e6) }),
    (_pack, err) => onError(err.message),
  );
  return "started";
}
