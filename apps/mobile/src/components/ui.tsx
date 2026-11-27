/** Large, high-contrast building blocks for use outdoors on small phones. */
import { ActivityIndicator, Pressable, StyleSheet, Text, TextInput, View, type TextInputProps, type ViewStyle } from "react-native";
import type { ReactNode } from "react";

export const colors = {
  primary: "#0F5132",
  primaryText: "#FFFFFF",
  danger: "#B42318",
  warning: "#B54708",
  text: "#101828",
  muted: "#475467",
  border: "#D0D5DD",
  bg: "#F5F7F6",
  card: "#FFFFFF",
  ok: "#067647",
};

export function Button({
  title, onPress, variant = "primary", disabled, loading, style,
}: {
  title: string;
  onPress: () => void;
  variant?: "primary" | "secondary" | "danger";
  disabled?: boolean;
  loading?: boolean;
  style?: ViewStyle;
}) {
  const bg = variant === "primary" ? colors.primary : variant === "danger" ? colors.danger : colors.card;
  const fg = variant === "secondary" ? colors.primary : colors.primaryText;
  return (
    <Pressable
      accessibilityRole="button"
      onPress={onPress}
      disabled={disabled || loading}
      style={({ pressed }) => [
        styles.button,
        { backgroundColor: bg, borderColor: variant === "secondary" ? colors.primary : bg, opacity: disabled ? 0.5 : pressed ? 0.85 : 1 },
        style,
      ]}
    >
      {loading ? <ActivityIndicator color={fg} /> : <Text style={[styles.buttonText, { color: fg }]}>{title}</Text>}
    </Pressable>
  );
}

export function Card({ children, style }: { children: ReactNode; style?: ViewStyle }) {
  return <View style={[styles.card, style]}>{children}</View>;
}

export function Label({ children }: { children: ReactNode }) {
  return <Text style={styles.label}>{children}</Text>;
}

export function Muted({ children }: { children: ReactNode }) {
  return <Text style={styles.muted}>{children}</Text>;
}

export function H1({ children }: { children: ReactNode }) {
  return <Text style={styles.h1}>{children}</Text>;
}

export function Input(props: TextInputProps) {
  return <TextInput placeholderTextColor={colors.muted} {...props} style={[styles.input, props.style]} />;
}

export function Chip({ text, tone = "neutral" }: { text: string; tone?: "neutral" | "ok" | "warn" | "danger" }) {
  const c = tone === "ok" ? colors.ok : tone === "warn" ? colors.warning : tone === "danger" ? colors.danger : colors.muted;
  return (
    <View style={[styles.chip, { borderColor: c }]}>
      <Text style={[styles.chipText, { color: c }]}>{text}</Text>
    </View>
  );
}

export function ErrorText({ children }: { children: ReactNode }) {
  if (!children) return null;
  return <Text style={styles.error}>{children}</Text>;
}

export const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  content: { padding: 16, gap: 12 },
  button: { minHeight: 52, borderRadius: 10, borderWidth: 2, alignItems: "center", justifyContent: "center", paddingHorizontal: 16 },
  buttonText: { fontSize: 17, fontWeight: "700" },
  card: { backgroundColor: colors.card, borderRadius: 12, padding: 16, gap: 8, borderWidth: 1, borderColor: colors.border },
  label: { fontSize: 17, fontWeight: "600", color: colors.text },
  muted: { fontSize: 14, color: colors.muted },
  h1: { fontSize: 22, fontWeight: "800", color: colors.text },
  input: { minHeight: 52, borderWidth: 1.5, borderColor: colors.border, borderRadius: 10, paddingHorizontal: 12, fontSize: 17, color: colors.text, backgroundColor: "#fff" },
  chip: { borderWidth: 1.5, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 3, alignSelf: "flex-start" },
  chipText: { fontSize: 13, fontWeight: "700" },
  error: { color: colors.danger, fontSize: 15, fontWeight: "600" },
  row: { flexDirection: "row", alignItems: "center", gap: 8 },
});
