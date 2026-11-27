# 008 — Supervisor web dashboard

**Status:** accepted (27 Nov 2026)

**Same server, same origin.** The spec's production target is S3 + CloudFront for the dashboard. For the pilot (ADR 001) Caddy on the pilot server serves the built app at `/`, next to the API at `/api`. Same origin means no CORS, one HTTPS certificate, one URL for users, and the deploy stays one job (it builds the app and ships it in the existing bundle). Hashed assets are cached for a year and `index.html` is never cached, so a deploy shows up on the next page load. Moving to S3 + CloudFront later only changes hosting.

**Stack.** React + TypeScript + Vite, MapLibre GL, TanStack Table v8 (v9 is a new API; v8 is stable and enough for sortable tables). Charts are small hand-built SVG components instead of a charting library: the dashboard needs a few single-series column charts, and this keeps ~150 kB out of a bundle that must load over slow connections. Charts follow one-axis rules, label selectively, have hover tooltips and a table view.

**Colour.** Collector status on the live map uses four hues (blue moving, green at a point, amber idle, red no signal) plus neutral gray for off shift; the set was checked for colour-blind separation. Points use fill and outline (white to visit, dark green done, gray not found/refused) so they never compete with the status hues. Coverage uses a single-hue sequential blue. The dark variant of the status set failed the colour-blind check, so the dashboard is light-only for now.

**Data comes pre-shaped from the server.** New endpoints return exactly what each page draws: `/map/areas.geojson` (simplified ~30 m), `/map/points.geojson`, `/analytics/team`, `/analytics/coverage`, the day replay now includes the planned route, and `/exports/*`. Tracks are simplified to ~10 m before sending. Ranges are capped (3 months for analytics, 31 days for track exports).

**Personal data.** Sessions live in `sessionStorage` (closing the tab signs out). Viewing a collector's track and every export are written to the audit log; CSV/XLSX cells that look like formulas are neutralised. Managers are limited to teams in their geography (this also fixed `/team/live`, which showed managers every team).

**Spot checks count as data.** The submissions export includes the team supervisors' own submissions, so supervision checklists reach M&E along with the surveys.
