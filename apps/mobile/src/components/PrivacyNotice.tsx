/** The privacy notice: a gate at first sign-in, and a page under Settings. */
import { useEffect, useState } from "react";
import { ScrollView, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { router } from "expo-router";
import { acceptedVersion, currentNotice, recordConsent, type Notice } from "@/lib/consent";
import { useT } from "@/lib/i18n";
import { useSession } from "@/lib/session";
import { Button, Card, H1, Muted, colors, styles } from "./ui";

function Sections({ notice }: { notice: Notice }) {
  return (
    <>
      {notice.sections.map((s) => (
        <Card key={s.heading}>
          <Text style={{ fontSize: 17, fontWeight: "700", color: colors.text }}>{s.heading}</Text>
          <Text style={{ fontSize: 16, lineHeight: 23, color: colors.text }}>{s.body}</Text>
        </Card>
      ))}
    </>
  );
}

/** Full-screen gate: nothing else in the app until it is accepted. */
export function PrivacyGate() {
  const t = useT();
  const { acceptPrivacy, signOut } = useSession();
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    currentNotice().then(setNotice);
  }, []);
  if (!notice) return <View style={styles.screen} />;
  return (
    <SafeAreaView style={styles.screen}>
      <ScrollView contentContainerStyle={[styles.content, { paddingBottom: 40 }]}>
        <H1>{notice.title}</H1>
        <Muted>{t("Please read this before you start. You can read it again any time in Settings.")}</Muted>
        <Sections notice={notice} />
        <Button
          title={t("I have read this and agree")}
          loading={busy}
          onPress={async () => {
            setBusy(true);
            await acceptPrivacy();
          }}
        />
        <Button title={t("I don't agree — sign out")} variant="secondary" onPress={signOut} />
        <Muted>{t("If you don't agree, talk to your supervisor before using the app.")}</Muted>
      </ScrollView>
    </SafeAreaView>
  );
}

/** Read-only page (Settings → Privacy). */
export function PrivacyPage() {
  const t = useT();
  const [notice, setNotice] = useState<Notice | null>(null);
  const [accepted, setAccepted] = useState<{ version: string | null; at: string | null }>({ version: null, at: null });
  useEffect(() => {
    currentNotice().then(setNotice);
    acceptedVersion().then(setAccepted);
  }, []);
  if (!notice) return <View style={styles.screen} />;
  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <H1>{notice.title}</H1>
      {accepted.version && (
        <Muted>
          {t("You agreed to version {v}", { v: accepted.version })}
          {accepted.at ? ` · ${new Date(accepted.at).toLocaleDateString()}` : ""}
        </Muted>
      )}
      <Sections notice={notice} />
      {accepted.version !== notice.version && (
        <Button
          title={t("I have read this and agree")}
          onPress={async () => {
            await recordConsent();
            router.back();
          }}
        />
      )}
    </ScrollView>
  );
}
