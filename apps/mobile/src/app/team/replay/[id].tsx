/**
 * Replay a collector's day: the track drawn on the map, stops numbered with their dwell
 * time, and a play head that moves along the track.
 */
import { useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Pressable, ScrollView, Text, View } from "react-native";
import { TeamMap, type MapStop } from "@/components/TeamMap";
import { Button, Chip, ErrorText, Label, Muted, colors, styles } from "@/components/ui";
import { formatDuration } from "@/lib/routing";
import { asOf, collectorDay, type Cached, type CollectorDay } from "@/lib/team";

const STOP_TEXT: Record<string, string> = {
  at_assigned_point: "at point",
  near_assigned_point: "near point",
  unassigned_location: "elsewhere",
  base: "base/home",
};
const SPEEDS = [60, 300, 900]; // seconds of the day per real second

function localDate(d = new Date()): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
const hhmm = (unix: number) => new Date(unix * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

export default function Replay() {
  const { id } = useLocalSearchParams<{ id: string }>();
  const [date, setDate] = useState(localDate());
  const [day, setDay] = useState<Cached<CollectorDay> | null>(null);
  const [loading, setLoading] = useState(false);
  const [t, setT] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(SPEEDS[1]!);
  const [barWidth, setBarWidth] = useState(300);
  const tick = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setPlaying(false);
    const d = await collectorDay(id, date);
    setDay(d);
    setT(d?.data.track[0]?.[2] ?? null);
    setLoading(false);
  }, [id, date]);
  useFocusEffect(useCallback(() => { load(); }, [load]));

  const track = day?.data.track ?? [];
  const start = track[0]?.[2] ?? 0;
  const end = track[track.length - 1]?.[2] ?? 0;

  useEffect(() => {
    if (!playing) return;
    tick.current = setInterval(() => {
      setT((cur) => {
        const next = (cur ?? start) + speed / 5;
        if (next >= end) {
          setPlaying(false);
          return end;
        }
        return next;
      });
    }, 200);
    return () => {
      if (tick.current) clearInterval(tick.current);
    };
  }, [playing, speed, start, end]);

  // Track up to the play head (drawn), and where the head is now.
  const { shown, cursor } = useMemo(() => {
    if (!track.length || t === null) return { shown: null, cursor: null };
    let i = 0;
    while (i < track.length - 1 && track[i + 1]![2] <= t) i++;
    const pts = track.slice(0, i + 1).map(([x, y]) => [x, y] as [number, number]);
    return { shown: pts.length >= 2 ? pts : null, cursor: { lon: track[i]![0], lat: track[i]![1] } };
  }, [track, t]);

  const stops: MapStop[] = (day?.data.stops ?? [])
    .filter((s) => t === null || new Date(s.started_at).getTime() / 1000 <= t)
    .map((s, i) => ({
      id: s.id, lat: s.lat, lon: s.lon, label: String(i + 1),
      color: s.classification === "at_assigned_point" ? colors.ok : s.classification === "base" ? "#667085" : colors.warning,
    }));
  const s = day?.data.summary;
  const shiftDay = (n: number) => {
    const d = new Date(`${date}T12:00:00`);
    d.setDate(d.getDate() + n);
    setDate(localDate(d));
  };

  return (
    <View style={styles.screen}>
      <View style={{ flex: 1 }}>
        {track.length > 0 ? (
          <TeamMap line={shown} stops={stops} cursor={cursor} center={[track[0]![0], track[0]![1]]} zoom={15} lineColor="#7A5AF8" />
        ) : (
          <View style={{ flex: 1, alignItems: "center", justifyContent: "center", padding: 24 }}>
            <Muted>{loading ? "Loading…" : "No synced track for this day yet."}</Muted>
          </View>
        )}
      </View>
      <ScrollView style={{ maxHeight: 300, backgroundColor: colors.card, borderTopWidth: 1, borderColor: colors.border }} contentContainerStyle={{ padding: 12, gap: 8 }}>
        <View style={[styles.row, { justifyContent: "space-between" }]}>
          <Button title="‹" variant="secondary" onPress={() => shiftDay(-1)} style={{ minWidth: 52 }} />
          <Label>{new Date(`${date}T12:00:00`).toDateString()}</Label>
          <Button title="›" variant="secondary" disabled={date >= localDate()} onPress={() => shiftDay(1)} style={{ minWidth: 52 }} />
        </View>
        {day?.stale && <Muted>Offline — saved copy from {asOf(day.fetchedAt)}</Muted>}
        {!day && !loading && <ErrorText>Can't load this day (no connection and nothing saved).</ErrorText>}
        {s && (
          <Muted>
            {(s.distance_m / 1000).toFixed(1)} km · {s.n_stops} stops · {formatDuration(s.total_dwell_s)} stopped · {s.completed}/{s.assigned} visits
            {s.n_gaps ? ` · ${s.n_gaps} gap(s), ${formatDuration(s.gap_s)} without signal` : ""}
          </Muted>
        )}
        {track.length > 1 && t !== null && (
          <>
            <Pressable
              onLayout={(e) => setBarWidth(e.nativeEvent.layout.width)}
              onPress={(e) => {
                const f = e.nativeEvent.locationX / Math.max(1, barWidth);
                setT(start + Math.min(1, Math.max(0, f)) * (end - start));
              }}
              hitSlop={12}
              style={{ height: 14, borderRadius: 7, backgroundColor: colors.border, overflow: "hidden" }}
            >
              <View style={{ height: 14, width: `${((t - start) / Math.max(1, end - start)) * 100}%`, backgroundColor: "#7A5AF8" }} />
            </Pressable>
            <View style={[styles.row, { justifyContent: "space-between" }]}>
              <Text style={{ fontSize: 15, fontWeight: "700", color: colors.text }}>{hhmm(t)}</Text>
              <Muted>{hhmm(start)} – {hhmm(end)}</Muted>
            </View>
            <View style={styles.row}>
              <Button title={playing ? "Pause" : t >= end ? "Replay" : "Play"} onPress={() => { if (t >= end) setT(start); setPlaying((p) => !p); }} style={{ flex: 1 }} />
              <Button title={`${speed / 60}× min/s`} variant="secondary" onPress={() => setSpeed(SPEEDS[(SPEEDS.indexOf(speed) + 1) % SPEEDS.length]!)} style={{ flex: 1 }} />
              <Button title="All" variant="secondary" onPress={() => { setPlaying(false); setT(end); }} style={{ flex: 1 }} />
            </View>
          </>
        )}
        {(day?.data.stops ?? []).map((st, i) => (
          <Pressable key={st.id} onPress={() => { setPlaying(false); setT(new Date(st.ended_at).getTime() / 1000); }} style={[styles.row, { justifyContent: "space-between" }]}>
            <Text style={{ flex: 1, color: colors.text }}>
              {i + 1}. {new Date(st.started_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })} · {formatDuration(st.dwell_s)}
              {st.visit_point_name ? ` · ${st.visit_point_name}` : ""}
            </Text>
            <Chip text={STOP_TEXT[st.classification] ?? st.classification} tone={st.classification === "at_assigned_point" ? "ok" : st.classification === "unassigned_location" ? "warn" : "neutral"} />
          </Pressable>
        ))}
      </ScrollView>
    </View>
  );
}
