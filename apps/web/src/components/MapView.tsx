/**
 * MapLibre map. Uses the server's self-hosted FCT basemap when it has been prepared,
 * otherwise OpenStreetMap raster tiles. `onReady` runs once the style is loaded, so
 * pages can add their own sources and layers.
 */
import * as maplibregl from "maplibre-gl";
import type { Map as MlMap, StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre runs tile parsing in a Web Worker shipped as its own file; let Vite serve it.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?url";
import { useEffect, useRef } from "react";

maplibregl.setWorkerUrl(workerUrl);

const OSM: StyleSpecification = {
  version: 8,
  glyphs: "https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf",
  sources: {
    osm: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, attribution: "© OpenStreetMap contributors", maxzoom: 19 },
  },
  layers: [{ id: "osm", type: "raster", source: "osm", paint: { "raster-saturation": -0.6, "raster-opacity": 0.85 } }],
};

let styleChoice: Promise<string | StyleSpecification> | null = null;
function pickStyle(): Promise<string | StyleSpecification> {
  styleChoice ??= fetch("/api/v1/map/style.json").then((r) => (r.ok ? "/api/v1/map/style.json" : OSM)).catch(() => OSM);
  return styleChoice;
}

/** Fonts that exist in the chosen style's glyph server (for map labels). */
export async function labelFont(): Promise<string[]> {
  return (await pickStyle()) === OSM ? ["Open Sans Semibold"] : ["Noto Sans Medium"];
}

export function MapView({ onReady, className = "map" }: { onReady: (map: MlMap) => void | (() => void); className?: string }) {
  const el = useRef<HTMLDivElement>(null);
  const ready = useRef(onReady);
  ready.current = onReady;

  useEffect(() => {
    let map: MlMap | null = null;
    let cleanup: void | (() => void);
    let cancelled = false;
    pickStyle().then((style) => {
      if (cancelled || !el.current) return;
      const m = new maplibregl.Map({ container: el.current, style, center: [7.49, 9.06], zoom: 10, attributionControl: { compact: true } });
      map = m;
      m.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
      m.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-right");
      m.on("load", () => {
        cleanup = ready.current(m);
      });
    });
    return () => {
      cancelled = true;
      if (typeof cleanup === "function") cleanup();
      map?.remove();
    };
  }, []);

  return <div ref={el} className={className} />;
}

/** Fit the map to [lon, lat] points (no-op when empty). */
export function fitTo(map: MlMap, coords: [number, number][], maxZoom = 16): void {
  if (!coords.length) return;
  const b = new maplibregl.LngLatBounds(coords[0], coords[0]);
  for (const c of coords) b.extend(c);
  map.fitBounds(b, { padding: 60, maxZoom, duration: 0 });
}

/** Add or replace a GeoJSON source's data. */
export function setData(map: MlMap, id: string, data: GeoJSON.FeatureCollection | GeoJSON.Feature): void {
  const src = map.getSource(id) as maplibregl.GeoJSONSource | undefined;
  if (src) src.setData(data);
  else map.addSource(id, { type: "geojson", data });
}

export const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
