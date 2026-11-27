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

// --- Mobile data used by the app (for the battery/data report) ------------------------
// Upload: exact body size plus ~600 bytes of headers per request. Download: the size the
// server reports, or the uncompressed size when it doesn't (an upper bound — responses
// are gzip-compressed in transit).
const HEADER_BYTES = 600;
const usage = { up: 0, down: 0, dirty: 0 };
let usageLoaded = false;

async function loadUsage(): Promise<void> {
  if (usageLoaded) return;
  usageLoaded = true;
  usage.up += Number((await getMeta("usage_bytes_up")) ?? 0);
  usage.down += Number((await getMeta("usage_bytes_down")) ?? 0);
}

function bodySize(body: unknown): number {
  if (typeof body === "string") return body.length;
  if (body instanceof Uint8Array) return body.byteLength;
  if (body instanceof ArrayBuffer) return body.byteLength;
  return 0; // FormData (photos) is counted by the caller
}

export function countBytes(up: number, down: number): void {
  usage.up += up;
  usage.down += down;
  if (++usage.dirty >= 10) void flushUsage();
}

export async function flushUsage(): Promise<void> {
  await loadUsage();
  usage.dirty = 0;
  await setMeta("usage_bytes_up", String(Math.round(usage.up)));
  await setMeta("usage_bytes_down", String(Math.round(usage.down)));
}

/** Total bytes sent/received by the app since install. */
export async function usageTotals(): Promise<{ up: number; down: number }> {
  await loadUsage();
  return { up: usage.up, down: usage.down };
}

async function rawFetch(path: string, init: RequestInit & { timeoutMs?: number } = {}): Promise<Response> {
  const base = await apiUrl();
  if (!base) throw new Error("Server address is not set (Settings → Server)");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), init.timeoutMs ?? TIMEOUT_MS);
  try {
    const res = await fetch(`${base}${path}`, { ...init, signal: controller.signal });
    countBytes(HEADER_BYTES + bodySize(init.body), HEADER_BYTES / 2 + Number(res.headers.get("content-length") ?? 0));
    return res;
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
  const text = await res.text();
  if (!res.headers.get("content-length")) countBytes(0, text.length);
  return JSON.parse(text) as T;
}

/** Authenticated request returning the raw response (caller handles status, e.g. 304). */
export async function apiResponse(path: string, init: RequestInit & { timeoutMs?: number } = {}): Promise<Response> {
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
  if (!res.ok && res.status !== 304) throw await errorFrom(res);
  return res;
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
