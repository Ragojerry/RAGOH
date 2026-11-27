# GPS simulator

Generates realistic synthetic collector days and pushes them to a RAGOH API, so stop detection and (from Phase 5) the anomaly flags can be demonstrated without walking around Abuja.

The code lives in `backend/app/simulator.py` (it is also used by the automated tests).

| Scenario | What it simulates |
|---|---|
| `honest` | Walks between assigned points, stays 6–15 min at each, fills a survey there |
| `cheater` | Stays at home all day and submits short surveys from there |
| `spoofer` | Uses a fake-GPS app: every point is flagged mock and jumps between houses |
| `gaps` | Honest day, but the phone is switched off for 45 minutes mid-day |
| `jitter` | Honest day with very noisy GPS (±25 m) |

## Run against staging

From a Claude Code cloud session (or any machine with the repo):

```bash
cd backend
uv run python -m app.simulator --api https://<staging-host> --username col1 --password '<DEMO_PASSWORD>' --scenario cheater
```

It signs in as that collector, takes up to 6 of their pending assignments, generates today's day (from 07:30 Lagos time) and pushes GPS points, the shift and the surveys. Then view the result:

`GET /api/v1/collectors/<col1 user id>/day?date=<today>` (as `sup1` or `admin`, in `/docs`).

> Simulated data is real data in the database. Use it on staging only.
