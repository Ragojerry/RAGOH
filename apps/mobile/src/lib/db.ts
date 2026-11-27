/**
 * Encrypted local database (SQLCipher). Everything the collector does is written here
 * first, so the app works for days without a connection.
 */
import * as SQLite from "expo-sqlite";
import { databaseKey } from "./secure";

let dbPromise: Promise<SQLite.SQLiteDatabase> | null = null;

const MIGRATIONS: string[] = [
  `
  CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
  CREATE TABLE IF NOT EXISTS campaigns (id TEXT PRIMARY KEY, json TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS forms (
    version_id TEXT PRIMARY KEY, form_id TEXT NOT NULL, campaign_id TEXT NOT NULL,
    kind TEXT NOT NULL, name TEXT NOT NULL, json TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS assignments (
    id TEXT PRIMARY KEY, status TEXT NOT NULL, sort INTEGER NOT NULL, json TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS shifts (
    id TEXT PRIMARY KEY, started_at TEXT NOT NULL, ended_at TEXT, json TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY, form_version_id TEXT NOT NULL, assignment_id TEXT,
    started_at TEXT NOT NULL, updated_at TEXT NOT NULL, json TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS submissions (
    id TEXT PRIMARY KEY, form_version_id TEXT NOT NULL, form_name TEXT NOT NULL,
    assignment_id TEXT, point_name TEXT, ended_at TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS outbox (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, state TEXT NOT NULL DEFAULT 'pending', error TEXT
  );
  CREATE INDEX IF NOT EXISTS outbox_state ON outbox(state, created_at);
  CREATE TABLE IF NOT EXISTS media (
    id TEXT PRIMARY KEY, submission_id TEXT NOT NULL, field_name TEXT NOT NULL,
    uri TEXT NOT NULL, mime TEXT NOT NULL, uploaded INTEGER NOT NULL DEFAULT 0, error TEXT
  );
  `,
];

async function open(): Promise<SQLite.SQLiteDatabase> {
  const db = await SQLite.openDatabaseAsync("ragoh.db");
  // Must be the first statement on an SQLCipher database.
  await db.execAsync(`PRAGMA key = '${await databaseKey()}';`);
  await db.execAsync("PRAGMA journal_mode = WAL; PRAGMA foreign_keys = ON;");
  const row = await db.getFirstAsync<{ user_version: number }>("PRAGMA user_version");
  const current = row?.user_version ?? 0;
  for (let v = current; v < MIGRATIONS.length; v++) {
    await db.withTransactionAsync(async () => {
      await db.execAsync(MIGRATIONS[v]!);
      await db.execAsync(`PRAGMA user_version = ${v + 1}`);
    });
  }
  return db;
}

export function getDb(): Promise<SQLite.SQLiteDatabase> {
  if (!dbPromise) dbPromise = open();
  return dbPromise;
}

export async function getMeta(key: string): Promise<string | null> {
  const db = await getDb();
  const row = await db.getFirstAsync<{ value: string }>("SELECT value FROM meta WHERE key = ?", [key]);
  return row?.value ?? null;
}

export async function setMeta(key: string, value: string | null): Promise<void> {
  const db = await getDb();
  if (value === null) await db.runAsync("DELETE FROM meta WHERE key = ?", [key]);
  else await db.runAsync("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", [key, value]);
}

/** Remove one user's working data (used when a different user signs in on a shared phone). */
export async function clearUserData(): Promise<void> {
  const db = await getDb();
  await db.execAsync(
    "DELETE FROM campaigns; DELETE FROM forms; DELETE FROM assignments; DELETE FROM shifts; " +
      "DELETE FROM drafts; DELETE FROM submissions; DELETE FROM outbox; DELETE FROM media; " +
      "DELETE FROM meta WHERE key NOT IN ('api_url');",
  );
}
