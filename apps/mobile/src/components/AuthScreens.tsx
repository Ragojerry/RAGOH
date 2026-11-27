import { useEffect, useState } from "react";
import { KeyboardAvoidingView, ScrollView, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";
import { apiUrl, setApiUrl } from "@/lib/api";
import { useSession } from "@/lib/session";
import { Button, Card, ErrorText, H1, Input, Label, Muted, styles } from "./ui";

export function LoginScreen() {
  const { signIn } = useSession();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [server, setServer] = useState("");
  const [showServer, setShowServer] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiUrl().then((u) => {
      setServer(u);
      if (!u) setShowServer(true);
    });
  }, []);

  const submit = async () => {
    setError(null);
    if (!server.startsWith("https://") && !server.startsWith("http://")) {
      setShowServer(true);
      setError("Enter the server address, starting with https://");
      return;
    }
    setBusy(true);
    try {
      await setApiUrl(server);
      await signIn(username, password);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <SafeAreaView style={styles.screen}>
      <KeyboardAvoidingView behavior="padding" style={{ flex: 1 }}>
        <ScrollView contentContainerStyle={[styles.content, { paddingTop: 48 }]} keyboardShouldPersistTaps="handled">
          <H1>RAGOH</H1>
          <Muted>Field supervision and surveys. The first sign-in needs an internet connection.</Muted>
          <Card>
            <Label>Username</Label>
            <Input value={username} onChangeText={setUsername} autoCapitalize="none" autoCorrect={false} textContentType="username" />
            <Label>Password</Label>
            <Input value={password} onChangeText={setPassword} secureTextEntry textContentType="password" onSubmitEditing={submit} />
            {showServer ? (
              <>
                <Label>Server address</Label>
                <Input value={server} onChangeText={setServer} autoCapitalize="none" autoCorrect={false} keyboardType="url" placeholder="https://…" />
              </>
            ) : (
              <Text onPress={() => setShowServer(true)} style={{ color: "#0F5132", fontWeight: "600", paddingVertical: 6 }}>
                Change server
              </Text>
            )}
            <ErrorText>{error}</ErrorText>
            <Button title="Sign in" onPress={submit} loading={busy} disabled={!username || !password} />
          </Card>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function PinPad({ title, subtitle, onDone, error }: { title: string; subtitle: string; onDone: (pin: string) => void; error?: string | null }) {
  const [value, setValue] = useState("");
  return (
    <SafeAreaView style={styles.screen}>
      <View style={[styles.content, { paddingTop: 64 }]}>
        <H1>{title}</H1>
        <Muted>{subtitle}</Muted>
        <Input
          value={value}
          onChangeText={(t) => setValue(t.replace(/\D/g, "").slice(0, 6))}
          keyboardType="number-pad"
          secureTextEntry
          autoFocus
          style={{ fontSize: 28, letterSpacing: 12, textAlign: "center" }}
        />
        <ErrorText>{error}</ErrorText>
        <Button title="Continue" onPress={() => { onDone(value); setValue(""); }} disabled={value.length < 4} />
      </View>
    </SafeAreaView>
  );
}

export function SetPinScreen() {
  const { savePin } = useSession();
  const [first, setFirst] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (first === null) {
    return <PinPad title="Create a PIN" subtitle="4–6 digits. You'll use it to open the app, even without internet." onDone={(p) => { setFirst(p); setError(null); }} error={error} />;
  }
  return (
    <PinPad
      title="Repeat your PIN"
      subtitle="Enter the same PIN again."
      onDone={(p) => {
        if (p === first) savePin(p);
        else { setFirst(null); setError("The PINs didn't match. Try again."); }
      }}
    />
  );
}

export function UnlockScreen() {
  const { unlock, user, signOut } = useSession();
  const [error, setError] = useState<string | null>(null);
  return (
    <View style={{ flex: 1 }}>
      <PinPad
        title={`Hello, ${user?.full_name.split(" ")[0] ?? ""}`}
        subtitle="Enter your PIN to continue."
        error={error}
        onDone={async (p) => {
          const r = await unlock(p);
          if (r !== "ok") setError(r > 0 ? `Wrong PIN. ${r} attempts left.` : "Too many wrong attempts. Sign in again.");
        }}
      />
      <View style={{ padding: 16 }}>
        <Button title="Sign in as someone else" variant="secondary" onPress={signOut} />
      </View>
    </View>
  );
}
