/** Move a collector's unstarted points to someone else in the same team. */
import { router, useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useState } from "react";
import { Alert, Pressable, ScrollView, Text, View } from "react-native";
import { Button, Card, ErrorText, Input, Label, Muted, colors, styles } from "@/components/ui";
import { roster, reassign, teamAssignments, type TeamAssignment, type TeamMember } from "@/lib/team";

export default function Reassign() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const [points, setPoints] = useState<TeamAssignment[] | null>(null);
  const [mates, setMates] = useState<TeamMember[]>([]);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [to, setTo] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    const all = await roster();
    const me = all.find((m) => m.id === id);
    setMates(all.filter((m) => m.id !== id && (!me || m.team_id === me.team_id)));
    try {
      setPoints(await teamAssignments(id));
      setError(null);
    } catch {
      setError("Moving points needs a connection.");
    }
  }, [id]);
  useFocusEffect(useCallback(() => { load(); }, [load]));

  const toggle = (aid: string) =>
    setPicked((cur) => {
      const next = new Set(cur);
      if (next.has(aid)) next.delete(aid);
      else next.add(aid);
      return next;
    });

  const submit = async () => {
    if (!to || !picked.size) return;
    setBusy(true);
    try {
      const r = await reassign([...picked], to, reason.trim());
      const name = mates.find((m) => m.id === to)?.full_name ?? "the collector";
      Alert.alert(
        "Points moved",
        `${r.moved.length} point(s) moved to ${name}.${r.skipped.length ? ` ${r.skipped.length} could not be moved (already started or done).` : ""} They get them at their next sync.`,
      );
      router.back();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <ErrorText>{error}</ErrorText>
      <Card>
        <View style={[styles.row, { justifyContent: "space-between" }]}>
          <Label>Points not yet visited</Label>
          {points && points.length > 0 && (
            <Pressable onPress={() => setPicked(picked.size === points.length ? new Set() : new Set(points.map((p) => p.id)))}>
              <Text style={{ color: colors.primary, fontWeight: "700" }}>{picked.size === points.length ? "None" : "All"}</Text>
            </Pressable>
          )}
        </View>
        {points?.length === 0 && <Muted>No open points.</Muted>}
        {points?.map((p) => (
          <Pressable key={p.id} onPress={() => toggle(p.id)} style={[styles.row, { paddingVertical: 8 }]}>
            <View style={{ width: 24, height: 24, borderRadius: 6, borderWidth: 2, borderColor: colors.primary, backgroundColor: picked.has(p.id) ? colors.primary : "#fff" }} />
            <Text style={{ fontSize: 16, color: colors.text, flex: 1 }}>
              {p.visit_point.name} <Text style={{ color: colors.muted }}>({p.visit_point.external_id}){p.kind === "backcheck" ? " · back-check" : ""}</Text>
            </Text>
          </Pressable>
        ))}
      </Card>
      <Card>
        <Label>Give them to</Label>
        {mates.length === 0 && <Muted>No one else in this team.</Muted>}
        {mates.map((m) => (
          <Pressable key={m.id} onPress={() => setTo(m.id)} style={[styles.row, { paddingVertical: 8 }]}>
            <View style={{ width: 22, height: 22, borderRadius: 11, borderWidth: 2, borderColor: colors.primary, backgroundColor: to === m.id ? colors.primary : "#fff" }} />
            <Text style={{ fontSize: 16, color: colors.text }}>{m.full_name}</Text>
          </Pressable>
        ))}
        <Input value={reason} onChangeText={setReason} placeholder="Reason (optional), e.g. sick, area too large" />
      </Card>
      <Button title={`Move ${picked.size} point(s)`} disabled={!to || !picked.size} loading={busy} onPress={submit} />
    </ScrollView>
  );
}
