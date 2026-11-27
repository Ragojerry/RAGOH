#!/bin/sh
# Apply migrations and idempotent seed data, then start the given command.
set -e

if [ "${RAGOH_RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "Running database migrations..."
  alembic upgrade head
  if [ "${RAGOH_SEED_DEMO:-false}" = "true" ]; then
    python -m app.seed --demo
  else
    python -m app.seed
  fi
fi

exec "$@"
