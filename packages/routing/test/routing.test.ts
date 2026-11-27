import { describe, expect, it } from "vitest";
import { haversineM, orderPoints, planOffline } from "../src/index";

describe("offline route ordering", () => {
  it("visits points on a line in order", () => {
    const line = Array.from({ length: 8 }, (_, i) => ({ lat: 9 + i * 0.002, lon: 7 }));
    const shuffled = [line[3]!, line[0]!, line[7]!, line[5]!, line[1]!, line[6]!, line[2]!, line[4]!];
    const order = orderPoints({ lat: 8.998, lon: 7 }, shuffled);
    expect(order.map((i) => shuffled[i])).toEqual(line);
  });

  it("avoids crossing paths", () => {
    const sq = [{ lat: 9, lon: 7 }, { lat: 9.01, lon: 7.01 }, { lat: 9, lon: 7.01 }, { lat: 9.01, lon: 7 }];
    const order = orderPoints({ lat: 9, lon: 7 }, sq, true);
    const tour = [{ lat: 9, lon: 7 }, ...order.map((i) => sq[i]!), { lat: 9, lon: 7 }];
    let len = 0;
    for (let i = 1; i < tour.length; i++) len += haversineM(tour[i - 1]!, tour[i]!);
    expect(len).toBeLessThan(haversineM({ lat: 9, lon: 7 }, { lat: 9.01, lon: 7 }) * 4.2);
  });

  it("matches the server's heuristic timing", () => {
    const plan = planOffline({ lat: 9, lon: 7 }, [{ lat: 9.0036, lon: 7, id: "a" }], { speedKmh: 3.6, serviceS: 600 });
    expect(plan.stops[0]!.legDistanceM).toBeGreaterThan(390);
    expect(plan.stops[0]!.legDistanceM).toBeLessThan(410);
    expect(plan.durationS).toBe(plan.stops[0]!.arrivalOffsetS + 600);
    expect(plan.stops[0]!.item.id).toBe("a");
    expect(plan.geometry).toHaveLength(2);
  });

  it("handles nothing to plan", () => {
    expect(planOffline({ lat: 9, lon: 7 }, []).stops).toEqual([]);
  });
});
