#!/usr/bin/env bash
# Runs ON the staging server (via AWS Systems Manager) to roll out a new API image.
# Usage: deploy.sh <api_image> <ragoh_host> <aws_region> <backup_bucket> [routing_bucket]
set -euo pipefail

API_IMAGE="$1"
RAGOH_HOST="$2"
REGION="$3"
BACKUP_BUCKET="$4"
ROUTING_BUCKET="${5:-}"
APP_DIR=/opt/ragoh
PARAM_PREFIX=/ragoh/staging

cd "$APP_DIR"
mkdir -p data/postgres data/caddy data/media data/osrm data/tiles/assets
chown 10001 data/media  # API container user

param() {
  aws ssm get-parameter --region "$REGION" --with-decryption \
    --name "$PARAM_PREFIX/$1" --query Parameter.Value --output text
}

umask 077
cat > .env <<EOF
API_IMAGE=$API_IMAGE
RAGOH_HOST=$RAGOH_HOST
DB_PASSWORD=$(param DB_PASSWORD)
JWT_SECRET=$(param JWT_SECRET)
ADMIN_PASSWORD=$(param ADMIN_PASSWORD)
DEMO_PASSWORD=$(param DEMO_PASSWORD)
EOF

REGISTRY="${API_IMAGE%%/*}"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"

# Routing and map data built by the "Routing data" workflow (absent until it has run once).
routing_changed=""
if [ -n "$ROUTING_BUCKET" ]; then
  for part in osrm tiles; do
    if [ "$(aws s3 sync "s3://$ROUTING_BUCKET/$part/" "data/$part/" --region "$REGION" --delete --dryrun | wc -l)" -gt 0 ]; then
      aws s3 sync "s3://$ROUTING_BUCKET/$part/" "data/$part/" --region "$REGION" --delete --only-show-errors
      routing_changed=1
    fi
  done
fi

docker compose pull --quiet
docker compose up -d --remove-orphans
if [ -n "$routing_changed" ]; then
  echo "Routing/map data updated; restarting routing services"
  docker compose restart osrm vroom tiles
fi

# Wait for the API to report healthy (migrations run on start).
for _ in $(seq 1 60); do
  if docker compose exec -T api curl -fsS http://localhost:8000/health >/dev/null 2>&1; then
    echo "API healthy"
    break
  fi
  sleep 5
done
docker compose exec -T api curl -fsS http://localhost:8000/health

# Nightly database backup to S3 (kept 30 days by a bucket lifecycle rule).
cat > /etc/cron.d/ragoh-backup <<EOF
15 2 * * * root cd $APP_DIR && docker compose exec -T db pg_dump -U ragoh -Fc ragoh | aws s3 cp - s3://$BACKUP_BUCKET/postgres/ragoh-\$(date +\%Y\%m\%d).dump --region $REGION >> /var/log/ragoh-backup.log 2>&1
EOF
chmod 644 /etc/cron.d/ragoh-backup

# Daily: delete raw GPS older than the retention period (stops and summaries are kept).
cat > /etc/cron.d/ragoh-maintenance <<EOF
45 2 * * * root cd $APP_DIR && docker compose exec -T api python -m app.maintenance retention >> /var/log/ragoh-maintenance.log 2>&1
30 3 * * * root cd $APP_DIR && docker compose exec -T api python -m app.maintenance flags >> /var/log/ragoh-maintenance.log 2>&1
EOF
chmod 644 /etc/cron.d/ragoh-maintenance

docker image prune -f >/dev/null
echo "Deployed $API_IMAGE to https://$RAGOH_HOST"
