/** Walk to a collector: route from my position to their last known position. */
import { useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useState } from "react";
import { Linking, View } from "react-native";
import { TeamMap } from "@/components/TeamMap";
import { Button, ErrorText, Label, Muted, colors, styles } from "@/components/ui";
import { formatDistance } from "@/lib/geo";
import { currentLocation } from "@/lib/location";
import { formatDuration } from "@/lib/routing";
import { ago, routeBetween, teamLive, type LiveCollector, type Route } from "@/lib/team";
import type { Location } from "@/lib/types";

export default function GoToCollector() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const [c, setC] = useState<LiveCollector | null>(null);
  const [me, setMe] = useState<Location | null>(null);
  const [route, setRoute] = useState<Route | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    const live = await teamLive();
    const found = live?.data.collectors.find((x) => x.id === id) ?? null;
    setC(found);
    const loc = await currentLocation();
    setMe(loc);
    if (!found || found.lat === null || found.lon === null) return setError("No known position for this collector.");
    if (!loc) return setError("Turn on GPS to get directions.");
    try {
      setRoute(await routeBetween(loc, { lat: found.lat, lon: found.lon! }));
    } catch {
      setRoute(null);
      setError("No connection for a walking route — the straight line is shown. Use the map to find your way.");
    }
  }, [id]);

  useFocusEffect(useCallback(() => {
    load();
    const t = setInterval(load, 60_000); // they keep moving
    return () => clearInterval(t);
  }, [load]));

  const line: [number, number][] | null = route?.geometry ?? (me && c?.lat != null && c.lon != null ? [[me.lon, me.lat], [c.lon, c.lat]] : null);

  return (
    <View style={styles.screen}>
      <View style={{ flex: 1 }}>
        <TeamMap collectors={c ? [c] : []} me={me} line={line} lineColor={route ? "#1570EF" : colors.warning} center={me ? [me.lon, me.lat] : undefined} zoom={15} />
      </View>
      <View style={{ padding: 12, gap: 8, backgroundColor: colors.card, borderTopWidth: 1, borderColor: colors.border }}>
        <Label>{c?.full_name ?? "Collector"}</Label>
        {route && <Muted>{formatDistance(route.distance_m)} · about {formatDuration(route.duration_s)} on foot</Muted>}
        {c && <Muted>Position from {ago(c.last_seen_at)} — they may have moved.</Muted>}
        <ErrorText>{error}</ErrorText>
        <View style={styles.row}>
          <Button title="Refresh" variant="secondary" onPress={load} style={{ flex: 1 }} />
          <Button title="Call" disabled={!c?.phone} onPress={() => c?.phone && Linking.openURL(`tel:${c.phone}`)} style={{ flex: 1 }} />
        </View>
      </View>
    </View>
  );
}
