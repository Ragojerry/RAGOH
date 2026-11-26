import { describe, expect, it } from "vitest";
import {
  addRepeat,
  choicesFor,
  computeForm,
  finalizeAnswers,
  getAnswer,
  interpolate,
  removeRepeat,
  setAnswer,
  validateForm,
} from "../src/engine";
import type { Answers, FormSchema } from "../src/types";
import household from "./fixtures/survey.json";

const survey = household as unknown as FormSchema;
const visibleNames = (a: Answers) => computeForm(survey, a).visible.map((n) => n.field.name);

describe("household survey (real server-compiled form)", () => {
  it("shows only consent questions until consent is given", () => {
    expect(visibleNames({})).toEqual(["intro", "consent", "comments"]);
  });

  it("branches on consent", () => {
    expect(visibleNames({ consent: "no" })).toEqual(["intro", "consent", "refusal_reason", "comments"]);
    const yes = visibleNames({ consent: "yes" });
    expect(yes).toContain("hh_size");
    expect(yes).not.toContain("nets"); // only after has_net = yes
    expect(visibleNames({ consent: "yes", has_net: "yes" })).toContain("nets");
  });

  it("requires answers and enforces constraints with messages from the form", () => {
    const a: Answers = { consent: "yes", respondent_name: "Amina", respondent_sex: "female", hh_size: 3, under5: 5, has_net: "no" };
    const errors = validateForm(survey, computeForm(survey, a));
    expect(errors).toEqual({ under5: "Cannot be more than the household size" });

    const missing = validateForm(survey, computeForm(survey, { consent: "yes" }));
    expect(Object.keys(missing).sort()).toEqual(["has_net", "hh_size", "respondent_name", "respondent_sex", "under5"]);
    expect(missing.hh_size).toBe("This question is required");

    expect(validateForm(survey, computeForm(survey, { ...a, under5: 1 }))).toEqual({});
    expect(validateForm(survey, computeForm(survey, { ...a, under5: 1, hh_size: 2.5 })).hh_size).toBe("Enter a whole number");
  });

  it("calculates and interpolates note text", () => {
    const state = computeForm(survey, { consent: "yes", hh_size: 4, under5: 1 });
    expect(state.values.pct_under5).toBe(25);
    const note = survey.fields.find((f) => f.name === "summary")!;
    expect(interpolate(note.label, survey, state.values)).toBe("Children under 5: 1 of 4 (25%)");
  });

  it("removes answers to questions that became hidden before submitting", () => {
    const final = finalizeAnswers(survey, { consent: "no", refusal_reason: "busy", hh_size: 4, nets: 2 });
    expect(final).toEqual({ consent: "no", refusal_reason: "busy", pct_under5: 0 });
  });
});

const repeatForm: FormSchema = {
  schema_version: 1, id_string: "r", version: "1", title: "R",
  fields: [
    { type: "integer", name: "n", label: "How many children?", required: "yes" },
    {
      type: "repeat", name: "kids", label: "Child", relevant: "${n} > 0",
      children: [
        { type: "text", name: "kname", label: "Name of child (${n} total)", required: "yes" },
        { type: "integer", name: "age", label: "Age", constraint: ". < 18" },
        { type: "select_one", name: "school", label: "In school?", relevant: "${age} >= 5",
          choices: [{ name: "yes", label: "Yes" }, { name: "no", label: "No" }] },
      ],
    },
    { type: "calculate", name: "total_age", calculation: "sum(${age})" },
    { type: "calculate", name: "num_kids", calculation: "count(${kids})" },
    { type: "text", name: "st", label: "State code" },
    { type: "select_one", name: "lga", label: "LGA", choices: [{ name: "amac", label: "AMAC", state: "fct" }, { name: "kano_m", label: "Kano Municipal", state: "kano" }],
      choice_filter: "state = ${st}" },
    { type: "group", name: "g", label: "Group", relevant: "${n} = 0", children: [{ type: "text", name: "why", label: "Why none?" }] },
  ],
};

describe("repeats, groups and choice filters", () => {
  it("evaluates relevance inside each repeat instance with sibling references", () => {
    let a: Answers = { n: 2 };
    a = addRepeat(a, "kids");
    a = addRepeat(a, "kids");
    a = setAnswer(a, "kids[0].age", 3);
    a = setAnswer(a, "kids[1].age", 9);
    const paths = computeForm(repeatForm, a).visible.map((n) => n.path);
    expect(paths).toContain("kids[1].school");
    expect(paths).not.toContain("kids[0].school");
  });

  it("validates per instance and aggregates across instances", () => {
    let a: Answers = { n: 2, kids: [{ kname: "A", age: 20 }, { age: 4 }] };
    const state = computeForm(repeatForm, a);
    expect(validateForm(repeatForm, state)).toEqual({
      "kids[0].age": "This answer is not allowed",
      "kids[1].kname": "This question is required",
    });
    expect(state.values.total_age).toBe(24);
    expect(state.values.num_kids).toBe(2);
    a = removeRepeat(a, "kids", 0);
    expect(getAnswer(a, "kids[0].age")).toBe(4);
  });

  it("hides groups and their children together", () => {
    expect(computeForm(repeatForm, { n: 0 }).visible.map((x) => x.path)).toContain("why");
    expect(computeForm(repeatForm, { n: 1 }).visible.map((x) => x.path)).not.toContain("why");
  });

  it("interpolates labels inside repeats", () => {
    const a: Answers = { n: 3, kids: [{}] };
    const label = repeatForm.fields[1]!.children![0]!.label;
    expect(interpolate(label, repeatForm, a, "kids[0].kname")).toBe("Name of child (3 total)");
  });

  it("filters choices by choice-sheet columns", () => {
    const state = computeForm(repeatForm, { st: "fct" } as Answers);
    const node = state.visible.find((n) => n.field.name === "lga")!;
    expect(choicesFor(repeatForm, state, node).map((c) => c.name)).toEqual(["amac"]);
  });

  it("never mutates the caller's answers", () => {
    const a: Answers = { n: 1 };
    setAnswer(a, "n", 5);
    computeForm(repeatForm, a);
    expect(a).toEqual({ n: 1 });
  });
});
