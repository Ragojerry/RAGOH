import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";
import { ActivityIndicator, View } from "react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { LoginScreen, SetPinScreen, UnlockScreen } from "@/components/AuthScreens";
import { colors } from "@/components/ui";
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
    </Stack>
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
