"""Stop detection (stay points) from a GPS track — pure Python, no database.

Algorithm (docs/ARCHITECTURE.md §5.1):
 1. Drop mock points; points with poor accuracy are ignored.
 2. Smooth positions with a 3-point median to reduce jitter.
 3. Walk the track: a candidate stop grows while points stay within R of its running
    centroid; it becomes a stop if it lasts at least T.
 4. Merge consecutive stops separated by a short gap and closer than R (GPS dropouts
    inside a building).
 5. Gaps longer than G with no points become tracking gaps, never stops — a phone that
    was switched off at home must not count as "time spent at a visit".
"""

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Fix:
    t: datetime
    lat: float
    lon: float
    accuracy_m: float | None = None
    is_mock: bool = False


@dataclass
class DetectedStop:
    started_at: datetime
    ended_at: datetime
    lat: float
    lon: float
    n_points: int

    @property
    def dwell_s(self) -> int:
        return int((self.ended_at - self.started_at).total_seconds())


@dataclass
class Gap:
    started_at: datetime
    ended_at: datetime

    @property
    def duration_s(self) -> int:
        return int((self.ended_at - self.started_at).total_seconds())


@dataclass
class TrackAnalysis:
    stops: list[DetectedStop] = field(default_factory=list)
    gaps: list[Gap] = field(default_factory=list)
    distance_m: float = 0.0
    used_points: int = 0
    mock_points: int = 0
    first_fix: datetime | None = None
    last_fix: datetime | None = None
    tracked_s: int = 0  # time covered by the track, excluding gaps


@dataclass(frozen=True)
class StopParams:
    radius_m: float = 50
    min_duration_s: int = 180
    merge_gap_s: int = 120
    max_accuracy_m: float = 50
    gap_s: int = 600
    max_speed_mps: float = 40
    max_outliers: int = 3
    min_step_m: float = 15


EARTH_R = 6_371_000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_R * math.asin(min(1.0, math.sqrt(h)))


def _smooth(points: list[Fix]) -> list[Fix]:
    if len(points) < 3:
        return points
    out = [points[0]]
    for i in range(1, len(points) - 1):
        a, b, c = points[i - 1], points[i], points[i + 1]
        out.append(
            Fix(
                b.t,
                statistics.median([a.lat, b.lat, c.lat]),
                statistics.median([a.lon, b.lon, c.lon]),
                b.accuracy_m,
            )
        )
    out.append(points[-1])
    return out


def _segments(points: list[Fix], gap_s: int) -> tuple[list[list[Fix]], list[Gap]]:
    """Split the track wherever there is no fix for longer than gap_s."""
    segments: list[list[Fix]] = []
    gaps: list[Gap] = []
    current: list[Fix] = []
    for p in points:
        if current and (p.t - current[-1].t).total_seconds() > gap_s:
            gaps.append(Gap(current[-1].t, p.t))
            segments.append(current)
            current = []
        current.append(p)
    if current:
        segments.append(current)
    return segments, gaps


def _stops_in_segment(seg: list[Fix], params: StopParams) -> list[DetectedStop]:
    stops: list[DetectedStop] = []
    i, n = 0, len(seg)
    while i < n:
        members = [seg[i]]
        lat_sum, lon_sum = seg[i].lat, seg[i].lon
        j = i + 1
        while j < n:
            clat, clon = lat_sum / len(members), lon_sum / len(members)
            if haversine_m(clat, clon, seg[j].lat, seg[j].lon) <= params.radius_m:
                members.append(seg[j])
                lat_sum += seg[j].lat
                lon_sum += seg[j].lon
                j += 1
                continue
            # Tolerate a few noisy outliers if the track comes back inside the radius.
            back = next(
                (
                    k
                    for k in range(j + 1, min(j + 1 + params.max_outliers, n))
                    if haversine_m(clat, clon, seg[k].lat, seg[k].lon) <= params.radius_m
                ),
                None,
            )
            if back is None:
                break
            j = back
        duration = (members[-1].t - members[0].t).total_seconds()
        if duration >= params.min_duration_s:
            stop = _trim(members, params)
            if stop.dwell_s >= params.min_duration_s:
                stops.append(stop)
            i = j
        else:
            i += 1
    return stops


def _trim(members: list[Fix], params: StopParams) -> DetectedStop:
    """Arrival/departure walking points sit at the edge of the radius; start and end the stop
    at the first/last point close to its centre so dwell time isn't inflated."""
    clat = statistics.fmean(m.lat for m in members)
    clon = statistics.fmean(m.lon for m in members)
    accs = [m.accuracy_m for m in members if m.accuracy_m is not None]
    noise = statistics.median(accs) if accs else 0.0
    core_r = max(params.radius_m * 0.5, 20.0, 1.5 * noise)
    core = [m for m in members if haversine_m(clat, clon, m.lat, m.lon) <= core_r] or members
    return DetectedStop(core[0].t, core[-1].t, clat, clon, len(members))


def _merge(stops: list[DetectedStop], params: StopParams) -> list[DetectedStop]:
    merged: list[DetectedStop] = []
    for s in stops:
        if merged:
            prev = merged[-1]
            close = haversine_m(prev.lat, prev.lon, s.lat, s.lon) <= params.radius_m * 1.5
            soon = (s.started_at - prev.ended_at).total_seconds() <= params.merge_gap_s
            if close and soon:
                total = prev.n_points + s.n_points
                merged[-1] = DetectedStop(
                    prev.started_at,
                    s.ended_at,
                    (prev.lat * prev.n_points + s.lat * s.n_points) / total,
                    (prev.lon * prev.n_points + s.lon * s.n_points) / total,
                    total,
                )
                continue
        merged.append(s)
    return merged


def analyse_track(fixes: list[Fix], params: StopParams | None = None) -> TrackAnalysis:
    params = params or StopParams()
    fixes = sorted(fixes, key=lambda f: f.t)
    result = TrackAnalysis(mock_points=sum(1 for f in fixes if f.is_mock))
    good = [
        f
        for f in fixes
        if not f.is_mock and (f.accuracy_m is None or f.accuracy_m <= params.max_accuracy_m)
    ]
    result.used_points = len(good)
    if not good:
        return result
    result.first_fix, result.last_fix = good[0].t, good[-1].t

    segments, result.gaps = _segments(good, params.gap_s)
    stops: list[DetectedStop] = []
    for seg in segments:
        result.tracked_s += int((seg[-1].t - seg[0].t).total_seconds())
        seg_stops = _stops_in_segment(_smooth(seg), params)
        result.distance_m += _distance(_collapse(seg, seg_stops), params)
        stops.extend(seg_stops)
    result.stops = _merge(stops, params)
    return result


def _collapse(seg: list[Fix], stops: list[DetectedStop]) -> list[Fix]:
    """Replace each stop's jittery points with its centroid: standing still adds no distance."""
    out: list[Fix] = []
    k = 0
    for p in _smooth(seg):
        while k < len(stops) and p.t > stops[k].ended_at:
            k += 1
        if k < len(stops) and stops[k].started_at <= p.t <= stops[k].ended_at:
            c = Fix(p.t, stops[k].lat, stops[k].lon, 0.0)
            if not out or (out[-1].lat, out[-1].lon) != (c.lat, c.lon):
                out.append(c)
            continue
        out.append(p)
    return out


def _distance(seg: list[Fix], params: StopParams) -> float:
    """Distance walked: only count movement beyond GPS noise (anchor-to-anchor), and skip
    physically impossible jumps."""
    total = 0.0
    anchor = seg[0]
    for p in seg[1:]:
        d = haversine_m(anchor.lat, anchor.lon, p.lat, p.lon)
        threshold = max(params.min_step_m, p.accuracy_m or 0, anchor.accuracy_m or 0)
        if d <= threshold:
            continue
        dt = (p.t - anchor.t).total_seconds()
        if dt > 0 and d / dt <= params.max_speed_mps:
            total += d
        anchor = p
    return total
