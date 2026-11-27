import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { coverage, setLanguage, t, translate } from "../src/lib/i18n";

const dir = new URL("../src/i18n/", import.meta.url).pathname;
const strings = JSON.parse(readFileSync(`${dir}strings.json`, "utf8")) as string[];
const holes = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(",");

describe("translations", () => {
  it("fall back to English and fill placeholders", () => {
    setLanguage("ha");
    expect(t("Wrong PIN. {n} attempts left.", { n: 2 })).toBe("Wrong PIN. 2 attempts left.");
    expect(translate("en", "Hello, {name}", { name: "Amina" })).toBe("Hello, Amina");
    setLanguage("xx"); // unknown → English
    expect(t("Sync now")).toBe("Sync now");
  });

  it("every catalog keeps the English placeholders and only known strings", () => {
    for (const lang of ["ha", "yo", "ig", "pcm"]) {
      const cat = JSON.parse(readFileSync(`${dir}${lang}.json`, "utf8")) as Record<string, string>;
      for (const [en, tr] of Object.entries(cat)) {
        expect(strings, `${lang}: "${en}" is not used in the app`).toContain(en);
        if (tr) expect(holes(tr), `${lang}: placeholders differ in "${en}"`).toBe(holes(en));
      }
    }
  });

  it("strings.json is up to date (run pnpm i18n:extract)", () => {
    expect(strings).toContain("I have read this and agree");
    expect(strings).toContain("Start shift");
    expect(coverage(strings).en).toBe(100);
  });
});
