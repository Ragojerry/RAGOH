/** Input widgets for each XLSForm question type. */
import * as ImageManipulator from "expo-image-manipulator";
import * as ImagePicker from "expo-image-picker";
import { Directory, File, Paths } from "expo-file-system";
import { useState } from "react";
import { Alert, Image, Pressable, Text, View } from "react-native";
import type { AnswerValue, Choice, GeoValue, NodeState } from "@ragoh/form-engine";
import { currentLocation } from "@/lib/location";
import { Button, ErrorText, Input, Muted, colors, styles } from "./ui";

interface Props {
  node: NodeState;
  label: string;
  hint?: string;
  value: AnswerValue | undefined;
  choices: Choice[];
  error?: string;
  photoUri?: string;
  onChange(value: AnswerValue | undefined): void;
  onPhoto(uri: string | undefined): void;
}

const pad = (n: number) => String(n).padStart(2, "0");
const today = () => { const d = new Date(); return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`; };
const nowTime = () => { const d = new Date(); return `${pad(d.getHours())}:${pad(d.getMinutes())}`; };

function Option({ selected, label, onPress, multi }: { selected: boolean; label: string; onPress: () => void; multi?: boolean }) {
  return (
    <Pressable
      onPress={onPress}
      accessibilityRole={multi ? "checkbox" : "radio"}
      accessibilityState={{ checked: selected }}
      style={[styles.row, { minHeight: 48, paddingHorizontal: 12, borderRadius: 10, borderWidth: 1.5, borderColor: selected ? colors.primary : colors.border, backgroundColor: selected ? "#E7F3EC" : "#fff" }]}
    >
      <View style={{ width: 22, height: 22, borderRadius: multi ? 4 : 11, borderWidth: 2, borderColor: colors.primary, alignItems: "center", justifyContent: "center" }}>
        {selected && <View style={{ width: 12, height: 12, borderRadius: multi ? 2 : 6, backgroundColor: colors.primary }} />}
      </View>
      <Text style={{ fontSize: 17, color: colors.text, flex: 1 }}>{label}</Text>
    </Pressable>
  );
}

async function takePhoto(): Promise<string | undefined> {
  const perm = await ImagePicker.requestCameraPermissionsAsync();
  if (!perm.granted) {
    Alert.alert("Camera needed", "Allow RAGOH to use the camera to answer photo questions.");
    return undefined;
  }
  const res = await ImagePicker.launchCameraAsync({ quality: 0.8, mediaTypes: ["images"] });
  if (res.canceled || !res.assets[0]) return undefined;
  // Shrink to save data, then keep a copy in the app's own storage.
  const small = await ImageManipulator.manipulateAsync(res.assets[0].uri, [{ resize: { width: 1600 } }], {
    compress: 0.6,
    format: ImageManipulator.SaveFormat.JPEG,
  });
  const dir = new Directory(Paths.document, "media");
  if (!dir.exists) dir.create({ intermediates: true });
  const dest = new File(dir, `${Date.now()}.jpg`);
  await new File(small.uri).copy(dest);
  return dest.uri;
}

export function Question({ node, label, hint, value, choices, error, photoUri, onChange, onPhoto }: Props) {
  const f = node.field;
  const [busy, setBusy] = useState(false);
  const str = value === undefined || value === null ? "" : typeof value === "object" ? "" : String(value);
  const readOnly = node.readOnly;

  let body: React.ReactNode = null;
  switch (f.type) {
    case "note":
      return (
        <View style={[styles.card, { backgroundColor: "#F0F7F3" }]}>
          <Text style={{ fontSize: 16, color: colors.text }}>{label}</Text>
        </View>
      );
    case "text":
    case "barcode": {
      const multiline = f.appearance?.includes("multiline");
      body = (
        <Input
          value={str}
          editable={!readOnly}
          onChangeText={(t) => onChange(t === "" ? undefined : t)}
          multiline={multiline}
          style={multiline ? { minHeight: 100, textAlignVertical: "top", paddingTop: 10 } : undefined}
        />
      );
      break;
    }
    case "integer":
    case "decimal":
      body = (
        <Input
          value={str}
          editable={!readOnly}
          keyboardType={f.type === "integer" ? "number-pad" : "decimal-pad"}
          onChangeText={(t) => {
            const clean = t.replace(",", ".");
            if (clean === "") return onChange(undefined);
            const n = Number(clean);
            onChange(Number.isNaN(n) || clean.endsWith(".") ? clean : n);
          }}
        />
      );
      break;
    case "select_one":
      body = (
        <View style={{ gap: 6 }}>
          {choices.map((c) => (
            <Option key={c.name} label={c.label} selected={str === c.name} onPress={() => !readOnly && onChange(str === c.name ? undefined : c.name)} />
          ))}
        </View>
      );
      break;
    case "select_multiple": {
      const selected = str.split(" ").filter(Boolean);
      body = (
        <View style={{ gap: 6 }}>
          {choices.map((c) => (
            <Option
              key={c.name}
              multi
              label={c.label}
              selected={selected.includes(c.name)}
              onPress={() => {
                if (readOnly) return;
                const next = selected.includes(c.name) ? selected.filter((s) => s !== c.name) : [...selected, c.name];
                onChange(next.length ? next.join(" ") : undefined);
              }}
            />
          ))}
        </View>
      );
      break;
    }
    case "date":
    case "time":
    case "datetime":
      body = (
        <View style={styles.row}>
          <Input
            value={str}
            onChangeText={(t) => onChange(t || undefined)}
            placeholder={f.type === "date" ? "YYYY-MM-DD" : f.type === "time" ? "HH:MM" : "YYYY-MM-DD HH:MM"}
            style={{ flex: 1 }}
          />
          <Button
            title={f.type === "time" ? "Now" : "Today"}
            variant="secondary"
            onPress={() => onChange(f.type === "date" ? today() : f.type === "time" ? nowTime() : `${today()} ${nowTime()}`)}
          />
        </View>
      );
      break;
    case "geopoint": {
      const g = value as GeoValue | undefined;
      body = (
        <View style={{ gap: 6 }}>
          {g?.lat !== undefined ? <Muted>{g.lat.toFixed(6)}, {g.lon.toFixed(6)} (±{Math.round(g.accuracy_m ?? 0)} m)</Muted> : <Muted>No location captured yet.</Muted>}
          <Button
            title={g ? "Capture again" : "Capture location"}
            variant="secondary"
            loading={busy}
            onPress={async () => {
              setBusy(true);
              const loc = await currentLocation(20_000);
              setBusy(false);
              if (!loc) Alert.alert("No GPS fix", "Move outside or near a window and try again.");
              else onChange(loc);
            }}
          />
        </View>
      );
      break;
    }
    case "image":
      body = (
        <View style={{ gap: 6 }}>
          {photoUri && <Image source={{ uri: photoUri }} style={{ width: "100%", height: 200, borderRadius: 10 }} resizeMode="cover" />}
          <View style={styles.row}>
            <Button
              title={photoUri ? "Retake photo" : "Take photo"}
              variant="secondary"
              loading={busy}
              style={{ flex: 1 }}
              onPress={async () => {
                setBusy(true);
                try {
                  const uri = await takePhoto();
                  if (uri) {
                    onPhoto(uri);
                    onChange(uri.split("/").pop());
                  }
                } finally {
                  setBusy(false);
                }
              }}
            />
            {photoUri && <Button title="Remove" variant="secondary" onPress={() => { onPhoto(undefined); onChange(undefined); }} />}
          </View>
        </View>
      );
      break;
    case "acknowledge":
      body = <Option multi label="OK" selected={str === "OK"} onPress={() => onChange(str === "OK" ? undefined : "OK")} />;
      break;
    default:
      body = <Muted>Unsupported question type: {f.type}</Muted>;
  }

  return (
    <View style={[styles.card, error ? { borderColor: colors.danger, borderWidth: 2 } : null]}>
      <Text style={{ fontSize: 17, fontWeight: "600", color: colors.text }}>
        {label}
        {node.required ? <Text style={{ color: colors.danger }}> *</Text> : null}
      </Text>
      {hint ? <Muted>{hint}</Muted> : null}
      {body}
      <ErrorText>{error}</ErrorText>
    </View>
  );
}
