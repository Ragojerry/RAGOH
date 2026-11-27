# Progress

| Phase | Status |
|---|---|
| Section 0 — Architecture & plan | Done (approved 26 Nov 2026) |
| 1 — Foundation + AWS staging deploy | In review (PR #2) |
| 2 — Mobile core (offline) | In review (PR #3) |
| 3 — Tracking + stops | **In review** (PR) |
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

## Phase 2 — what's built
- Backend: XLSForm upload compiled with pyxform to a JSON schema (supported subset validated at upload), form versioning, three seed pilot forms (household survey, supervision checklist, back-check), shifts, submissions with timing/GPS/clock-offset metadata, snapshot `GET /sync/pull`, idempotent batched `POST /sync/push`, photo upload. 38 backend tests.
- `packages/form-engine`: renders the compiled forms on the phone — XPath-subset expressions, relevance, required, constraints, calculations, repeats, choice filters, ODK-style removal of hidden answers. 20 tests using the real compiled forms.
- `packages/sync-core`: outbox/push/pull engine; safe retries, batch halving, photo upload after acceptance, local status overlay. 10 tests.
- `apps/mobile` (Expo SDK 58, Expo Router): online sign-in + offline PIN unlock, shared-phone protection, encrypted SQLite (SQLCipher), shifts with GPS, points list (nearest first) + map, point outcomes (not found / refused / skip), form filling with autosave drafts, photos (resized), My day, settings, automatic sync.
- CI: TypeScript type checks + tests + Android bundle; APK built on GitHub runners (`.github/workflows/mobile.yml`). Guide: `docs/MOBILE.md`.

## Phase 3 — what's built
- Phone: background GPS tracking during shifts only (Android foreground service), adaptive sampling (≈10 s moving / ≈90 s still), mock-location flag, battery and network type per point, auto-restart when the app was killed, GPS-off and tracking-restart events, 12-hour shift auto-end, one-time location notice (NDPA) and battery-optimisation tip, tracking status on the home screen.
- Server: monthly-partitioned GPS storage with idempotent bulk insert, device events, stop detection, stop classification and survey linking, gap detection inside shifts, daily collector summaries recomputed after every sync, `GET /collectors/{id}/day` (summary, stops, simplified track, surveys, phone events — scoped and audited), recompute endpoint, consent record, 90-day raw GPS retention job (daily cron).
- GPS simulator (`backend/app/simulator.py`, guide in `tools/gps-simulator/`) with honest / cheater / spoofer / gaps / jitter scenarios, used by the tests and runnable against staging.
- Tests: 60 backend (22 new for tracking), 11 sync engine. ADR 004.

## Decisions
- AWS region eu-north-1 (Stockholm) — the account is on the AWS free plan, which only allows this region.
- AWS bootstrap done: OIDC role `ragoh-github-deploy`, state bucket `ragoh-tfstate-568898409880-eu-north-1`, budget `ragoh-monthly` (USD 100). GitHub variables and `production` environment set.
- Pilot: 4 collectors + 2 supervisors in FCT Abuja, on foot. See ADR 001 (single-server hosting), ADR 002 (scope via area paths), ADR 003 (offline sync).
- Stack: React Native + Expo, FastAPI + PostGIS, OSRM (foot, FCT only) + VROOM, React + Vite dashboard.

## Open TODO(stub) items
- FCT area-council boundaries in the seed are approximate rectangles — import official boundaries (GRID3/OCHA) via `POST /api/v1/admin-areas/import`.
- MinIO is in the local compose file but not used until photo upload (Phase 2).
- PIN is handled on the phone only (salted, hashed; wiped after 5 wrong tries) — no server endpoint needed.
- Map uses online OpenStreetMap tiles until offline tiles arrive in Phase 4.
- Photos inside repeat groups are not uploaded yet (server accepts top-level photo fields only).
- APK is signed with the default debug key — fine for the pilot; a proper release key is needed before wider distribution (Phase 8).
- Barcode questions are typed by hand (camera scanning later).
- Background tracking has not been field-tested yet: test on the pilot team's actual phones (battery savers on Tecno/Infinix/itel are the main risk). Tracking does not restart by itself after a phone reboot until the app is opened.
