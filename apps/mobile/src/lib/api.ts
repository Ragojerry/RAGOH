/** HTTP client for the RAGOH API with automatic token refresh. */
import Constants from "expo-constants";
import { HttpError } from "@ragoh/sync-core";
import { getMeta, setMeta } from "./db";
import { tokens } from "./secure";

const TIMEOUT_MS = 30_000;

export async function apiUrl(): Promise<string> {
  const saved = await getMeta("api_url");
  const fallback =
    process.env.EXPO_PUBLIC_API_URL ?? (Constants.expoConfig?.extra?.defaultApiUrl as string | undefined) ?? "";
  return (saved || fallback).replace(/\/+$/, "");
}

export async function setApiUrl(url: string): Promise<void> {
  await setMeta("api_url", url.trim().replace(/\/+$/, ""));
}

/** Raised when the refresh token is no longer valid: the user must sign in online again. */
export class SessionExpiredError extends Error {
  constructor() {
    super("Your session has expired. Sign in again to sync.");
  }
}

async function rawFetch(path: string, init: RequestInit & { timeoutMs?: number } = {}): Promise<Response> {
  const base = await apiUrl();
  if (!base) throw new Error("Server address is not set (Settings → Server)");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), init.timeoutMs ?? TIMEOUT_MS);
  try {
    return await fetch(`${base}${path}`, { ...init, signal: controller.signal });
  } catch (err) {
    if (controller.signal.aborted) throw new Error("The server took too long to answer");
    throw new Error("No connection to the server");
  } finally {
    clearTimeout(timer);
  }
}

async function errorFrom(res: Response): Promise<HttpError> {
  let msg = res.statusText || "Request failed";
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === "string") msg = body.detail;
    else if (Array.isArray(body.detail)) msg = "Some data was not accepted by the server";
  } catch {
    // not JSON
  }
  return new HttpError(res.status, msg);
}

let refreshing: Promise<void> | null = null;

async function refreshTokens(): Promise<void> {
  const refresh = await tokens.refresh();
  if (!refresh) throw new SessionExpiredError();
  const res = await rawFetch("/api/v1/auth/refresh", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refresh }),
  });
  if (res.status === 401) {
    await tokens.clear();
    throw new SessionExpiredError();
  }
  if (!res.ok) throw await errorFrom(res);
  const data = (await res.json()) as { access_token: string; refresh_token: string };
  await tokens.save(data.access_token, data.refresh_token);
}

/** Authenticated request; refreshes the access token once on 401. */
export async function api<T>(path: string, init: RequestInit & { timeoutMs?: number } = {}): Promise<T> {
  const attempt = async () => {
    const access = await tokens.access();
    if (!access) throw new SessionExpiredError();
    return rawFetch(path, { ...init, headers: { ...(init.headers ?? {}), Authorization: `Bearer ${access}` } });
  };
  let res = await attempt();
  if (res.status === 401) {
    refreshing = refreshing ?? refreshTokens().finally(() => (refreshing = null));
    await refreshing;
    res = await attempt();
  }
  if (!res.ok) throw await errorFrom(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export interface LoginResult {
  access_token: string;
  refresh_token: string;
  user: { id: string; username: string; full_name: string; role: string; language: string };
}

export async function login(username: string, password: string, deviceId: string): Promise<LoginResult> {
  const res = await rawFetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: username.trim(), password, device_id: deviceId }),
  });
  if (!res.ok) throw await errorFrom(res);
  return (await res.json()) as LoginResult;
}

export async function logoutRemote(): Promise<void> {
  const refresh = await tokens.refresh();
  if (!refresh) return;
  try {
    await rawFetch("/api/v1/auth/logout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
      timeoutMs: 8000,
    });
  } catch {
    // offline logout is fine; the token simply expires
  }
}
