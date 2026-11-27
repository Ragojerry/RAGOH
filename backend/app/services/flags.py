"""Abnormal-behaviour rules (R1–R15, S1–S2) and the collector risk score.

Each rule looks at one collector-day (or, for S1/S2, at supervision across teams) and
returns candidate flags. Evaluation is idempotent: re-running a day updates existing flags,
keeps anything a supervisor has already acted on, and removes untouched flags that no
longer apply (e.g. GPS points that arrived late filled a "gap").

Thresholds come from the campaign config ("flags" section, see core/defaults.py).
"""

import statistics
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from geoalchemy2.shape import to_shape
from shapely.geometry import Point
from shapely.prepared import prep
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.defaults import merged_config
from app.db.models import (
    AdminArea,
    Assignment,
    AssignmentStatus,
    Campaign,
    CollectorDaySummary,
    DeviceEvent,
    Flag,
    FlagStatus,
    Form,
    FormKind,
    FormVersion,
    GpsPoint,
    Role,
    Severity,
    Shift,
    Stop,
    Submission,
    Team,
    TeamMember,
    TeamRole,
    User,
    VisitPoint,
)
from app.services.stops import haversine_m
from app.services.tracking import DEFAULT_TZ, day_bounds, user_campaign

TITLES = {
    "R1": "Survey filled away from the visit point",
    "R2": "Interview too short",
    "R3": "Interview unusually long",
    "R4": "Too many surveys in a short time",
    "R5": "Very short stop at a completed point",
    "R6": "Point marked done without a visit",
    "R7": "Impossible movement (GPS jump)",
    "R8": "Fake GPS (mock location) detected",
    "R9": "Long idle time away from assigned points",
    "R10": "Location tracking gap during shift",
    "R11": "Working outside the assigned area",
    "R12": "Activity outside working hours",
    "R13": "Phone clock is wrong",
    "R14": "Data-quality pattern",
    "R15": "Low productivity compared with the team",
    "S1": "Not visited by a supervisor recently",
    "S2": "Serious flag left without follow-up",
}


@dataclass
class Candidate:
    rule_id: str
    subject_type: str
    subject_id: str
    details: dict[str, Any]
    title: str | None = None


@dataclass
class SubInfo:
    sub: Submission
    form_kind: FormKind
    id_string: str
    schema: dict[str, Any]
    point: VisitPoint | None
    point_xy: tuple[float, float] | None  # lon, lat


@dataclass
class DayContext:
    db: Session
    user: User
    day: date
    tz: str
    cfg: dict[str, Any]
    campaign: Campaign | None
    start: datetime
    end: datetime
    subs: list[SubInfo] = field(default_factory=list)
    stops: list[Stop] = field(default_factory=list)
    fixes: list[tuple[datetime, float, float, float | None, bool]] = field(
        default_factory=list
    )  # t, lat, lon, acc, mock
    events: list[DeviceEvent] = field(default_factory=list)
    shifts: list[Shift] = field(default_factory=list)
    completed: list[tuple[Assignment, VisitPoint]] = field(default_factory=list)
    summary: CollectorDaySummary | None = None

    def p(self, rule: str) -> dict[str, Any]:
        return dict(self.cfg["flags"][rule])

    def local(self, t: datetime) -> datetime:
        return t.astimezone(ZoneInfo(self.tz))


def _xy(geom: Any) -> tuple[float, float] | None:
    if geom is None:
        return None
    p = to_shape(geom)
    return p.x, p.y


def build_context(db: Session, user: User, day: date) -> DayContext:
    campaign = user_campaign(db, user.id)
    tz = campaign.timezone if campaign else DEFAULT_TZ
    start, end = day_bounds(day, tz)
    ctx = DayContext(
        db,
        user,
        day,
        tz,
        merged_config(campaign.config if campaign else None),
        campaign,
        start,
        end,
    )
    rows = db.execute(
        select(Submission, Form, FormVersion, VisitPoint)
        .join(FormVersion, FormVersion.id == Submission.form_version_id)
        .join(Form, Form.id == FormVersion.form_id)
        .outerjoin(VisitPoint, VisitPoint.id == Submission.visit_point_id)
        .where(
            Submission.user_id == user.id, Submission.ended_at >= start, Submission.ended_at < end
        )
        .order_by(Submission.started_at)
    ).all()
    ctx.subs = [
        SubInfo(s, f.kind, f.id_string, fv.schema, vp, _xy(vp.geom) if vp else None)
        for s, f, fv, vp in rows
    ]
    ctx.stops = list(
        db.scalars(
            select(Stop)
            .where(Stop.user_id == user.id, Stop.local_date == day)
            .order_by(Stop.started_at)
        )
    )
    for t, g, acc, mock in db.execute(
        select(GpsPoint.recorded_at, GpsPoint.geom, GpsPoint.accuracy_m, GpsPoint.is_mock)
        .where(
            GpsPoint.user_id == user.id, GpsPoint.recorded_at >= start, GpsPoint.recorded_at < end
        )
        .order_by(GpsPoint.recorded_at)
    ):
        x, y = _xy(g) or (0.0, 0.0)
        ctx.fixes.append((t, y, x, acc, bool(mock)))
    ctx.events = list(
        db.scalars(
            select(DeviceEvent).where(
                DeviceEvent.user_id == user.id,
                DeviceEvent.occurred_at >= start,
                DeviceEvent.occurred_at < end,
            )
        )
    )
    ctx.shifts = list(
        db.scalars(
            select(Shift).where(
                Shift.user_id == user.id,
                Shift.started_at < end,
                (Shift.ended_at.is_(None)) | (Shift.ended_at >= start),
            )
        )
    )
    ctx.completed = list(
        db.execute(
            select(Assignment, VisitPoint)
            .join(VisitPoint, VisitPoint.id == Assignment.visit_point_id)
            .where(
                Assignment.collector_id == user.id,
                Assignment.status == AssignmentStatus.completed,
                Assignment.status_changed_at >= start,
                Assignment.status_changed_at < end,
            )
        ).tuples()
    )
    ctx.summary = db.get(CollectorDaySummary, (user.id, day))
    return ctx


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------
def r1_outside_geofence(ctx: DayContext) -> list[Candidate]:
    tol = ctx.p("R1")["tolerance_m"]
    out = []
    for si in ctx.subs:
        s = si.sub
        if si.point is None or si.point_xy is None or s.end_geom is None:
            continue
        ex, ey = _xy(s.end_geom) or (0.0, 0.0)
        d = haversine_m(ey, ex, si.point_xy[1], si.point_xy[0])
        allowed = si.point.geofence_m + min(s.accuracy_m or 0, tol)
        if d > allowed:
            out.append(
                Candidate(
                    "R1",
                    "submission",
                    str(s.id),
                    {
                        "distance_m": round(d),
                        "geofence_m": si.point.geofence_m,
                        "point": si.point.name,
                    },
                )
            )
    return out


def r2_too_short(ctx: DayContext) -> list[Candidate]:
    p = ctx.p("R2")
    out = []
    for si in ctx.subs:
        limit = p.get(f"min_duration_s:{si.id_string}", p["min_duration_s"])
        if si.form_kind == FormKind.survey and si.sub.duration_s < limit:
            out.append(
                Candidate(
                    "R2",
                    "submission",
                    str(si.sub.id),
                    {"duration_s": si.sub.duration_s, "min_s": limit},
                )
            )
    return out


def r3_too_long(ctx: DayContext) -> list[Candidate]:
    limit = ctx.p("R3")["max_duration_s"]
    return [
        Candidate(
            "R3", "submission", str(si.sub.id), {"duration_s": si.sub.duration_s, "max_s": limit}
        )
        for si in ctx.subs
        if si.sub.duration_s > limit
    ]


def r4_too_fast(ctx: DayContext) -> list[Candidate]:
    p = ctx.p("R4")
    subs = sorted(
        (si.sub for si in ctx.subs if si.form_kind == FormKind.survey), key=lambda s: s.started_at
    )
    out: list[Candidate] = []
    window = timedelta(minutes=p["window_min"])
    worst, j = 0, 0
    for i, s in enumerate(subs):
        while subs[j].ended_at < s.ended_at - window:
            j += 1
        worst = max(worst, i - j + 1)
    if worst > p["max_count"]:
        out.append(
            Candidate(
                "R4",
                "day",
                ctx.day.isoformat(),
                {"max_in_window": worst, "window_min": p["window_min"], "allowed": p["max_count"]},
            )
        )
    overlaps = [
        (a, b)
        for a, b in zip(subs, subs[1:], strict=False)
        if b.started_at < a.ended_at - timedelta(seconds=30)
    ]
    if overlaps:
        out.append(
            Candidate(
                "R4",
                "overlap",
                ctx.day.isoformat(),
                {"overlapping_pairs": len(overlaps)},
                "Surveys overlap in time",
            )
        )
    return out


def _stops_near(ctx: DayContext, point: VisitPoint, factor: float = 1.0) -> list[Stop]:
    px, py = _xy(point.geom) or (0.0, 0.0)
    res = []
    for st in ctx.stops:
        cx, cy = _xy(st.centroid) or (0.0, 0.0)
        if haversine_m(cy, cx, py, px) <= point.geofence_m * factor:
            res.append(st)
    return res


def r5_short_stop(ctx: DayContext) -> list[Candidate]:
    limit = ctx.p("R5")["min_dwell_s"]
    out = []
    for a, vp in ctx.completed:
        near = _stops_near(ctx, vp)
        if near and max(s.dwell_s for s in near) < limit:
            out.append(
                Candidate(
                    "R5",
                    "assignment",
                    str(a.id),
                    {"point": vp.name, "dwell_s": max(s.dwell_s for s in near), "min_s": limit},
                )
            )
    return out


def r6_not_visited(ctx: DayContext) -> list[Candidate]:
    if not any(not f[4] for f in ctx.fixes):
        return []  # no usable tracking at all — R10/R8 cover that
    out = []
    near_factor = ctx.cfg["stops"]["near_factor"]
    for a, vp in ctx.completed:
        if _stops_near(ctx, vp, near_factor):
            continue
        px, py = _xy(vp.geom) or (0.0, 0.0)
        # Any genuine GPS fix inside the geofence counts as having been there.
        if any(
            not mock and haversine_m(lat, lon, py, px) <= vp.geofence_m
            for _, lat, lon, _, mock in ctx.fixes
        ):
            continue
        out.append(Candidate("R6", "assignment", str(a.id), {"point": vp.name}))
    return out


def r7_impossible_movement(ctx: DayContext) -> list[Candidate]:
    max_speed = ctx.cfg["gps"]["max_speed_mps"]
    max_acc = ctx.cfg["gps"]["max_accuracy_m"]
    good = [f for f in ctx.fixes if not f[4] and (f[3] is None or f[3] <= max_acc)]
    jumps = []
    for a, b in zip(good, good[1:], strict=False):
        dt = (b[0] - a[0]).total_seconds()
        if dt <= 0:
            continue
        d = haversine_m(a[1], a[2], b[1], b[2])
        if d > 200 and d / dt > max_speed:
            jumps.append(
                {"at": b[0].isoformat(), "distance_m": round(d), "speed_mps": round(d / dt, 1)}
            )
    if len(jumps) >= ctx.p("R7")["min_jumps"]:
        return [
            Candidate(
                "R7", "day", ctx.day.isoformat(), {"jumps": len(jumps), "examples": jumps[:5]}
            )
        ]
    return []


def r8_mock(ctx: DayContext) -> list[Candidate]:
    mock_points = sum(1 for f in ctx.fixes if f[4])
    mock_events = [e for e in ctx.events if e.type == "mock_location"]
    if mock_points or mock_events:
        return [
            Candidate(
                "R8",
                "day",
                ctx.day.isoformat(),
                {"mock_points": mock_points, "mock_events": len(mock_events)},
            )
        ]
    return []


def r9_idle(ctx: DayContext) -> list[Candidate]:
    limit = ctx.p("R9")["max_idle_s"]
    out = []
    for st in ctx.stops:
        if (
            st.classification in ("unassigned_location", "base")
            and st.dwell_s > limit
            and _in_shift(ctx, st.started_at)
        ):
            out.append(
                Candidate(
                    "R9",
                    "stop",
                    str(st.started_at.timestamp()),
                    {
                        "dwell_s": st.dwell_s,
                        "place": st.classification,
                        "from": st.started_at.isoformat(),
                    },
                )
            )
    return out


def _in_shift(ctx: DayContext, t: datetime) -> bool:
    now = datetime.now(UTC)
    return any(s.started_at <= t <= (s.ended_at or now) for s in ctx.shifts)


def r10_gaps(ctx: DayContext) -> list[Candidate]:
    out = []
    min_gap = ctx.p("R10")["min_gap_s"]
    if ctx.summary and ctx.summary.n_gaps and ctx.summary.gap_s >= min_gap:
        out.append(
            Candidate(
                "R10",
                "day",
                ctx.day.isoformat(),
                {"gaps": ctx.summary.n_gaps, "gap_s": ctx.summary.gap_s},
            )
        )
    for sh in ctx.shifts:
        end = sh.ended_at or datetime.now(UTC)
        if (end - sh.started_at).total_seconds() > 1800 and not any(
            sh.started_at <= f[0] <= end for f in ctx.fixes
        ):
            out.append(
                Candidate(
                    "R10",
                    "shift",
                    str(sh.id),
                    {"shift_minutes": int((end - sh.started_at).total_seconds() / 60)},
                    "No location recorded during a shift",
                )
            )
    return out


def r11_outside_area(ctx: DayContext) -> list[Candidate]:
    area = ctx.db.scalar(
        select(AdminArea)
        .join(Team, Team.admin_area_id == AdminArea.id)
        .join(TeamMember, TeamMember.team_id == Team.id)
        .where(TeamMember.user_id == ctx.user.id, TeamMember.active_to.is_(None))
        .limit(1)
    )
    good = [f for f in ctx.fixes if not f[4]]
    if area is None or area.geom is None or len(good) < 20:
        return []
    poly = prep(to_shape(area.geom).buffer(0.002))  # ~200 m tolerance at the boundary
    outside = sum(1 for _, lat, lon, _, _ in good if not poly.contains(Point(lon, lat)))
    share = outside / len(good)
    if share > ctx.p("R11")["max_outside_share"]:
        return [
            Candidate(
                "R11",
                "day",
                ctx.day.isoformat(),
                {"outside_share": round(share, 2), "area": area.name},
            )
        ]
    return []


def r12_hours(ctx: DayContext) -> list[Candidate]:
    p = ctx.p("R12")
    start = (
        ctx.campaign.work_start_time
        if ctx.campaign and ctx.campaign.work_start_time
        else time.fromisoformat(p["start"])
    )
    end = (
        ctx.campaign.work_end_time
        if ctx.campaign and ctx.campaign.work_end_time
        else time.fromisoformat(p["end"])
    )
    outside = [si.sub for si in ctx.subs if not start <= ctx.local(si.sub.ended_at).time() <= end]
    if outside:
        return [
            Candidate(
                "R12",
                "day",
                ctx.day.isoformat(),
                {"surveys_outside_hours": len(outside), "window": f"{start:%H:%M}–{end:%H:%M}"},
            )
        ]
    return []


def r13_clock(ctx: DayContext) -> list[Candidate]:
    limit = ctx.p("R13")["max_offset_s"]
    bad = [
        si.sub
        for si in ctx.subs
        if si.sub.clock_offset_s is not None and abs(si.sub.clock_offset_s) > limit
    ]
    if bad or any(e.type == "time_change" for e in ctx.events):
        worst = max((abs(s.clock_offset_s or 0) for s in bad), default=0)
        return [
            Candidate(
                "R13", "day", ctx.day.isoformat(), {"worst_offset_s": worst, "surveys": len(bad)}
            )
        ]
    return []


ANSWER_TYPES = {"select_one", "select_multiple", "integer", "decimal"}


def _answer_fields(schema: dict[str, Any]) -> list[str]:
    names: list[str] = []

    def walk(fields: list[dict[str, Any]]) -> None:
        for f in fields:
            if f.get("type") == "group":
                walk(f.get("children", []))
            elif f.get("type") in ANSWER_TYPES:
                names.append(f["name"])

    walk(schema.get("fields", []))
    return names


def r14_quality(ctx: DayContext) -> list[Candidate]:
    p = ctx.p("R14")
    out: list[Candidate] = []
    by_assignment = Counter(
        si.sub.assignment_id
        for si in ctx.subs
        if si.sub.assignment_id and si.form_kind == FormKind.survey
    )
    for aid, n in by_assignment.items():
        if n > 1:
            out.append(
                Candidate(
                    "R14",
                    "assignment",
                    str(aid),
                    {"surveys_for_same_point": n},
                    "Duplicate surveys for the same point",
                )
            )
    by_form: dict[str, list[SubInfo]] = {}
    for si in ctx.subs:
        if si.form_kind == FormKind.survey:
            by_form.setdefault(si.id_string, []).append(si)
    for form_id, items in by_form.items():
        if len(items) < p["min_submissions"]:
            continue
        fields = _answer_fields(items[0].schema)
        if fields:
            patterns = Counter(tuple(str(si.sub.data.get(k)) for k in fields) for si in items)
            common, count = patterns.most_common(1)[0]
            if any(v != "None" for v in common) and count / len(items) >= p["same_answers_share"]:
                out.append(
                    Candidate(
                        "R14",
                        "straightlining",
                        form_id,
                        {"identical_surveys": count, "of": len(items)},
                        "Many surveys with identical answers",
                    )
                )
        if len(items) >= p["min_for_refusal"]:
            refused = sum(1 for si in items if si.sub.data.get("consent") == "no")
            if refused / len(items) >= p["refusal_share"]:
                out.append(
                    Candidate(
                        "R14",
                        "refusals",
                        form_id,
                        {"refusals": refused, "of": len(items)},
                        "Unusually many refusals",
                    )
                )
    return out


def r15_productivity(ctx: DayContext) -> list[Candidate]:
    p = ctx.p("R15")
    team_ids = ctx.db.scalars(
        select(TeamMember.team_id).where(
            TeamMember.user_id == ctx.user.id, TeamMember.active_to.is_(None)
        )
    )
    mates = list(
        ctx.db.scalars(
            select(TeamMember.user_id).where(
                TeamMember.team_id.in_(list(team_ids)),
                TeamMember.role_in_team == TeamRole.collector,
                TeamMember.active_to.is_(None),
            )
        )
    )
    if len(set(mates)) < p["min_team"]:
        return []
    done = {
        uid: n
        for uid, n in ctx.db.execute(
            select(CollectorDaySummary.user_id, CollectorDaySummary.completed).where(
                CollectorDaySummary.user_id.in_(mates), CollectorDaySummary.local_date == ctx.day
            )
        ).tuples()
    }
    worked = [n for n in done.values()]
    if len(worked) < p["min_team"]:
        return []
    median = statistics.median(worked)
    mine = done.get(ctx.user.id, 0)
    if median >= p["min_median"] and mine < median * p["share_of_median"]:
        return [
            Candidate("R15", "day", ctx.day.isoformat(), {"completed": mine, "team_median": median})
        ]
    return []


DAY_RULES: dict[str, Callable[[DayContext], list[Candidate]]] = {
    "R1": r1_outside_geofence,
    "R2": r2_too_short,
    "R3": r3_too_long,
    "R4": r4_too_fast,
    "R5": r5_short_stop,
    "R6": r6_not_visited,
    "R7": r7_impossible_movement,
    "R8": r8_mock,
    "R9": r9_idle,
    "R10": r10_gaps,
    "R11": r11_outside_area,
    "R12": r12_hours,
    "R13": r13_clock,
    "R14": r14_quality,
    "R15": r15_productivity,
}


# ---------------------------------------------------------------------------
# Persisting flags and risk
# ---------------------------------------------------------------------------
def _store(
    db: Session,
    user_id: uuid.UUID,
    campaign_id: uuid.UUID | None,
    day: date,
    cfg: dict[str, Any],
    candidates: list[Candidate],
    rules: set[str],
) -> None:
    existing = {
        (f.rule_id, f.subject_type, f.subject_id): f
        for f in db.scalars(
            select(Flag).where(
                Flag.collector_id == user_id, Flag.local_date == day, Flag.rule_id.in_(rules)
            )
        )
    }
    seen = set()
    new_high: list[Flag] = []
    for c in candidates:
        key = (c.rule_id, c.subject_type, c.subject_id)
        seen.add(key)
        flag = existing.get(key)
        is_new = flag is None
        if flag is None:
            flag = Flag(
                rule_id=c.rule_id, campaign_id=campaign_id, collector_id=user_id, local_date=day,
                subject_type=c.subject_type, subject_id=c.subject_id, status=FlagStatus.new,
            )  # fmt: skip
            db.add(flag)
        flag.severity = Severity(cfg["flags"][c.rule_id]["severity"])
        flag.title = c.title or TITLES[c.rule_id]
        flag.details = c.details
        if is_new and flag.severity == Severity.high:
            new_high.append(flag)
    # Untouched flags that no longer apply disappear; anything a person acted on stays.
    for key, flag in existing.items():
        if key not in seen and flag.status == FlagStatus.new:
            db.delete(flag)
    db.flush()
    if new_high and cfg.get("alerts", {}).get("push_high_flags", True):
        _notify_high_flags(db, user_id, new_high)


def _notify_high_flags(db: Session, collector_id: uuid.UUID, flags: list[Flag]) -> None:
    from app.services.push import notify, supervisors_of

    collector = db.get(User, collector_id)
    name = collector.full_name if collector else "A collector"
    for sup in supervisors_of(db, collector_id):
        for f in flags:
            notify(
                db,
                sup,
                "flag",
                f"High-priority flag: {name}",
                f.title,
                {"flag_id": str(f.id), "collector_id": str(collector_id)},
                dedupe_key=f"flag:{f.id}:{sup}",
            )


def update_risk(db: Session, user_id: uuid.UUID, day: date, cfg: dict[str, Any]) -> int:
    weights = cfg["risk"]["weights"]
    flags = db.scalars(
        select(Flag).where(
            Flag.collector_id == user_id,
            Flag.local_date == day,
            Flag.status != FlagStatus.dismissed,
        )
    )
    breakdown: dict[str, int] = {}
    score = 0
    for f in flags:
        breakdown[f.rule_id] = breakdown.get(f.rule_id, 0) + 1
        score += weights[f.severity.value]
    score = min(score, cfg["risk"]["max"])
    summary = db.get(CollectorDaySummary, (user_id, day))
    if summary is None:
        summary = CollectorDaySummary(user_id=user_id, local_date=day)
        db.add(summary)
    summary.risk_score = score
    summary.risk_breakdown = breakdown
    db.flush()
    return score


def evaluate_day(db: Session, user_id: uuid.UUID, day: date) -> list[Flag]:
    user = db.get(User, user_id)
    if user is None or user.role != Role.collector:
        return []
    ctx = build_context(db, user, day)
    enabled = {r for r, fn in DAY_RULES.items() if ctx.cfg["flags"][r]["enabled"]}
    candidates: list[Candidate] = []
    for rule in sorted(enabled, key=lambda r: int(r[1:])):
        candidates.extend(DAY_RULES[rule](ctx))
    _store(
        db,
        user_id,
        ctx.campaign.id if ctx.campaign else None,
        day,
        ctx.cfg,
        candidates,
        set(DAY_RULES),
    )
    update_risk(db, user_id, day, ctx.cfg)
    return list(
        db.scalars(select(Flag).where(Flag.collector_id == user_id, Flag.local_date == day))
    )


def evaluate_supervision(db: Session, today: date, now: datetime | None = None) -> int:
    """S1 (collector not supervised in N days) and S2 (high flag ignored for H hours)."""
    now = now or datetime.now(UTC)
    created = 0
    collectors = db.execute(
        select(User, Team)
        .join(TeamMember, TeamMember.user_id == User.id)
        .join(Team, Team.id == TeamMember.team_id)
        .where(
            TeamMember.role_in_team == TeamRole.collector,
            TeamMember.active_to.is_(None),
            User.role == Role.collector,
        )
    ).tuples()
    for user, team in collectors:
        campaign = db.get(Campaign, team.campaign_id)
        cfg = merged_config(campaign.config if campaign else None)
        candidates: list[Candidate] = []
        s1, s2 = cfg["flags"]["S1"], cfg["flags"]["S2"]
        if s1["enabled"]:
            since = today - timedelta(days=s1["days"])
            worked = db.scalar(
                select(CollectorDaySummary.local_date)
                .where(
                    CollectorDaySummary.user_id == user.id,
                    CollectorDaySummary.local_date > since,
                    CollectorDaySummary.n_submissions > 0,
                )
                .limit(1)
            )
            supervised = db.scalar(
                select(Submission.id)
                .join(FormVersion, FormVersion.id == Submission.form_version_id)
                .join(Form, Form.id == FormVersion.form_id)
                .where(
                    Form.kind == FormKind.supervision,
                    Submission.data["collector_username"].astext == user.username,
                    Submission.ended_at >= datetime.combine(since, time.min, tzinfo=UTC),
                )
                .limit(1)
            )
            if worked and not supervised:
                candidates.append(Candidate("S1", "collector", str(user.id), {"days": s1["days"]}))
        if s2["enabled"]:
            cutoff = now - timedelta(hours=s2["hours"])
            ignored = db.scalars(
                select(Flag).where(
                    Flag.collector_id == user.id,
                    Flag.severity == Severity.high,
                    Flag.status == FlagStatus.new,
                    Flag.created_at < cutoff,
                    Flag.rule_id.not_in(["S1", "S2"]),
                )  # fmt: skip
            )
            for f in ignored:
                candidates.append(
                    Candidate(
                        "S2",
                        "flag",
                        str(f.id),
                        {"flag": f.title, "since": f.created_at.isoformat(), "hours": s2["hours"]},
                    )
                )
        _store(db, user.id, campaign.id if campaign else None, today, cfg, candidates, {"S1", "S2"})
        update_risk(db, user.id, today, cfg)
        created += len(candidates)
    return created
