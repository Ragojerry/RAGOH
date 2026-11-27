"""Connectors that send RAGOH data to other systems (Phase 7b).

Each connector runs on a schedule (`python -m app.integrations run`, every few minutes from
cron), reads from the database since where it last got to, and pushes to the external
system. They run outside the request path: an external system that is slow or down never
slows the field app — the connector records the error and tries again next time.
"""
