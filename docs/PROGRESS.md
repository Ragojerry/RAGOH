# Progress

| Phase | Status |
|---|---|
| Section 0 — Architecture & plan | Done (approved 26 Nov 2026) |
| 1 — Foundation + AWS staging deploy | In review (PR #2) |
| 2 — Mobile core (offline) | In review (PR #3) |
| 3 — Tracking + stops | In review (PR #4) |
| 4 — Routing | In review (PR #5) |
| 5 — Flags + follow-up | In review (PR #6) |
| 6 — Supervisor mobile mode | In review (PR #7) |
| 7 — Supervisor web dashboard | In review (PR #8) |
| 7b — Integrations (DHIS2, Elasticsearch, DIGIT HCM) | **In review** (PR) |
| 8 — Hardening | In review (PR #9) |

## Phase 7b — what's built
- Connector framework: scheduled runner (cron every 5 min, per-connector `every_min`), per-campaign state and cursors with a 5-minute look-back, savepoint per connector, partial-failure progress, admin API (status, run now, DHIS2 preview).
- Elasticsearch: submissions, stops, day summaries, flags; index templates (geo_point, flattened answers); idempotent upserts; pseudonymous labels option.
- DHIS2: 10 daily indicators per mapped ward/LGA via `dataValueSets` (`CREATE_AND_UPDATE`, dry run), last N days re-sent.
- DIGIT HCM: visit outcomes, supervision/back-check results and closed flags as event batches, exactly once; DIGIT egov-user OAuth + RequestInfo, or HMAC-signed webhook; receiving endpoint configurable.
- Dashboard **Integrations** page (admins): status, errors, run now, settings, DHIS2 preview. Integration settings admin-only (fixes managers being able to redirect data).
- Docs: `docs/INTEGRATIONS.md`, ADR 010. Tests: 12 new (backend 138).

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
- Tests: backend 106 (17 new), sync engine 20 (8 new, including a vector sealed by the phone library and opened by the server). ADR 007.

## Phase 7 — what's built
- Web dashboard (`apps/web`, React + Vite + MapLibre): live map, route replay with planned vs actual route and stops sized by dwell time, team overview (tiles, daily charts, sortable collector comparison), coverage (map + table + supervision coverage), flags inbox with every follow-up action, exports. Guide: `docs/DASHBOARD.md`.
- Server: `/map/areas.geojson`, `/map/points.geojson`, `/analytics/team`, `/analytics/coverage`, `/exports/{submissions,stops,flags,points,tracks}` (CSV/XLSX/GeoJSON, scoped, audited, formula-safe); planned route in the day replay.
- Pilot server: Caddy serves the dashboard at the site root; the deploy builds and ships it; smoke test checks it.
- Fixed: managers saw every team on the live view; now limited to their geography.
- Simulator CLI marks visits done like the phone.
- Checked in a real browser against a local API with simulated days (live map, replay, team, coverage, flags drawer → resolve, Excel export download).
- Tests: backend 116 (10 new), web 6. ADR 008.

## Phase 8 — what's built
- Privacy notice (NDPA) generated from config, versioned, required at first sign-in and after changes; readable in the app and on the dashboard.
- Lost or stolen phone: block (sessions revoked, sync and sign-in refused) and remote erase; Phones page in the dashboard with crash counts.
- Audit log viewer (managers/admins); retention extended to device events, notifications and audit log.
- Mobile data: gzip uploads, ETag pulls, fewer heartbeats — ≈1.5 → 0.6 MB/day estimated; battery and data measured per shift by the phones and shown on the Phones page.
- Performance: composite indexes; load test with 1,000 collectors (live view 0.55 s, 3,000-point replay 0.08 s).
- Translations framework (5 languages wired, English complete, others await translators), crash capture on the phone, JSON logs and optional Sentry on the server, optional release signing of the APK.
- Guides: `docs/guides/COLLECTOR_GUIDE.md`, `docs/guides/SUPERVISOR_GUIDE.md`; operations in `docs/DEPLOYMENT.md`; measurements in `docs/MEASUREMENTS.md`. ADR 009.
- Tests: backend 126 (10 new), sync engine 21, mobile 6, web 6.

## Decisions
- AWS region eu-north-1 (Stockholm) — the account is on the AWS free plan, which only allows this region.
- AWS bootstrap done: OIDC role `ragoh-github-deploy`, state bucket `ragoh-tfstate-568898409880-eu-north-1`, budget `ragoh-monthly` (USD 100). GitHub variables and `production` environment set.
- Pilot: 4 collectors + 2 supervisors in FCT Abuja, on foot. See ADR 001 (single-server hosting), ADR 002 (scope via area paths), ADR 003 (offline sync).
- Stack: React Native + Expo, FastAPI + PostGIS, OSRM (foot, FCT only) + VROOM, React + Vite dashboard.

## Open TODO(stub) items
- Push notifications need the one-time Firebase + Expo setup in `docs/MOBILE.md` → Push (owner action). Until then supervisors get in-app alerts only.
- Background tracking, heartbeats and QR handover still need testing on real pilot phones (Tecno/Infinix/itel).
- The seed has area councils but no wards; coverage by ward appears once ward boundaries are imported.
- Dashboard is light mode only (the dark status colours failed the colour-blind check).
- Hausa, Yorùbá, Igbo and Pidgin translations need native translators (`pnpm --filter @ragoh/mobile i18n:extract` produces the list); the privacy notice is English only for now.
- Battery use is not measured yet: run the protocol in `docs/MEASUREMENTS.md` during the first pilot week.
- Release signing key not created yet (owner action, `docs/DEPLOYMENT.md` → Signing).
- DIGIT HCM: confirm with the HCM team which endpoint/entity receives RAGOH events before switching the connector on (`docs/INTEGRATIONS.md`).
- FCT area-council boundaries in the seed are approximate rectangles — import official boundaries (GRID3/OCHA) via `POST /api/v1/admin-areas/import`.
- PIN is handled on the phone only (salted, hashed; wiped after 5 wrong tries) — no server endpoint needed.
- The offline map and road routing need one run of the **Routing data** workflow after the first deploy (until then the app uses OpenStreetMap online tiles and straight-line estimates). The workflow itself has not run yet.
- Photos inside repeat groups are not uploaded yet (server accepts top-level photo fields only).
- Barcode questions are typed by hand (camera scanning later).
- Background tracking has not been field-tested yet: test on the pilot team's actual phones (battery savers on Tecno/Infinix/itel are the main risk). Tracking does not restart by itself after a phone reboot until the app is opened.
