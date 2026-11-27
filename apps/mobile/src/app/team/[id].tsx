/** One collector: where they are, how their day is going, and everything a supervisor can do. */
import { router, useFocusEffect, useLocalSearchParams, useNavigation } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { Alert, Linking, Pressable, ScrollView, Text, View } from "react-native";
import { Button, Card, Chip, ErrorText, Input, Label, Muted, colors, styles } from "@/components/ui";
import { listForms, queueMessage } from "@/lib/repo";
import { useSession } from "@/lib/session";
import { STATUS_TEXT, ago, asOf, openFlags, roster, statusTone, teamLive, type Flag, type LiveCollector } from "@/lib/team";
import type { FormInfo } from "@/lib/types";

export default function CollectorDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const navigation = useNavigation();
  const { runSync } = useSession();
  const [c, setC] = useState<LiveCollector | null>(null);
  const [fallback, setFallback] = useState<{ full_name: string; username: string; phone: string | null } | null>(null);
  const [asOfText, setAsOfText] = useState("");
  const [flags, setFlags] = useState<Flag[]>([]);
  const [checklists, setChecklists] = useState<FormInfo[]>([]);
  const [text, setText] = useState("");

  const load = useCallback(async () => {
    const live = await teamLive();
    const found = live?.data.collectors.find((x) => x.id === id) ?? null;
    setC(found);
    if (live) setAsOfText(`${live.stale ? "offline, " : ""}as of ${asOf(live.fetchedAt)}`);
    if (!found) setFallback((await roster()).find((m) => m.id === id) ?? null);
    const f = await openFlags(id);
    setFlags(f?.data ?? []);
    setChecklists(await listForms("supervision"));
  }, [id]);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const name = c?.full_name ?? fallback?.full_name ?? "Collector";
  const username = c?.username ?? fallback?.username ?? "";
  const phone = c?.phone ?? fallback?.phone ?? null;
  useEffect(() => navigation.setOptions({ title: name }), [navigation, name]);

  const send = async () => {
    if (!text.trim()) return;
    await queueMessage(id, text);
    setText("");
    runSync();
    Alert.alert("Message queued", "It will be delivered at the next sync.");
  };

  const spotCheck = (f: FormInfo) =>
    router.push({ pathname: "/form/[versionId]", params: { versionId: f.version_id, prefill: JSON.stringify({ collector_username: username }) } });

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Card>
        {c ? (
          <>
            <View style={[styles.row, { justifyContent: "space-between" }]}>
              <Label>{c.team_name}</Label>
              <Chip text={STATUS_TEXT[c.status]} tone={statusTone(c.status)} />
            </View>
            <Muted>Last seen {ago(c.last_seen_at)}{c.accuracy_m ? ` (±${Math.round(c.accuracy_m)} m)` : ""} · {asOfText}</Muted>
            {c.at_point_name && <Muted>At {c.at_point_name}</Muted>}
            <Muted>
              {c.on_shift ? `On shift since ${c.shift_started_at ? new Date(c.shift_started_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "—"}` : "Not on shift"}
              {c.battery_pct !== null ? ` · battery ${c.battery_pct}%` : ""}
            </Muted>
            <Text style={{ fontSize: 26, fontWeight: "800", color: colors.text }}>
              {c.done_today} <Text style={{ fontSize: 16, color: colors.muted }}>of {c.assigned_today} visits · {(c.distance_m / 1000).toFixed(1)} km</Text>
            </Text>
            {c.pending_sync > 0 && <Muted>{c.pending_sync} record(s) on their phone not synced yet.</Muted>}
            {c.risk_score > 0 && <Muted>Risk score today: {c.risk_score}/100</Muted>}
          </>
        ) : (
          <Muted>No live data — connect to the internet to see where {name} is.</Muted>
        )}
      </Card>

      <View style={styles.row}>
        <Button title="Call" disabled={!phone} onPress={() => phone && Linking.openURL(`tel:${phone}`)} style={{ flex: 1 }} />
        <Button title="Go to" disabled={!c?.lat} onPress={() => router.push({ pathname: "/team/go/[id]", params: { id } })} style={{ flex: 1 }} />
      </View>
      <Button title="Today's route (replay)" variant="secondary" onPress={() => router.push({ pathname: "/team/replay/[id]", params: { id } })} />
      <Button title="Move points to another collector" variant="secondary" onPress={() => router.push({ pathname: "/team/reassign/[id]", params: { id } })} />

      <Card>
        <Label>Spot check</Label>
        {checklists.length ? (
          checklists.map((f) => <Button key={f.version_id} title={f.name} variant="secondary" onPress={() => spotCheck(f)} />)
        ) : (
          <Muted>No supervision checklist downloaded yet — sync first.</Muted>
        )}
      </Card>

      <Card>
        <Label>Open flags ({flags.length})</Label>
        {flags.length === 0 && <Muted>None.</Muted>}
        {flags.map((f) => (
          <Pressable key={f.id} onPress={() => router.push({ pathname: "/team/flag/[id]", params: { id: f.id } })} style={{ paddingVertical: 6 }}>
            <View style={[styles.row, { justifyContent: "space-between" }]}>
              <Text style={{ flex: 1, fontSize: 15, color: colors.text }}>{f.title}</Text>
              <Chip text={f.severity} tone={f.severity === "high" ? "danger" : f.severity === "medium" ? "warn" : "neutral"} />
            </View>
            <Muted>{f.local_date} · {f.status.replace("_", " ")}</Muted>
          </Pressable>
        ))}
      </Card>

      <Card>
        <Label>Message {name.split(" ")[0]}</Label>
        <Input value={text} onChangeText={setText} placeholder="Type a message" multiline />
        <ErrorText>{text.length > 2000 ? "Too long" : null}</ErrorText>
        <Button title="Send" onPress={send} disabled={!text.trim() || text.length > 2000} />
      </Card>
    </ScrollView>
  );
}
