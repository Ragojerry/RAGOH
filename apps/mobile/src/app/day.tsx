import { useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { FlatList, Text, View } from "react-native";
import { Chip, ErrorText, Muted, colors, styles } from "@/components/ui";
import { submissionLog, type LogEntry } from "@/lib/repo";
import { useSession } from "@/lib/session";

export default function MyDay() {
  const { sync } = useSession();
  const [items, setItems] = useState<LogEntry[]>([]);
  const [range, setRange] = useState<"today" | "week">("today");

  useFocusEffect(
    useCallback(() => {
      const since = new Date();
      since.setHours(0, 0, 0, 0);
      if (range === "week") since.setDate(since.getDate() - 7);
      submissionLog(since.toISOString()).then(setItems);
    }, [range, sync.lastOkAt, sync.pending]),
  );

  return (
    <FlatList
      style={styles.screen}
      contentContainerStyle={styles.content}
      data={items}
      keyExtractor={(i) => i.id}
      ListHeaderComponent={
        <View style={styles.row}>
          <Text onPress={() => setRange("today")} style={{ fontWeight: range === "today" ? "800" : "400", fontSize: 16, color: colors.text }}>Today</Text>
          <Text> · </Text>
          <Text onPress={() => setRange("week")} style={{ fontWeight: range === "week" ? "800" : "400", fontSize: 16, color: colors.text }}>Last 7 days</Text>
          <Muted>  ({items.length} surveys)</Muted>
        </View>
      }
      ListEmptyComponent={<Muted>No surveys yet.</Muted>}
      renderItem={({ item }) => (
        <View style={styles.card}>
          <View style={[styles.row, { justifyContent: "space-between" }]}>
            <Text style={{ fontSize: 16, fontWeight: "700", color: colors.text, flex: 1 }}>{item.point_name ?? item.form_name}</Text>
            <Chip
              text={item.state === "synced" ? "Sent" : item.state === "rejected" ? "Refused" : "Waiting"}
              tone={item.state === "synced" ? "ok" : item.state === "rejected" ? "danger" : "warn"}
            />
          </View>
          <Muted>{item.form_name} · {new Date(item.ended_at).toLocaleTimeString()}</Muted>
          {item.state === "rejected" && <ErrorText>Server said: {item.error}. Tell your supervisor.</ErrorText>}
        </View>
      )}
    />
  );
}
