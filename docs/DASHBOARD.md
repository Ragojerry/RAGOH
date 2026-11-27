# Supervisor web dashboard

Open the staging address in a browser on a laptop (e.g. `https://13-48-10-20.sslip.io`) and sign in with your RAGOH account. It is for **supervisors, managers and admins** — collectors use the phone app. You see only your own teams (supervisors) or your geography (managers).

You stay signed in until you close the browser tab. The dashboard shows people's locations, so sign out on shared computers.

## Pages
| Page | What it's for |
|---|---|
| **Live map** | Each collector's last position, coloured by status (moving / at a point / idle / no signal / off shift), refreshed every 30 s. Points: white = to visit, dark green = done, gray = not found or refused. Ward/LGA boundaries. Click a collector (map or list) for details, **Replay today**, open flags, call. |
| **Route replay** | Pick a collector and a day. The actual track (solid blue) plays back on a timeline (play/pause, 1–60 minutes per second); the route they planned on the phone is the dashed line. Stops are circles sized by time spent — click one for the time, duration, matched point and any survey filled there. Tiles: distance (vs planned), stops, time at points, visits, flags, risk. |
| **Team overview** | For any date range (up to 3 months): coverage, surveys, flags and spot checks; surveys per day and flags per day (each chart has a **Table** view); a sortable table comparing collectors — coverage, days worked, median interview time, average time at points, distance, flags per 100 surveys, highest risk score, spot checks. Click a row to replay that collector. |
| **Coverage** | Visited vs not visited points by ward or LGA (map shaded by % visited, and a table, lowest first), and supervision coverage: which collectors had no spot check in the period. Falls back to LGAs when no ward boundaries are loaded. |
| **Flags** | All flags, high first, filtered by status, severity, collector and date. Click one for the evidence, a link to replay that day, the history, and actions: acknowledge, ask the collector to explain (appears on their phone), message, note, log a call, request a revisit, assign a back-check (to yourself or a team member), escalate, resolve or dismiss (with a comment). |
| **Exports** | Surveys and checklists (one column per answer), stops, flags, visit points (CSV or Excel), and GPS tracks (GeoJSON for QGIS/ArcGIS). Filter by dates, team and area. Every export is recorded in the audit log. |

## For developers
- Code: `apps/web` (React + TypeScript + Vite, MapLibre GL, TanStack Table; charts are small SVG components in `src/components/charts.tsx`).
- Run locally against a local API: `cd backend && uv run uvicorn app.main:app` then `cd apps/web && pnpm dev` (proxies `/api` to `http://localhost:8000`; set `RAGOH_API=https://<host>` to use staging instead).
- Tests: `pnpm --filter @ragoh/web test`; build: `pnpm --filter @ragoh/web build`.
- Hosting: the deploy builds it and Caddy serves it at the site root; `/api`, `/docs`, `/tiles`, `/map-assets` are unchanged (see `deploy/staging/Caddyfile`).
- The base map uses the self-hosted FCT map when the Routing data workflow has run, otherwise OpenStreetMap tiles.
