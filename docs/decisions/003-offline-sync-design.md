# 003 — Offline sync: outbox push + snapshot pull

**Status:** accepted (26 Nov 2026)

**Context.** Collectors work for days without data. Nothing may be lost or duplicated when a connection drops mid-sync, a phone dies, or a push succeeds on the server but the reply never arrives.

**Decision.**
- Every record created on the phone (shift, submission, assignment status change) gets a client UUID and is written to an encrypted SQLite **outbox** in the same transaction as the local change.
- **Push** sends outbox records in batches. The server upserts by UUID: a repeated submission is acknowledged without changes; a shift may be re-sent to add its end time. Each record is marked synced only after the server lists it as accepted; permanently refused records are kept (state `rejected`) and shown to the user.
- Assignment status uses **last change wins by phone time**.
- Photos upload separately, only after their submission is accepted.
- **Pull** returns a full **snapshot** (profile, campaign config, latest form versions, assignments) instead of a delta. A collector has tens to hundreds of assignments, so this is small and avoids missed-deletion bugs. Status changes still in the outbox are overlaid on the snapshot so the phone never shows an older server state.
- The engine (`packages/sync-core`) is platform-independent and unit-tested; the app supplies an SQLite store and an HTTP transport.

**Consequences.** Retries are always safe. Snapshot pull cost grows with assignments per user; switch to cursors if a user ever has thousands. Clock skew is recorded per push (`clock_offset_s`) for anomaly rules in Phase 5.
