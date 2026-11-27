/** Live map of the team. Positions refresh every 30 s while the screen is open. */
import { router, useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { Pressable, Text, View } from "react-native";
import { STATUS_COLOR, TeamMap } from "@/components/TeamMap";
import { Button, Chip, Muted, colors, styles } from "@/components/ui";
import { currentLocation } from "@/lib/location";
import { STATUS_TEXT, ago, asOf, statusTone, teamLive, type Cached, type LiveCollector, type TeamLive } from "@/lib/team";
import type { Location } from "@/lib/types";

export default function LiveMap() {
  const [live, setLive] = useState<Cached<TeamLive> | null>(null);
  const [me, setMe] = useState<Location | null>(null);
  const [selected, setSelected] = useState<LiveCollector | null>(null);

  const load = useCallback(async () => {
    const l = await teamLive();
    setLive(l);
    setSelected((cur) => (cur ? (l?.data.collectors.find((c) => c.id === cur.id) ?? null) : null));
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
      currentLocation(10_000).then(setMe);
      const t = setInterval(load, 30_000);
      return () => clearInterval(t);
    }, [load]),
  );

  const collectors = live?.data.collectors ?? [];
  const unplaced = collectors.filter((c) => c.lat === null);

  return (
    <View style={styles.screen}>
      <View style={{ flex: 1 }}>
        <TeamMap collectors={collectors} me={me} onSelect={setSelected} />
      </View>
      <View style={{ padding: 12, gap: 8, backgroundColor: colors.card, borderTopWidth: 1, borderColor: colors.border }}>
        <View style={[styles.row, { flexWrap: "wrap" }]}>
          {(["moving", "at_point", "idle", "offline"] as const).map((s) => (
            <View key={s} style={styles.row}>
              <View style={{ width: 10, height: 10, borderRadius: 5, backgroundColor: STATUS_COLOR[s] }} />
              <Text style={{ fontSize: 12, color: colors.muted }}>{STATUS_TEXT[s]}</Text>
            </View>
          ))}
        </View>
        {live && <Muted>{live.stale ? "Offline — last known positions " : "Positions "}as of {asOf(live.fetchedAt)}</Muted>}
        {unplaced.length > 0 && <Muted>No position yet: {unplaced.map((c) => c.full_name).join(", ")}</Muted>}
        {selected ? (
          <Pressable onPress={() => router.push({ pathname: "/team/[id]", params: { id: selected.id } })} style={{ gap: 4 }}>
            <View style={[styles.row, { justifyContent: "space-between" }]}>
              <Text style={{ fontSize: 17, fontWeight: "800", color: colors.text }}>{selected.full_name}</Text>
              <Chip text={STATUS_TEXT[selected.status]} tone={statusTone(selected.status)} />
            </View>
            <Muted>
              {selected.done_today}/{selected.assigned_today} visits · seen {ago(selected.last_seen_at)}
              {selected.accuracy_m ? ` · ±${Math.round(selected.accuracy_m)} m` : ""}
            </Muted>
            <View style={styles.row}>
              <Button title="Go to" onPress={() => router.push({ pathname: "/team/go/[id]", params: { id: selected.id } })} style={{ flex: 1 }} />
              <Button title="Details" variant="secondary" onPress={() => router.push({ pathname: "/team/[id]", params: { id: selected.id } })} style={{ flex: 1 }} />
            </View>
          </Pressable>
        ) : (
          <Muted>Tap a collector for details.</Muted>
        )}
      </View>
    </View>
  );
}
