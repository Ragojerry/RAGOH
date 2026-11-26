/**
 * Map of assigned points. Uses online OpenStreetMap tiles for now; Phase 4 switches to
 * offline FCT tiles. Points still show (on a blank background) when offline.
 */
import { Camera, Map, Marker } from "@maplibre/maplibre-react-native";
import type { StyleSpecification } from "@maplibre/maplibre-react-native";
import { Text, View } from "react-native";
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
  assignments, me, onSelect,
}: {
  assignments: Assignment[];
  me: Location | null;
  onSelect: (a: Assignment) => void;
}) {
  const first = assignments[0]?.visit_point;
  const center: [number, number] = me ? [me.lon, me.lat] : first ? [first.lon, first.lat] : [7.49, 9.06];
  return (
    <Map style={{ flex: 1 }} mapStyle={OSM_STYLE} attribution compass={false} logo={false}>
      <Camera initialViewState={{ center, zoom: 14 }} />
      {assignments.map((a) => {
        const done = DONE_STATUSES.includes(a.status);
        return (
          <Marker key={a.id} id={a.id} lngLat={[a.visit_point.lon, a.visit_point.lat]} onPress={() => onSelect(a)}>
            <View
              style={{
                width: 22, height: 22, borderRadius: 11, borderWidth: 2, borderColor: "#fff",
                backgroundColor: done ? colors.ok : a.status === "pending" ? colors.warning : colors.primary,
              }}
            />
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
