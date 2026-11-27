"""DHIS2: daily aggregate indicators per ward/LGA, sent with the dataValueSets API.

Only what is mapped is sent: `org_units` maps RAGOH area codes to DHIS2 org unit uids, and
`data_elements` maps RAGOH indicators to data element uids. Each run recomputes the last
`days_back` days (phones sync late, so recent numbers change) and sends them with
importStrategy=CREATE_AND_UPDATE — re-sending a day overwrites it, it never doubles it.

Indicators (per org unit, per day, period yyyyMMdd):
  visits_done           points visited that day (completed + not found + refused)
  visits_completed      points with a completed survey that day
  visits_not_found_or_refused
  surveys               survey submissions finished that day
  points_total          points in the area (at the time of the run)
  coverage_pct          % of the area's points visited by the end of that day
  flags_total, flags_high   flags raised for that day's work (dismissed ones excluded)
  spot_checks           supervision checklists done that day about the area's collectors
  active_collectors     collectors with activity that day
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    AdminArea,
    Assignment,
    AssignmentKind,
    AssignmentStatus,
    Campaign,
    CollectorDaySummary,
    Flag,
    FlagStatus,
    Form,
    FormKind,
    FormVersion,
    Severity,
    Submission,
    Team,
    TeamMember,
    TeamRole,
    User,
    VisitPoint,
)
from app.integrations.base import ConfigError, RunResult, client

INDICATORS = [
    "visits_done", "visits_completed", "visits_not_found_or_refused", "surveys", "points_total",
    "coverage_pct", "flags_total", "flags_high", "spot_checks", "active_collectors",
]  # fmt: skip
DONE = (AssignmentStatus.completed, AssignmentStatus.not_found, AssignmentStatus.refused)


def compute(
    db: Session, campaign: Campaign, area_codes: list[str], days: list[date]
) -> dict[tuple[str, date], dict[str, float]]:
    """Indicator values per (area code, day). Exposed for the dashboard preview and tests."""
    tz = ZoneInfo(campaign.timezone)
    areas = {
        a.code: a.path for a in db.scalars(select(AdminArea).where(AdminArea.code.in_(area_codes)))
    }
    missing = sorted(set(area_codes) - set(areas))
    if missing:
        raise ConfigError(f"Unknown RAGOH area codes in org_units: {', '.join(missing[:10])}")

    def containing(path: str | None) -> list[str]:
        return [c for c, p in areas.items() if path and path.startswith(p)]

    def local_day(t: datetime | None) -> date | None:
        return t.astimezone(tz).date() if t else None

    out: dict[tuple[str, date], dict[str, float]] = defaultdict(
        lambda: dict.fromkeys(INDICATORS, 0.0)
    )
    dayset = set(days)
    last_end = datetime.combine(max(days) + timedelta(days=1), time.min, tzinfo=tz)

    # Points and visits (via the point's area).
    points_in: dict[str, int] = defaultdict(int)
    done_by: dict[str, list[date]] = defaultdict(list)
    for path, status, changed in db.execute(
        select(AdminArea.path, Assignment.status, Assignment.status_changed_at)
        .select_from(VisitPoint)
        .join(AdminArea, AdminArea.id == VisitPoint.admin_area_id)
        .outerjoin(
            Assignment,
            and_(
                Assignment.visit_point_id == VisitPoint.id, Assignment.kind == AssignmentKind.visit
            ),
        )
        .where(VisitPoint.campaign_id == campaign.id, VisitPoint.deleted_at.is_(None))
    ):
        for code in containing(path):
            points_in[code] += 1
            if status in DONE and changed is not None and changed < last_end:
                d = local_day(changed)
                assert d is not None
                done_by[code].append(d)
                if d in dayset:
                    v = out[(code, d)]
                    v["visits_done"] += 1
                    if status == AssignmentStatus.completed:
                        v["visits_completed"] += 1
                    else:
                        v["visits_not_found_or_refused"] += 1

    # Surveys (via the point's area).
    start = datetime.combine(min(days), time.min, tzinfo=tz)
    for path, ended in db.execute(
        select(AdminArea.path, Submission.ended_at)
        .join(FormVersion, FormVersion.id == Submission.form_version_id)
        .join(Form, Form.id == FormVersion.form_id)
        .join(VisitPoint, VisitPoint.id == Submission.visit_point_id)
        .join(AdminArea, AdminArea.id == VisitPoint.admin_area_id)
        .where(
            Form.campaign_id == campaign.id,
            Form.kind == FormKind.survey,
            Submission.ended_at >= start,
            Submission.ended_at < last_end,
        )
    ):
        d = local_day(ended)
        for code in containing(path):
            if d in dayset:
                out[(code, d)]["surveys"] += 1  # type: ignore[index]

    # People-based indicators use the collector's team area.
    team_area: dict[Any, str] = {}
    username_to_user: dict[str, Any] = {}
    for uid, uname, path in db.execute(
        select(User.id, User.username, AdminArea.path)
        .join(TeamMember, TeamMember.user_id == User.id)
        .join(Team, Team.id == TeamMember.team_id)
        .join(AdminArea, AdminArea.id == Team.admin_area_id)
        .where(Team.campaign_id == campaign.id, TeamMember.role_in_team == TeamRole.collector)
    ):
        team_area.setdefault(uid, path)
        username_to_user.setdefault(uname, uid)

    for uid, d, sev in db.execute(
        select(Flag.collector_id, Flag.local_date, Flag.severity).where(
            Flag.campaign_id == campaign.id,
            Flag.local_date.in_(days),
            Flag.status != FlagStatus.dismissed,
        )
    ):
        for code in containing(team_area.get(uid)):
            out[(code, d)]["flags_total"] += 1
            if sev == Severity.high:
                out[(code, d)]["flags_high"] += 1

    for uname, ended in db.execute(
        select(Submission.data["collector_username"].astext, Submission.ended_at)
        .join(FormVersion, FormVersion.id == Submission.form_version_id)
        .join(Form, Form.id == FormVersion.form_id)
        .where(
            Form.campaign_id == campaign.id,
            Form.kind == FormKind.supervision,
            Submission.ended_at >= start,
            Submission.ended_at < last_end,
        )
    ):
        d = local_day(ended)
        for code in containing(team_area.get(username_to_user.get(uname or ""))):
            if d in dayset:
                out[(code, d)]["spot_checks"] += 1  # type: ignore[index]

    for uid, d in db.execute(
        select(CollectorDaySummary.user_id, CollectorDaySummary.local_date).where(
            CollectorDaySummary.user_id.in_(list(team_area)),
            CollectorDaySummary.local_date.in_(days),
            (CollectorDaySummary.n_submissions > 0) | (CollectorDaySummary.n_gps_points > 0),
        )
    ):
        for code in containing(team_area.get(uid)):
            out[(code, d)]["active_collectors"] += 1

    for code in areas:
        total = points_in.get(code, 0)
        for d in days:
            v = out[(code, d)]
            v["points_total"] = total
            done_by_day = sum(1 for x in done_by.get(code, []) if x <= d)
            v["coverage_pct"] = round(100 * done_by_day / total, 1) if total else 0.0
    return dict(out)


def run(
    db: Session, campaign: Campaign, cfg: dict[str, Any], cursor: dict[str, Any], now: datetime
) -> RunResult:
    url, user = cfg.get("url"), cfg.get("username")
    org_units: dict[str, str] = cfg.get("org_units") or {}
    elements: dict[str, str] = cfg.get("data_elements") or {}
    password = get_settings().dhis2_password
    if not url or not user or not password:
        raise ConfigError(
            "Set integrations.dhis2.url and username in the campaign config, and RAGOH_DHIS2_PASSWORD on the server"
        )
    if not org_units or not elements:
        raise ConfigError("Map at least one area (org_units) and one indicator (data_elements)")
    unknown = sorted(set(elements) - set(INDICATORS))
    if unknown:
        raise ConfigError(
            f"Unknown indicators in data_elements: {', '.join(unknown)}. Known: {', '.join(INDICATORS)}"
        )

    today = now.astimezone(ZoneInfo(campaign.timezone)).date()
    days = [today - timedelta(days=i) for i in range(int(cfg.get("days_back") or 7))]
    values = compute(db, campaign, list(org_units), days)
    data_values = []
    for (code, d), v in sorted(values.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        for ind, de in elements.items():
            dv: dict[str, str] = {
                "dataElement": de, "period": d.strftime("%Y%m%d"), "orgUnit": org_units[code],
                "value": str(int(v[ind])) if float(v[ind]).is_integer() else str(v[ind]),
            }  # fmt: skip
            if cfg.get("category_option_combo"):
                dv["categoryOptionCombo"] = str(cfg["category_option_combo"])
            if cfg.get("attribute_option_combo"):
                dv["attributeOptionCombo"] = str(cfg["attribute_option_combo"])
            data_values.append(dv)
    body: dict[str, Any] = {"dataValues": data_values}
    if cfg.get("data_set"):
        body["dataSet"] = cfg["data_set"]

    params = {
        "importStrategy": "CREATE_AND_UPDATE",
        "dryRun": "true" if cfg.get("dry_run") else "false",
    }
    with client(base_url=str(url).rstrip("/"), auth=(str(user), password)) as h:
        r = h.post("/api/dataValueSets", params=params, json=body)
        if r.status_code in (401, 403):
            raise ConfigError(f"DHIS2 refused the credentials ({r.status_code})")
        r.raise_for_status()
        res = r.json()
    summary = res.get("response", res)  # DHIS2 ≥ 2.38 wraps the import summary in "response"
    counts = summary.get("importCount", {})
    conflicts = summary.get("conflicts") or []
    if summary.get("status") == "ERROR" or (
        conflicts and not counts.get("imported") and not counts.get("updated")
    ):
        raise RuntimeError(f"DHIS2 import failed: {conflicts[:3] or summary.get('description')}")
    return RunResult(
        stats={
            "values_sent": len(data_values),
            "days": len(days),
            "org_units": len(org_units),
            "imported": counts.get("imported", 0),
            "updated": counts.get("updated", 0),
            "ignored": counts.get("ignored", 0),
            "conflicts": len(conflicts),
            "dry_run": bool(cfg.get("dry_run")),
        },  # fmt: skip
        cursor={"last_period": today.isoformat()},
    )
