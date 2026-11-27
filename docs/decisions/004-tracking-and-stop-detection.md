# 004 — Tracking and stop detection

**Status:** accepted (27 Nov 2026)

**Tracking on the phone.** `expo-location` background updates in an Android foreground service (persistent notification), started with the shift and stopped when it ends — the task also refuses to record if no shift is active and auto-ends a shift after `tracking.max_shift_hours`. Sampling adapts from the fixes themselves: ~10 s while moving, ~90 s once the phone has stayed within 30 m for 3 minutes, back to moving when it leaves a 40 m radius. Each fix (with accuracy, speed, battery, network type and Android's mock-location flag) goes into the encrypted outbox. On every app open, if a shift is active but tracking is not running (app killed, phone restarted), tracking restarts and a `tracking_restarted` event is recorded. We chose this over the commercial background-geolocation library to stay free; the tracking module is isolated so it can be swapped if field tests on Tecno/Infinix/itel phones show it is unreliable.

**Storage.** `gps_points` is range-partitioned by month (`id, recorded_at` primary key) with a DEFAULT partition for phones with wrong clocks; partitions are created on demand only for plausible months. Retention drops whole partitions after `retention.raw_gps_days` (90); stops and summaries are kept.

**Stop detection** (`app/services/stops.py`, pure Python): ignore mock and low-accuracy points; split the track at gaps longer than `gps.gap_s` (a switched-off phone is a gap, never a stop); median-smooth; grow a candidate while points stay within `stops.radius_m` of its running centroid, tolerating up to 3 outliers; keep candidates lasting `stops.min_duration_s`; trim arrival/departure walking at the edges (core radius adapts to reported accuracy); merge neighbours separated by ≤ `merge_gap_s`. Distance counts only movement beyond GPS noise, with stops collapsed to their centroid and impossible speeds ignored.

**Validation.** The simulator's ground truth: across seeds, honest and ±25 m-noise days produce exactly one stop per visit, start times within 90 s and dwell within 150 s of the truth.

**Processing.** After each push containing GPS or submissions, the affected collector-days are recomputed from scratch in a background task (idempotent). Stops are classified `at_assigned_point` (inside the point's geofence), `near_assigned_point` (≤ 2× geofence), `base` (long stays on ≥ 2 of the previous 14 days, e.g. home) or `unassigned_location`, and linked to surveys finished during them.
