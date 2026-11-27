# 009 — Hardening for the pilot

**Status:** accepted (27 Nov 2026)

**Privacy notice comes from the server and is versioned.** The text is generated from campaign config, so the retention periods it states are the ones the retention job enforces. Accepting it is required at first sign-in (not first shift), before anything else, and again whenever `notice_version` changes. A bundled copy covers a phone that has never been online. Acceptance is stored server-side (`consents`) and audited. The notice is not machine-translated: translations of legal text need a reviewed source of truth, so localised notices will come through config later.

**Lost phones: block, revoke, erase.** Blocking revokes the phone's refresh tokens and refuses its syncs and sign-ins. Erasing has to work even though the phone can no longer authenticate, so a public `POST /devices/status` tells a device id whether it must wipe; it reveals nothing else, and device ids are random UUIDs that only the phone knows. The phone deletes its database rows, photos, offline map, keys and sign-in, then confirms; both steps are audited. Unsynced work on an erased phone is lost by design.

**Audit log viewer.** Admins see everything, managers entries by or about people in their area; the default view is access to personal data (route views, exports, blocks, erasures, relays, consents). Audit entries are kept two years, longer than the data they describe, so access to deleted data can still be accounted for.

**Mobile data: compress up, don't re-download.** Request bodies are gzip-compressed above 2 KB (server middleware with a zip-bomb guard); the phone falls back to plain uploads if a compressed one is ever rejected. `/sync/pull` carries an ETag computed without the timestamp, so the 5-minute automatic syncs mostly get 304. Heartbeats are skipped right after a successful sync. Estimated 1.5 → 0.6 MB per collector per day (MEASUREMENTS.md).

**Measuring battery and data in the field, not in theory.** Each phone sends an end-of-shift report (battery start/end, charging, minutes, GPS points, bytes up/down); the dashboard aggregates it per collector against the 25 % target. Byte counts are app-level estimates (headers approximated; compressed downloads counted at their uncompressed size when the size isn't sent), labelled as such.

**Crash reporting without a vendor on the phone.** JavaScript errors become `app_error` device events that sync like any record and show as a per-phone crash count, which works offline and adds no SDK, account or native module. The server can send errors to Sentry (hosted or self-hosted) when a DSN is configured; events are scrubbed of bodies, headers, query strings, users and local variables before sending.

**Translations: English text as keys.** A tiny `t()` with fallback to English, a hook that re-renders on language change, an extraction script and a CI check keep `strings.json` current for translators; a test checks every translation keeps the English placeholders. Hausa, Yorùbá, Igbo and Naijá catalogs are empty until native speakers translate them: machine translation is not shipped for field staff.

**Release signing is optional and outside the generated project.** The CI build re-signs the APK with `apksigner` when keystore secrets exist; the Android project stays fully generated (no hand edits).

**Load test as code.** `app.perftest` seeds 1,000 collectors and times the live view and replay; results are in MEASUREMENTS.md (0.55 s and 0.08 s).
