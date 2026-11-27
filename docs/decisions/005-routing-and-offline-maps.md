# 005 — Routing and offline maps

**Status:** accepted (27 Nov 2026)

**Routing engines.** OSRM (foot profile, MLD) on an OpenStreetMap extract clipped to the FCT, and VROOM for ordering a day's visits (priorities, time windows, 10-minute service time per visit, optional return to start). Both run as containers on the pilot server; OSRM needs only ~100–200 MB RAM for the FCT. The data is built by the **Routing data** GitHub workflow (Geofabrik Nigeria extract → osmium clip → osrm-extract/partition/customize → smoke-test a route in Abuja → S3), never on the server; deploys copy it from S3 and restart the routing containers when it changes. Monthly rebuild by schedule.

**Fallback everywhere.** If OSRM/VROOM are not configured, not yet built, or not reachable, the API answers with straight-line estimates (nearest neighbour + 2-opt) and says so in `source`. The phone runs the same algorithm (`packages/routing`) when offline, labelled "estimated — offline". Visits VROOM cannot fit (e.g. a time window already passed) are appended at the end, marked unscheduled. Every plan is stored (`routes_planned`) for plan-vs-actual views later.

**Offline maps.** OpenStreetMap's public tile servers forbid bulk downloads, so we host our own map: a vector tile extract of the FCT from the daily Protomaps world build (`pmtiles extract`, zoom ≤ 15, tens of MB), Protomaps "light" style layers, fonts and icons — all produced by the same workflow. The server runs a small `pmtiles serve` container behind Caddy (`/tiles/…`, `/map-assets/…`), and the API publishes a MapLibre style at `/api/v1/map/style.json` (404 until the data exists). The phone uses that style when available and lets the user download the area around their assigned points (MapLibre offline pack, zoom 11–16); otherwise it shows OpenStreetMap's online tiles.

**Consequences.** Nothing about routing is on the critical path: the app works (with estimates) before the workflow has ever run. The workflow itself hasn't run yet — its first run needs the AWS deploy to have created the routing bucket.
