/**
 * Collector: hand unsynced work to the supervisor's phone when there's no network.
 * The records are sealed with this collector's key — the supervisor's phone can carry
 * them but not read or change them. They also stay on this phone until it syncs itself.
 */
import * as Application from "expo-application";
import * as Crypto from "expo-crypto";
import { useFocusEffect } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { ScrollView, Text, useWindowDimensions, View } from "react-native";
import { relayBody, sealRelay, toFrames } from "@ragoh/sync-core";
import { fromBase64 } from "@ragoh/sync-core";
import { QrCode } from "@/components/QrCode";
import { Button, Card, ErrorText, Label, Muted, colors, styles } from "@/components/ui";
import { getMeta, setMeta } from "@/lib/db";
import { relayKey } from "@/lib/live";
import { newId, relayableItems } from "@/lib/repo";
import { deviceId } from "@/lib/secure";
import { useSession } from "@/lib/session";

const CHUNK = 350; // characters per code: readable by cheap cameras at arm's length
const FRAME_MS = 300;

export default function SendToSupervisor() {
  const { user } = useSession();
  const { width } = useWindowDimensions();
  const [frames, setFrames] = useState<string[] | null>(null);
  const [count, setCount] = useState(0);
  const [i, setI] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [last, setLast] = useState<{ at: string; count: number } | null>(null);

  useFocusEffect(useCallback(() => {
    getMeta("last_handover").then((v) => setLast(v ? (JSON.parse(v) as { at: string; count: number }) : null));
  }, []));

  useEffect(() => {
    if (!frames || frames.length < 2) return;
    const t = setInterval(() => setI((x) => (x + 1) % frames.length), FRAME_MS);
    return () => clearInterval(t);
  }, [frames]);

  const prepare = async () => {
    setError(null);
    if (!user) return;
    const key = await relayKey(user.id);
    if (!key) {
      setError("This phone isn't set up for handover yet. Connect to the internet once and sync, then try again.");
      return;
    }
    const items = await relayableItems();
    if (!items.length) {
      setError("Nothing to hand over — everything is already synced.");
      return;
    }
    const body = relayBody(
      { id: await deviceId(), app_version: Application.nativeApplicationVersion ?? null },
      items,
      new Date(),
    );
    const env = sealRelay({ id: newId(), collectorId: user.id, key: fromBase64(key), nonce: Crypto.getRandomBytes(12), body });
    setCount(env.count);
    setFrames(toFrames(env, CHUNK));
    setI(0);
  };

  const done = async () => {
    const v = { at: new Date().toISOString(), count };
    await setMeta("last_handover", JSON.stringify(v));
    setLast(v);
    setFrames(null);
  };

  const size = Math.min(width - 32, 420);
  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      {!frames ? (
        <>
          <Card>
            <Label>Hand your work to your supervisor</Label>
            <Muted>Use this when you have no network for a long time. Your supervisor opens "Receive data from a collector" and points their camera at your screen. It takes a few seconds.</Muted>
            <Muted>Your work also stays on this phone and is sent again when you next sync — nothing is lost if their phone is.</Muted>
            <Button title="Show codes" onPress={prepare} />
            <ErrorText>{error}</ErrorText>
          </Card>
          {last && <Muted>Last handover: {new Date(last.at).toLocaleString()} ({last.count} records)</Muted>}
        </>
      ) : (
        <>
          <View style={{ alignItems: "center", gap: 8 }}>
            <QrCode value={frames[i]!} size={size} />
            <Text style={{ fontSize: 15, color: colors.muted }}>
              Code {i + 1} of {frames.length} · {count} records · keep the screen bright and still
            </Text>
          </View>
          <Button title="My supervisor has it" onPress={done} />
          <Button title="Cancel" variant="secondary" onPress={() => setFrames(null)} />
        </>
      )}
    </ScrollView>
  );
}
