/**
 * Fill a form. Answers autosave as a draft on every change, so nothing is lost if the
 * phone dies or the app is closed. "Finish" validates, records GPS + timing metadata,
 * queues the submission for sync and marks the visit as done.
 */
import { router, useLocalSearchParams, useNavigation } from "expo-router";
import { useEffect, useMemo, useRef, useState } from "react";
import { Alert, KeyboardAvoidingView, ScrollView, Text, View } from "react-native";
import {
  addRepeat,
  choicesFor,
  computeForm,
  finalizeAnswers,
  getAnswer,
  interpolate,
  removeRepeat,
  setAnswer,
  validateForm,
  type AnswerValue,
  type NodeState,
} from "@ragoh/form-engine";
import { Question } from "@/components/Question";
import { Button, Card, ErrorText, H1, Muted, colors, styles } from "@/components/ui";
import { currentLocation } from "@/lib/location";
import { activeShift, finishSubmission, findDraft, getAssignment, getForm, newId, saveDraft, type Draft } from "@/lib/repo";
import { beginShift } from "@/lib/tracking";
import { useSession } from "@/lib/session";
import type { Assignment, FormInfo } from "@/lib/types";

export default function FormScreen() {
  const { versionId, assignmentId, prefill } = useLocalSearchParams<{ versionId: string; assignmentId?: string; prefill?: string }>();
  const navigation = useNavigation();
  const { runSync } = useSession();
  const [form, setForm] = useState<FormInfo | null>(null);
  const [assignment, setAssignment] = useState<Assignment | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    (async () => {
      const f = await getForm(versionId);
      if (!f) {
        Alert.alert("Form not available", "Sync to download the latest forms.");
        router.back();
        return;
      }
      setForm(f);
      navigation.setOptions({ title: f.name });
      const a = assignmentId ? await getAssignment(assignmentId) : null;
      setAssignment(a);
      // Answers filled in by the screen that opened the form (e.g. which collector a spot check is about).
      let preset: Record<string, string> = {};
      try {
        preset = prefill ? (JSON.parse(prefill) as Record<string, string>) : {};
      } catch {
        preset = {};
      }
      const existing = await findDraft(f.version_id, assignmentId ?? null);
      const sameSubject = existing && Object.entries(preset).every(([k, v]) => existing.answers[k] === v);
      if (existing && sameSubject) {
        setDraft(existing);
      } else {
        const d: Draft = {
          id: newId(),
          formVersionId: f.version_id,
          assignmentId: assignmentId ?? null,
          startedAt: new Date().toISOString(),
          startLocation: null,
          answers: { ...preset },
          media: {},
        };
        setDraft(d);
        await saveDraft(d);
        // Location at the start of the interview (in the background — don't block typing).
        currentLocation().then((loc) => setDraft((cur) => (cur ? { ...cur, startLocation: loc } : cur)));
      }
    })();
  }, [versionId, assignmentId, prefill, navigation]);

  // Debounced autosave.
  useEffect(() => {
    if (!draft) return;
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => saveDraft(draft), 400);
    return () => { if (saveTimer.current) clearTimeout(saveTimer.current); };
  }, [draft]);

  const state = useMemo(() => (form && draft ? computeForm(form.schema, draft.answers) : null), [form, draft]);

  if (!form || !draft || !state) return <View style={styles.screen}><Muted>Loading…</Muted></View>;
  const schema = form.schema;

  const update = (path: string, value: AnswerValue | undefined) => {
    setDraft((d) => (d ? { ...d, answers: setAnswer(d.answers, path, value) } : d));
    if (errors[path]) setErrors(({ [path]: _, ...rest }) => rest);
  };
  const updatePhoto = (path: string, uri: string | undefined) =>
    setDraft((d) => {
      if (!d) return d;
      const media = { ...d.media };
      if (uri) media[path] = uri;
      else delete media[path];
      return { ...d, media };
    });

  const finish = async () => {
    const errs = validateForm(schema, state);
    setErrors(errs);
    const n = Object.keys(errs).length;
    if (n) {
      Alert.alert("Check your answers", `${n} question${n > 1 ? "s need" : " needs"} attention (shown in red).`);
      return;
    }
    setSaving(true);
    try {
      let shift = await activeShift();
      const endLocation = await currentLocation();
      if (!shift) shift = (await beginShift(endLocation)).shift; // working without a shift: start one automatically
      // Photos are stored by top-level field name; nested repeat photos keep their path.
      const answers = finalizeAnswers(schema, draft.answers);
      const media = Object.fromEntries(Object.entries(draft.media).filter(([path]) => getAnswer(answers, path) !== undefined));
      await finishSubmission({
        draft: { ...draft, media },
        form,
        answers,
        endLocation,
        shiftId: shift.id,
        pointName: assignment?.visit_point.name ?? null,
        visitPointId: assignment?.visit_point.id ?? null,
      });
      runSync();
      router.back();
    } catch (e) {
      Alert.alert("Could not save", e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const renderNodes = (nodes: NodeState[]): React.ReactNode[] =>
    nodes.flatMap((node): React.ReactNode[] => {
      if (!node.relevant || node.field.type === "calculate") return [];
      const f = node.field;
      if (f.type === "group") {
        return [
          f.label ? <Text key={`${node.path}#h`} style={{ fontSize: 19, fontWeight: "800", color: colors.primary, marginTop: 8 }}>{interpolate(f.label, schema, state.values, node.path)}</Text> : null,
          ...renderNodes(node.children ?? []),
        ];
      }
      if (f.type === "repeat") {
        const label = f.label ?? f.name;
        return [
          <Card key={node.path}>
            <Text style={{ fontSize: 18, fontWeight: "800", color: colors.text }}>{label}</Text>
            {(node.instances ?? []).map((inst, i) => (
              <View key={`${node.path}[${i}]`} style={{ gap: 8, borderTopWidth: 1, borderColor: colors.border, paddingTop: 8 }}>
                <View style={[styles.row, { justifyContent: "space-between" }]}>
                  <Muted>{label} {i + 1}</Muted>
                  <Button title="Remove" variant="secondary" style={{ minHeight: 36 }} onPress={() =>
                    Alert.alert("Remove?", `Remove ${label} ${i + 1} and its answers?`, [
                      { text: "Cancel", style: "cancel" },
                      { text: "Remove", style: "destructive", onPress: () => setDraft((d) => (d ? { ...d, answers: removeRepeat(d.answers, node.path, i) } : d)) },
                    ])}
                  />
                </View>
                {renderNodes(inst)}
              </View>
            ))}
            <Button title={`Add ${label}`} variant="secondary" onPress={() => setDraft((d) => (d ? { ...d, answers: addRepeat(d.answers, node.path) } : d))} />
          </Card>,
        ];
      }
      return [
        <Question
          key={node.path}
          node={node}
          label={interpolate(f.label ?? f.name, schema, state.values, node.path)}
          hint={f.hint ? interpolate(f.hint, schema, state.values, node.path) : undefined}
          value={getAnswer(state.values, node.path)}
          choices={f.choices ? choicesFor(schema, state, node) : []}
          error={errors[node.path]}
          photoUri={draft.media[node.path]}
          onChange={(v) => update(node.path, v)}
          onPhoto={(uri) => updatePhoto(node.path, uri)}
        />,
      ];
    });

  return (
    <KeyboardAvoidingView behavior="padding" style={{ flex: 1 }}>
      <ScrollView style={styles.screen} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
        <H1>{form.name}</H1>
        {assignment && <Muted>{assignment.visit_point.name} · {assignment.visit_point.external_id}</Muted>}
        <Muted>Answers save automatically. Started {new Date(draft.startedAt).toLocaleTimeString()}.</Muted>
        {renderNodes(state.nodes)}
        {Object.keys(errors).length > 0 && <ErrorText>Some answers need attention (in red).</ErrorText>}
        <Button title="Finish and save" onPress={finish} loading={saving} />
        <View style={{ height: 40 }} />
      </ScrollView>
    </KeyboardAvoidingView>
  );
}
