# Integrations: Elasticsearch/Kibana, DHIS2, DIGIT HCM

All three connectors are **off by default**. An admin switches them on per campaign in the
dashboard (**Integrations → Settings**), and the passwords/keys go into AWS Parameter Store
(never into the settings). They run on the server every 5 minutes
(`python -m app.integrations run` from cron), each at its own `every_min`. If an external
system is down, RAGOH keeps working; the connector shows the error on the Integrations
page and picks up where it left off next time. **Run now** runs one immediately.

Only admins can see or change integration settings (they decide where personal data goes),
and every change and manual run is in the audit log.

## Server secrets (Parameter Store)
Create these as **SecureString** parameters in eu-north-1, then re-run the Deploy workflow:

| Parameter | For |
|---|---|
| `/ragoh/staging/ES_API_KEY` | Elasticsearch API key (the base64 `id:api_key` value Kibana shows when you create a key) |
| `/ragoh/staging/DHIS2_PASSWORD` | Password of the DHIS2 user in the settings |
| `/ragoh/staging/DIGIT_PASSWORD` | Password of the DIGIT user in the settings (`auth: digit`) |
| `/ragoh/staging/WEBHOOK_SECRET` | Shared secret for signed webhooks (`auth: hmac`) |

## Elasticsearch / Kibana
```json
"elasticsearch": { "enabled": true, "url": "https://es.example.org:9200", "index_prefix": "ragoh", "collector_label": "name" }
```
- Indices: `ragoh-submissions`, `ragoh-stops`, `ragoh-day-summaries`, `ragoh-flags`. Index templates are created on the first run: locations are `geo_point`, times are `date`, survey answers are a `flattened` field (`answers.hh_size`, `answers.has_net`…).
- Documents use RAGOH ids: re-sending updates a document, never duplicates it. A recomputed day replaces that day's stops.
- `collector_label`: `name` (full name), `username`, or `id` (pseudonymous — use this if the Elasticsearch cluster is shared more widely than the dashboard).
- The API key needs `create_index`, `index`, `delete` on `ragoh-*` and `manage_index_templates`.
- **Kibana:** create data views `ragoh-submissions*` (time field `ended_at`), `ragoh-stops*` (`started_at`), `ragoh-day-summaries*` (`date`), `ragoh-flags*` (`date`). Useful first visuals: surveys per day by team; a Maps layer of submissions coloured by `form`; stops by `classification`; flags by `rule` and `severity`; `risk_score` per collector per day.
- Note: a flag that disappears before anyone acts on it (the problem went away when more data arrived) stays in Elasticsearch with status `new`.

## DHIS2 (aggregate)
```json
"dhis2": {
  "enabled": true, "url": "https://dhis2.example.org", "username": "ragoh_integration",
  "data_set": "AbC123xYz9k", "days_back": 7, "dry_run": true,
  "org_units": { "NG-FC-AMAC": "OuAmac00001", "NG-FC-BWARI": "OuBwari0001" },
  "data_elements": { "visits_completed": "DeVisit0001", "coverage_pct": "DeCover0001", "flags_high": "DeFlagH0001" }
}
```
1. In DHIS2, create a data set with **daily** period type and data elements for the indicators you want (number type; `coverage_pct` as a percentage/number). Assign it to the org units.
2. Map RAGOH area codes (Dashboard → Coverage, or `GET /api/v1/admin-areas`) to DHIS2 org unit uids in `org_units`. Wards or LGAs both work: a ward's numbers are counted inside its LGA's too.
3. Map the indicators you want in `data_elements`. Available: `visits_done`, `visits_completed`, `visits_not_found_or_refused`, `surveys`, `points_total`, `coverage_pct`, `flags_total`, `flags_high`, `spot_checks`, `active_collectors`.
4. Check the numbers in **Integrations → DHIS2 preview**, run once with `"dry_run": true` (DHIS2 validates without saving; the result shows imported/updated/ignored and conflicts), then set `"dry_run": false`.
- Each run re-sends the last `days_back` days with `CREATE_AND_UPDATE`, because late syncs change recent numbers. Re-sending overwrites; it never doubles.
- Optional `category_option_combo` / `attribute_option_combo` uids if your data set uses them.

## DIGIT HCM
```json
"digit": {
  "enabled": true, "url": "https://hcm.example.org", "endpoint": "/ragoh-adapter/v1/events/_create",
  "auth": "digit", "tenant_id": "ng", "username": "RAGOH_INTEGRATION", "user_type": "EMPLOYEE"
}
```
- Sends **visit outcomes** (completed / not found / refused, with the point's `external_id`, the collector and the submission id), **supervision and back-check results** (with answers) and **closed flags** (resolved/dismissed with the comment), in batches of 200, oldest first, each event exactly once.
- `auth: digit`: signs in with egov-user (`/user/oauth/token`, password grant, tenant, user type; client `egov-user-client:` by default — change with `RAGOH_DIGIT_CLIENT_AUTH`) and wraps each batch in a DIGIT `RequestInfo` (authToken + userInfo).
- `auth: hmac`: plain JSON signed with `X-RAGOH-Signature: sha256=<HMAC of the body>` for an adapter service you run next to HCM.
- **Linking to HCM records:** import visit points with the HCM household (or boundary) identifier as `external_id`; every event carries it as `point_external_id`.
- **To confirm with your HCM team before switching on:** which service receives the events (an HCM module endpoint or a small adapter), which HCM entity each event should update (e.g. a project task for a visit outcome), and the service user's roles in the tenant. The endpoint is a setting, so no code change is needed once that is decided.

Event batch:
```json
{ "RequestInfo": { "apiId": "ragoh", "authToken": "…", "userInfo": { … } },
  "source": "ragoh", "campaign": "FCT Pilot Survey", "tenantId": "ng",
  "events": [
    { "type": "visit_outcome", "id": "…:completed", "point_external_id": "HH-000123", "status": "completed",
      "collector_username": "col1", "at": "2026-11-27T09:41:00+00:00", "submission_id": "…", "point_location": { "lat": 9.07, "lon": 7.39 } },
    { "type": "supervision", "id": "…", "supervisor_username": "sup1", "collector_username": "col1", "answers": { … } },
    { "type": "flag_closed", "id": "…", "rule": "R1", "severity": "high", "status": "resolved", "comment": "…" } ] }
```

## Exports
For everything else, the dashboard's **Exports** page gives CSV/Excel and GeoJSON (see DASHBOARD.md).
