/**
 * Translations. The English text is the key ("gettext style"): screens read naturally,
 * a missing translation falls back to English, and `pnpm i18n:extract` lists every
 * string for translators (src/i18n/strings.json). Placeholders: t("{n} visits", { n: 3 }).
 *
 * Add a language: create src/i18n/<code>.json mapping English → translation, add it to
 * CATALOGS and LANGUAGES below. Translations must be checked by a native speaker,
 * especially the privacy notice.
 */
import { useSyncExternalStore } from "react";
import ha from "../i18n/ha.json";
import ig from "../i18n/ig.json";
import pcm from "../i18n/pcm.json";
import yo from "../i18n/yo.json";

export const LANGUAGES = [
  { code: "en", name: "English" },
  { code: "ha", name: "Hausa" },
  { code: "yo", name: "Yorùbá" },
  { code: "ig", name: "Igbo" },
  { code: "pcm", name: "Naijá (Pidgin)" },
] as const;
export type Lang = (typeof LANGUAGES)[number]["code"];

const CATALOGS: Record<Lang, Record<string, string>> = { en: {}, ha, yo, ig, pcm };

let current: Lang = "en";
const listeners = new Set<() => void>();

export function setLanguage(lang: string): void {
  const next = (LANGUAGES.some((l) => l.code === lang) ? lang : "en") as Lang;
  if (next === current) return;
  current = next;
  listeners.forEach((l) => l());
}

export function language(): Lang {
  return current;
}

export function translate(lang: Lang, text: string, vars?: Record<string, string | number>): string {
  const s = CATALOGS[lang][text] || text;
  return vars ? s.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k]) : m)) : s;
}

/** Translate in the current language (for code outside React components). */
export function t(text: string, vars?: Record<string, string | number>): string {
  return translate(current, text, vars);
}

/** Hook: re-renders the component when the language changes. */
export function useT(): typeof t {
  useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => current,
  );
  return t;
}

/** How complete each language is, for Settings. */
export function coverage(all: string[]): Record<Lang, number> {
  const out = {} as Record<Lang, number>;
  for (const { code } of LANGUAGES) {
    out[code] = code === "en" || !all.length ? 100 : Math.round((100 * all.filter((s) => CATALOGS[code][s]).length) / all.length);
  }
  return out;
}
