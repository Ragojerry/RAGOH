# Progress

| Phase | Status |
|---|---|
| Section 0 — Architecture & plan | Done (approved 26 Nov 2026) |
| 1 — Foundation + AWS staging deploy | **In review** (PR) |
| 2 — Mobile core (offline) | Not started |
| 3 — Tracking + stops | Not started |
| 4 — Routing | Not started |
| 5 — Flags + follow-up | Not started |
| 6 — Supervisor mobile mode | Not started |
| 7 — Supervisor web dashboard | Not started |
| 7b — Integrations (DHIS2, Elasticsearch, DIGIT HCM) | Not started |
| 8 — Hardening | Not started |

## Phase 1 — what's built
- Backend (`backend/`): FastAPI app, settings, PostgreSQL/PostGIS models, first Alembic migration, idempotent FCT seed.
- Auth: login, JWT access tokens (15 min), rotating refresh tokens (30 days) with reuse detection, logout, failed-login rate limiting (Redis or in-memory), disabled users locked out immediately.
- Roles + geographic scope enforced in the API (admin / manager / supervisor / collector).
- Admin APIs: users, teams and members, admin areas (incl. GeoJSON boundary import), campaigns and campaign config (merged with defaults), visit points (CSV/XLSX/GeoJSON import with dry run, auto area placement, assignments), `GET /me/assignments`.
- Audit log for logins and all admin changes.
- 27 automated tests (auth, scope, imports).
- Infrastructure: Terraform pilot server (EC2 + Caddy HTTPS + ECR + S3 + SSM secrets), CI and deploy workflows, nightly DB backups.

## Decisions
- AWS region eu-north-1 (Stockholm) — the account is on the AWS free plan, which only allows this region.
- AWS bootstrap done: OIDC role `ragoh-github-deploy`, state bucket `ragoh-tfstate-568898409880-eu-north-1`, budget `ragoh-monthly` (USD 100). GitHub variables and `production` environment set.
- Pilot: 4 collectors + 2 supervisors in FCT Abuja, on foot. See ADR 001 (single-server hosting) and ADR 002 (scope via area paths).
- Stack: React Native + Expo, FastAPI + PostGIS, OSRM (foot, FCT only) + VROOM, React + Vite dashboard.

## Open TODO(stub) items
- FCT area-council boundaries in the seed are approximate rectangles — import official boundaries (GRID3/OCHA) via `POST /api/v1/admin-areas/import`.
- MinIO is in the local compose file but not used until photo upload (Phase 2).
- Device-PIN endpoint deferred to Phase 2 (offline login is a mobile feature).
