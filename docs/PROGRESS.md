# Progress

| Phase | Status |
|---|---|
| Section 0 — Architecture & plan | In review (PR) |
| 1 — Foundation + AWS staging deploy | Not started |
| 2 — Mobile core (offline) | Not started |
| 3 — Tracking + stops | Not started |
| 4 — Routing | Not started |
| 5 — Flags + follow-up | Not started |
| 6 — Supervisor mobile mode | Not started |
| 7 — Supervisor web dashboard | Not started |
| 8 — Hardening | Not started |

## Decisions
- AWS region eu-north-1 (Stockholm) — account is on the AWS free plan, which only allows this region.
- AWS bootstrap done: OIDC role `ragoh-github-deploy`, state bucket `ragoh-tfstate-568898409880-eu-north-1`, budget `ragoh-monthly` (USD 100). GitHub variables and `production` environment set.
- Proposed in ARCHITECTURE.md: React Native + Expo, FastAPI + PostGIS, OSRM + VROOM, React + Vite dashboard.

## Open TODO(stub) items
_None yet._
