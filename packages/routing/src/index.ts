/**
 * Offline route ordering for the phone: nearest neighbour + 2-opt on straight-line
 * distances. Mirrors backend/app/services/routing.py (heuristic_plan) so online and
 * offline plans behave the same way when the road engines aren't available.
 */

export interface LatLon {
  lat: number;
  lon: number;
}

export interface PlannedStop<T> {
  item: T;
  order: number;
  legDistanceM: number;
  legDurationS: number;
  arrivalOffsetS: number;
}

export interface Plan<T> {
  source: "heuristic";
  stops: PlannedStop<T>[];
  distanceM: number;
  durationS: number;
  geometry: [number, number][]; // [lon, lat]
}

export function haversineM(a: LatLon, b: LatLon): number {
  const R = 6_371_000;
  const rad = (d: number) => (d * Math.PI) / 180;
  const dLat = rad(b.lat - a.lat);
  const dLon = rad(b.lon - a.lon);
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

function tourLength(order: number[], d: number[][], end: number | null): number {
  const path = [0, ...order, ...(end === null ? [] : [end])];
  let total = 0;
  for (let i = 1; i < path.length; i++) total += d[path[i - 1]!]![path[i]!]!;
  return total;
}

/** Visit order as indices into `points`. */
export function orderPoints(start: LatLon, points: LatLon[], returnToStart = false): number[] {
  if (!points.length) return [];
  const nodes = [start, ...points];
  const d = nodes.map((a) => nodes.map((b) => haversineM(a, b)));
  const remaining = new Set(nodes.map((_, i) => i).slice(1));
  let order: number[] = [];
  let cur = 0;
  while (remaining.size) {
    let best = -1;
    for (const j of remaining) if (best < 0 || d[cur]![j]! < d[cur]![best]!) best = j;
    order.push(best);
    remaining.delete(best);
    cur = best;
  }
  const end = returnToStart ? 0 : null;
  let improved = true;
  while (improved) {
    improved = false;
    for (let i = 0; i < order.length - 1; i++) {
      for (let k = i + 1; k < order.length; k++) {
        const candidate = [...order.slice(0, i), ...order.slice(i, k + 1).reverse(), ...order.slice(k + 1)];
        if (tourLength(candidate, d, end) < tourLength(order, d, end) - 1e-6) {
          order = candidate;
          improved = true;
        }
      }
    }
  }
  return order.map((i) => i - 1);
}

export function planOffline<T extends LatLon>(
  start: LatLon,
  items: T[],
  opts: { speedKmh?: number; serviceS?: number; returnToStart?: boolean } = {},
): Plan<T> {
  const mps = ((opts.speedKmh ?? 4) * 1000) / 3600;
  const service = opts.serviceS ?? 600;
  const order = orderPoints(start, items, opts.returnToStart ?? false);
  const stops: PlannedStop<T>[] = [];
  let t = 0;
  let total = 0;
  let prev: LatLon = start;
  order.forEach((idx, n) => {
    const item = items[idx]!;
    const leg = haversineM(prev, item);
    const legS = Math.round(leg / mps);
    t += legS;
    stops.push({ item, order: n + 1, legDistanceM: Math.round(leg), legDurationS: legS, arrivalOffsetS: t });
    t += service;
    total += leg;
    prev = item;
  });
  const geometry: [number, number][] = [[start.lon, start.lat], ...stops.map((s): [number, number] => [s.item.lon, s.item.lat])];
  if (opts.returnToStart && stops.length) {
    const back = haversineM(prev, start);
    total += back;
    t += Math.round(back / mps);
    geometry.push([start.lon, start.lat]);
  }
  return { source: "heuristic", stops, distanceM: Math.round(total), durationS: t, geometry };
}
