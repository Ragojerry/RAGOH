import * as Application from "expo-application";
import { useEffect, useState } from "react";
import { Alert, ScrollView } from "react-native";
import { Button, Card, Input, Label, Muted, styles } from "@/components/ui";
import { apiUrl, setApiUrl } from "@/lib/api";
import { deviceId } from "@/lib/secure";
import { useSession } from "@/lib/session";

export default function Settings() {
  const { user, signOut, sync } = useSession();
  const [server, setServer] = useState("");
  const [device, setDevice] = useState("");

  useEffect(() => {
    apiUrl().then(setServer);
    deviceId().then(setDevice);
  }, []);

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
