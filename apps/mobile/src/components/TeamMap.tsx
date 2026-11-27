/**
 * Map for supervisor screens: collectors as labelled dots coloured by status, plus an
 * optional line (a walking route, or a collector's track for replay) and stop markers.
 */
import { Camera, GeoJSONSource, Layer, Map, Marker } from "@maplibre/maplibre-react-native";
import { Text, View } from "react-native";
import type { LiveCollector, LiveStatus } from "@/lib/team";
import type { Location } from "@/lib/types";
import { useMapStyle } from "./PointsMap";
import { colors } from "./ui";

export const STATUS_COLOR: Record<LiveStatus, string> = {
  moving: "#1570EF",
  at_point: colors.ok,
  idle: colors.warning,
  off_shift: "#667085",
  offline: colors.danger,
  never_seen: "#98A2B3",
};

export interface MapStop {
  id: string;
  lat: number;
  lon: number;
  label: string;
  color?: string;
}

export function TeamMap({
  collectors = [], me, line, lineColor = "#1570EF", stops = [], cursor, onSelect, center, zoom = 13,
}: {
  collectors?: LiveCollector[];
  me?: Location | null;
  /** [lon, lat] pairs */
  line?: [number, number][] | null;
  lineColor?: string;
  stops?: MapStop[];
  /** moving marker for route replay */
  cursor?: { lat: number; lon: number } | null;
  onSelect?: (c: LiveCollector) => void;
  center?: [number, number];
  zoom?: number;
}) {
  const style = useMapStyle();
  const placed = collectors.filter((c) => c.lat !== null && c.lon !== null);
  const c0: [number, number] =
    center ?? (placed[0] ? [placed[0].lon!, placed[0].lat!] : me ? [me.lon, me.lat] : line?.[0] ?? [7.49, 9.06]);
  if (!style) return <View style={{ flex: 1, backgroundColor: "#EEF2EF" }} />;
  return (
    <Map style={{ flex: 1 }} mapStyle={style} attribution compass={false} logo={false}>
      <Camera initialViewState={{ center: c0, zoom }} />
      {line && line.length >= 2 && (
        <GeoJSONSource id="line" data={{ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: line } }}>
          <Layer id="line-casing" type="line" paint={{ "line-color": "#ffffff", "line-width": 7 }} layout={{ "line-cap": "round", "line-join": "round" }} />
          <Layer id="line-main" type="line" paint={{ "line-color": lineColor, "line-width": 4 }} layout={{ "line-cap": "round", "line-join": "round" }} />
        </GeoJSONSource>
      )}
      {stops.map((s) => (
        <Marker key={s.id} id={`stop-${s.id}`} lngLat={[s.lon, s.lat]}>
          <View style={{ minWidth: 22, height: 22, borderRadius: 11, paddingHorizontal: 4, borderWidth: 2, borderColor: "#fff", backgroundColor: s.color ?? colors.warning, alignItems: "center", justifyContent: "center" }}>
            <Text style={{ color: "#fff", fontSize: 11, fontWeight: "800" }}>{s.label}</Text>
          </View>
        </Marker>
      ))}
      {placed.map((c) => (
        <Marker key={c.id} id={c.id} lngLat={[c.lon!, c.lat!]} onPress={() => onSelect?.(c)}>
          <View style={{ alignItems: "center" }}>
            <View style={{ width: 22, height: 22, borderRadius: 11, borderWidth: 3, borderColor: "#fff", backgroundColor: STATUS_COLOR[c.status] }} />
            <View style={{ backgroundColor: "#ffffffdd", borderRadius: 4, paddingHorizontal: 3 }}>
              <Text style={{ fontSize: 11, fontWeight: "800", color: colors.text }}>
                {c.full_name.split(" ")[0]}
                {c.open_high_flags > 0 ? " ⚠" : ""}
              </Text>
            </View>
          </View>
        </Marker>
      ))}
      {cursor && (
        <Marker id="cursor" lngLat={[cursor.lon, cursor.lat]}>
          <View style={{ width: 20, height: 20, borderRadius: 10, backgroundColor: colors.danger, borderWidth: 3, borderColor: "#fff" }} />
        </Marker>
      )}
      {me && (
        <Marker id="me" lngLat={[me.lon, me.lat]}>
          <View style={{ alignItems: "center" }}>
            <View style={{ width: 18, height: 18, borderRadius: 4, backgroundColor: colors.primary, borderWidth: 3, borderColor: "#fff" }} />
            <Text style={{ fontSize: 11, fontWeight: "700", color: colors.primary }}>You</Text>
          </View>
        </Marker>
      )}
    </Map>
  );
}
