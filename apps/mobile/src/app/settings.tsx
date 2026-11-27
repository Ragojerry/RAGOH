import * as Application from "expo-application";
import { useEffect, useState } from "react";
import { Alert, ScrollView } from "react-native";
import { Button, Card, Input, Label, Muted, styles } from "@/components/ui";
import { apiUrl, setApiUrl } from "@/lib/api";
import { currentPack, deletePack, downloadArea, type PackInfo } from "@/lib/offlineMap";
import { deviceId } from "@/lib/secure";
import { useSession } from "@/lib/session";

export default function Settings() {
  const { user, signOut, sync } = useSession();
  const [server, setServer] = useState("");
  const [device, setDevice] = useState("");
  const [pack, setPack] = useState<PackInfo | null>(null);
  const [mapMsg, setMapMsg] = useState<string | null>(null);

  useEffect(() => {
    apiUrl().then(setServer);
    deviceId().then(setDevice);
    currentPack().then(setPack).catch(() => setPack(null));
  }, []);

  const download = async () => {
    setMapMsg(null);
    const r = await downloadArea(setPack, (msg) => setMapMsg(`Download problem: ${msg}`));
    if (r === "no_style") setMapMsg("The offline map isn't ready on the server yet. Ask your supervisor, or try again later.");
    if (r === "no_points") setMapMsg("Sync first: the map covers the area around your assigned points.");
  };

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Card>
        <Label>Signed in as</Label>
        <Muted>{user?.full_name} ({user?.username}) · {user?.role}</Muted>
      </Card>
      <Card>
        <Label>Server</Label>
        <Input value={server} onChangeText={setServer} autoCapitalize="none" autoCorrect={false} keyboardType="url" />
        <Button title="Save server address" variant="secondary" onPress={async () => { await setApiUrl(server); Alert.alert("Saved"); }} />
      </Card>
      <Card>
        <Label>Offline map</Label>
        <Muted>Download the map around your points so it works without internet. Use Wi-Fi if you can (about 10–40 MB).</Muted>
        {pack && (
          <Muted>
            {pack.state === "complete" ? `Downloaded (${pack.sizeMb} MB)` : pack.state === "active" ? `Downloading… ${pack.percentage}% (${pack.sizeMb} MB)` : `Paused at ${pack.percentage}%`}
          </Muted>
        )}
        {mapMsg && <Muted>{mapMsg}</Muted>}
        <Button title={pack ? "Download again" : "Download offline map"} variant="secondary" onPress={download} disabled={pack?.state === "active"} />
        {pack && <Button title="Delete offline map" variant="secondary" onPress={async () => { await deletePack(); setPack(null); }} />}
      </Card>
      <Card>
        <Label>About this phone</Label>
        <Muted>App version {Application.nativeApplicationVersion ?? "dev"} ({Application.nativeBuildVersion ?? "-"})</Muted>
        <Muted>Device ID {device}</Muted>
        <Muted>{sync.pending} record(s) and {sync.media} photo(s) waiting to sync</Muted>
      </Card>
      <Button
        title="Sign out"
        variant="danger"
        onPress={() =>
          Alert.alert(
            "Sign out?",
            sync.pending ? "You have unsynced work. It stays on this phone and will be sent after you sign in again." : "You will need internet to sign in again.",
            [{ text: "Cancel", style: "cancel" }, { text: "Sign out", style: "destructive", onPress: signOut }],
          )
        }
      />
    </ScrollView>
  );
}
