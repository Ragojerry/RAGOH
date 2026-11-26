# RAGOH — instructions for Claude Code

RAGOH is a field supervision, survey and route-tracking system for public health campaigns in Nigeria: an offline-first Android app (collector and supervisor modes), a supervisor web dashboard, and a FastAPI + PostGIS backend with routing (OSRM/VROOM), stop detection and anomaly flags.

**The full specification is `docs/BUILD_PROMPT.md`. Read it before doing any work and follow it.**

## How we work
- The owner works only through Claude Code cloud sessions and GitHub. Nothing is run on a local machine.
- Build one phase at a time (BUILD_PROMPT Section 10). At the end of each phase, report using Section 11 and stop for approval.
- Track progress in `docs/PROGRESS.md`: current phase, what is done, decisions made, open TODO(stub) items. Update it in every PR.
- Record architecture decisions in `docs/decisions/NNN-title.md` (short ADRs).
- Work on a feature branch and open a pull request. Never push directly to `main`.
- Run the tests (and `docker compose` services they need) in the session before opening a PR. CI must be green.

## AWS
- You never have AWS credentials. Deployment happens only through GitHub Actions using OIDC (`AWS_ROLE_ARN`, `AWS_REGION`, `TF_STATE_BUCKET` repo variables). See BUILD_PROMPT Section 12.
- All infrastructure is Terraform in `infra/terraform/`. Name every AWS resource and IAM role with the prefix `ragoh-` (the deploy role can only manage IAM resources with that prefix).
- Never commit secrets, `.env` files or `*.tfvars` with real values.

## Conventions
- Python: FastAPI, SQLAlchemy 2 + Alembic, pytest, ruff, mypy.
- TypeScript: strict mode, ESLint, Prettier, Vitest.
- All thresholds and tunables live in config, not code.
- Commit messages: imperative, short subject, body explaining why.
