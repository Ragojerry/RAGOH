/**
 * Supervisor: receive a collector's sealed work by scanning the codes on their screen.
 * Works with both phones offline; the bundle goes to the server with this phone's next sync.
 */
import { CameraView, useCameraPermissions, type BarcodeScanningResult } from "expo-camera";
import { useFocusEffect } from "expo-router";
import { useCallback, useRef, useState } from "react";
import { ScrollView, Text, View } from "react-native";
import { FrameCollector, type FrameProgress } from "@ragoh/sync-core";
import { Button, Card, Chip, ErrorText, Label, Muted, colors, styles } from "@/components/ui";
import { queueRelay, receivedRelays, type ReceivedRelay } from "@/lib/repo";
import { useSession } from "@/lib/session";
import { roster } from "@/lib/team";

type Received = ReceivedRelay & { state: string; error: string | null };

export default function ScanFromCollector() {
  const { runSync } = useSession();
  const [perm, requestPerm] = useCameraPermissions();
  const [scanning, setScanning] = useState(false);
  const [progress, setProgress] = useState<FrameProgress | null>(null);
  const [message, setMessage] = useState<{ text: string; tone: "ok" | "danger" } | null>(null);
  const [history, setHistory] = useState<Received[]>([]);
  const collector = useRef(new FrameCollector());
  const finishing = useRef(false);

  const loadHistory = useCallback(async () => setHistory(await receivedRelays()), []);
  useFocusEffect(useCallback(() => { loadHistory(); }, [loadHistory]));

  const start = async () => {
    if (!perm?.granted && !(await requestPerm()).granted) {
      setMessage({ text: "Camera permission is needed to receive data.", tone: "danger" });
      return;
    }
    collector.current.reset();
    finishing.current = false;
    setProgress(null);
    setMessage(null);
    setScanning(true);
  };

  const onScan = async (r: BarcodeScanningResult) => {
    if (finishing.current) return;
    let p: FrameProgress | null;
    try {
      p = collector.current.add(r.data);
    } catch (e) {
      setMessage({ text: e instanceof Error ? e.message : "Unreadable data", tone: "danger" });
      collector.current.reset();
      return;
    }
    if (!p) return; // some other QR code
    setProgress(p);
    if (!p.envelope) return;
    finishing.current = true;
    setScanning(false);
    const env = p.envelope;
    const member = (await roster()).find((m) => m.id === env.collector_id);
    if (!member) {
      setMessage({ text: "This collector isn't in your team, so the server won't accept their data from you.", tone: "danger" });
      return;
    }
    const result = await queueRelay(env, member.full_name);
    setMessage(
      result === "duplicate"
        ? { text: `Already received this from ${member.full_name}.`, tone: "ok" }
        : { text: `Received ${env.count} records from ${member.full_name}. They'll be sent at your next sync.`, tone: "ok" },
    );
    await loadHistory();
    runSync().then(loadHistory);
  };

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      {scanning ? (
        <>
          <View style={{ height: 380, borderRadius: 12, overflow: "hidden", backgroundColor: "#000" }}>
            <CameraView style={{ flex: 1 }} facing="back" barcodeScannerSettings={{ barcodeTypes: ["qr"] }} onBarcodeScanned={onScan} />
          </View>
          <Text style={{ fontSize: 17, fontWeight: "700", color: colors.text, textAlign: "center" }}>
            {progress ? `${progress.have} of ${progress.total} codes` : "Point the camera at the collector's screen"}
          </Text>
          {progress && (
            <View style={{ height: 10, borderRadius: 5, backgroundColor: colors.border, overflow: "hidden" }}>
              <View style={{ height: 10, width: `${(progress.have / progress.total) * 100}%`, backgroundColor: colors.ok }} />
            </View>
          )}
          <Button title="Stop" variant="secondary" onPress={() => setScanning(false)} />
        </>
      ) : (
        <Card>
          <Label>Receive data from a collector</Label>
          <Muted>On the collector's phone: Home → "Hand data to supervisor" → Show codes. Then scan here. No network needed on either phone.</Muted>
          <Button title={history.length ? "Scan another" : "Start scanning"} onPress={start} />
        </Card>
      )}
      {message && (message.tone === "danger" ? <ErrorText>{message.text}</ErrorText> : <Text style={{ color: colors.ok, fontSize: 15, fontWeight: "700" }}>{message.text}</Text>)}

      {history.length > 0 && (
        <Card>
          <Label>Received</Label>
          {history.map((h) => (
            <View key={h.id} style={[styles.row, { justifyContent: "space-between", paddingVertical: 4 }]}>
              <View style={{ flex: 1 }}>
                <Text style={{ color: colors.text, fontWeight: "600" }}>{h.collector_name} · {h.count} records</Text>
                <Muted>{new Date(h.scanned_at).toLocaleString()}{h.error ? ` · ${h.error}` : ""}</Muted>
              </View>
              <Chip text={h.state === "synced" ? "Sent" : h.state === "rejected" ? "Refused" : "Waiting"} tone={h.state === "synced" ? "ok" : h.state === "rejected" ? "danger" : "warn"} />
            </View>
          ))}
        </Card>
      )}
    </ScrollView>
  );
}
