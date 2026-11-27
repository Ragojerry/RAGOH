// Lists every t("…") string in the app → src/i18n/strings.json (hand this to translators),
// and reports how many are missing per language. Usage: pnpm i18n:extract [--check]
import { readFileSync, readdirSync, statSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const root = new URL("../src", import.meta.url).pathname;
const files = [];
(function walk(d) {
  for (const f of readdirSync(d)) {
    const p = join(d, f);
    if (statSync(p).isDirectory()) walk(p);
    else if (/\.(ts|tsx)$/.test(f)) files.push(p);
  }
})(root);

const re = /\bt\(\s*"((?:[^"\\]|\\.)*)"/g;
const strings = new Set();
for (const f of files) {
  for (const m of readFileSync(f, "utf8").matchAll(re)) strings.add(JSON.parse(`"${m[1]}"`));
}
const all = [...strings].sort();
writeFileSync(join(root, "i18n", "strings.json"), JSON.stringify(all, null, 2) + "\n");
console.log(`${all.length} strings → src/i18n/strings.json`);
for (const lang of ["ha", "yo", "ig", "pcm"]) {
  const cat = JSON.parse(readFileSync(join(root, "i18n", `${lang}.json`), "utf8"));
  const missing = all.filter((s) => !cat[s]).length;
  const stale = Object.keys(cat).filter((k) => !strings.has(k)).length;
  console.log(`${lang}: ${all.length - missing}/${all.length} translated${stale ? `, ${stale} no longer used` : ""}`);
}
