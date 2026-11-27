import * as Application from "expo-application";
import { useEffect, useState } from "react";
import { Alert, ScrollView } from "react-native";
import { Button, Card, Input, Label, Muted, colors, styles } from "@/components/ui";
import { apiUrl, setApiUrl } from "@/lib/api";
import { currentPack, deletePack, downloadArea, type PackInfo } from "@/lib/offlineMap";
import { deviceId } from "@/lib/secure";
import { router } from "expo-router";
import { Pressable, Text, View } from "react-native";
import strings from "@/i18n/strings.json";
import { setMeta } from "@/lib/db";
import { LANGUAGES, coverage, language, setLanguage, useT } from "@/lib/i18n";
import { useSession } from "@/lib/session";

export default function Settings() {
  const t = useT();
  const { user, signOut, sync } = useSession();
  const done = coverage(strings as string[]);
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
    if (r === "no_style") setMapMsg(t("The offline map isn't ready on the server yet. Ask your supervisor, or try again later."));
    if (r === "no_points") setMapMsg(t("Sync first: the map covers the area around your assigned points."));
  };

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <Card>
        <Label>{t("Language")}</Label>
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
          {LANGUAGES.map((l) => (
            <Pressable
              key={l.code}
              onPress={async () => { setLanguage(l.code); await setMeta("language", l.code); }}
              style={{ paddingHorizontal: 14, paddingVertical: 10, borderRadius: 999, borderWidth: 1.5, borderColor: colors.primary, backgroundColor: language() === l.code ? colors.primary : "#fff" }}
            >
              <Text style={{ color: language() === l.code ? "#fff" : colors.primary, fontWeight: "700" }}>
                {l.name}{done[l.code] < 100 ? ` (${done[l.code]}%)` : ""}
              </Text>
            </Pressable>
          ))}
        </View>
        <Muted>{t("Words not translated yet are shown in English.")}</Muted>
      </Card>
      <Card>
        <Label>{t("Privacy")}</Label>
        <Muted>{t("What RAGOH records about you, who can see it and how long it is kept.")}</Muted>
        <Button title={t("Read the privacy notice")} variant="secondary" onPress={() => router.push("/privacy")} />
      </Card>
      <Card>
        <Label>{t("Signed in as")}</Label>
        <Muted>{user?.full_name} ({user?.username}) · {user?.role}</Muted>
      </Card>
      <Card>
        <Label>{t("Server")}</Label>
        <Input value={server} onChangeText={setServer} autoCapitalize="none" autoCorrect={false} keyboardType="url" />
        <Button title={t("Save server address")} variant="secondary" onPress={async () => { await setApiUrl(server); Alert.alert(t("Saved")); }} />
      </Card>
      <Card>
        <Label>{t("Offline map")}</Label>
        <Muted>{t("Download the map around your points so it works without internet. Use Wi-Fi if you can (about 10–40 MB).")}</Muted>
        {pack && (
          <Muted>
            {pack.state === "complete" ? `Downloaded (${pack.sizeMb} MB)` : pack.state === "active" ? `Downloading… ${pack.percentage}% (${pack.sizeMb} MB)` : `Paused at ${pack.percentage}%`}
          </Muted>
        )}
        {mapMsg && <Muted>{mapMsg}</Muted>}
        <Button title={pack ? t("Download again") : t("Download offline map")} variant="secondary" onPress={download} disabled={pack?.state === "active"} />
        {pack && <Button title={t("Delete offline map")} variant="secondary" onPress={async () => { await deletePack(); setPack(null); }} />}
      </Card>
      <Card>
        <Label>{t("About this phone")}</Label>
        <Muted>App version {Application.nativeApplicationVersion ?? "dev"} ({Application.nativeBuildVersion ?? "-"})</Muted>
        <Muted>Device ID {device}</Muted>
        <Muted>{t("{n} record(s) and {m} photo(s) waiting to sync", { n: sync.pending, m: sync.media })}</Muted>
      </Card>
      <Button
        title={t("Sign out")}
        variant="danger"
        onPress={() =>
          Alert.alert(
            t("Sign out?"),
            sync.pending ? t("You have unsynced work. It stays on this phone and will be sent after you sign in again.") : t("You will need internet to sign in again."),
            [{ text: t("Cancel"), style: "cancel" }, { text: t("Sign out"), style: "destructive", onPress: signOut }],
          )
        }
      />
    </ScrollView>
  );
}
