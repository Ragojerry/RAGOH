# MASTER BUILD PROMPT — RAGOH: Field Supervision, Survey & Route-Tracking App

> Paste this whole prompt into your coding agent (Claude Code, Cursor, etc.). Replace anything in `[BRACKETS]`. The agent should build one phase at a time and stop for review after each phase.

---

## 0. Your role and how to work

You are a senior full-stack engineer and solutions architect with experience in offline-first mobile apps, geospatial systems (PostGIS, OSRM, MapLibre) and field data collection for public health programmes in low-connectivity settings.

Build a production-ready system called **RAGOH**. It has three parts:

1. **Mobile app** (Android first, offline-first) with two modes from the same app, chosen by the user's role at login: **Collector mode** and **Supervisor mode** (supervisors track and follow up their assigned team from the field).
2. **Web dashboard** for supervisors, managers and admins.
3. **Backend API and processing services**.

Rules for how you work:
- Before writing code, give me: (a) your assumptions, (b) the final architecture diagram (Mermaid), (c) the database schema, (d) the repo structure. Wait for my "go".
- Build in the phases in Section 10. **Stop after each phase**, show me what was built, how to run it and how to test it, and wait for approval.
- Never use placeholder logic for core features (stop detection, route optimisation, anomaly rules, sync). If something must be stubbed, label it `TODO(stub)` and tell me.
- Write tests for every algorithm. Keep all thresholds in config, never hard-coded.
- Everything runs locally with `docker compose up` plus one command to run the mobile app.

---

## 1. Context

- Deployment: public health field campaigns in **Nigeria** (e.g. household surveys, SMC/ITN/immunisation supervision, facility visits). Many areas have weak or no mobile data, unreliable power, and incomplete road maps.
- Devices: low-to-mid range Android phones (Android 8+, 2–3 GB RAM, limited storage), often shared and with poor batteries.
- Users: hundreds to a few thousand data collectors per campaign, organised by State → LGA → Ward → Team.
- Data protection: must comply with the **Nigeria Data Protection Act 2023 (NDPA)**. Location tracking is personal data.

---

## 2. User roles and permissions (RBAC)

| Role | Can do |
|---|---|
| **Data Collector** | Log in, start/end shift, see assigned visit points, get routes, fill surveys, sync, see own summary |
| **Supervisor** | On **mobile and web**, for their assigned team only: live team map, route replay, stops, flags, follow-up actions, back-checks, supervision checklists, assign/reassign points. Their own movement is also tracked during their shift |
| **Manager / M&E** | All teams in their geography, analytics, exports, threshold configuration |
| **Admin** | Users, teams, geography hierarchy, forms, visit-point uploads, system settings, audit logs |

Access is restricted by geography (a supervisor sees only their team/ward/LGA).

---

## 3. Core features

### 3.1 Visit points and assignments
- Admin uploads visit points (CSV/GeoJSON/XLSX): `id, name, type (household/settlement/facility/school/other), lat, lon, admin area, priority, expected visit window, assigned collector, planned date`.
- Optional import of settlement points or boundaries from external sources (e.g. GRID3 or OSM extracts).
- Each point has a **geofence radius** (default from config, e.g. 50 m for households, 150 m for settlements), editable per type.
- Statuses: `pending → en_route → arrived → in_progress → completed / not_found / refused / skipped (with reason)`.
- Supervisors can reassign points, add ad-hoc points and set planned dates.

### 3.2 Route suggestion (shortest route)
- **Single destination:** shortest road route from the collector's current location to a chosen point, showing distance, ETA and turn-by-turn steps on the map.
- **Multi-stop day plan:** best order to visit all of today's assigned points (travelling-salesman / vehicle-routing problem), starting from current location or a set start point, with optional return to base. Respect priority and time windows where given.
- Travel modes: walking, motorcycle, car (configurable speed profiles).
- **Engine:** self-hosted **OSRM** (or Valhalla) using a Nigeria OpenStreetMap extract for routing, and **VROOM** for multi-stop optimisation.
- **Offline fallback (required):** if there is no connection, the app computes a nearest-neighbour + 2-opt ordering on the device using straight-line (haversine) distance, clearly labelled "estimated – offline". Cache the last server-optimised plan and its route geometry on the device.
- Re-optimise when the collector skips a point or is reassigned new points.
- Where roads are missing, show a straight line to the point with bearing and distance.

### 3.3 Location tracking
- Tracking runs **only during an active shift** (collector taps "Start shift" / "End shift"). Auto-end the shift after a configurable maximum (e.g. 12 h) with a flag.
- Android foreground service with a persistent notification; survives app being backgrounded and phone reboot during a shift.
- Adaptive sampling to save battery: e.g. every 10–15 s when moving, every 60–120 s when stationary (use the activity-recognition / motion API). All intervals in config.
- Each GPS point stores: `timestamp (device + server-received), lat, lon, accuracy, altitude, speed, bearing, battery level, is_mock_location, provider, network status`.
- Discard or tag points with poor accuracy (> configurable metres).
- Points are stored in local SQLite first, then synced in compressed batches.
- Detect and record: mock/fake GPS apps, GPS switched off, airplane mode, device time changed, app force-stopped (gaps in the track).

### 3.4 Stop detection and time-at-stop
Implement a **stay-point detection algorithm** (run on the backend over the synced track, with a light version on the device for live display):
- A stop = the collector stays within radius **R** (default 50 m) for at least **T** minutes (default 3 min).
- For each stop compute: start time, end time, **dwell duration**, centroid, number of GPS points, and nearest visit point + distance.
- Classify stops: `at_assigned_point` (inside a geofence), `near_assigned_point`, `unassigned_location`, `base/home` (repeated long stops at a known location).
- Link stops to survey submissions (a submission whose GPS and time fall inside a stop).
- Per collector per day, report: number of stops, stops at assigned points vs elsewhere, total/average/median dwell time, travel time vs stationary time, distance travelled, first and last activity time, points completed.
- Unit-test this with synthetic GPS traces, including GPS jitter, signal gaps and slow walking.

### 3.5 Surveys and supervision checklists
- Form engine on the device, ideally able to **import XLSForm** (ODK/Kobo style) so existing forms can be reused. Support: text, integer, decimal, select one/multiple, date/time, GPS point, photo, signature, barcode/QR, repeat groups, skip logic (relevance), constraints, calculated fields, required fields.
- Every submission automatically records: start time, end time, **duration**, GPS at start and end, accuracy, device ID, app version, collector ID, linked visit point and linked stop.
- Form types: (a) **survey forms** filled by collectors, (b) **supervision checklists** filled by supervisors during spot checks, (c) **back-check forms** (short re-interview to verify a sample of submissions).
- Form versioning; old submissions keep their form version.
- Drafts saved automatically; nothing is lost if the app crashes.

### 3.6 Abnormal behaviour detection (flags)
A rules engine runs on the backend after each sync (and on a schedule). Each rule has an ID, description, severity (low/medium/high), threshold values in config, and can be switched on or off per campaign. Minimum rules:

| # | Rule | Example trigger |
|---|---|---|
| R1 | Submission outside geofence | Survey GPS > geofence radius from assigned point |
| R2 | Too-short interview | Duration < X min (per form) |
| R3 | Too-long interview | Duration > Y min |
| R4 | Too many submissions too fast | > N submissions within M minutes, or overlapping times |
| R5 | Short stop at assigned point | Dwell < T min but point marked completed |
| R6 | Completed without visiting | Point marked completed with no stop inside its geofence |
| R7 | Impossible movement | Speed between consecutive points > configurable max (teleporting) |
| R8 | Mock location detected | `is_mock_location = true` or known spoofing app |
| R9 | Long idle during shift | Stationary > Z min at an unassigned location |
| R10 | Tracking gaps | No GPS points for > G min during an active shift |
| R11 | Route deviation | Far outside the assigned area / ward boundary |
| R12 | Working outside hours | Activity outside the configured work window |
| R13 | Device time tampering | Device time differs from server time by > S min |
| R14 | Data-quality patterns | Duplicate submissions, same answers repeated across many forms (straight-lining), many "don't know"/refusals |
| R15 | Low productivity | Points completed per day far below team median (e.g. < 50% of median) |

Also compute a daily **collector risk score** (weighted sum of flags) so supervisors can sort by who needs attention first. Keep the weighting simple and explainable — every score must show which flags caused it.

### 3.7 Supervisor follow-up workflow
- **Flags inbox:** filter by severity, rule, collector, date, area; status `new → acknowledged → under_review → resolved / dismissed (false positive)` with a required comment.
- Follow-up actions from a flag: add note, call collector (tap-to-call), send in-app message, **assign a back-check** of the affected point to another collector or the supervisor, request re-visit, escalate to manager.
- Every action is logged (who, what, when) and visible on the flag's timeline.
- Collector gets a notification and can respond with an explanation in the app.
- Dismissal reasons are recorded so thresholds can be tuned later.

### 3.8 Supervisor web dashboard
- **Live map:** latest position of each collector on shift (colour by status: moving / at a point / idle / offline / last seen time), assigned points coloured by status, ward/LGA boundaries.
- **Route replay:** pick collector + date → play back the track on a timeline with speed control; show stops as circles sized by dwell time; click a stop to see time, duration and linked submissions; overlay planned route vs actual route.
- **Collector day summary:** distance, stops, time at points, completed vs assigned, flags, risk score.
- **Team overview:** table + charts comparing collectors (coverage %, avg interview duration, avg dwell time, flags per 100 submissions).
- **Coverage:** map and table of visited vs not visited points by area.
- **Exports:** CSV/XLSX of submissions, stops, tracks (GeoJSON) and flags; filtered by area and date.
- Works on a laptop over a slow connection (paginate, simplify track geometry before sending).

### 3.9 Collector mobile screens
1. Login (with offline login after first successful login) and PIN for shared devices.
2. Home: shift start/end, today's summary, sync status (pending items, last sync time).
3. Map + list of today's points with distance from me, "Optimise my route" button, navigation to the next point.
4. Point detail: info, status, start survey, mark not found / refused (reason required).
5. Form filling.
6. My day: stops, completed points, distance, any flags/messages from the supervisor and reply.
7. Settings: language, offline map download per LGA, storage use, app version, send logs.
- Languages: English first, built for translation (Hausa, Yoruba, Igbo, Pidgin later).
- Large buttons, high contrast, usable in bright sunlight, works on small screens.

### 3.10 Supervisor mobile app (Supervisor mode)
Supervisors spend most of the day in the field, so the mobile app must let them track and follow up their **assigned team** without a laptop. Same app and codebase as Collector mode; the screens shown depend on the role.

**Team assignment**
- Each supervisor is linked to one or more teams; each team has its data collectors and area (wards/settlements). Admin or manager sets this on the web; it syncs to the supervisor's phone.
- The supervisor sees **only their assigned collectors** (enforced by the API, not just hidden in the app).

**Screens**
1. **Team home:** shift start/end for the supervisor, team summary for today (collectors on shift / not started / offline, points completed vs assigned, open flags by severity), sync status.
2. **Team live map:** each collector's last known position with status colour (moving / at a point / idle / offline) and "last seen X min ago"; team's visit points by status; area boundaries; the supervisor's own position. Tap a collector → quick card (status, today's progress, open flags, call/message buttons).
3. **Collector list:** sortable by risk score, flags, progress or last seen; search by name.
4. **Collector detail:** today's track on the map, stops with dwell time, completed / skipped points, submissions with duration, flags, and a **route replay** for any date (simplified track to save data).
5. **Flags inbox (mobile):** same workflow as the web (3.7) — acknowledge, add note, call, message, request re-visit, assign back-check, resolve/dismiss with comment, escalate.
6. **Go to collector / point:** shortest route from the supervisor's location to a collector's last position or to any team visit point (for spot checks), using the same routing engine and offline fallback as 3.2.
7. **Spot check & back-check:** fill supervision checklists and back-check forms, automatically linked to the collector, the point and the supervisor's GPS/stop at that point.
8. **Reassign points:** move pending points between collectors in the team; the change syncs to both collectors and their route plans re-optimise.
9. **Messages:** team broadcast and one-to-one messages to collectors; collectors' replies to flags.

**Connectivity behaviour**
- Live positions need a connection. Collectors' phones send a small **"heartbeat"** (latest position, battery, status, pending sync count) every [2–5] minutes when they have network, separate from the full GPS batch sync, so the team map stays fresh even on weak data.
- When the supervisor is offline, show the last downloaded team data with a clear "as of HH:MM" label. Never show stale positions as live.
- **Device-to-device sync (no network areas):** when supervisor and collector are physically together without internet, the supervisor can pull the collector's pending submissions, stops and track directly from the collector's phone (Wi-Fi Direct / Bluetooth / nearby sharing, or QR-chunked transfer as a last resort), review them on the spot, and forward them to the server later. Data must stay encrypted and be de-duplicated by UUID on upload.
- **Push notifications** (when online) for high-severity flags in the team, a collector going offline for > [G] min during a shift, a collector not starting their shift by [time], or a mock-location detection.
- Low-data mode: download summaries and simplified tracks first; full tracks and photos only on request.

**Supervisor's own tracking**
- The supervisor's movements, stops and spot-check visits are tracked during their shift in the same way as collectors, so managers can see supervision coverage (which collectors and points were visited by the supervisor, and how long they spent).
- Add supervision rules to the flags engine: e.g. collector not visited by a supervisor in [N] days; high-risk collector with no follow-up action within [H] hours.

---

## 4. Offline-first and sync (critical)

- The mobile app must work fully offline for at least **[7] days**: assignments, forms, cached maps, tracking, submissions.
- **Offline maps:** downloadable vector tiles (PMTiles/MBTiles) per LGA or ward, plus cached route plans.
- Local storage: SQLite (e.g. WatermelonDB or Drift) with an outbox queue.
- Sync: incremental, resumable, compressed batch uploads; every record has a client-generated UUID so uploads are **idempotent** (safe to retry). Downloads use "changes since last sync" cursors.
- Conflict policy: server wins for assignments/config; client wins for submissions and tracks; record conflicts in an audit log.
- Sync automatically on Wi-Fi or mobile data (configurable), plus a manual "Sync now" button. Show clear sync status.
- Photos uploaded separately, compressed, after the form data.

---

## 5. Recommended tech stack

Use this unless you have a strong reason to change it (explain any change before you make it):

- **Mobile:** React Native (Expo, with a custom dev build for background location) **or** Flutter — pick one and justify it based on background-location reliability on Android. Maps: MapLibre. Local DB: SQLite.
- **Backend:** Python **FastAPI**, **PostgreSQL + PostGIS**, Redis + Celery (or RQ) for background jobs (stop detection, flag rules, route optimisation).
- **Routing:** OSRM (Nigeria OSM extract) + VROOM, in Docker.
- **Web dashboard:** React + TypeScript + Vite, MapLibre GL, a table library and a charting library.
- **Auth:** JWT with refresh tokens, role- and geography-based access control.
- **Storage:** S3-compatible object storage (MinIO locally) for photos.
- **Optional integrations (design for, build later):** export to DHIS2, push to Elasticsearch/Kibana for analytics, import forms from KoboToolbox/ODK.
- **Infra:** Docker Compose for local; document deployment to a single Linux VM.

---

## 6. Data model (minimum tables)

`organisations, campaigns, admin_areas (state/lga/ward with geometry), teams, team_members, supervisor_team_assignments, heartbeats (latest status per device), notifications, supervision_visits, users, roles, user_area_access, devices, visit_points, assignments, shifts, gps_points (partition by day), stops, routes_planned, forms, form_versions, submissions, submission_media, flag_rules, flags, flag_actions, messages, backchecks, sync_logs, audit_logs, app_config`

Requirements: PostGIS geometry columns with spatial indexes; `gps_points` must handle tens of millions of rows (partitioning + indexing on `user_id, recorded_at`); soft deletes; created/updated timestamps; all IDs UUIDs.

---

## 7. Privacy, consent and security

- Show a clear consent and privacy notice at first login explaining what location data is collected, when (only during shift), why, who can see it and how long it is kept. Record acceptance.
- No tracking outside an active shift — enforce this in code.
- Data retention policy in config (e.g. raw GPS kept [90] days, then only stops and summaries kept).
- Encrypt local database on the device; HTTPS only; hashed passwords; rate-limited login; remote logout/wipe of a lost device.
- Full audit log of who viewed or exported tracking data.

---

## 8. Non-functional requirements

- Battery: a full 8-hour shift with tracking should use no more than about [20–25]% battery on a typical mid-range phone. Measure and report.
- Mobile data: report estimated MB per collector per day; keep it low.
- Performance: dashboard live map handles 1,000+ active collectors; route replay of one day loads in < 3 s. On the supervisor's phone, the team map (up to ~[30] collectors) loads in < 3 s on a 3G connection.
- App APK size kept small; works on Android 8+.
- Logging and crash reporting (e.g. Sentry, self-hostable).

---

## 9. Testing

- Unit tests: stop detection, each flag rule, offline route ordering, sync idempotency.
- A **GPS trace simulator** script that generates realistic synthetic collector days (walking between points, stops, idle periods, a "cheating" collector who fakes visits, a mock-location user) and replays them into the system. Use it to demo every flag rule.
- Seed data: one campaign, 2 LGAs, 4 wards, 3 supervisors, 20 collectors, 500 visit points, 1 survey form, 1 supervision checklist, 1 back-check form.
- End-to-end test of: assign → route → track → survey offline → sync → stops computed → flags raised → supervisor notified on mobile → supervisor navigates to collector → spot check → flag resolved.

---

## 10. Build phases (stop and wait for my approval after each)

**Phase 1 — Foundation:** repo structure, Docker Compose (Postgres/PostGIS, Redis, MinIO, API), database schema + migrations, auth + RBAC, admin APIs for users/teams/areas/visit points, seed data, API docs (OpenAPI).
*Done when:* I can log in via API, upload visit points, and see them in the database with geometry.

**Phase 2 — Mobile core (offline):** login, shift start/end, assigned points list + map, local DB, outbox sync, form engine with XLSForm import, submissions with auto metadata.
*Done when:* I can fill a survey in airplane mode and it syncs correctly when back online, with no duplicates after repeated syncs.

**Phase 3 — Tracking + stops:** background tracking service, adaptive sampling, mock-location detection, GPS batch sync, backend stop detection, daily collector summaries, GPS simulator.
*Done when:* a simulated day produces correct stops, dwell times and summaries, verified by tests.

**Phase 4 — Routing:** OSRM + VROOM containers, single-destination routing, multi-stop day plan, offline fallback ordering, offline map tile download.
*Done when:* the app shows an optimised visit order online and an estimated order offline.

**Phase 5 — Flags + follow-up:** rules engine with all rules in 3.6, risk score, flags inbox API, follow-up actions, back-check assignment, notifications, collector replies.
*Done when:* the "cheating collector" simulation triggers the expected flags and a supervisor can resolve them end-to-end.

**Phase 6 — Supervisor mobile mode:** role-based mode switch, team home, team live map with heartbeat updates, collector list and detail, route replay, mobile flags inbox and follow-up actions, go-to-collector routing, spot checks/back-checks, point reassignment, messages, push notifications, device-to-device sync, supervisor's own tracking and supervision rules.
*Done when:* a supervisor on a phone can see their simulated team live, receive a high-severity flag notification, navigate to the collector, complete a spot check and resolve the flag — and can pull a collector's data device-to-device with both phones offline.

**Phase 7 — Supervisor web dashboard:** live map, route replay with stops, planned vs actual route, collector and team summaries, supervision coverage, flags inbox UI, exports.

**Phase 8 — Hardening:** privacy/consent screens, retention jobs, encryption, audit logs, performance tuning, battery and data-usage measurement, translations framework, deployment guide, user guides for collectors and supervisors.

---

## 11. What to give me at the end of each phase

1. Summary of what was built and any decisions or deviations.
2. Exact commands to run and test it.
3. Test results.
4. Known limitations and `TODO(stub)` list.
5. What the next phase will do.

---

## 12. Cloud build and deployment (no local machine)

This project is built entirely in **Claude Code cloud sessions** working on the GitHub repo `Ragojerry/RAGOH`, and deployed to **AWS** by **GitHub Actions**. The owner does not run anything locally.

### 12.1 Ground rules
- Claude never holds AWS keys. All AWS changes happen through GitHub Actions, which signs in with **GitHub OIDC** to the role `ragoh-github-deploy` (created once by `infra/bootstrap/github-oidc-role.yaml`). The role ARN, region and state bucket are stored as GitHub repository **variables**: `AWS_ROLE_ARN`, `AWS_REGION`, `TF_STATE_BUCKET`.
- Never commit secrets. App secrets (DB password, JWT key, Expo token, etc.) live in **AWS Secrets Manager** or **GitHub Actions secrets**, generated by Terraform where possible.
- All work goes through pull requests. Tests must pass in CI before merge. Merge to `main` deploys to **staging**; a git tag `v*` deploys to **production** (behind a GitHub "production" environment that needs manual approval).
- In the cloud session, use `docker compose` for local services (Postgres/PostGIS, Redis, MinIO) and run all tests there before opening a PR.
- Keep costs low by default: one small staging environment, single-AZ, smallest sensible instance sizes, autoscaling off until needed. Add an **AWS Budgets** alarm (default USD [100]/month, emailed to [EMAIL]).

### 12.2 AWS target architecture (Terraform, in `infra/terraform/`)
- **Networking:** VPC with public and private subnets, NAT (or VPC endpoints to save cost), security groups.
- **API + workers:** container images in **ECR**, run on **ECS Fargate** behind an **Application Load Balancer** (HTTPS with ACM certificate when a domain is provided; until then the ALB DNS name).
- **Database:** **RDS PostgreSQL** with the **PostGIS** extension, automated backups, encryption at rest.
- **Queue/cache:** **ElastiCache Redis** (or Redis on Fargate for staging to save cost — justify the choice).
- **Media:** **S3** bucket for photos (private, presigned URLs), lifecycle rules for retention.
- **Web dashboard:** static build on **S3 + CloudFront**.
- **Routing:** OSRM and VROOM as ECS services. OSRM data is prepared by a separate GitHub Actions workflow (download the Nigeria OSM extract from Geofabrik, run `osrm-extract/partition/customize` per profile, upload to S3); the OSRM container loads it from S3 at start-up. Never do this heavy processing inside the Claude session.
- **Offline map tiles:** generate PMTiles per LGA in a GitHub Actions job, store in S3, serve through CloudFront.
- **Push notifications:** Firebase Cloud Messaging (credentials in Secrets Manager).
- **Monitoring:** CloudWatch logs and alarms (API 5xx, DB CPU/storage, queue backlog), plus Sentry if configured.
- Terraform state in the S3 bucket created by the bootstrap template (`use_lockfile = true`).

### 12.3 GitHub Actions workflows (in `.github/workflows/`)
- `ci.yml` — on every PR: lint, type-check, unit tests, API tests against Postgres/PostGIS service containers, `terraform fmt`/`validate`/`plan` (plan posted as a PR comment).
- `deploy.yml` — on merge to `main`: build and push images to ECR, `terraform apply` for staging, run DB migrations as a one-off ECS task, deploy web dashboard to S3 and invalidate CloudFront, smoke-test the health endpoint. On tag `v*`: same for production after approval.
- `osrm-data.yml` — manual/monthly: rebuild routing data and upload to S3.
- `tiles.yml` — manual: rebuild offline map tiles.
- `mobile-build.yml` — build the Android APK/AAB in the cloud (Expo EAS Build with `EXPO_TOKEN`, or Gradle on the runner if using Flutter/bare RN), attach the APK to the workflow run and to GitHub Releases on tags, so it can be installed on test phones. Staging and production builds point at their own API URLs.

### 12.4 Phase 1 additions
In Phase 1, also deliver: the Terraform skeleton for staging, `ci.yml`, `deploy.yml` (deploying the API and DB), and `docs/DEPLOYMENT.md`. *Done when:* merging to `main` deploys the API to AWS staging and `GET /health` returns OK from the ALB URL.

Start now with Section 0: assumptions, architecture diagram, schema and repo structure.
