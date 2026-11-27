/**
 * API client. Tokens live in sessionStorage (cleared when the tab closes — the
 * dashboard shows personal tracking data, so a forgotten laptop should not stay
 * signed in). The access token is refreshed transparently when it expires.
 */
export interface User {
  id: string;
  username: string;
  full_name: string;
  role: "collector" | "supervisor" | "manager" | "admin";
}

const KEY = "ragoh.session";
interface Stored {
  access: string;
  refresh: string;
  user: User;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

let session: Stored | null = (() => {
  try {
    const raw = sessionStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Stored) : null;
  } catch {
    return null;
  }
})();
const listeners = new Set<(u: User | null) => void>();

function save(s: Stored | null): void {
  session = s;
  if (s) sessionStorage.setItem(KEY, JSON.stringify(s));
  else sessionStorage.removeItem(KEY);
  listeners.forEach((l) => l(s?.user ?? null));
}

export const currentUser = (): User | null => session?.user ?? null;
export function onAuthChange(l: (u: User | null) => void): () => void {
  listeners.add(l);
  return () => listeners.delete(l);
}

async function errorText(r: Response): Promise<string> {
  try {
    const body = (await r.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return body.detail.map((d: { msg?: string }) => d.msg).join("; ");
  } catch {
    // not JSON
  }
  return `${r.status} ${r.statusText}`;
}

export async function login(username: string, password: string): Promise<User> {
  const r = await fetch("/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!r.ok) throw new ApiError(r.status, await errorText(r));
  const t = (await r.json()) as { access_token: string; refresh_token: string; user: User };
  if (t.user.role === "collector") throw new ApiError(403, "The dashboard is for supervisors, managers and admins.");
  save({ access: t.access_token, refresh: t.refresh_token, user: t.user });
  return t.user;
}

let refreshing: Promise<boolean> | null = null;
async function refresh(): Promise<boolean> {
  if (!session) return false;
  refreshing ??= (async () => {
    const r = await fetch("/api/v1/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: session!.refresh }),
    });
    if (!r.ok) {
      save(null);
      return false;
    }
    const t = (await r.json()) as { access_token: string; refresh_token: string; user: User };
    save({ access: t.access_token, refresh: t.refresh_token, user: t.user });
    return true;
  })().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

export async function logout(): Promise<void> {
  if (session) {
    await fetch("/api/v1/auth/logout", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: session.refresh }),
    }).catch(() => undefined);
  }
  save(null);
}

async function raw(path: string, init: RequestInit = {}, retry = true): Promise<Response> {
  const headers = new Headers(init.headers);
  if (session) headers.set("Authorization", `Bearer ${session.access}`);
  const r = await fetch(`/api/v1${path}`, { ...init, headers });
  if (r.status === 401 && retry && (await refresh())) return raw(path, init, false);
  if (!r.ok) throw new ApiError(r.status, await errorText(r));
  return r;
}

export async function get<T>(path: string, params?: Record<string, string | number | boolean | null | undefined>): Promise<T> {
  return (await raw(withQuery(path, params))).json() as Promise<T>;
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await raw(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return (r.status === 204 ? undefined : await r.json()) as T;
}

export async function put<T>(path: string, body: unknown): Promise<T> {
  const r = await raw(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return (r.status === 204 ? undefined : await r.json()) as T;
}

/** Download a file (export) with the auth header, then hand it to the browser. */
export async function download(path: string, params: Record<string, string | null | undefined>): Promise<void> {
  const r = await raw(withQuery(path, params));
  const name = /filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") ?? "")?.[1] ?? "ragoh-export";
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

export function withQuery(path: string, params?: Record<string, string | number | boolean | null | undefined>): string {
  if (!params) return path;
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== "") q.set(k, String(v));
  const s = q.toString();
  return s ? `${path}?${s}` : path;
}
