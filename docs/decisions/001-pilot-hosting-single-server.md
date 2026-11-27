# 001 — Pilot hosting on a single server

**Status:** accepted (26 Nov 2026)

**Context.** The first deployment is 4 collectors and 2 supervisors in FCT Abuja. The full ECS design (ARCHITECTURE §3.2) costs USD 70–95/month and the AWS account is on the free plan (eu-north-1 only).

**Decision.** Run staging as one EC2 server (`t3.small`, x86) with `docker compose`: API, PostgreSQL/PostGIS, Redis and Caddy (automatic Let's Encrypt HTTPS on a free `<ip>.sslip.io` hostname). Images in ECR, secrets in SSM Parameter Store (free tier) rather than Secrets Manager, deploys through Systems Manager (no SSH, no open port 22). PostgreSQL runs in a container with nightly `pg_dump` to S3 instead of RDS.

**Consequences.** ~USD 25/month. Single point of failure and no automatic failover — acceptable for a pilot. Moving to full mode later means new Terraform modules; the containers, schema and migrations are unchanged. x86 was chosen over ARM (`t4g`) so images build natively on GitHub's standard runners.
