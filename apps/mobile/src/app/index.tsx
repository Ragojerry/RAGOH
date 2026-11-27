import { router, useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { Alert, RefreshControl, ScrollView, Text, View } from "react-native";
import { Button, Card, Chip, ErrorText, H1, Label, Muted, colors, styles } from "@/components/ui";
import { currentLocation } from "@/lib/location";
import { NOTICE_TEXT, hasConsented, recordConsent } from "@/lib/consent";
import { getMeta, setMeta } from "@/lib/db";
import { activeShift, listAssignments, listForms } from "@/lib/repo";
import { beginShift, finishShift, openBatterySettings, trackingStats } from "@/lib/tracking";
import { useSession } from "@/lib/session";
import { DONE_STATUSES, type FormInfo, type Shift } from "@/lib/types";

function timeAgo(iso: string | null): string {
  if (!iso) return "never";
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60_000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const h = Math.round(mins / 60);
  return h < 24 ? `${h} h ago` : new Date(iso).toLocaleDateString();
}

export default function Home() {
  const { user, sync, runSync } = useSession();
  const [shift, setShift] = useState<Shift | null>(null);
  const [counts, setCounts] = useState({ total: 0, done: 0 });
  const [otherForms, setOtherForms] = useState<FormInfo[]>([]);
  const [busy, setBusy] = useState(false);
  const [tracking, setTracking] = useState<{ running: boolean; lastFixAt: string | null; pointsToday: number } | null>(null);

  const load = useCallback(async () => {
    setShift(await activeShift());
    setTracking(await trackingStats());
    const a = await listAssignments();
    setCounts({ total: a.length, done: a.filter((x) => DONE_STATUSES.includes(x.status)).length });
    // Supervisors fill checklists that aren't tied to a visit point.
    setOtherForms(user?.role === "collector" ? [] : await listForms("supervision"));
  }, [user]);

  useFocusEffect(useCallback(() => { load(); }, [load, sync.lastOkAt]));

  const ask = (title: string, message: string, ok: string) =>
    new Promise<boolean>((resolve) =>
      Alert.alert(title, message, [
        { text: "Cancel", style: "cancel", onPress: () => resolve(false) },
        { text: ok, onPress: () => resolve(true) },
      ]),
    );

  const toggleShift = async () => {
    if (!shift) {
      if (!(await hasConsented())) {
        if (!(await ask("Location while on shift", NOTICE_TEXT, "I understand"))) return;
        await recordConsent();
      }
      if (!(await getMeta("battery_tip_shown"))) {
        await setMeta("battery_tip_shown", "1");
        const open = await ask(
          "Keep tracking on",
          "Some phones stop apps in the background to save battery. Find RAGOH in the next screen and choose \"Don't optimise\" / \"Allow\". Then come back and start your shift.",
          "Open settings",
        );
        if (open) {
          await openBatterySettings();
          return;
        }
      }
    }
    setBusy(true);
    try {
      const loc = await currentLocation();
      if (!loc) {
        Alert.alert("Location needed", "Turn on location (GPS) and allow RAGOH to use it, then try again.");
        return;
      }
      if (shift) {
        await finishShift(shift.id, loc);
      } else {
        const { tracking: t } = await beginShift(loc);
        if (t === "no_background_permission") {
          Alert.alert(
            "Allow location all the time",
            "Your shift has started, but tracking needs location access \"Allow all the time\". Open RAGOH's app settings → Permissions → Location and choose it.",
          );
        } else if (t === "gps_off") {
          Alert.alert("GPS is off", "Turn on Location in your phone's quick settings.");
        }
      }
      await load();
      runSync();
    } finally {
      setBusy(false);
    }
  };

  const confirmEnd = () =>
    Alert.alert("End shift?", "Your work stays on the phone and will be sent when you have internet.", [
      { text: "Cancel", style: "cancel" },
      { text: "End shift", style: "destructive", onPress: toggleShift },
    ]);

  const syncTone = sync.error ? "danger" : sync.pending > 0 ? "warn" : "ok";

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content} refreshControl={<RefreshControl refreshing={sync.running} onRefresh={() => { runSync(); }} />}>
      <H1>Hello, {user?.full_name}</H1>
      <Muted>{user?.role === "supervisor" ? "Supervisor" : "Data collector"} · {new Date().toDateString()}</Muted>

      <Card>
        <View style={[styles.row, { justifyContent: "space-between" }]}>
          <Label>Shift</Label>
          <Chip text={shift ? "On shift" : "Not started"} tone={shift ? "ok" : "neutral"} />
        </View>
        {shift ? <Muted>Started at {new Date(shift.started_at).toLocaleTimeString()}</Muted> : <Muted>Start your shift before your first visit.</Muted>}
        {shift && tracking && (
          tracking.running ? (
            <Muted>Location tracking on · {tracking.pointsToday} points today · last {timeAgo(tracking.lastFixAt)}</Muted>
          ) : (
            <ErrorText>Location tracking is not running. Check that GPS is on and location is allowed "all the time".</ErrorText>
          )
        )}
        <Button title={shift ? "End shift" : "Start shift"} variant={shift ? "secondary" : "primary"} onPress={shift ? confirmEnd : toggleShift} loading={busy} />
      </Card>

      <Card>
        <Label>Today's visits</Label>
        <Text style={{ fontSize: 32, fontWeight: "800", color: colors.text }}>
          {counts.done} <Text style={{ fontSize: 18, color: colors.muted }}>of {counts.total} done</Text>
        </Text>
        <Button title="My points" onPress={() => router.push("/points")} />
        <Button title="My day" variant="secondary" onPress={() => router.push("/day")} />
      </Card>

      {otherForms.length > 0 && (
        <Card>
          <Label>Supervision</Label>
          {otherForms.map((f) => (
            <Button key={f.version_id} title={f.name} variant="secondary" onPress={() => router.push({ pathname: "/form/[versionId]", params: { versionId: f.version_id } })} />
          ))}
        </Card>
      )}

      <Card>
        <View style={[styles.row, { justifyContent: "space-between" }]}>
          <Label>Sync</Label>
          <Chip text={sync.running ? "Syncing…" : sync.pending ? `${sync.pending} waiting` : "Up to date"} tone={syncTone} />
        </View>
        <Muted>Last successful sync: {timeAgo(sync.lastOkAt)}</Muted>
        {sync.media > 0 && <Muted>{sync.media} photo(s) waiting to upload</Muted>}
        {sync.rejected > 0 && <ErrorText>{sync.rejected} record(s) were refused by the server — see My day.</ErrorText>}
        <ErrorText>{sync.error}</ErrorText>
        <Button title="Sync now" variant="secondary" onPress={() => { runSync(); }} loading={sync.running} />
      </Card>

      <Button title="Settings" variant="secondary" onPress={() => router.push("/settings")} />
    </ScrollView>
  );
}
