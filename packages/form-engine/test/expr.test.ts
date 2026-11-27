import { describe, expect, it } from "vitest";
import { evaluate, ExprError, references, type Value } from "../src/expr";

const ctx = (vars: Record<string, Value>, current?: Value) => ({
  ref: (n: string) => vars[n] ?? "",
  current,
  now: () => new Date(2026, 10, 26, 9, 30),
});
const ev = (src: string, vars: Record<string, Value> = {}, current?: Value) => evaluate(src, ctx(vars, current));

describe("expressions", () => {
  it("does arithmetic with XPath operators and precedence", () => {
    expect(ev("1 + 2 * 3")).toBe(7);
    expect(ev("(1 + 2) * 3")).toBe(9);
    expect(ev("7 div 2")).toBe(3.5);
    expect(ev("7 mod 2")).toBe(1);
    expect(ev("-3 + 5")).toBe(2);
    expect(ev("10 - 2 - 3")).toBe(5); // left-associative
  });

  it("compares numbers numerically and strings as text", () => {
    expect(ev("${a} > 9", { a: "10" })).toBe(true);
    expect(ev("${a} = 'yes'", { a: "yes" })).toBe(true);
    expect(ev("${a} != 'yes'", { a: "no" })).toBe(true);
    expect(ev("${a} = 5", { a: 5 })).toBe(true);
  });

  it("treats empty answers like XPath (no match, false)", () => {
    expect(ev("${a} > 0", { a: "" })).toBe(false);
    expect(ev("${a} < 0", { a: "" })).toBe(false);
    expect(ev("${a} = ''", { a: "" })).toBe(true);
    expect(ev("${missing} = 'yes'")).toBe(false);
  });

  it("supports . for the current value in constraints", () => {
    expect(ev(". >= 1 and . <= 40", {}, 12)).toBe(true);
    expect(ev(". >= 1 and . <= 40", {}, 41)).toBe(false);
    expect(ev(". <= ${hh}", { hh: 4 }, 5)).toBe(false);
  });

  it("handles and/or with short-circuit semantics", () => {
    expect(ev("${a} = 'x' or ${b} = 'y'", { a: "n", b: "y" })).toBe(true);
    expect(ev("${a} = 'x' and ${b} = 'y'", { a: "x", b: "n" })).toBe(false);
  });

  it("implements the common functions", () => {
    expect(ev("selected(${s}, 'cough')", { s: "fever cough" })).toBe(true);
    expect(ev("selected(${s}, 'none')", { s: "fever cough" })).toBe(false);
    expect(ev("count-selected(${s})", { s: "fever cough" })).toBe(2);
    expect(ev("if(${a} > 0, 'pos', 'neg')", { a: -1 })).toBe("neg");
    expect(ev("concat('a', ${b}, 3)", { b: "-" })).toBe("a-3");
    expect(ev("round(2 div 3 * 100, 1)")).toBe(66.7);
    expect(ev("string-length('hello')")).toBe(5);
    expect(ev("today()")).toBe("2026-11-26");
    expect(ev("not(true())")).toBe(false);
    expect(ev("coalesce(${a}, 'x')", { a: "" })).toBe("x");
    expect(ev("int(3.9)")).toBe(3);
    expect(ev("regex(${p}, '^0[789][01][0-9]{8}$')", { p: "08031234567" })).toBe(true);
  });

  it("counts and sums repeat values", () => {
    expect(ev("count(${kids})", { kids: [1, 2, 3] as Value })).toBe(3);
    expect(ev("sum(${age})", { age: [1, "2", ""] as Value })).toBe(3);
  });

  it("reports unsupported functions and syntax errors clearly", () => {
    expect(() => ev("pulldata('x')")).toThrow(ExprError);
    expect(() => ev("(1 + 2")).toThrow(/Missing '\)'/);
    expect(() => ev("1 +")).toThrow(ExprError);
    expect(() => ev("hh_size > 1")).toThrow(/did you mean/);
  });

  it("lists references", () => {
    expect(references("if(${a} > ${b}, ${c}, 0)")).toEqual(["a", "b", "c"]);
  });
});
