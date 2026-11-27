/** Supervisor mode home: the team at a glance, refreshed every minute while open. */
import { router, useFocusEffect } from "expo-router";
import { useCallback, useRef, useState } from "react";
import { Pressable, RefreshControl, ScrollView, Text, View } from "react-native";
import { Button, Card, Chip, ErrorText, H1, Label, Muted, colors, styles } from "@/components/ui";
import { STATUS_COLOR } from "@/components/TeamMap";
import { setMeta } from "@/lib/db";
import { useSession } from "@/lib/session";
import { STATUS_TEXT, ago, asOf, openFlags, statusTone, teamLive, type Cached, type TeamLive } from "@/lib/team";

const REFRESH_MS = 60_000;

export default function TeamHome() {
  const { user, sync } = useSession();
  const [live, setLive] = useState<Cached<TeamLive> | null>(null);
  const [highFlags, setHighFlags] = useState(0);
  const [loading, setLoading] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setLive(await teamLive());
      const f = await openFlags();
      setHighFlags(f ? f.data.filter((x) => x.severity === "high").length : 0);
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
      timer.current = setInterval(load, REFRESH_MS);
      return () => {
        if (timer.current) clearInterval(timer.current);
      };
    }, [load]),
  );

  const toMyWork = async () => {
    await setMeta("home_mode", "self");
    router.replace("/");
  };

  const collectors = live?.data.collectors ?? [];
  const attention = collectors.filter((c) => c.status === "offline" || c.status === "idle" || c.open_high_flags > 0);

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content} refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}>
      <View style={[styles.row, { justifyContent: "space-between" }]}>
        <H1>My team</H1>
        <Pressable onPress={toMyWork} accessibilityRole="button" style={{ padding: 8 }}>
          <Text style={{ color: colors.primary, fontWeight: "700", fontSize: 15 }}>My own work ›</Text>
        </Pressable>
      </View>
      {live ? (
        <Muted>
          {live.stale ? "Offline — showing " : "Updated "}as of {asOf(live.fetchedAt)}
        </Muted>
      ) : (
        <ErrorText>{loading ? "" : "Can't reach the server and nothing saved yet. Connect once to load your team."}</ErrorText>
      )}

      {live?.data.teams.map((t) => (
        <Card key={t.id}>
          <View style={[styles.row, { justifyContent: "space-between" }]}>
            <Label>{t.name}</Label>
            <Muted>{t.collectors} collectors</Muted>
          </View>
          <View style={[styles.row, { flexWrap: "wrap" }]}>
            <Chip text={`${t.on_shift} on shift`} tone="ok" />
            {t.offline > 0 && <Chip text={`${t.offline} no signal`} tone="danger" />}
            {t.open_flags > 0 && <Chip text={`${t.open_flags} open flags`} tone="warn" />}
          </View>
          <Text style={{ fontSize: 26, fontWeight: "800", color: colors.text }}>
            {t.done} <Text style={{ fontSize: 16, color: colors.muted }}>of {t.assigned} visits done</Text>
          </Text>
        </Card>
      ))}

      <View style={[styles.row]}>
        <Button title="Live map" onPress={() => router.push("/team/map")} style={{ flex: 1 }} />
        <Button
          title={highFlags ? `Flags (${highFlags} high)` : "Flags"}
          variant={highFlags ? "danger" : "secondary"}
          onPress={() => router.push("/team/flags")}
          style={{ flex: 1 }}
        />
      </View>

      {attention.length > 0 && (
        <Card style={{ borderColor: colors.warning }}>
          <Label>Needs attention</Label>
          {attention.map((c) => (
            <Pressable key={c.id} onPress={() => router.push({ pathname: "/team/[id]", params: { id: c.id } })}>
              <Text style={{ fontSize: 15, color: colors.text, paddingVertical: 4 }}>
                <Text style={{ fontWeight: "700" }}>{c.full_name}</Text> —{" "}
                {c.open_high_flags > 0 ? `${c.open_high_flags} high flag${c.open_high_flags > 1 ? "s" : ""}` : STATUS_TEXT[c.status].toLowerCase()}
                {c.status === "offline" ? ` (last seen ${ago(c.last_seen_at)})` : ""}
              </Text>
            </Pressable>
          ))}
        </Card>
      )}

      <Label>Collectors</Label>
      {collectors.map((c) => (
        <Pressable key={c.id} onPress={() => router.push({ pathname: "/team/[id]", params: { id: c.id } })}>
          <Card>
            <View style={[styles.row, { justifyContent: "space-between" }]}>
              <View style={[styles.row, { flex: 1 }]}>
                <View style={{ width: 12, height: 12, borderRadius: 6, backgroundColor: STATUS_COLOR[c.status] }} />
                <Label>{c.full_name}</Label>
              </View>
              <Chip text={STATUS_TEXT[c.status]} tone={statusTone(c.status)} />
            </View>
            <Muted>
              {c.done_today}/{c.assigned_today} visits · {(c.distance_m / 1000).toFixed(1)} km · seen {ago(c.last_seen_at)}
              {c.battery_pct !== null ? ` · battery ${c.battery_pct}%` : ""}
            </Muted>
            {c.at_point_name && <Muted>At {c.at_point_name}</Muted>}
            {(c.open_flags > 0 || c.pending_sync > 0) && (
              <View style={[styles.row, { flexWrap: "wrap" }]}>
                {c.open_flags > 0 && <Chip text={`${c.open_flags} flag${c.open_flags > 1 ? "s" : ""}`} tone={c.open_high_flags ? "danger" : "warn"} />}
                {c.pending_sync > 0 && <Chip text={`${c.pending_sync} not synced`} />}
                {c.risk_score > 0 && <Chip text={`risk ${c.risk_score}`} tone={c.risk_score >= 50 ? "danger" : "neutral"} />}
              </View>
            )}
          </Card>
        </Pressable>
      ))}
      {live && collectors.length === 0 && <Muted>No collectors in your teams yet.</Muted>}

      <Card>
        <Label>No network in the field?</Label>
        <Muted>Collect a collector's unsynced work on your phone by scanning the codes on their screen. It's sent to the server when you next sync.</Muted>
        <Button title="Receive data from a collector" variant="secondary" onPress={() => router.push("/relay/scan")} />
      </Card>

      <View style={styles.row}>
        <Button title="Messages" variant="secondary" onPress={() => router.push("/messages")} style={{ flex: 1 }} />
        <Button title="Settings" variant="secondary" onPress={() => router.push("/settings")} style={{ flex: 1 }} />
      </View>
      {sync.pending > 0 && <Muted>{sync.pending} record(s) on this phone waiting to sync.</Muted>}
      <Muted>Signed in as {user?.full_name}</Muted>
    </ScrollView>
  );
}
