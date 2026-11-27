/** Inbox: questions from the supervisor about flagged work, and messages both ways. Works offline. */
import { useFocusEffect } from "expo-router";
import { useCallback, useState } from "react";
import { Alert, KeyboardAvoidingView, Linking, ScrollView, Text, View } from "react-native";
import { Button, Card, Chip, H1, Input, Label, Muted, colors, styles } from "@/components/ui";
import { setMeta } from "@/lib/db";
import { inbox, queueFlagReply, queueMessage } from "@/lib/repo";
import { useSession } from "@/lib/session";
import type { Contact, FlagToExplain, InboxMessage } from "@/lib/types";

function FlagCard({ flag, question, onReply }: { flag: FlagToExplain; question?: InboxMessage; onReply: (text: string) => Promise<void> }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <Card style={{ borderColor: colors.warning, borderWidth: 2 }}>
      <View style={[styles.row, { justifyContent: "space-between" }]}>
        <Label>{flag.title}</Label>
        <Chip text="Please explain" tone="warn" />
      </View>
      <Muted>Day: {flag.local_date}</Muted>
      {question && <Text style={{ fontSize: 16, color: colors.text }}>“{question.body}” — {question.sender_name}</Text>}
      <Input value={text} onChangeText={setText} multiline placeholder="Your explanation" style={{ minHeight: 80, textAlignVertical: "top", paddingTop: 10 }} />
      <Button
        title="Send explanation"
        loading={busy}
        disabled={!text.trim()}
        onPress={async () => {
          setBusy(true);
          try {
            await onReply(text);
            setText("");
          } finally {
            setBusy(false);
          }
        }}
      />
    </Card>
  );
}

export default function Messages() {
  const { user, runSync } = useSession();
  const [messages, setMessages] = useState<InboxMessage[]>([]);
  const [flags, setFlags] = useState<FlagToExplain[]>([]);
  const [supervisors, setSupervisors] = useState<Contact[]>([]);
  const [to, setTo] = useState<string | null>(null);
  const [text, setText] = useState("");

  const load = useCallback(async () => {
    const box = await inbox();
    setMessages(box.messages);
    setFlags(box.flags);
    setSupervisors(box.supervisors);
    setTo((cur) => cur ?? box.supervisors[0]?.id ?? null);
    await setMeta("inbox_seen_at", new Date().toISOString());
  }, []);

  useFocusEffect(useCallback(() => { load(); }, [load]));

  const repliedFlags = new Set(messages.filter((m) => m.pending && m.flag_id).map((m) => m.flag_id));

  const send = async () => {
    if (!to) return Alert.alert("No supervisor", "Sync first to download your supervisor's details.");
    await queueMessage(to, text);
    setText("");
    await load();
    runSync();
  };

  return (
    <KeyboardAvoidingView behavior="padding" style={{ flex: 1 }}>
      <ScrollView style={styles.screen} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <H1>Messages</H1>
        {flags.filter((f) => !repliedFlags.has(f.id)).map((f) => (
          <FlagCard
            key={f.id}
            flag={f}
            question={messages.find((m) => m.flag_id === f.id && !m.pending && m.sender_id !== user?.id)}
            onReply={async (t) => {
              await queueFlagReply(f.id, t);
              await load();
              runSync();
            }}
          />
        ))}

        <Card>
          <Label>Message your supervisor</Label>
          {supervisors.length > 1 && (
            <View style={[styles.row, { flexWrap: "wrap" }]}>
              {supervisors.map((s) => (
                <Button key={s.id} title={s.full_name} variant={to === s.id ? "primary" : "secondary"} onPress={() => setTo(s.id)} style={{ minHeight: 40 }} />
              ))}
            </View>
          )}
          {supervisors.length === 1 && <Muted>To {supervisors[0]!.full_name}</Muted>}
          <Input value={text} onChangeText={setText} multiline placeholder="Write a message" style={{ minHeight: 70, textAlignVertical: "top", paddingTop: 10 }} />
          <View style={styles.row}>
            <Button title="Send" onPress={send} disabled={!text.trim() || !to} style={{ flex: 1 }} />
            {supervisors.find((s) => s.id === to)?.phone && (
              <Button title="Call" variant="secondary" onPress={() => Linking.openURL(`tel:${supervisors.find((s) => s.id === to)!.phone}`)} />
            )}
          </View>
          <Muted>Messages are sent when you have internet.</Muted>
        </Card>

        {messages.length === 0 && <Muted>No messages yet.</Muted>}
        {messages.map((m) => {
          const mine = m.pending || m.sender_id === user?.id;
          return (
            <View key={m.id} style={[styles.card, { alignSelf: mine ? "flex-end" : "flex-start", maxWidth: "88%", backgroundColor: mine ? "#E7F3EC" : colors.card }]}>
              <Muted>{mine ? "You" : m.sender_name} · {new Date(m.sent_at).toLocaleString()}{m.pending ? " · waiting to send" : ""}</Muted>
              <Text style={{ fontSize: 16, color: colors.text }}>{m.body}</Text>
            </View>
          );
        })}
      </ScrollView>
    </KeyboardAvoidingView>
  );
}
