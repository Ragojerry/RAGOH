/** App-wide session: who is signed in, PIN lock, and sync status. */
import { AppState } from "react-native";
import * as Network from "expo-network";
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import type { SyncReport } from "@ragoh/sync-core";
import { login, logoutRemote } from "./api";
import { clearUserData, getMeta, setMeta } from "./db";
import { outboxCounts, pruneOutbox } from "./repo";
import { ensureRelayKey, heartbeat, listenForTaps, registerPush } from "./live";
import { ensureTracking } from "./tracking";
import { hasConsented, recordConsent, refreshNotice, sendPendingConsent } from "./consent";
import { setLanguage } from "./i18n";
import { checkRemoteWipe } from "./wipe";
import { cachedUser, deviceId, pin as pinStore, tokens } from "./secure";
import { friendlySyncError, onSync, syncNow } from "./sync";
import type { User } from "./types";

export type Phase = "loading" | "signedOut" | "locked" | "setPin" | "privacy" | "ready";

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
  acceptPrivacy(): Promise<void>;
  /** Set when a supervisor erased this phone remotely (shown on the sign-in screen). */
  erased: boolean;
  runSync(): Promise<SyncReport | null>;
  refreshCounts(): Promise<void>;
}

const Ctx = createContext<SessionValue | null>(null);
const AUTO_SYNC_MS = 5 * 60_000;

export function SessionProvider({ children }: { children: ReactNode }) {
  const [phase, setPhase] = useState<Phase>("loading");
  const [user, setUser] = useState<User | null>(null);
  const [sync, setSync] = useState<SyncStatus>({ running: false, pending: 0, rejected: 0, media: 0, lastOkAt: null, error: null });
  const [erased, setErased] = useState(false);
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
      if (r?.blocked && (await checkRemoteWipe())) {
        setErased(true);
        setUser(null);
        setPhase("signedOut");
        return r;
      }
      await pruneOutbox().catch(() => undefined);
      if (r && !r.error) {
        await sendPendingConsent();
        const u = await cachedUser.get<User>();
        if (u) await ensureRelayKey(u.id);
        registerPush().catch(() => undefined);
        heartbeat().catch(() => undefined);
      }
      return r;
    } catch (err) {
      setSync((s) => ({ ...s, error: err instanceof Error ? err.message : String(err) }));
      // A revoked sign-in may mean the phone was reported lost: ask if it must be erased.
      if (await checkRemoteWipe()) {
        setErased(true);
        setUser(null);
        setPhase("signedOut");
      }
      return null;
    } finally {
      setSync((s) => ({ ...s, running: false }));
      await refreshCounts();
    }
  }, [refreshCounts]);

  // Initial state from the secure store.
  useEffect(() => {
    (async () => {
      if (await checkRemoteWipe()) setErased(true);
      if ((await getMeta("wiped_notice")) === "1") setErased(true);
      setLanguage((await getMeta("language")) ?? "en");
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
    ensureTracking().catch(() => undefined); // restart tracking if the app was killed mid-shift
    runSync();
    const timer = setInterval(() => phaseRef.current === "ready" && runSync(), AUTO_SYNC_MS);
    const appSub = AppState.addEventListener("change", (s) => {
      if (s !== "active") return;
      ensureTracking().catch(() => undefined);
      heartbeat(true).catch(() => undefined);
      runSync();
    });
    const netSub = Network.addNetworkStateListener((st) => st.isInternetReachable && runSync());
    const unsub = onSync(() => refreshCounts());
    const untap = listenForTaps();
    return () => {
      untap();
      clearInterval(timer);
      appSub.remove();
      netSub.remove();
      unsub();
    };
  }, [phase, runSync, refreshCounts]);

  /** After PIN set/unlock: the privacy notice must be accepted (again, if it changed). */
  const afterUnlock = useCallback(async () => {
    const needs = await refreshNotice(); // null when offline
    setPhase(needs === true || (needs === null && !(await hasConsented())) ? "privacy" : "ready");
  }, []);

  const acceptPrivacy = useCallback(async () => {
    await recordConsent();
    setPhase("ready");
  }, []);

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
    await setMeta("wiped_notice", null);
    setErased(false);
    if (!(await getMeta("language"))) setLanguage(u.language);
    setUser(u);
    if (await pinStore.isSet(u.id)) await afterUnlock();
    else setPhase("setPin");
  }, [afterUnlock]);

  const unlock = useCallback(async (value: string) => {
    const r = await pinStore.check(value);
    if (r === "ok") await afterUnlock();
    else if (r === 0) setPhase("signedOut");
    return r;
  }, [afterUnlock]);

  const savePin = useCallback(async (value: string) => {
    if (!user) return;
    await pinStore.save(user.id, value);
    await afterUnlock();
  }, [user, afterUnlock]);

  const signOut = useCallback(async () => {
    await logoutRemote();
    await tokens.clear();
    setPhase("signedOut"); // local data stays until a different user signs in
  }, []);

  const value = useMemo<SessionValue>(
    () => ({ phase, user, sync, signIn, unlock, savePin, signOut, runSync, refreshCounts, acceptPrivacy, erased }),
    [phase, user, sync, signIn, unlock, savePin, signOut, runSync, refreshCounts, acceptPrivacy, erased],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSession(): SessionValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useSession outside SessionProvider");
  return v;
}
