/** One flag: what was detected and the follow-up actions (each is recorded on the flag). */
import { router, useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useState } from "react";
import { Alert, Linking, ScrollView, Text, View } from "react-native";
import { Button, Card, Chip, ErrorText, Input, Label, Muted, colors, styles } from "@/components/ui";
import { listForms } from "@/lib/repo";
import { actOnFlag, getFlag, roster, teamLive, type Flag, type FlagActionName } from "@/lib/team";

const ACTION_TEXT: Record<string, string> = {
  acknowledge: "Acknowledged", note: "Note", call: "Called", message: "Messaged",
  request_explanation: "Asked for an explanation", reply: "Collector replied", revisit: "Revisit requested",
  backcheck: "Back-check assigned", escalate: "Escalated", resolve: "Resolved", dismiss: "Dismissed", reopen: "Reopened",
};

function Detail({ k, v }: { k: string; v: unknown }) {
  const value = typeof v === "number" ? (Number.isInteger(v) ? String(v) : v.toFixed(1)) : typeof v === "object" ? JSON.stringify(v) : String(v);
  return <Muted>{k.replace(/_/g, " ")}: {value}</Muted>;
}

export default function FlagDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const [flag, setFlag] = useState<Flag | null>(null);
  const [phone, setPhone] = useState<string | null>(null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const f = await getFlag(id);
      setFlag(f);
      setError(null);
      const live = await teamLive();
      setPhone(live?.data.collectors.find((c) => c.id === f.collector_id)?.phone ?? (await roster()).find((m) => m.id === f.collector_id)?.phone ?? null);
    } catch (e) {
      setError(e instanceof Error ? `Can't load the flag: ${e.message}` : "Can't load the flag");
    }
  }, [id]);
  useFocusEffect(useCallback(() => { load(); }, [load]));

  const act = async (action: FlagActionName, extra: { phone?: string } = {}) => {
    setBusy(action);
    setError(null);
    try {
      setFlag(await actOnFlag(id, { action, comment: comment.trim() || undefined, ...extra }));
      setComment("");
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(null);
    }
  };

  const call = async () => {
    if (!phone) return;
    await act("call", { phone });
    Linking.openURL(`tel:${phone}`);
  };

  const spotCheck = async () => {
    const forms = await listForms("supervision");
    const member = (await roster()).find((m) => m.id === flag?.collector_id);
    if (!forms[0]) return Alert.alert("No checklist", "Sync to download the supervision checklist.");
    router.push({ pathname: "/form/[versionId]", params: { versionId: forms[0].version_id, prefill: JSON.stringify({ collector_username: member?.username ?? "" }) } });
  };

  if (!flag) return <View style={[styles.screen, styles.content]}>{error ? <ErrorText>{error}</ErrorText> : <Muted>Loading…</Muted>}</View>;
  const closed = flag.status === "resolved" || flag.status === "dismissed";
  const needText = (a: string) => ["note", "message", "request_explanation", "resolve", "dismiss", "revisit"].includes(a) && !comment.trim();

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Card style={flag.severity === "high" ? { borderColor: colors.danger } : undefined}>
        <View style={[styles.row, { justifyContent: "space-between" }]}>
          <Label>{flag.collector_name}</Label>
          <Chip text={flag.severity} tone={flag.severity === "high" ? "danger" : flag.severity === "medium" ? "warn" : "neutral"} />
        </View>
        <Text style={{ fontSize: 17, color: colors.text }}>{flag.title}</Text>
        <Muted>{flag.local_date} · rule {flag.rule_id} · {flag.status.replace("_", " ")}{flag.escalated ? " · escalated" : ""}</Muted>
        {Object.entries(flag.details).slice(0, 8).map(([k, v]) => <Detail key={k} k={k} v={v} />)}
      </Card>

      <View style={styles.row}>
        <Button title="Go to collector" variant="secondary" onPress={() => router.push({ pathname: "/team/go/[id]", params: { id: flag.collector_id } })} style={{ flex: 1 }} />
        <Button title="Their day" variant="secondary" onPress={() => router.push({ pathname: "/team/replay/[id]", params: { id: flag.collector_id } })} style={{ flex: 1 }} />
      </View>

      <Card>
        <Label>Follow up</Label>
        <Input value={comment} onChangeText={setComment} placeholder="Comment (needed for messages, notes, resolving)" multiline />
        <ErrorText>{error}</ErrorText>
        {closed ? (
          <Button title="Reopen" variant="secondary" loading={busy === "reopen"} onPress={() => act("reopen")} />
        ) : (
          <>
            {flag.status === "new" && <Button title="Acknowledge" loading={busy === "acknowledge"} onPress={() => act("acknowledge")} />}
            <View style={styles.row}>
              <Button title="Call" disabled={!phone} loading={busy === "call"} onPress={call} style={{ flex: 1 }} />
              <Button title="Spot check" variant="secondary" onPress={spotCheck} style={{ flex: 1 }} />
            </View>
            <Button title="Ask collector to explain" variant="secondary" disabled={needText("request_explanation")} loading={busy === "request_explanation"} onPress={() => act("request_explanation")} />
            <View style={styles.row}>
              <Button title="Send message" variant="secondary" disabled={needText("message")} loading={busy === "message"} onPress={() => act("message")} style={{ flex: 1 }} />
              <Button title="Add note" variant="secondary" disabled={needText("note")} loading={busy === "note"} onPress={() => act("note")} style={{ flex: 1 }} />
            </View>
            {flag.subject_type === "assignment" || flag.subject_type === "submission" ? (
              <View style={styles.row}>
                <Button title="Request revisit" variant="secondary" disabled={needText("revisit")} loading={busy === "revisit"} onPress={() => act("revisit")} style={{ flex: 1 }} />
                <Button title="I'll back-check" variant="secondary" loading={busy === "backcheck"} onPress={() => act("backcheck")} style={{ flex: 1 }} />
              </View>
            ) : null}
            {!flag.escalated && <Button title="Escalate to manager" variant="secondary" loading={busy === "escalate"} onPress={() => act("escalate")} />}
            <View style={styles.row}>
              <Button title="Resolve" disabled={needText("resolve")} loading={busy === "resolve"} onPress={() => act("resolve")} style={{ flex: 1 }} />
              <Button title="Dismiss" variant="danger" disabled={needText("dismiss")} loading={busy === "dismiss"} onPress={() => act("dismiss")} style={{ flex: 1 }} />
            </View>
          </>
        )}
      </Card>

      {flag.actions && flag.actions.length > 0 && (
        <Card>
          <Label>History</Label>
          {flag.actions.map((a) => (
            <View key={a.id} style={{ paddingVertical: 4 }}>
              <Text style={{ color: colors.text, fontWeight: "600" }}>
                {ACTION_TEXT[a.action] ?? a.action} · {a.actor_name ?? ""} · {new Date(a.created_at).toLocaleString()}
              </Text>
              {a.comment ? <Muted>{a.comment}</Muted> : null}
            </View>
          ))}
        </Card>
      )}
    </ScrollView>
  );
}
