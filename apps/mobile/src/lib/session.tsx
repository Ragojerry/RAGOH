/** App-wide session: who is signed in, PIN lock, and sync status. */
import { AppState } from "react-native";
import * as Network from "expo-network";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { SyncReport } from "@ragoh/sync-core";
import { login, logoutRemote } from "./api";
import { clearUserData, getMeta } from "./db";
import { outboxCounts } from "./repo";
import { cachedUser, deviceId, pin as pinStore, tokens } from "./secure";
import { friendlySyncError, onSync, syncNow } from "./sync";
import type { User } from "./types";

export type Phase = "loading" | "signedOut" | "locked" | "setPin" | "ready";

interface SyncStatus {
  running: boolean;
  pending: number;
  rejected: number;
  media: number;
  lastOkAt: string | null;
  error: string | null;
}

interface SessionValue {
  phase: Phase;
  user: User | null;
  sync: SyncStatus;
  signIn(username: string, password: string): Promise<void>;
  unlock(pin: string): Promise<"ok" | number>;
  savePin(pin: string): Promise<void>;
  signOut(): Promise<void>;
  runSync(): Promise<SyncReport | null>;
  refreshCounts(): Promise<void>;
}

const Ctx = createContext<SessionValue | null>(null);
const AUTO_SYNC_MS = 5 * 60_000;

export function SessionProvider({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [user, setUser] = useState<User | null>(null);
  const [sync, setSync] = useState<SyncStatus>({ running: false, pending: 0, rejected: 0, media: 0, lastOkAt: null, error: null });
  const phaseRef = useRef(phase);
  phaseRef.current = phase;

  const refreshCounts = useCallback(async () => {
    const c = await outboxCounts();
    const lastOkAt = await getMeta("last_sync_ok_at");
    setSync((s) => ({ ...s, ...c, lastOkAt }));
  }, []);

  const runSync = useCallback(async () => {
    setSync((s) => ({ ...s, running: true }));
    try {
      const r = await syncNow();
      setSync((s) => ({ ...s, error: friendlySyncError(r) }));
      return r;
    } catch (err) {
      setSync((s) => ({ ...s, error: err instanceof Error ? err.message : String(err) }));
      return null;
    } finally {
      setSync((s) => ({ ...s, running: false }));
      await refreshCounts();
    }
  }, [refreshCounts]);

  // Initial state from the secure store.
  useEffect(() => {
    (async () => {
      const u = await cachedUser.get<User>();
      if (u && (await tokens.refresh()) && (await pinStore.isSet(u.id))) {
        setUser(u);
        setPhase("locked");
      } else {
        setPhase("signedOut");
      }
    })();
  }, []);

  // Auto-sync: periodically, when the app returns to the foreground, and when the network comes back.
  useEffect(() => {
    if (phase !== "ready") return;
    refreshCounts();
    runSync();
    const timer = setInterval(() => phaseRef.current === "ready" && runSync(), AUTO_SYNC_MS);
    const appSub = AppState.addEventListener("change", (s) => s === "active" && runSync());
    const netSub = Network.addNetworkStateListener((st) => st.isInternetReachable && runSync());
    const unsub = onSync(() => refreshCounts());
    return () => {
      clearInterval(timer);
      appSub.remove();
      netSub.remove();
      unsub();
    };
  }, [phase, runSync, refreshCounts]);

  const signIn = useCallback(async (username: string, password: string) => {
    const result = await login(username, password, await deviceId());
    const previous = await cachedUser.get<User>();
    if (previous && previous.id !== result.user.id) {
      // Shared phone: never drop the previous user's unsynced work.
      if ((await outboxCounts()).pending > 0) {
        await syncNow().catch(() => null);
        if ((await outboxCounts()).pending > 0) {
          throw new Error(`${previous.full_name} still has unsynced work on this phone. Ask them to sign in and sync first.`);
        }
      }
      await clearUserData();
      await pinStore.clear();
    }
    await tokens.save(result.access_token, result.refresh_token);
    const u = result.user as User;
    await cachedUser.set(u);
    setUser(u);
    setPhase((await pinStore.isSet(u.id)) ? "ready" : "setPin");
  }, []);

  const unlock = useCallback(async (value: string) => {
    const r = await pinStore.check(value);
    if (r === "ok") setPhase("ready");
    else if (r === 0) setPhase("signedOut");
    return r;
  }, []);

  const savePin = useCallback(async (value: string) => {
    if (!user) return;
    await pinStore.save(user.id, value);
    setPhase("ready");
  }, [user]);

  const signOut = useCallback(async () => {
    await logoutRemote();
    await tokens.clear();
    setPhase("signedOut"); // local data stays until a different user signs in
  }, []);

  const value = useMemo<SessionValue>(
    () => ({ phase, user, sync, signIn, unlock, savePin, signOut, runSync, refreshCounts }),
    [phase, user, sync, signIn, unlock, savePin, signOut, runSync, refreshCounts],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession(): SessionValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSession outside SessionProvider");
  return v;
}
