# RAGOH — one-time setup (about 20 minutes, all in the browser)

You need: the GitHub repo `Ragojerry/RAGOH`, an AWS account, and a Claude plan with Claude Code cloud sessions (claude.ai/code).

## 1. Give Claude access to the repo
1. Go to https://github.com/apps/claude → **Configure** → choose your account `Ragojerry`.
2. Under **Repository access**, add **RAGOH** → **Save**.

## 2. Connect AWS to GitHub (no AWS keys stored anywhere)
1. Sign in to the AWS Console. Pick your region. (RAGOH uses `eu-north-1` Stockholm because AWS free-plan accounts only allow one region.)
2. Open **CloudFormation → Create stack → With new resources**.
3. Choose **Upload a template file** and upload `infra/bootstrap/github-oidc-role.yaml` from this repo (download it from GitHub first).
4. Stack name: `ragoh-bootstrap`. Leave the parameters as they are. If the stack fails because the GitHub identity provider already exists, delete the failed stack and retry with `CreateOIDCProvider = false`.
5. Tick "I acknowledge that AWS CloudFormation might create IAM resources with custom names" → **Submit**.
6. When it shows **CREATE_COMPLETE**, open the **Outputs** tab. Copy the three values.

## 3. Add the AWS values to GitHub
In GitHub: **RAGOH → Settings → Secrets and variables → Actions → Variables tab → New repository variable**. Add:

| Name | Value |
|---|---|
| `AWS_ROLE_ARN` | `AwsRoleArn` output |
| `AWS_REGION` | `AwsRegion` output |
| `TF_STATE_BUCKET` | `TfStateBucket` output |

Then **Settings → Environments → New environment** → name it `production` → tick **Required reviewers** and add yourself. (Production deploys will wait for your approval.)

## 4. Set a budget alarm
AWS Console → **Billing → Budgets → Create budget** → monthly cost budget (e.g. USD 100) with an email alert at 80%.

## 5. Start building
1. Go to https://claude.ai/code and select the repo **Ragojerry/RAGOH**.
2. In the environment settings, keep network access on **Trusted**.
3. Send this message:

   > Read CLAUDE.md and docs/BUILD_PROMPT.md. Do Section 0 (assumptions, architecture diagram, schema, repo structure) and put it in docs/ARCHITECTURE.md in a pull request. Then stop and wait for my go.

4. Review the PR. Reply "go" to start Phase 1. After each phase: review the PR, merge it, and check the deploy run in the **Actions** tab.

## Later (when needed)
- **Mobile builds:** create a free Expo account, make an access token, and add it as the GitHub **secret** `EXPO_TOKEN`.
- **Push notifications:** create a Firebase project and give Claude the steps it asks for (the credentials go into AWS Secrets Manager, never the repo).
- **Domain name:** if you want e.g. `ragoh.yourdomain.org`, tell Claude the domain; it will add the certificate and DNS in Terraform.
