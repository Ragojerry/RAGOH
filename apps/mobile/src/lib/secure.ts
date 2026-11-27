/** Small wrapper over the Android Keystore-backed secure store. */
import * as Crypto from "expo-crypto";
import * as SecureStore from "expo-secure-store";

const K = {
  dbKey: "ragoh.db_key",
  deviceId: "ragoh.device_id",
  access: "ragoh.access_token",
  refresh: "ragoh.refresh_token",
  pin: "ragoh.pin",
  user: "ragoh.user",
} as const;

async function get(key: string): Promise<string | null> {
  return SecureStore.getItemAsync(key);
}
async function set(key: string, value: string | null): Promise<void> {
  if (value === null) await SecureStore.deleteItemAsync(key);
  else await SecureStore.setItemAsync(key, value);
}

const hex = (bytes: Uint8Array) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");

/** Encryption key for the local database, created once per install. */
export async function databaseKey(): Promise<string> {
  let key = await get(K.dbKey);
  if (!key) {
    key = hex(await Crypto.getRandomBytesAsync(32));
    await set(K.dbKey, key);
  }
  return key;
}

export async function deviceId(): Promise<string> {
  let id = await get(K.deviceId);
  if (!id) {
    id = Crypto.randomUUID();
    await set(K.deviceId, id);
  }
  return id;
}

export const tokens = {
  access: () => get(K.access),
  refresh: () => get(K.refresh),
  async save(access: string, refresh: string) {
    await set(K.access, access);
    await set(K.refresh, refresh);
  },
  async clear() {
    await set(K.access, null);
    await set(K.refresh, null);
  },
};

export const cachedUser = {
  async get<T>(): Promise<T | null> {
    const v = await get(K.user);
    return v ? (JSON.parse(v) as T) : null;
  },
  set: (u: unknown) => set(K.user, u === null ? null : JSON.stringify(u)),
};

// --- PIN (offline unlock) ----------------------------------------------------
// Stored as salt + many rounds of SHA-256, so the PIN can't be read back.
async function pinHash(pin: string, salt: string): Promise<string> {
  let h = `${salt}:${pin}`;
  for (let i = 0; i < 2000; i++) {
    h = await Crypto.digestStringAsync(Crypto.CryptoDigestAlgorithm.SHA256, h);
  }
  return h;
}

export const pin = {
  async isSet(userId: string): Promise<boolean> {
    const v = await get(K.pin);
    return !!v && (JSON.parse(v) as { userId: string }).userId === userId;
  },
  async save(userId: string, value: string): Promise<void> {
    const salt = hex(await Crypto.getRandomBytesAsync(16));
    await set(K.pin, JSON.stringify({ userId, salt, hash: await pinHash(value, salt), fails: 0 }));
  },
  /** Returns remaining attempts on failure, or "ok". After 5 failures the PIN is wiped. */
  async check(value: string): Promise<"ok" | number> {
    const raw = await get(K.pin);
    if (!raw) return 0;
    const rec = JSON.parse(raw) as { userId: string; salt: string; hash: string; fails: number };
    if ((await pinHash(value, rec.salt)) === rec.hash) {
      await set(K.pin, JSON.stringify({ ...rec, fails: 0 }));
      return "ok";
    }
    rec.fails += 1;
    if (rec.fails >= 5) {
      await set(K.pin, null); // must sign in online again
      return 0;
    }
    await set(K.pin, JSON.stringify(rec));
    return 5 - rec.fails;
  },
  clear: () => set(K.pin, null),
};
