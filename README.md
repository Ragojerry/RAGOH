# RAGOH

Field supervision, survey and route-tracking system for public health campaigns.

- **Data collectors** (Android, offline-first): assigned visit points, shortest-route suggestions, surveys, GPS tracking during shifts.
- **Supervisors** (Android + web): live team map, route replay, stops and time spent at each, abnormal-behaviour flags and follow-up.
- **Backend:** FastAPI, PostgreSQL/PostGIS, Redis, OSRM/VROOM routing, deployed to AWS via GitHub Actions.

## Docs
- [Setup (start here)](docs/SETUP.md)
- [Build specification](docs/BUILD_PROMPT.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Deployment](docs/DEPLOYMENT.md)
- [Progress](docs/PROGRESS.md)

Status: Phase 1 (backend foundation + AWS staging) in review. See [Deployment](docs/DEPLOYMENT.md).
