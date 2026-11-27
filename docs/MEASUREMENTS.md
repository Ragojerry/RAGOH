# Performance, battery and mobile data

Targets from the build prompt: a day's route replay loads in **under 3 s**; the live map
handles **1,000+ active collectors**; tracking uses **≤ 25 % battery** over an 8-hour shift
on a mid-range Android phone; mobile data stays small (pilot phones use prepaid bundles).

## Server performance (measured 27 Nov 2026)

`python -m app.perftest --collectors 1000` seeds 1,000 collectors in 40 teams, each with a
heartbeat and 60 recent GPS points, plus one collector with a 3,000-point day, then times
real HTTP requests (median of 5). Measured on the build machine against PostgreSQL 16 +
PostGIS; the pilot server (t3.small, 2 vCPU) should be within 2× of this.

| Request | Median | Target |
|---|---|---|
| Live view, **1,000 collectors** (`GET /team/live`, admin) | **0.55 s** (514 KB, ~60 KB gzipped) | live map usable at 1,000+ |
| Live view, one team of 25 (supervisor) | **0.05 s** | — |
| Day replay, **3,000 GPS points** (`GET /collectors/{id}/day`) | **0.08 s** | < 3 s |

Composite indexes added in Phase 8 cover the replay, analytics, flags inbox, audit log and
retention queries. Re-run the load test against a scratch database after large changes
(`--cleanup` removes the test data).

## Mobile data per collector per day (estimated)

Measured payload sizes for an honest 8-hour simulated day: 2,000 GPS points at 342 bytes
each (666 KB as JSON, **145 KB gzipped**); a full sync snapshot 22 KB (4.3 KB gzipped);
a heartbeat ~250 bytes. Assumptions: automatic sync every 5 min, heartbeat every 3 min,
~600 bytes of HTTP headers each way per request, the snapshot changing ~12 times a day.

| | Before Phase 8 | After Phase 8 |
|---|---|---|
| Uploads (GPS, surveys) | 781 KB | 260 KB — request bodies gzip-compressed |
| Sync downloads | 528 KB | 167 KB — unchanged snapshots answered with 304 (ETag) |
| Heartbeats | 248 KB | ~150 KB — skipped right after a successful sync |
| **Total** | **≈ 1.5 MB/day** | **≈ 0.6 MB/day** |

Photos are extra: each is resized on the phone to 1280 px / JPEG 0.7 (≈ 150–300 KB).
The offline map is a one-time 10–40 MB download, best on Wi-Fi.

**Field figures replace these estimates**: from app 0.8, each phone sends an end-of-shift
report (bytes sent and received by the app), shown per collector on the dashboard's
**Phones** page. Downloads there are counted uncompressed when the server doesn't state a
size, so they are an upper bound.

## Battery

Not measurable without the real phones. What the app does to save battery: adaptive GPS
sampling (high accuracy only while moving, balanced every 60–120 s when still), batched
writes, no GPS when the shift is off, heartbeats skipped after syncs, and no wake-ups
between GPS fixes.

**How it is measured in the pilot:** each phone records its battery level when the shift
starts and ends, and whether it was charging; shifts on the charger are left out. The
dashboard's **Phones** page shows battery used per hour and projected for an 8-hour shift,
per collector and overall, flagged red above the 25 % target.

**Field protocol (first pilot week):**
1. Charge the phones fully the night before; no charging during the shift.
2. Same settings on every phone: battery optimisation off for RAGOH, screen brightness
   medium, no other heavy apps.
3. Normal work day, 6–8 hours.
4. After 3–5 days, read the **Phones** page. If a model goes over 25 %: lengthen the still
   interval in campaign config `tracking`, or lower moving accuracy, and repeat.
