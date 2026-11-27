import { router, useFocusEffect } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { Alert, RefreshControl, ScrollView, Text, View } from "react-native";
import { Button, Card, Chip, ErrorText, H1, Label, Muted, colors, styles } from "@/components/ui";
import { currentLocation } from "@/lib/location";
import { hasConsented } from "@/lib/consent";
import { getMeta, setMeta } from "@/lib/db";
import { activeShift, listAssignments, listForms, unreadCount } from "@/lib/repo";
import { beginShift, finishShift, openBatterySettings, trackingStats } from "@/lib/tracking";
import { useT } from "@/lib/i18n";
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

let modeChecked = false; // once per app start: supervisors land on their team

export default function Home() {
  const t = useT();
  const { user, sync, runSync } = useSession();
  const isStaff = !!user && user.role !== "collector";
  useEffect(() => {
    if (!isStaff || modeChecked) return;
    modeChecked = true;
    getMeta("home_mode").then((m) => m !== "self" && router.push("/team"));
  }, [isStaff]);
  const toTeam = async () => {
    await setMeta("home_mode", "team");
    router.push("/team");
  };
  const [shift, setShift] = useState<Shift | null>(null);
  const [counts, setCounts] = useState({ total: 0, done: 0 });
  const [otherForms, setOtherForms] = useState<FormInfo[]>([]);
  const [unread, setUnread] = useState(0);
  const [tasks, setTasks] = useState(0);
  const [busy, setBusy] = useState(false);
  const [tracking, setTracking] = useState<{ running: boolean; lastFixAt: string | null; pointsToday: number } | null>(null);

  const load = useCallback(async () => {
    setShift(await activeShift());
    setTracking(await trackingStats());
    const a = await listAssignments();
    setCounts({ total: a.length, done: a.filter((x) => DONE_STATUSES.includes(x.status)).length });
    setTasks(a.filter((x) => !DONE_STATUSES.includes(x.status) && (x.kind === "backcheck" || !!x.note)).length);
    setUnread(await unreadCount());
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
        // Normally accepted at sign-in; this covers a notice that changed meanwhile.
        router.push("/privacy");
        return;
      }
      if (!(await getMeta("battery_tip_shown"))) {
        await setMeta("battery_tip_shown", "1");
        const open = await ask(
          t("Keep tracking on"),
          t("Some phones stop apps in the background to save battery. Find RAGOH in the next screen and choose \"Don't optimise\" / \"Allow\". Then come back and start your shift."),
          t("Open settings"),
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
        Alert.alert(t("Location needed"), t("Turn on location (GPS) and allow RAGOH to use it, then try again."));
        return;
      }
      if (shift) {
        await finishShift(shift.id, loc);
      } else {
        const { tracking: started } = await beginShift(loc);
        if (started === "no_background_permission") {
          Alert.alert(
            t("Allow location all the time"),
            t("Your shift has started, but tracking needs location access \"Allow all the time\". Open RAGOH's app settings → Permissions → Location and choose it."),
          );
        } else if (started === "gps_off") {
          Alert.alert(t("GPS is off"), t("Turn on Location in your phone's quick settings."));
        }
      }
      await load();
      runSync();
    } finally {
      setBusy(false);
    }
  };

  const confirmEnd = () =>
    Alert.alert(t("End shift?"), t("Your work stays on the phone and will be sent when you have internet."), [
      { text: t("Cancel"), style: "cancel" },
      { text: t("End shift"), style: "destructive", onPress: toggleShift },
    ]);

  const syncTone = sync.error ? "danger" : sync.pending > 0 ? "warn" : "ok";

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content} refreshControl={<RefreshControl refreshing={sync.running} onRefresh={() => { runSync(); }} />}>
      <H1>{t("Hello, {name}", { name: user?.full_name ?? "" })}</H1>
      <Muted>{user?.role === "supervisor" ? t("Supervisor") : t("Data collector")} · {new Date().toDateString()}</Muted>
      {isStaff && <Button title={t("My team →")} onPress={toTeam} />}

      <Card>
        <View style={[styles.row, { justifyContent: "space-between" }]}>
          <Label>{t("Shift")}</Label>
          <Chip text={shift ? t("On shift") : t("Not started")} tone={shift ? "ok" : "neutral"} />
        </View>
        {shift ? <Muted>{t("Started at {time}", { time: new Date(shift.started_at).toLocaleTimeString() })}</Muted> : <Muted>{t("Start your shift before your first visit.")}</Muted>}
        {shift && tracking && (
          tracking.running ? (
            <Muted>{t("Location tracking on · {n} points today · last {when}", { n: tracking.pointsToday, when: timeAgo(tracking.lastFixAt) })}</Muted>
          ) : (
            <ErrorText>{t("Location tracking is not running. Check that GPS is on and location is allowed \"all the time\".")}</ErrorText>
          )
        )}
        <Button title={shift ? t("End shift") : t("Start shift")} variant={shift ? "secondary" : "primary"} onPress={shift ? confirmEnd : toggleShift} loading={busy} />
      </Card>

      <Button title={unread ? t("Messages ({n} new)", { n: unread }) : t("Messages")} variant={unread ? "primary" : "secondary"} onPress={() => router.push("/messages")} />

      <Card>
        <Label>{t("Today's visits")}</Label>
        {tasks > 0 && <Chip text={t("{n} back-check / revisit task(s) from your supervisor", { n: tasks })} tone="warn" />}
        <Text style={{ fontSize: 32, fontWeight: "800", color: colors.text }}>
          {counts.done} <Text style={{ fontSize: 18, color: colors.muted }}>{t("of {n} done", { n: counts.total })}</Text>
        </Text>
        <Button title={t("My points")} onPress={() => router.push("/points")} />
        <Button title={t("My day")} variant="secondary" onPress={() => router.push("/day")} />
      </Card>

      {otherForms.length > 0 && (
        <Card>
          <Label>{t("Supervision")}</Label>
          {otherForms.map((f) => (
            <Button key={f.version_id} title={f.name} variant="secondary" onPress={() => router.push({ pathname: "/form/[versionId]", params: { versionId: f.version_id } })} />
          ))}
        </Card>
      )}

      <Card>
        <View style={[styles.row, { justifyContent: "space-between" }]}>
          <Label>{t("Sync")}</Label>
          <Chip text={sync.running ? t("Syncing…") : sync.pending ? t("{n} waiting", { n: sync.pending }) : t("Up to date")} tone={syncTone} />
        </View>
        <Muted>{t("Last successful sync: {when}", { when: timeAgo(sync.lastOkAt) })}</Muted>
        {sync.media > 0 && <Muted>{t("{n} photo(s) waiting to upload", { n: sync.media })}</Muted>}
        {sync.rejected > 0 && <ErrorText>{t("{n} record(s) were refused by the server — see My day.", { n: sync.rejected })}</ErrorText>}
        <ErrorText>{sync.error}</ErrorText>
        <Button title={t("Sync now")} variant="secondary" onPress={() => { runSync(); }} loading={sync.running} />
        {!isStaff && sync.pending > 0 && (
          <Button title={t("No network? Hand data to supervisor")} variant="secondary" onPress={() => router.push("/relay/send")} />
        )}
      </Card>

      <Button title={t("Settings")} variant="secondary" onPress={() => router.push("/settings")} />
    </ScrollView>
  );
}
