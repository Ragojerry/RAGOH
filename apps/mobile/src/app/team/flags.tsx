/** Flags inbox for the supervisor's teams, high severity first. */
import { router, useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { Pressable, RefreshControl, ScrollView, Text, View } from "react-native";
import { Card, Chip, ErrorText, Muted, colors, styles } from "@/components/ui";
import { asOf, openFlags, type Cached, type Flag } from "@/lib/team";

const ORDER = { high: 0, medium: 1, low: 2 } as const;
type Filter = "all" | "high" | "new";

export default function FlagsInbox() {
  const [flags, setFlags] = useState<Cached<Flag[]> | null>(null);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<Filter>("all");

  const load = useCallback(async () => {
    setLoading(true);
    setFlags(await openFlags());
    setLoading(false);
  }, []);
  useFocusEffect(useCallback(() => { load(); }, [load]));

  const list = (flags?.data ?? [])
    .filter((f) => filter === "all" || (filter === "high" ? f.severity === "high" : f.status === "new"))
    .sort((a, b) => ORDER[a.severity] - ORDER[b.severity] || b.local_date.localeCompare(a.local_date));

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content} refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}>
      <View style={styles.row}>
        {(["all", "high", "new"] as Filter[]).map((f) => (
          <Pressable key={f} onPress={() => setFilter(f)} style={{ paddingHorizontal: 14, paddingVertical: 8, borderRadius: 999, backgroundColor: filter === f ? colors.primary : colors.card, borderWidth: 1, borderColor: colors.primary }}>
            <Text style={{ color: filter === f ? "#fff" : colors.primary, fontWeight: "700" }}>{f === "all" ? "All open" : f === "high" ? "High" : "Not looked at"}</Text>
          </Pressable>
        ))}
      </View>
      {flags?.stale && <Muted>Offline — saved list from {asOf(flags.fetchedAt)}. Actions need a connection.</Muted>}
      {!flags && !loading && <ErrorText>Can't load flags (no connection and nothing saved).</ErrorText>}
      {flags && list.length === 0 && <Muted>Nothing here. 🎉</Muted>}
      {list.map((f) => (
        <Pressable key={f.id} onPress={() => router.push({ pathname: "/team/flag/[id]", params: { id: f.id } })}>
          <Card style={f.severity === "high" ? { borderColor: colors.danger } : undefined}>
            <View style={[styles.row, { justifyContent: "space-between" }]}>
              <Text style={{ fontSize: 16, fontWeight: "700", color: colors.text, flex: 1 }}>{f.collector_name}</Text>
              <Chip text={f.severity} tone={f.severity === "high" ? "danger" : f.severity === "medium" ? "warn" : "neutral"} />
            </View>
            <Text style={{ fontSize: 15, color: colors.text }}>{f.title}</Text>
            <Muted>{f.local_date} · {f.rule_id} · {f.status.replace("_", " ")}{f.escalated ? " · escalated" : ""}</Muted>
          </Card>
        </Pressable>
      ))}
    </ScrollView>
  );
}
