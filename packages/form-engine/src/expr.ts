/**
 * Small evaluator for the XLSForm/XPath expression subset RAGOH supports:
 *   literals 'a' "a" 1 2.5 · ${name} · . (current value) · bare names (choice columns)
 *   or and = != < <= > >= + - * div mod, unary -, parentheses
 *   if, selected, count-selected, selected-at, concat, string-length, substr, contains,
 *   starts-with, coalesce, not, true, false, number, int, string, round, today, now,
 *   count (repeat instances), sum (field across repeat instances), min, max, regex
 */

export type Value = string | number | boolean | null | Value[];

export interface EvalContext {
  /** Value of ${name}. Arrays are returned for fields inside repeats (for sum/count). */
  ref(name: string): Value;
  /** Value of "." — the field being validated. */
  current?: Value;
  /** Bare identifiers (used in choice filters to read choice columns). */
  bare?(name: string): Value;
  now?(): Date;
}

type Node =
  | { k: "lit"; v: Value }
  | { k: "ref"; name: string }
  | { k: "bare"; name: string }
  | { k: "dot" }
  | { k: "neg"; e: Node }
  | { k: "bin"; op: string; l: Node; r: Node }
  | { k: "call"; fn: string; args: Node[] };

type Tok = { t: "num" | "str" | "ref" | "name" | "op" | "("  | ")" | "," | "dot"; v: string };

export class ExprError extends Error {}

function tokenize(src: string): Tok[] {
  const out: Tok[] = [];
  let i = 0;
  while (i < src.length) {
    const c = src[i]!;
    if (/\s/.test(c)) {
      i++;
    } else if (c === "'" || c === '"') {
      const end = src.indexOf(c, i + 1);
      if (end < 0) throw new ExprError(`Unterminated string in: ${src}`);
      out.push({ t: "str", v: src.slice(i + 1, end) });
      i = end + 1;
    } else if (c === "$" && src[i + 1] === "{") {
      const end = src.indexOf("}", i);
      if (end < 0) throw new ExprError(`Unterminated \${...} in: ${src}`);
      out.push({ t: "ref", v: src.slice(i + 2, end).trim() });
      i = end + 1;
    } else if (/[0-9]/.test(c) || (c === "." && /[0-9]/.test(src[i + 1] ?? ""))) {
      const m = /^[0-9]*\.?[0-9]+/.exec(src.slice(i))!;
      out.push({ t: "num", v: m[0] });
      i += m[0].length;
    } else if (c === ".") {
      out.push({ t: "dot", v: "." });
      i++;
    } else if (c === "(" || c === ")" || c === ",") {
      out.push({ t: c, v: c });
      i++;
    } else if ("!<>".includes(c) && src[i + 1] === "=") {
      out.push({ t: "op", v: c + "=" });
      i += 2;
    } else if ("=<>+-*".includes(c)) {
      out.push({ t: "op", v: c });
      i++;
    } else if (/[A-Za-z_]/.test(c)) {
      const m = /^[A-Za-z_][A-Za-z0-9_\-:]*/.exec(src.slice(i))!;
      let word = m[0];
      // "count-selected" vs "a -b": a trailing '-' is never part of a name
      while (word.endsWith("-")) word = word.slice(0, -1);
      const isOp = ["and", "or", "div", "mod"].includes(word);
      out.push({ t: isOp ? "op" : "name", v: word });
      i += word.length;
    } else {
      throw new ExprError(`Unexpected '${c}' in: ${src}`);
    }
  }
  return out;
}

const PREC: Record<string, number> = {
  or: 1, and: 2, "=": 3, "!=": 3, "<": 4, "<=": 4, ">": 4, ">=": 4,
  "+": 5, "-": 5, "*": 6, div: 6, mod: 6,
};

function parse(src: string): Node {
  const toks = tokenize(src);
  let pos = 0;
  const peek = () => toks[pos];
  const next = () => toks[pos++];

  function primary(): Node {
    const tk = next();
    if (!tk) throw new ExprError(`Unexpected end of: ${src}`);
    switch (tk.t) {
      case "num":
        return { k: "lit", v: Number(tk.v) };
      case "str":
        return { k: "lit", v: tk.v };
      case "ref":
        return { k: "ref", name: tk.v };
      case "dot":
        return { k: "dot" };
      case "(": {
        const e = expr(0);
        if (next()?.t !== ")") throw new ExprError(`Missing ')' in: ${src}`);
        return e;
      }
      case "op":
        if (tk.v === "-") return { k: "neg", e: primary() };
        break;
      case "name":
        if (peek()?.t === "(") {
          next();
          const args: Node[] = [];
          if (peek()?.t !== ")") {
            args.push(expr(0));
            while (peek()?.t === ",") {
              next();
              args.push(expr(0));
            }
          }
          if (next()?.t !== ")") throw new ExprError(`Missing ')' after ${tk.v}( in: ${src}`);
          return { k: "call", fn: tk.v, args };
        }
        return { k: "bare", name: tk.v };
    }
    throw new ExprError(`Unexpected '${tk.v}' in: ${src}`);
  }

  function expr(minPrec: number): Node {
    let left = primary();
    for (;;) {
      const tk = peek();
      if (!tk || tk.t !== "op") break;
      const p = PREC[tk.v];
      if (p === undefined || p < minPrec) break;
      next();
      const right = expr(p + 1);
      left = { k: "bin", op: tk.v, l: left, r: right };
    }
    return left;
  }

  const node = expr(0);
  if (pos < toks.length) throw new ExprError(`Unexpected '${toks[pos]!.v}' in: ${src}`);
  return node;
}

const cache = new Map<string, Node>();
export function compile(src: string): Node {
  let n = cache.get(src);
  if (!n) {
    n = parse(src);
    cache.set(src, n);
  }
  return n;
}

// ---------------------------------------------------------------------------
// XPath-like conversions
// ---------------------------------------------------------------------------
const first = (v: Value): Value => (Array.isArray(v) ? (v[0] ?? null) : v);

export function toNumber(v: Value): number {
  v = first(v);
  if (v === null || v === "") return NaN;
  if (typeof v === "boolean") return v ? 1 : 0;
  return Number(v);
}

export function toStr(v: Value): string {
  v = first(v);
  if (v === null) return "";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(Number(v.toFixed(10)));
  return Array.isArray(v) ? v.map(toStr).join(" ") : v;
}

export function toBool(v: Value): boolean {
  if (Array.isArray(v)) return v.length > 0;
  if (v === null) return false;
  if (typeof v === "boolean") return v;
  if (typeof v === "number") return v !== 0 && !Number.isNaN(v);
  return v !== "";
}

function compare(op: string, a: Value, b: Value): boolean {
  a = first(a);
  b = first(b);
  if (op === "=" || op === "!=") {
    let eq: boolean;
    if (typeof a === "boolean" || typeof b === "boolean") eq = toBool(a) === toBool(b);
    else if (typeof a === "number" || typeof b === "number") eq = toNumber(a) === toNumber(b);
    else eq = toStr(a) === toStr(b);
    return op === "=" ? eq : !eq;
  }
  const x = toNumber(a);
  const y = toNumber(b);
  if (Number.isNaN(x) || Number.isNaN(y)) return false;
  switch (op) {
    case "<": return x < y;
    case "<=": return x <= y;
    case ">": return x > y;
    default: return x >= y;
  }
}

const pad = (n: number) => String(n).padStart(2, "0");
const isoDate = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

function call(fn: string, args: Node[], ctx: EvalContext): Value {
  const ev = (i: number) => (args[i] ? evaluateNode(args[i]!, ctx) : null);
  const all = () => args.map((a) => evaluateNode(a, ctx));
  switch (fn) {
    case "if": return toBool(ev(0)) ? ev(1) : ev(2);
    case "selected": return toStr(ev(0)).split(" ").filter(Boolean).includes(toStr(ev(1)));
    case "count-selected": return toStr(ev(0)).split(" ").filter(Boolean).length;
    case "selected-at": return toStr(ev(0)).split(" ").filter(Boolean)[toNumber(ev(1))] ?? "";
    case "concat": return all().map(toStr).join("");
    case "string-length": return toStr(ev(0)).length;
    case "substr": {
      const s = toStr(ev(0));
      const start = toNumber(ev(1));
      return args.length > 2 ? s.substring(start, toNumber(ev(2))) : s.substring(start);
    }
    case "contains": return toStr(ev(0)).includes(toStr(ev(1)));
    case "starts-with": return toStr(ev(0)).startsWith(toStr(ev(1)));
    case "regex": return new RegExp(toStr(ev(1))).test(toStr(ev(0)));
    case "coalesce": {
      const a = ev(0);
      return toStr(a) !== "" ? a : ev(1);
    }
    case "not": return !toBool(ev(0));
    case "true": return true;
    case "false": return false;
    case "number": return toNumber(ev(0));
    case "int": return Math.trunc(toNumber(ev(0)));
    case "string": return toStr(ev(0));
    case "round": {
      const digits = args.length > 1 ? toNumber(ev(1)) : 0;
      const f = 10 ** digits;
      return Math.round(toNumber(ev(0)) * f) / f;
    }
    case "today": return isoDate(ctx.now ? ctx.now() : new Date());
    case "now": return (ctx.now ? ctx.now() : new Date()).toISOString();
    case "count": {
      const v = ev(0);
      return Array.isArray(v) ? v.length : toStr(v) === "" ? 0 : 1;
    }
    case "sum": {
      const v = ev(0);
      return (Array.isArray(v) ? v : [v]).reduce<number>((s, x) => s + (Number.isNaN(toNumber(x)) ? 0 : toNumber(x)), 0);
    }
    case "min":
    case "max": {
      const vals = all().flatMap((v) => (Array.isArray(v) ? v : [v])).map(toNumber).filter((n) => !Number.isNaN(n));
      if (!vals.length) return NaN;
      return fn === "min" ? Math.min(...vals) : Math.max(...vals);
    }
    default:
      throw new ExprError(`Function ${fn}() is not supported`);
  }
}

function evaluateNode(n: Node, ctx: EvalContext): Value {
  switch (n.k) {
    case "lit": return n.v;
    case "ref": return ctx.ref(n.name);
    case "dot": return ctx.current ?? null;
    case "bare":
      if (ctx.bare) return ctx.bare(n.name);
      throw new ExprError(`Unknown name '${n.name}' (did you mean \${${n.name}}?)`);
    case "neg": return -toNumber(evaluateNode(n.e, ctx));
    case "call": return call(n.fn, n.args, ctx);
    case "bin": {
      if (n.op === "and") return toBool(evaluateNode(n.l, ctx)) && toBool(evaluateNode(n.r, ctx));
      if (n.op === "or") return toBool(evaluateNode(n.l, ctx)) || toBool(evaluateNode(n.r, ctx));
      const l = evaluateNode(n.l, ctx);
      const r = evaluateNode(n.r, ctx);
      switch (n.op) {
        case "+": return toNumber(l) + toNumber(r);
        case "-": return toNumber(l) - toNumber(r);
        case "*": return toNumber(l) * toNumber(r);
        case "div": return toNumber(l) / toNumber(r);
        case "mod": return toNumber(l) % toNumber(r);
        default: return compare(n.op, l, r);
      }
    }
  }
}

export function evaluate(src: string, ctx: EvalContext): Value {
  return evaluateNode(compile(src), ctx);
}

/** All ${name} references in an expression (used for dependency checks). */
export function references(src: string): string[] {
  const out: string[] = [];
  const walk = (n: Node): void => {
    if (n.k === "ref") out.push(n.name);
    else if (n.k === "neg") walk(n.e);
    else if (n.k === "bin") { walk(n.l); walk(n.r); }
    else if (n.k === "call") n.args.forEach(walk);
  };
  walk(compile(src));
  return out;
}
