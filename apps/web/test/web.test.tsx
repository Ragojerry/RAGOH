import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { ColumnChart } from "../src/components/charts";
import { DataTable } from "../src/components/Table";
import { withQuery } from "../src/lib/api";
import { addDays, duration, km } from "../src/lib/format";

afterEach(cleanup);

describe("formatting", () => {
  it("durations and distances read naturally", () => {
    expect(duration(null)).toBe("—");
    expect(duration(170)).toBe("3 min");
    expect(duration(3 * 3600 + 5 * 60)).toBe("3 h 05");
    expect(km(640)).toBe("640 m");
    expect(km(12_345)).toBe("12.3 km");
  });
  it("date arithmetic crosses months", () => {
    expect(addDays("2026-11-30", 1)).toBe("2026-12-01");
    expect(addDays("2026-03-01", -1)).toBe("2026-02-28");
  });
  it("query strings drop empty filters", () => {
    expect(withQuery("/flags", { team_id: "", date_from: "2026-11-01", limit: 5, x: null })).toBe("/flags?date_from=2026-11-01&limit=5");
    expect(withQuery("/flags", {})).toBe("/flags");
  });
});

describe("DataTable", () => {
  const rows = [{ id: "a", name: "Amina", n: 3 }, { id: "b", name: "Bola", n: 10 }, { id: "c", name: "Chidi", n: 1 }];
  const cols = [{ header: "Name", accessorKey: "name" }, { header: "Surveys", accessorKey: "n" }];
  it("sorts when a header is clicked and reports row clicks", () => {
    const clicked: string[] = [];
    render(<DataTable data={rows} columns={cols} rowId={(r) => r.id} onRowClick={(r) => clicked.push(r.name)} />);
    const names = () => screen.getAllByRole("row").slice(1).map((r) => r.textContent);
    fireEvent.click(screen.getByText("Surveys"));
    const first = names();
    fireEvent.click(screen.getByText(/Surveys/));
    expect(names()).toEqual([...first].reverse());
    fireEvent.click(screen.getByText("Bola"));
    expect(clicked).toEqual(["Bola"]);
  });
  it("says so when empty", () => {
    render(<DataTable data={[]} columns={cols} empty="No collectors." />);
    expect(screen.getByText("No collectors.")).toBeTruthy();
  });
});

describe("ColumnChart", () => {
  const data = [3, 0, 12, 7].map((v, i) => ({ key: String(i), label: `D${i}`, tip: `Day ${i}`, value: v }));
  it("labels only the peak and the last value, and has a table view", () => {
    const { container } = render(<ColumnChart title="Surveys per day" unit="surveys" data={data} />);
    const texts = [...container.querySelectorAll("svg text")].map((t) => t.textContent);
    expect(texts.filter((t) => t === "12" || t === "7")).toHaveLength(2); // peak + last
    expect(texts).not.toContain("3"); // no number on every column
    fireEvent.click(screen.getByText("Table"));
    expect(screen.getAllByRole("row")).toHaveLength(5);
  });
});
