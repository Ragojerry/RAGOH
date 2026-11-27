import { router, useFocusEffect } from "expo-router";
import { useCallback, useMemo, useState } from "react";
import { Alert, FlatList, Pressable, Text, View } from "react-native";
import { PointsMap } from "@/components/PointsMap";
import { Button, Chip, Muted, colors, styles } from "@/components/ui";
import { formatDistance, haversineM } from "@/lib/geo";
import { currentLocation } from "@/lib/location";
import { listAssignments } from "@/lib/repo";
import { formatDuration, planDay, planIsStale, savedPlan, type DayPlan, type PlanStop } from "@/lib/routing";
import { DONE_STATUSES, STATUS_LABEL, type Assignment, type Location } from "@/lib/types";

type Filter = "todo" | "done" | "all";

const SOURCE_LABEL: Record<DayPlan["source"], string> = {
  vroom: "Best walking order on roads",
  heuristic: "Estimated order (road routing not available yet)",
  offline: "Estimated order — offline, straight-line distances",
};

export default function Points() {
  const [items, setItems] = useState<Assignment[]>([]);
  const [me, setMe] = useState<Location | null>(null);
  const [view, setView] = useState<"list" | "map">("list");
  const [filter, setFilter] = useState<Filter>("todo");
  const [plan, setPlan] = useState<DayPlan | null>(null);
  const [planning, setPlanning] = useState(false);

  useFocusEffect(
    useCallback(() => {
      listAssignments().then(setItems);
      savedPlan().then(setPlan);
      currentLocation(8000).then(setMe);
    }, []),
  );

  const open = useMemo(() => items.filter((a) => !DONE_STATUSES.includes(a.status)), [items]);
  const stale = plan ? planIsStale(plan, open) : false;
  const planByAssignment = useMemo(() => {
    const m: Record<string, PlanStop> = {};
    plan?.stops.forEach((s) => (m[s.assignmentId] = s));
    return m;
  }, [plan]);

  const shown = useMemo(() => {
    const rows = items
      .filter((a) => filter === "all" || (filter === "done") === DONE_STATUSES.includes(a.status))
      .map((a) => ({ a, d: me ? haversineM(me, a.visit_point) : undefined, step: planByAssignment[a.id] }));
    // Planned order first (when a plan exists), otherwise nearest first; priority breaks ties.
    rows.sort((x, y) => {
      if (x.step && y.step) return x.step.order - y.step.order;
      if (x.step || y.step) return x.step ? -1 : 1;
      return (y.a.visit_point.priority - x.a.visit_point.priority) || ((x.d ?? 0) - (y.d ?? 0));
    });
    return rows;
  }, [items, me, filter, planByAssignment]);

  const makePlan = async () => {
    setPlanning(true);
    try {
      const loc = me ?? (await currentLocation(15_000));
      if (!loc) {
        Alert.alert("Location needed", "Turn on GPS to plan your route from where you are.");
        return;
      }
      setMe(loc);
      if (!open.length) {
        Alert.alert("Nothing to plan", "All your visits are done.");
        return;
      }
      setPlan(await planDay(loc));
    } finally {
      setPlanning(false);
    }
  };

  const openPoint = (a: Assignment) => router.push({ pathname: "/point/[id]", params: { id: a.id } });
  const orderMap = Object.fromEntries(Object.values(planByAssignment).map((s) => [s.assignmentId, s.order]));

  return (
    <View style={styles.screen}>
      <View style={[styles.row, { padding: 12, paddingBottom: 6, gap: 8 }]}>
        {(["todo", "done", "all"] as Filter[]).map((f) => (
          <Button key={f} title={f === "todo" ? "To do" : f === "done" ? "Done" : "All"} variant={filter === f ? "primary" : "secondary"} onPress={() => setFilter(f)} style={{ flex: 1, minHeight: 44 }} />
        ))}
        <Button title={view === "list" ? "Map" : "List"} variant="secondary" onPress={() => setView(view === "list" ? "map" : "list")} style={{ minHeight: 44 }} />
      </View>
      <View style={{ paddingHorizontal: 12, paddingBottom: 6, gap: 4 }}>
        <Button title={plan && !stale ? "Re-plan my route" : "Plan my route"} variant={stale || !plan ? "primary" : "secondary"} onPress={makePlan} loading={planning} style={{ minHeight: 44 }} />
        {plan && (
          <Muted>
            {SOURCE_LABEL[plan.source]} · {plan.stops.length} stops · {formatDistance(plan.distanceM)} · about {formatDuration(plan.durationS)} incl. visits
            {stale ? " · out of date, re-plan" : ""}
          </Muted>
        )}
      </View>
      {view === "map" ? (
        <PointsMap assignments={shown.map((x) => x.a)} me={me} onSelect={openPoint} route={plan && !stale ? plan.geometry : null} order={orderMap} />
      ) : (
        <FlatList
          data={shown}
          keyExtractor={(x) => x.a.id}
          contentContainerStyle={{ padding: 12, gap: 8 }}
          ListEmptyComponent={<Muted>{items.length ? "Nothing here." : "No points assigned yet. Pull down on the home screen to sync."}</Muted>}
          renderItem={({ item: { a, d, step } }) => (
            <Pressable onPress={() => openPoint(a)} style={({ pressed }) => [styles.card, { opacity: pressed ? 0.8 : 1 }]}>
              <View style={[styles.row, { justifyContent: "space-between" }]}>
                <Text style={{ fontSize: 17, fontWeight: "700", color: colors.text, flex: 1 }}>
                  {step && !DONE_STATUSES.includes(a.status) ? `${step.order}. ` : ""}
                  {a.visit_point.name}
                </Text>
                <Text style={{ fontSize: 15, fontWeight: "700", color: colors.primary }}>{formatDistance(d)}</Text>
              </View>
              <View style={[styles.row, { justifyContent: "space-between" }]}>
                <Muted>
                  {a.visit_point.external_id}
                  {step && !DONE_STATUSES.includes(a.status) ? ` · walk ${formatDistance(step.legDistanceM)}, ${formatDuration(step.legDurationS)}` : ` · ${a.visit_point.type}`}
                </Muted>
                <Chip text={STATUS_LABEL[a.status]} tone={DONE_STATUSES.includes(a.status) ? "ok" : "neutral"} />
              </View>
            </Pressable>
          )}
        />
      )}
    </View>
  );
}
