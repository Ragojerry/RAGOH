/**
 * Map of assigned points with an optional route line.
 * Uses the server's self-hosted FCT map (downloadable for offline use) when available,
 * otherwise OpenStreetMap's online tiles.
 */
import { Camera, GeoJSONSource, Layer, Map, Marker } from "@maplibre/maplibre-react-native";
import type { StyleSpecification } from "@maplibre/maplibre-react-native";
import { useEffect, useState } from "react";
import { Text, View } from "react-native";
import { mapStyleUrl } from "@/lib/offlineMap";
import type { Assignment, Location } from "@/lib/types";
import { DONE_STATUSES } from "@/lib/types";
import { colors } from "./ui";

const OSM_STYLE: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
      maxzoom: 19,
    },
  },
  layers: [
    { id: "bg", type: "background", paint: { "background-color": "#EEF2EF" } },
    { id: "osm", type: "raster", source: "osm" },
  ],
};

export function PointsMap({
  assignments, me, onSelect, route, order, zoom = 14,
}: {
  assignments: Assignment[];
  me: Location | null;
  onSelect?: (a: Assignment) => void;
  /** Route line as [lon, lat] pairs. */
  route?: [number, number][] | null;
  /** assignment id → visit order number shown on the marker */
  order?: Record<string, number>;
  zoom?: number;
}) {
  const [style, setStyle] = useState<string | StyleSpecification | null>(null);
  useEffect(() => {
    mapStyleUrl().then((url) => setStyle(url ?? OSM_STYLE));
  }, []);

  const first = assignments[0]?.visit_point;
  const center: [number, number] = me ? [me.lon, me.lat] : first ? [first.lon, first.lat] : [7.49, 9.06];
  if (!style) return <View style={{ flex: 1, backgroundColor: "#EEF2EF" }} />;

  return (
    <Map style={{ flex: 1 }} mapStyle={style} attribution compass={false} logo={false}>
      <Camera initialViewState={{ center, zoom }} />
      {route && route.length >= 2 && (
        <GeoJSONSource id="route" data={{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: route } }}>
          <Layer id="route-casing" type="line" paint={{ "line-color": "#ffffff", "line-width": 7 }} layout={{ "line-cap": "round", "line-join": "round" }} />
          <Layer id="route-line" type="line" paint={{ "line-color": "#1570EF", "line-width": 4 }} layout={{ "line-cap": "round", "line-join": "round" }} />
        </GeoJSONSource>
      )}
      {assignments.map((a) => {
        const done = DONE_STATUSES.includes(a.status);
        const n = order?.[a.id];
        return (
          <Marker key={a.id} id={a.id} lngLat={[a.visit_point.lon, a.visit_point.lat]} onPress={() => onSelect?.(a)}>
            <View
              style={{
                minWidth: 24, height: 24, borderRadius: 12, borderWidth: 2, borderColor: "#fff", paddingHorizontal: 4,
                alignItems: "center", justifyContent: "center",
                backgroundColor: done ? colors.ok : a.status === "pending" ? colors.warning : colors.primary,
              }}
            >
              {n !== undefined && <Text style={{ color: "#fff", fontWeight: "800", fontSize: 12 }}>{n}</Text>}
            </View>
          </Marker>
        );
      })}
      {me && (
        <Marker id="me" lngLat={[me.lon, me.lat]}>
          <View style={{ alignItems: "center" }}>
            <View style={{ width: 18, height: 18, borderRadius: 9, backgroundColor: "#1570EF", borderWidth: 3, borderColor: "#fff" }} />
            <Text style={{ fontSize: 11, fontWeight: "700", color: "#1570EF" }}>You</Text>
          </View>
        </Marker>
      )}
    </Map>
  );
}
