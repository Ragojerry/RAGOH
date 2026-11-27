"""GPS simulator: realistic synthetic collector days for tests and demos.

Scenarios
  honest   walks between assigned points, stays 6–15 min at each, fills a survey there
  cheater  stays at home all day and submits short surveys from there ("visits" never happen)
  spoofer  uses a fake-GPS app: points flagged mock, jumps between points instantly
  gaps     honest day, but the phone is switched off for 45 minutes mid-day
  jitter   honest day with very noisy GPS (±25 m) — must not split stops

Use in tests:   day = simulate("honest", points, start)
Use as a demo:  python -m app.simulator --api https://<host> --username col1 --password … --scenario cheater
"""

import argparse
import json
import math
import random
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.stops import haversine_m

WALK_MPS = 1.2  # ~4.3 km/h


@dataclass
class SimPoint:
    """A place to visit."""

    lat: float
    lon: float
    assignment_id: str | None = None


@dataclass
class SimFix:
    t: datetime
    lat: float
    lon: float
    accuracy_m: float
    is_mock: bool = False
    speed_mps: float | None = None


@dataclass
class SimSubmission:
    assignment_id: str | None
    started_at: datetime
    ended_at: datetime
    lat: float
    lon: float


@dataclass
class SimVisit:
    """Ground truth: where the collector really stayed and for how long."""

    lat: float
    lon: float
    arrived: datetime
    left: datetime


@dataclass
class SimDay:
    scenario: str
    fixes: list[SimFix] = field(default_factory=list)
    submissions: list[SimSubmission] = field(default_factory=list)
    visits: list[SimVisit] = field(default_factory=list)
    shift_start: datetime | None = None
    shift_end: datetime | None = None


def _offset(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    dlat = north_m / 111_320
    dlon = east_m / (111_320 * math.cos(math.radians(lat)))
    return lat + dlat, lon + dlon


class _Walker:
    def __init__(
        self,
        rng: random.Random,
        start: datetime,
        lat: float,
        lon: float,
        noise_m: float,
        mock: bool = False,
    ):
        self.rng, self.t, self.lat, self.lon, self.noise, self.mock = (
            rng,
            start,
            lat,
            lon,
            noise_m,
            mock,
        )
        self.fixes: list[SimFix] = []

    def _fix(self, speed: float | None) -> None:
        n_lat, n_lon = _offset(
            self.lat, self.lon, self.rng.gauss(0, self.noise), self.rng.gauss(0, self.noise)
        )
        acc = max(3.0, abs(self.rng.gauss(self.noise * 1.2, 3)))
        self.fixes.append(SimFix(self.t, n_lat, n_lon, round(acc, 1), self.mock, speed))

    def stay(self, seconds: float, interval: float = 90) -> None:
        end = self.t + timedelta(seconds=seconds)
        self._fix(0.0)
        while self.t + timedelta(seconds=interval) <= end:
            self.t += timedelta(seconds=interval)
            self._fix(0.0)
        self.t = end
        self._fix(0.0)

    def walk_to(
        self, lat: float, lon: float, interval: float = 10, speed: float = WALK_MPS
    ) -> None:
        dist = haversine_m(self.lat, self.lon, lat, lon)
        steps = max(1, int(dist / (speed * interval)))
        lat0, lon0 = self.lat, self.lon
        for k in range(1, steps + 1):
            self.t += timedelta(seconds=interval)
            self.lat = lat0 + (lat - lat0) * k / steps
            self.lon = lon0 + (lon - lon0) * k / steps
            self._fix(speed)

    def jump_to(self, lat: float, lon: float) -> None:
        self.t += timedelta(seconds=20)
        self.lat, self.lon = lat, lon
        self._fix(None)


def simulate(
    scenario: str,
    points: list[SimPoint],
    start: datetime,
    home: tuple[float, float] | None = None,
    seed: int = 7,
) -> SimDay:
    rng = random.Random(seed)  # noqa: S311 — deterministic test data
    first = points[0]
    home = home or _offset(first.lat, first.lon, -600, -400)
    day = SimDay(scenario, shift_start=start)
    noise = 25.0 if scenario == "jitter" else 6.0
    w = _Walker(rng, start, home[0], home[1], noise, mock=(scenario == "spoofer"))

    if scenario == "cheater":
        # Never leaves home; produces short surveys spread over the day.
        for p in points:
            w.stay(rng.uniform(15, 25) * 60)
            sub_start = w.t
            w.stay(rng.uniform(2, 3) * 60, interval=60)
            day.submissions.append(SimSubmission(p.assignment_id, sub_start, w.t, w.lat, w.lon))
        w.stay(20 * 60)
    elif scenario == "spoofer":
        w.stay(5 * 60)
        for p in points:
            w.jump_to(p.lat, p.lon)
            sub_start = w.t
            w.stay(120, interval=30)
            day.submissions.append(SimSubmission(p.assignment_id, sub_start, w.t, w.lat, w.lon))
    else:
        w.stay(4 * 60, interval=60)  # leaving home
        for idx, p in enumerate(points):
            w.walk_to(p.lat, p.lon)
            arrived = w.t
            dwell = rng.uniform(6, 15) * 60
            if scenario == "gaps" and idx == len(points) // 2:
                # Phone switched off for 45 minutes in the middle of a visit.
                w.stay(dwell / 2, interval=60)
                w.t += timedelta(minutes=45)
                w.stay(dwell / 2, interval=60)
            else:
                w.stay(dwell, interval=60)
            day.visits.append(SimVisit(p.lat, p.lon, arrived, w.t))
            sub_end = w.t - timedelta(seconds=30)
            sub_start = max(
                arrived + timedelta(seconds=60), sub_end - timedelta(minutes=rng.uniform(5, 9))
            )
            day.submissions.append(SimSubmission(p.assignment_id, sub_start, sub_end, p.lat, p.lon))
        w.walk_to(home[0], home[1])
    day.fixes = w.fixes
    day.shift_end = w.t
    return day


# ---------------------------------------------------------------------------
# Push a simulated day to a running API (demo / staging smoke test)
# ---------------------------------------------------------------------------
def _request(url: str, body: Any = None, token: str | None = None) -> Any:
    data = json.dumps(body, default=str).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")  # noqa: S310
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
        return json.loads(r.read() or b"null")


def push_day(
    api: str, username: str, password: str, scenario: str, form_kind: str = "survey"
) -> dict[str, Any]:
    api = api.rstrip("/")
    tok = _request(f"{api}/api/v1/auth/login", {"username": username, "password": password})[
        "access_token"
    ]
    snap = _request(f"{api}/api/v1/sync/pull", token=tok)
    form = next(f for f in snap["forms"] if f["kind"] == form_kind)
    todo = [a for a in snap["assignments"] if a["status"] == "pending"][:6]
    if not todo:
        raise SystemExit("No pending assignments for this user")
    pts = [SimPoint(a["visit_point"]["lat"], a["visit_point"]["lon"], a["id"]) for a in todo]
    start = datetime.now(UTC).replace(hour=7, minute=30, second=0, microsecond=0)
    day = simulate(scenario, pts, start)
    shift_id, device = str(uuid.uuid4()), str(uuid.uuid4())
    loc = lambda la, lo: {"lat": la, "lon": lo, "accuracy_m": 8}  # noqa: E731
    gps = [
        {"id": str(uuid.uuid4()), "recorded_at": f.t.isoformat(), "lat": f.lat, "lon": f.lon, "accuracy_m": f.accuracy_m,
         "is_mock": f.is_mock, "speed_mps": f.speed_mps, "shift_id": shift_id, "battery_pct": 80}
        for f in day.fixes
    ]  # fmt: skip
    subs = [
        {"id": str(uuid.uuid4()), "form_version_id": form["version_id"], "shift_id": shift_id,
         "assignment_id": s.assignment_id, "started_at": s.started_at.isoformat(), "ended_at": s.ended_at.isoformat(),
         "start_location": loc(s.lat, s.lon), "end_location": loc(s.lat, s.lon),
         "data": {"consent": "yes", "respondent_name": "Simulated", "respondent_sex": "female", "hh_size": 4, "under5": 1, "has_net": "no"}}
        for s in day.submissions
    ]  # fmt: skip
    shift = {"id": shift_id, "started_at": day.shift_start.isoformat() if day.shift_start else None,
             "ended_at": day.shift_end.isoformat() if day.shift_end else None, "start_location": loc(gps[0]["lat"], gps[0]["lon"])}  # fmt: skip
    base = {
        "device": {"id": device, "model": "Simulator", "app_version": "sim"},
        "device_time": datetime.now(UTC).isoformat(),
    }
    results = []
    for i in range(0, len(gps), 800):
        results.append(
            _request(f"{api}/api/v1/sync/push", {**base, "gps_points": gps[i : i + 800]}, tok)
        )
    # Like the phone: finishing a survey also marks the visit done.
    updates = [
        {"id": str(uuid.uuid4()), "assignment_id": s.assignment_id, "status": "completed",
         "changed_at": s.ended_at.isoformat()}
        for s in day.submissions
    ]  # fmt: skip
    results.append(
        _request(
            f"{api}/api/v1/sync/push",
            {**base, "shifts": [shift], "submissions": subs, "assignment_updates": updates},
            tok,
        )
    )
    return {
        "scenario": scenario,
        "gps_points": len(gps),
        "submissions": len(subs),
        "pushes": len(results),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Push a simulated collector day to a RAGOH API")
    ap.add_argument("--api", required=True)
    ap.add_argument("--username", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument(
        "--scenario", default="honest", choices=["honest", "cheater", "spoofer", "gaps", "jitter"]
    )
    args = ap.parse_args()
    print(push_day(args.api, args.username, args.password, args.scenario))


if __name__ == "__main__":
    main()
