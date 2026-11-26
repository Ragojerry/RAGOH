import { router, useFocusEffect } from "expo-router";
import { useCallback, useMemo, useState } from "react";
import { FlatList, Pressable, Text, View } from "react-native";
import { PointsMap } from "@/components/PointsMap";
import { Button, Chip, Muted, colors, styles } from "@/components/ui";
import { formatDistance, haversineM } from "@/lib/geo";
import { currentLocation } from "@/lib/location";
import { listAssignments } from "@/lib/repo";
import { DONE_STATUSES, STATUS_LABEL, type Assignment, type Location } from "@/lib/types";

type Filter = "todo" | "done" | "all";

export default function Points() {
  const [items, setItems] = useState<Assignment[]>([]);
  const [me, setMe] = useState<Location | null>(null);
  const [view, setView] = useState<"list" | "map">("list");
  const [filter, setFilter] = useState<Filter>("todo");

  useFocusEffect(
    useCallback(() => {
      listAssignments().then(setItems);
      currentLocation(8000).then(setMe);
    }, []),
  );

  const shown = useMemo(() => {
    const withDist = items
      .filter((a) => filter === "all" || (filter === "done") === DONE_STATUSES.includes(a.status))
      .map((a) => ({ a, d: me ? haversineM(me, a.visit_point) : undefined }));
    // Nearest first (route optimisation arrives in Phase 4); priority breaks ties.
    withDist.sort((x, y) => (y.a.visit_point.priority - x.a.visit_point.priority) || ((x.d ?? 0) - (y.d ?? 0)));
    return withDist;
  }, [items, me, filter]);

  const open = (a: Assignment) => router.push({ pathname: "/point/[id]", params: { id: a.id } });

  return (
    <View style={styles.screen}>
      <View style={[styles.row, { padding: 12, gap: 8 }]}>
        {(["todo", "done", "all"] as Filter[]).map((f) => (
          <Button key={f} title={f === "todo" ? "To do" : f === "done" ? "Done" : "All"} variant={filter === f ? "primary" : "secondary"} onPress={() => setFilter(f)} style={{ flex: 1, minHeight: 44 }} />
        ))}
        <Button title={view === "list" ? "Map" : "List"} variant="secondary" onPress={() => setView(view === "list" ? "map" : "list")} style={{ minHeight: 44 }} />
      </View>
      {view === "map" ? (
        <PointsMap assignments={shown.map((x) => x.a)} me={me} onSelect={open} />
      ) : (
        <FlatList
          data={shown}
          keyExtractor={(x) => x.a.id}
          contentContainerStyle={{ padding: 12, gap: 8 }}
          ListEmptyComponent={<Muted>{items.length ? "Nothing here." : "No points assigned yet. Pull down on the home screen to sync."}</Muted>}
          renderItem={({ item: { a, d } }) => (
            <Pressable onPress={() => open(a)} style={({ pressed }) => [styles.card, { opacity: pressed ? 0.8 : 1 }]}>
              <View style={[styles.row, { justifyContent: "space-between" }]}>
                <Text style={{ fontSize: 17, fontWeight: "700", color: colors.text, flex: 1 }}>{a.visit_point.name}</Text>
                <Text style={{ fontSize: 15, fontWeight: "700", color: colors.primary }}>{formatDistance(d)}</Text>
              </View>
              <View style={[styles.row, { justifyContent: "space-between" }]}>
                <Muted>{a.visit_point.external_id} · {a.visit_point.type}</Muted>
                <Chip text={STATUS_LABEL[a.status]} tone={DONE_STATUSES.includes(a.status) ? "ok" : "neutral"} />
              </View>
            </Pressable>
          )}
        />
      )}
    </View>
  );
}
