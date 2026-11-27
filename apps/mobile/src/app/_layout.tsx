// Registers the background location task at startup (required for headless runs).
import "@/lib/tracking";
import { Stack, type ErrorBoundaryProps } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { useEffect } from "react";
import { ActivityIndicator, Text, View } from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { LoginScreen, SetPinScreen, UnlockScreen } from "@/components/AuthScreens";
import { PrivacyGate } from "@/components/PrivacyNotice";
import { Button, Muted, colors } from "@/components/ui";
import { installCrashHandler, recordError } from "@/lib/crash";
import { SessionProvider, useSession } from "@/lib/session";

function Gate() {
  const { phase } = useSession();
  if (phase === "loading") {
    return (
      <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
        <ActivityIndicator size="large" color={colors.primary} />
      </View>
    );
  }
  if (phase === "signedOut") return <LoginScreen />;
  if (phase === "setPin") return <SetPinScreen />;
  if (phase === "locked") return <UnlockScreen />;
  if (phase === "privacy") return <PrivacyGate />;
  return (
    <Stack
      screenOptions={{
        headerStyle: { backgroundColor: colors.primary },
        headerTintColor: "#fff",
        headerTitleStyle: { fontWeight: "700" },
        contentStyle: { backgroundColor: colors.bg },
      }}
    >
      <Stack.Screen name="index" options={{ title: "RAGOH" }} />
      <Stack.Screen name="points" options={{ title: "My points" }} />
      <Stack.Screen name="point/[id]" options={{ title: "Visit point" }} />
      <Stack.Screen name="form/[versionId]" options={{ title: "Survey" }} />
      <Stack.Screen name="day" options={{ title: "My day" }} />
      <Stack.Screen name="settings" options={{ title: "Settings" }} />
      <Stack.Screen name="messages" options={{ title: "Messages" }} />
      <Stack.Screen name="team/index" options={{ title: "My team" }} />
      <Stack.Screen name="team/map" options={{ title: "Live map" }} />
      <Stack.Screen name="team/[id]" options={{ title: "Collector" }} />
      <Stack.Screen name="team/go/[id]" options={{ title: "Go to collector" }} />
      <Stack.Screen name="team/replay/[id]" options={{ title: "Route replay" }} />
      <Stack.Screen name="team/flags" options={{ title: "Flags" }} />
      <Stack.Screen name="team/flag/[id]" options={{ title: "Flag" }} />
      <Stack.Screen name="team/reassign/[id]" options={{ title: "Move points" }} />
      <Stack.Screen name="relay/send" options={{ title: "Hand data to supervisor" }} />
      <Stack.Screen name="relay/scan" options={{ title: "Receive data" }} />
      <Stack.Screen name="privacy" options={{ title: "Privacy" }} />
    </Stack>
  );
}

installCrashHandler();

/** Shown instead of a white screen if a screen crashes; the error is reported. */
export function ErrorBoundary({ error, retry }: ErrorBoundaryProps) {
  useEffect(() => {
    void recordError(error, { boundary: true });
  }, [error]);
  return (
    <View style={{ flex: 1, justifyContent: "center", padding: 24, gap: 12, backgroundColor: colors.bg }}>
      <Text style={{ fontSize: 20, fontWeight: "800", color: colors.text }}>Something went wrong</Text>
      <Muted>Your work is saved on the phone. The problem has been reported. Try again; if it keeps happening, tell your supervisor.</Muted>
      <Button title="Try again" onPress={retry} />
    </View>
  );
}

export default function RootLayout() {
  return (
    <SafeAreaProvider>
      <SessionProvider>
        <StatusBar style="light" />
        <Gate />
      </SessionProvider>
    </SafeAreaProvider>
  );
}
