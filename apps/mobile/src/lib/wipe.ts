/**
 * Remote erase of a lost or stolen phone. When a supervisor blocks this phone with
 * "erase", the server answers the (sign-in free) status check with wipe=true; the app
 * then deletes all local data — surveys, GPS, photos, offline map, keys and sign-in —
 * and reports back that it is done.
 */
import { Directory, Paths } from "expo-file-system";
import * as SecureStore from "expo-secure-store";
import { apiUrl } from "./api";
import { clearUserData, getDb, setMeta } from "./db";
import { deletePack } from "./offlineMap";
import { cachedUser, deviceId, pin, tokens } from "./secure";

async function status(wiped = false): Promise<{ blocked: boolean; wipe: boolean } | null> {
  const base = await apiUrl();
  if (!base) return null;
  try {
    const res = await fetch(`${base}/api/v1/devices/status`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ device_id: await deviceId(), wiped }),
    });
    return res.ok ? ((await res.json()) as { blocked: boolean; wipe: boolean }) : null;
  } catch {
    return null;
  }
}

export async function eraseEverything(): Promise<void> {
  const user = await cachedUser.get<{ id: string }>();
  await clearUserData();
  const db = await getDb();
  await db.execAsync("DELETE FROM meta; VACUUM;").catch(() => undefined);
  try {
    const media = new Directory(Paths.document, "media");
    if (media.exists) media.delete();
  } catch {
    // nothing to delete
  }
  await deletePack().catch(() => undefined);
  await tokens.clear();
  await pin.clear();
  await cachedUser.set(null);
  if (user) await SecureStore.deleteItemAsync(`ragoh.relay_key.${user.id.replace(/-/g, "")}`).catch(() => undefined);
  await setMeta("wiped_notice", "1");
}

/** Check with the server; erase if told to. Returns true when the phone was erased. */
export async function checkRemoteWipe(): Promise<boolean> {
  const s = await status();
  if (!s?.wipe) return false;
  await eraseEverything();
  await status(true);
  return true;
}
