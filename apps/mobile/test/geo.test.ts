import { describe, expect, it } from "vitest";
import { bearingDeg, compassPoint, formatDistance, haversineM } from "../src/lib/geo";

describe("geo helpers", () => {
  it("measures distance between two FCT points", () => {
    // Area 1 Garki → Wuse Market, roughly 3.3 km
    const d = haversineM({ lat: 9.0331, lon: 7.4891 }, { lat: 9.0612, lon: 7.4797 });
    expect(d).toBeGreaterThan(3000);
    expect(d).toBeLessThan(3600);
  });

  it("formats distances for field staff", () => {
    expect(formatDistance(43)).toBe("40 m");
    expect(formatDistance(1234)).toBe("1.2 km");
    expect(formatDistance(25_400)).toBe("25 km");
    expect(formatDistance(undefined)).toBe("");
  });

  it("gives compass directions", () => {
    expect(compassPoint(bearingDeg({ lat: 9, lon: 7 }, { lat: 9.1, lon: 7 }))).toBe("N");
    expect(compassPoint(bearingDeg({ lat: 9, lon: 7 }, { lat: 9, lon: 7.1 }))).toBe("E");
    expect(compassPoint(bearingDeg({ lat: 9, lon: 7 }, { lat: 8.9, lon: 6.9 }))).toBe("SW");
  });
});
