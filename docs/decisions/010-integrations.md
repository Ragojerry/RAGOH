# 010 — Integrations: Elasticsearch, DHIS2, DIGIT HCM

**Status:** accepted (27 Nov 2026)

**Connectors are scheduled jobs, never part of a request.** `python -m app.integrations run` (cron, every 5 minutes; each connector has its own `every_min`) reads from the database and pushes. A slow or broken external system cannot slow down sync or the dashboard, and errors are visible to admins on the Integrations page instead of being lost in logs. Each connector runs in its own savepoint: a failure undoes only its own work, and other connectors still run.

**Cursors with a look-back window.** Each connector remembers how far it got per stream (`integration_states.cursor`). Timestamps such as `received_at` are set when the inserting transaction starts, so a slow sync can commit rows stamped just before a cursor that already moved on; every run therefore re-reads the last 5 minutes. Elasticsearch absorbs the overlap because documents are keyed by RAGOH ids (upserts); DIGIT keeps the ids of recently sent events so each is delivered once; DHIS2 is a full recompute of the last N days with `CREATE_AND_UPDATE`, which is idempotent by design and also captures late syncs. A DIGIT run that fails half-way keeps the progress of the batches already delivered.

**No vendor SDKs.** Elasticsearch `_bulk`, DHIS2 `dataValueSets` and DIGIT egov-user OAuth are plain HTTP calls with httpx, so there is no extra dependency and they are tested against fake servers.

**Elasticsearch:** four indices with index templates (geo_point, dates); survey answers in a `flattened` field so forms with clashing field types coexist; a recomputed day's stops are replaced (their ids change); collector label configurable down to opaque ids for clusters shared beyond the supervision team.

**DHIS2:** aggregate only, daily periods, explicit mapping of area codes to org units and indicators to data elements (nothing unmapped is sent), dry-run support, and a dashboard preview of the exact values first. Ward numbers also count towards their LGA. Tracker/event export was not built: individual-level data in DHIS2 was not asked for, and aggregates carry less personal data.

**DIGIT HCM:** HCM APIs and the entities to update differ by instance and programme, so the connector standardises the RAGOH side (event types, batching, exactly-once delivery, DIGIT authentication and `RequestInfo`) and leaves the receiving endpoint to configuration, with an HMAC-signed webhook mode for an adapter service. Visit points imported with HCM identifiers as `external_id` link events back to HCM records. Which HCM endpoint and entity each event maps to is to be agreed with the HCM team before switching it on.

**Who controls where data goes.** Integration settings are visible to and editable by admins only (managers can change other campaign settings but not these), secrets live only in Parameter Store, and every change and manual run is audited.
