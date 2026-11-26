/**
 * Form state: which questions are visible, calculated values, validation.
 *
 * Paths address answers: "hh_size", "kids[0].age" (repeat instance 0).
 * ${name} references resolve to the nearest field with that name: inside a repeat
 * instance, its siblings win; otherwise the top-level answer. A reference from outside a
 * repeat to a field inside it returns all instances' values (for count() / sum()).
 */

import { evaluate, toBool, toStr, type Value } from "./expr";
import type { AnswerValue, Answers, Choice, Field, FormSchema, GeoValue, RepeatInstance } from "./types";

export interface NodeState {
  path: string;
  field: Field;
  relevant: boolean;
  required: boolean;
  readOnly: boolean;
  /** Present for repeats: one entry per instance, each a list of child states. */
  instances?: NodeState[][];
  children?: NodeState[];
}

export interface FormState {
  /** Answers with calculations applied. */
  values: Answers;
  nodes: NodeState[];
  /** Flat list of visible, answerable leaf questions in display order. */
  visible: NodeState[];
}

const LEAF_INPUTS = new Set([
  "text", "integer", "decimal", "select_one", "select_multiple", "date", "time", "datetime",
  "geopoint", "image", "barcode", "acknowledge",
]);

/** Answers are plain JSON; this also works on Hermes (React Native), unlike structuredClone. */
const clone = <T>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

interface Scope {
  instance: RepeatInstance; // where this level's answers live
  parent?: Scope;
  fields: Field[]; // fields defined at this level (groups flattened)
}

function flatten(fields: Field[]): Field[] {
  // Groups are transparent for naming; repeats are a new level.
  return fields.flatMap((f) => (f.type === "group" ? [f, ...flatten(f.children ?? [])] : [f]));
}

function toValue(v: AnswerValue | undefined): Value {
  if (v === undefined || v === null) return "";
  if (Array.isArray(v)) return v.length as number;
  if (typeof v === "object") {
    const g = v as GeoValue;
    return `${g.lat} ${g.lon} 0 ${g.accuracy_m ?? 0}`;
  }
  return v;
}

function findInRepeats(name: string, fields: Field[], instance: RepeatInstance): Value[] | undefined {
  for (const f of flatten(fields)) {
    if (f.type !== "repeat") continue;
    const items = (instance[f.name] as RepeatInstance[] | undefined) ?? [];
    const childFields = flatten(f.children ?? []);
    if (childFields.some((c) => c.name === name && c.type !== "repeat")) {
      return items.map((it) => toValue(it[name]));
    }
    const nested = items.flatMap((it) => findInRepeats(name, f.children ?? [], it) ?? []);
    if (nested.length) return nested;
  }
  return undefined;
}

function resolver(scope: Scope): (name: string) => Value {
  return (name: string) => {
    for (let s: Scope | undefined = scope; s; s = s.parent) {
      const f = s.fields.find((x) => x.name === name);
      if (f) {
        if (f.type === "repeat") return ((s.instance[name] as RepeatInstance[] | undefined) ?? []) as unknown as Value;
        return toValue(s.instance[name]);
      }
      const inRepeat = findInRepeats(name, s.fields, s.instance);
      if (inRepeat) return inRepeat;
    }
    return "";
  };
}

function isTrue(expr: string | undefined, scope: Scope, now?: () => Date): boolean {
  if (!expr) return false;
  const e = expr.trim();
  if (e === "yes" || e === "true" || e === "true()") return true;
  if (e === "no" || e === "false" || e === "false()") return false;
  return toBool(evaluate(e, { ref: resolver(scope), now }));
}

function relevantOf(field: Field, scope: Scope, now?: () => Date): boolean {
  return !field.relevant || isTrue(field.relevant, scope, now);
}

export interface ComputeOptions {
  now?: () => Date;
}

/** Apply calculations (to a fixed point) and work out relevance for every question. */
export function computeForm(schema: FormSchema, answers: Answers, opts: ComputeOptions = {}): FormState {
  const values: Answers = clone(answers);

  const calcLevel = (fields: Field[], instance: RepeatInstance, parent?: Scope): boolean => {
    const scope: Scope = { instance, parent, fields: flatten(fields) };
    let changed = false;
    for (const f of scope.fields) {
      if (f.type === "calculate" && f.calculation) {
        let v: Value = "";
        try {
          v = evaluate(f.calculation, { ref: resolver(scope), now: opts.now });
        } catch {
          v = "";
        }
        const out = typeof v === "number" && Number.isNaN(v) ? "" : Array.isArray(v) ? toStr(v) : v;
        if (instance[f.name] !== out) {
          instance[f.name] = out;
          changed = true;
        }
      } else if (f.type === "repeat") {
        for (const inst of (instance[f.name] as RepeatInstance[] | undefined) ?? []) {
          changed = calcLevel(f.children ?? [], inst, scope) || changed;
        }
      }
    }
    return changed;
  };
  for (let i = 0; i < 10 && calcLevel(schema.fields, values); i++);

  const visible: NodeState[] = [];
  const build = (fields: Field[], scope: Scope, prefix: string, parentRelevant: boolean): NodeState[] =>
    fields.map((field) => {
      const path = prefix + field.name;
      const relevant = parentRelevant && relevantOf(field, scope, opts.now);
      const node: NodeState = {
        path,
        field,
        relevant,
        required: relevant && isTrue(field.required, scope, opts.now),
        readOnly: isTrue(field.read_only, scope, opts.now),
      };
      if (field.type === "group") {
        node.children = build(field.children ?? [], scope, prefix, relevant);
      } else if (field.type === "repeat") {
        const items = (scope.instance[field.name] as RepeatInstance[] | undefined) ?? [];
        node.instances = items.map((inst, i) => {
          const child: Scope = { instance: inst, parent: scope, fields: flatten(field.children ?? []) };
          return build(field.children ?? [], child, `${path}[${i}].`, relevant);
        });
      } else if (relevant && (LEAF_INPUTS.has(field.type) || field.type === "note")) {
        visible.push(node);
      }
      return node;
    });

  const rootScope: Scope = { instance: values, fields: flatten(schema.fields) };
  const nodes = build(schema.fields, rootScope, "", true);
  return { values, nodes, visible };
}

// ---------------------------------------------------------------------------
// Answer access by path
// ---------------------------------------------------------------------------
function splitPath(path: string): (string | number)[] {
  const parts: (string | number)[] = [];
  for (const seg of path.split(".")) {
    const m = /^(.+)\[(\d+)\]$/.exec(seg);
    if (m) parts.push(m[1]!, Number(m[2]));
    else parts.push(seg);
  }
  return parts;
}

export function getAnswer(answers: Answers, path: string): AnswerValue | undefined {
  let cur: unknown = answers;
  for (const p of splitPath(path)) {
    if (cur === undefined || cur === null) return undefined;
    cur = (cur as Record<string | number, unknown>)[p];
  }
  return cur as AnswerValue | undefined;
}

/** Immutable update of one answer. */
export function setAnswer(answers: Answers, path: string, value: AnswerValue | undefined): Answers {
  const parts = splitPath(path);
  const root: Answers = clone(answers);
  let cur: Record<string | number, unknown> = root;
  parts.forEach((p, i) => {
    if (i === parts.length - 1) {
      if (value === undefined) delete cur[p];
      else cur[p] = value;
      return;
    }
    if (cur[p] === undefined) cur[p] = typeof parts[i + 1] === "number" ? [] : {};
    cur = cur[p] as Record<string | number, unknown>;
  });
  return root;
}

export function addRepeat(answers: Answers, path: string): Answers {
  const items = (getAnswer(answers, path) as RepeatInstance[] | undefined) ?? [];
  return setAnswer(answers, path, [...items, {}]);
}

export function removeRepeat(answers: Answers, path: string, index: number): Answers {
  const items = (getAnswer(answers, path) as RepeatInstance[] | undefined) ?? [];
  return setAnswer(answers, path, items.filter((_, i) => i !== index));
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------
function isEmpty(v: AnswerValue | undefined): boolean {
  return v === undefined || v === null || v === "" || (Array.isArray(v) && v.length === 0);
}

function scopeForPath(schema: FormSchema, values: Answers, path: string): Scope {
  let scope: Scope = { instance: values, fields: flatten(schema.fields) };
  const parts = splitPath(path);
  for (let i = 0; i < parts.length - 1; i += 2) {
    const name = parts[i] as string;
    const idx = parts[i + 1] as number;
    const rf = scope.fields.find((f) => f.name === name && f.type === "repeat");
    const inst = ((scope.instance[name] as RepeatInstance[] | undefined) ?? [])[idx];
    if (!rf || !inst) break;
    scope = { instance: inst, parent: scope, fields: flatten(rf.children ?? []) };
  }
  return scope;
}

/** Error message for one visible question, or null if it is fine. */
export function validateNode(schema: FormSchema, state: FormState, node: NodeState, now?: () => Date): string | null {
  if (!node.relevant || node.field.type === "note") return null;
  const value = getAnswer(state.values, node.path);
  if (isEmpty(value)) {
    return node.required ? (node.field.required_message ?? "This question is required") : null;
  }
  const f = node.field;
  if (f.type === "integer" && !Number.isInteger(Number(value))) return "Enter a whole number";
  if (f.type === "decimal" && Number.isNaN(Number(value))) return "Enter a number";
  if (f.constraint) {
    const scope = scopeForPath(schema, state.values, node.path);
    let ok = false;
    try {
      ok = toBool(evaluate(f.constraint, { ref: resolver(scope), current: toValue(value), now }));
    } catch {
      ok = false;
    }
    if (!ok) return f.constraint_message ?? "This answer is not allowed";
  }
  return null;
}

export function validateForm(schema: FormSchema, state: FormState, now?: () => Date): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const node of state.visible) {
    const e = validateNode(schema, state, node, now);
    if (e) errors[node.path] = e;
  }
  return errors;
}

/**
 * Final answers to submit: calculations applied and answers to non-relevant questions
 * removed (as ODK does), so hidden stale values never reach the server.
 */
export function finalizeAnswers(schema: FormSchema, answers: Answers, opts: ComputeOptions = {}): Answers {
  const prune = (nodes: NodeState[], instance: RepeatInstance): void => {
    for (const n of nodes) {
      if (n.field.type === "group") {
        prune(n.children ?? [], instance);
      } else if (n.field.type === "repeat") {
        if (!n.relevant) delete instance[n.field.name];
        else {
          const items = (instance[n.field.name] as RepeatInstance[] | undefined) ?? [];
          (n.instances ?? []).forEach((childNodes, i) => items[i] && prune(childNodes, items[i]!));
        }
      } else if (!n.relevant || n.field.type === "note") {
        delete instance[n.field.name];
      }
    }
  };
  // Hidden answers must not feed calculations, so prune and recompute until stable.
  let current = answers;
  for (let i = 0; i < 5; i++) {
    const state = computeForm(schema, current, opts);
    prune(state.nodes, state.values);
    const same = JSON.stringify(state.values) === JSON.stringify(current);
    current = state.values;
    if (same) break;
  }
  return current;
}

// ---------------------------------------------------------------------------
// Display helpers
// ---------------------------------------------------------------------------
/** Replace ${name} in labels/notes with current answers. */
export function interpolate(text: string | undefined, schema: FormSchema, values: Answers, path = ""): string {
  if (!text) return "";
  const scope = scopeForPath(schema, values, path || "x");
  const ref = resolver(scope);
  return text.replace(/\$\{\s*([^}]+?)\s*\}/g, (_, name: string) => toStr(ref(name)));
}

/** Choices for a select, after applying choice_filter. */
export function choicesFor(schema: FormSchema, state: FormState, node: NodeState): Choice[] {
  const all = node.field.choices ?? [];
  const filter = node.field.choice_filter;
  if (!filter) return all;
  const scope = scopeForPath(schema, state.values, node.path);
  return all.filter((choice) => {
    try {
      return toBool(
        evaluate(filter, {
          ref: resolver(scope),
          bare: (name) => (name === "name" ? choice.name : (choice[name] as Value) ?? ""),
        }),
      );
    } catch {
      return true;
    }
  });
}
