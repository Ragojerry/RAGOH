import { router, useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useState } from "react";
import { Alert, Linking, Modal, ScrollView, Text, View } from "react-native";
import { PointsMap } from "@/components/PointsMap";
import { Button, Card, Chip, H1, Input, Label, Muted, colors, styles } from "@/components/ui";
import { formatDuration, routeTo, type WalkRoute } from "@/lib/routing";
import { bearingDeg, compassPoint, formatDistance, haversineM } from "@/lib/geo";
import { currentLocation } from "@/lib/location";
import { findDraft, getAssignment, listForms, setAssignmentStatus } from "@/lib/repo";
import { useSession } from "@/lib/session";
import { DONE_STATUSES, STATUS_LABEL, type Assignment, type AssignmentStatus, type FormInfo, type Location } from "@/lib/types";

const OUTCOMES: { status: AssignmentStatus; label: string; ask: string }[] = [
  { status: "not_found", label: "Not found", ask: "What happened? (e.g. house demolished, wrong location)" },
  { status: "refused", label: "Refused", ask: "Reason for refusal" },
  { status: "skipped", label: "Skip for now", ask: "Why are you skipping this point?" },
];

export default function PointDetail() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const { runSync } = useSession();
  const [a, setA] = useState<Assignment | null>(null);
  const [forms, setForms] = useState<FormInfo[]>([]);
  const [drafts, setDrafts] = useState<Record<string, boolean>>({});
  const [me, setMe] = useState<Location | null>(null);
  const [outcome, setOutcome] = useState<(typeof OUTCOMES)[number] | null>(null);
  const [reason, setReason] = useState("");
  const [route, setRoute] = useState<WalkRoute | null>(null);
  const [routing, setRouting] = useState(false);

  useFocusEffect(
    useCallback(() => {
      (async () => {
        const assignment = await getAssignment(id);
        setA(assignment);
        if (!assignment) return;
        // A back-check task uses the back-check form; normal visits use survey forms.
        const kind = assignment.kind === "backcheck" ? "backcheck" : "survey";
        const fs = (await listForms(kind)).filter((f) => f.campaign_id === assignment.visit_point.campaign_id);
        setForms(fs);
        const d: Record<string, boolean> = {};
        for (const f of fs) d[f.version_id] = !!(await findDraft(f.version_id, assignment.id));
        setDrafts(d);
        setMe(await currentLocation(8000));
      })();
    }, [id]),
  );

  if (!a) return <View style={styles.screen}><Muted>Loading…</Muted></View>;
  const p = a.visit_point;
  const dist = me ? haversineM(me, p) : undefined;
  const inside = dist !== undefined && dist <= p.geofence_m;

  const showRoute = async () => {
    setRouting(true);
    try {
      const loc = me ?? (await currentLocation(15_000));
      if (!loc) {
        Alert.alert("Location needed", "Turn on GPS to get a walking route.");
        return;
      }
      setMe(loc);
      setRoute(await routeTo(loc, a));
    } finally {
      setRouting(false);
    }
  };

  const saveOutcome = async () => {
    if (!outcome) return;
    if (!reason.trim()) return Alert.alert("Reason needed", outcome.ask);
    await setAssignmentStatus(a.id, outcome.status, reason.trim());
    setOutcome(null);
    setReason("");
    runSync();
    router.back();
  };

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <H1>{p.name}</H1>
      <View style={styles.row}>
        <Chip text={STATUS_LABEL[a.status]} tone={DONE_STATUSES.includes(a.status) ? "ok" : "neutral"} />
        {a.kind === "backcheck" && <Chip text="Back-check" tone="warn" />}
        <Muted>{p.external_id} · {p.type}</Muted>
      </View>
      {a.note && (
        <Card style={{ borderColor: colors.warning, borderWidth: 2 }}>
          <Label>From your supervisor</Label>
          <Text style={{ fontSize: 16, color: colors.text }}>{a.note}</Text>
        </Card>
      )}

      <Card>
        <Label>Where</Label>
        {dist !== undefined ? (
          <Text style={{ fontSize: 20, fontWeight: "800", color: inside ? colors.ok : colors.text }}>
            {inside ? "You are at this point" : `${formatDistance(dist)} ${compassPoint(bearingDeg(me!, p))}`}
          </Text>
        ) : (
          <Muted>Getting your location…</Muted>
        )}
        <Muted>{p.lat.toFixed(5)}, {p.lon.toFixed(5)} · arrival radius {p.geofence_m} m</Muted>
        <View style={styles.row}>
          <Button title="Walking route" onPress={showRoute} loading={routing} style={{ flex: 1 }} />
          <Button title="Open in Maps" variant="secondary" onPress={() => Linking.openURL(`geo:${p.lat},${p.lon}?q=${p.lat},${p.lon}(${encodeURIComponent(p.name)})`)} />
        </View>
      </Card>

      {route && (
        <Card style={{ padding: 0, overflow: "hidden" }}>
          <View style={{ height: 260 }}>
            <PointsMap assignments={[a]} me={me} route={route.geometry} zoom={15} />
          </View>
          <View style={{ padding: 16, gap: 6 }}>
            <Text style={{ fontSize: 18, fontWeight: "800", color: colors.text }}>
              {formatDistance(route.distanceM)} · about {formatDuration(route.durationS)} on foot
            </Text>
            <Muted>
              {route.source === "osrm" ? "Route along roads and paths." : route.source === "offline" ? "Straight line — you are offline. Follow the direction shown." : "Straight line — road routing isn't set up on the server yet."}
            </Muted>
            {route.steps.map((st, i) => (
              <Muted key={i}>{i + 1}. {st.instruction}{st.distance_m ? ` (${formatDistance(st.distance_m)})` : ""}</Muted>
            ))}
          </View>
        </Card>
      )}

      {Object.keys(p.attributes ?? {}).length > 0 && (
        <Card>
          <Label>Details</Label>
          {Object.entries(p.attributes).map(([k, v]) => (
            <Muted key={k}>{k.replace(/_/g, " ")}: {String(v)}</Muted>
          ))}
        </Card>
      )}

      <Card>
        <Label>Visit</Label>
        {forms.length === 0 && <Muted>No survey form available yet. Sync to download forms.</Muted>}
        {forms.map((f) => (
          <Button
            key={f.version_id}
            title={drafts[f.version_id] ? `Continue: ${f.name}` : `Start: ${f.name}`}
            onPress={() => router.push({ pathname: "/form/[versionId]", params: { versionId: f.version_id, assignmentId: a.id } })}
          />
        ))}
        <View style={[styles.row, { flexWrap: "wrap" }]}>
          {OUTCOMES.map((o) => (
            <Button key={o.status} title={o.label} variant="secondary" onPress={() => setOutcome(o)} style={{ flexGrow: 1 }} />
          ))}
        </View>
      </Card>

      <Modal visible={!!outcome} transparent animationType="slide" onRequestClose={() => setOutcome(null)}>
        <View style={{ flex: 1, justifyContent: "flex-end", backgroundColor: "rgba(0,0,0,0.4)" }}>
          <View style={[styles.card, { borderRadius: 0, padding: 20 }]}>
            <Label>{outcome?.label}</Label>
            <Muted>{outcome?.ask}</Muted>
            <Input value={reason} onChangeText={setReason} multiline style={{ minHeight: 90, textAlignVertical: "top" }} autoFocus />
            <Button title="Save" onPress={saveOutcome} />
            <Button title="Cancel" variant="secondary" onPress={() => setOutcome(null)} />
          </View>
        </View>
      </Modal>
    </ScrollView>
  );
}
