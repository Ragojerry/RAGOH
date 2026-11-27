# Progress

| Phase | Status |
|---|---|
| Section 0 — Architecture & plan | Done (approved 26 Nov 2026) |
| 1 — Foundation + AWS staging deploy | In review (PR #2) |
| 2 — Mobile core (offline) | In review (PR #3) |
| 3 — Tracking + stops | In review (PR #4) |
| 4 — Routing | In review (PR #5) |
| 5 — Flags + follow-up | In review (PR #6) |
| 6 — Supervisor mobile mode | **In review** (PR) |
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

## Phase 4 — what's built
- Server: walking route to a point (`GET /routes/to`, OSRM with readable turn-by-turn steps) and an optimised day plan (`POST /routes/day-plan`, VROOM with priorities, time windows and visit time), both falling back to straight-line estimates; plans stored in `routes_planned`; MapLibre style for the self-hosted FCT map (`GET /map/style.json`).
- Pilot server: OSRM, VROOM and a vector-tile server added to the stack, map tiles/fonts served by Caddy; deploy copies routing/map data from a new S3 bucket.
- **Routing data** workflow: builds the FCT foot network and offline map from OpenStreetMap/Protomaps, smoke-tests a route in Abuja, uploads to S3 (manual + monthly).
- Phone: "Plan my route" (numbered visit order, walk distance/time per leg, route line on the map, "out of date — re-plan" when visits change), offline plan with the same algorithm (`packages/routing`), walking route + steps on each point, offline map download for the team's area (Settings).
- Tests: backend 73 (13 new, OSRM/VROOM mocked), routing package 4. ADR 005.

## Phase 5 — what's built
- Rules engine (R1–R15 + S1–S2) with all thresholds in campaign config; runs after each sync and nightly; idempotent re-evaluation keeps flags a person acted on.
- Explainable daily risk score per collector (severity weights, per-rule breakdown).
- API: flags inbox with filters (`GET /flags`), flag detail with timeline, follow-up actions (`POST /flags/{id}/actions`), risk list (`GET /risk`), messages (`/messages`), back-checks (`GET /backchecks`) with automatic answer comparison.
- Phone: Messages screen (questions about flagged work with offline reply, two-way messages, call supervisor), back-check and revisit tasks marked in the points list with the supervisor's note, back-check form used for back-check tasks.
- Fixed: pushing phone events (GPS off, tracking restarted) failed on the server since Phase 3 — now tested.
- Tests: backend 89 (16 new for flags), sync engine 12. ADR 006.

## Phase 6 — what's built
- Server: heartbeats and `GET /team/live` (each collector's last position, status moving / at point / idle / off shift / no signal, progress, flags, risk); `GET /team/assignments` and `POST /assignments/reassign` (open points within a team, audited, both collectors notified); `GET /routes/between` (go to a collector); team roster in the sync pull.
- Push notifications via Expo: queued in a `notifications` table in the same transaction, sent after commit, deduplicated; high-severity flags, messages (any path), back-checks, reassignment; `python -m app.maintenance watch` every 10 min for "no signal during shift" and "shift not started", with quiet hours. Dead tokens are dropped.
- Device-to-device relay: per-collector key (`GET /me/relay-key`), sealed bundles (ChaCha20-Poly1305 + zlib) carried by a supervisor's phone and stored on the server as the collector's own records; tampering, wrong collector and other teams' supervisors are refused; old bundles don't count as a wrong phone clock.
- Phone: supervisor mode (team home, live map, collector detail, go-to route, route replay, flags inbox + all actions, spot check prefilled, move points, messages to collectors), cached for offline with "as of" times; collector heartbeat on shift; push registration and notification taps; animated-QR handover (collector shows, supervisor scans).
- Fixed: messages pushed from a supervisor's phone weren't limited to users the supervisor can see.
- Tests: backend 107 (17 new), sync engine 20 (8 new, including a vector sealed by the phone library and opened by the server). ADR 007.

## Decisions
- AWS region eu-north-1 (Stockholm) — the account is on the AWS free plan, which only allows this region.
- AWS bootstrap done: OIDC role `ragoh-github-deploy`, state bucket `ragoh-tfstate-568898409880-eu-north-1`, budget `ragoh-monthly` (USD 100). GitHub variables and `production` environment set.
- Pilot: 4 collectors + 2 supervisors in FCT Abuja, on foot. See ADR 001 (single-server hosting), ADR 002 (scope via area paths), ADR 003 (offline sync).
- Stack: React Native + Expo, FastAPI + PostGIS, OSRM (foot, FCT only) + VROOM, React + Vite dashboard.

## Open TODO(stub) items
- Push notifications need the one-time Firebase + Expo setup in `docs/MOBILE.md` → Push (owner action). Until then supervisors get in-app alerts only.
- Background tracking, heartbeats and QR handover still need testing on real pilot phones (Tecno/Infinix/itel).
- FCT area-council boundaries in the seed are approximate rectangles — import official boundaries (GRID3/OCHA) via `POST /api/v1/admin-areas/import`.
- MinIO is in the local compose file but not used until photo upload (Phase 2).
- PIN is handled on the phone only (salted, hashed; wiped after 5 wrong tries) — no server endpoint needed.
- The offline map and road routing need one run of the **Routing data** workflow after the first deploy (until then the app uses OpenStreetMap online tiles and straight-line estimates). The workflow itself has not run yet.
- Photos inside repeat groups are not uploaded yet (server accepts top-level photo fields only).
- APK is signed with the default debug key — fine for the pilot; a proper release key is needed before wider distribution (Phase 8).
- Barcode questions are typed by hand (camera scanning later).
- No push notifications yet (Phase 6): collectors see supervisor questions after their next sync.
- Background tracking has not been field-tested yet: test on the pilot team's actual phones (battery savers on Tecno/Infinix/itel are the main risk). Tracking does not restart by itself after a phone reboot until the app is opened.
