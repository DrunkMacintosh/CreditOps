# GitHub Actions Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a secure CI/CD pipeline that validates pull requests and deploys Supabase, Cloud Run, and Vercel from `main` using scoped credentials and Google OIDC.

**Architecture:** A validation workflow is shared by pull requests and pushes. A deployment workflow runs only on `main`, applies Supabase migrations first, authenticates to Google with Workload Identity Federation, publishes an immutable Artifact Registry image, deploys Cloud Run, and then deploys Vercel. The existing Terraform worker gate remains fail-closed.

**Tech Stack:** GitHub Actions, Node/pnpm, Python/uv, Supabase CLI, Google `auth`/`gcloud`/`artifact-registry` Actions, Docker Buildx, Vercel CLI, YAML, Markdown.

## Global Constraints

- No long-lived Google service-account JSON keys.
- No secrets or real banking data in source control or workflow logs.
- Supabase migrations run before application deployment.
- Cloud Run image references use commit SHA tags; deployment metadata must use immutable image references where supported.
- `worker_runtime_ready` remains false unless the real worker processor and live recovery path are verified.
- Development and demonstration remain synthetic-only.

---

### Task 1: Add shared CI workflow

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/actionlint.yml`

**Interfaces:**
- Consumes: repository `pnpm-lock.yaml`, `uv.lock`, `apps/web/package.json`, `services/api/tests`.
- Produces: required `ci` check for pull requests and pushes.

- [ ] **Step 1: Create the workflow with least-privilege permissions**

Use `contents: read`, `pull-requests: read`, and no write token. Run backend and frontend validation in separate jobs with pinned major action versions.

- [ ] **Step 2: Add explicit validation commands**

Backend commands: `uv run pytest services/api/tests -q`, `uv run ruff check services/api/src services/api/tests`, and `uv run mypy services/api/src/creditops`. Frontend commands: `pnpm --dir apps/web test -- --run`, `pnpm --dir apps/web typecheck`, `pnpm --dir apps/web lint`, and `pnpm --dir apps/web build`.

- [ ] **Step 3: Validate YAML locally**

Run `ruby -e 'require "yaml"; ARGV.each { |f| YAML.load_file(f); puts f }' .github/workflows/*.yml` and `git diff --check`.

- [ ] **Step 4: Commit the CI workflow**

```bash
git add .github/workflows/ci.yml .github/workflows/actionlint.yml
git commit -m "ci: add pull request validation"
```

### Task 2: Add Supabase migration job and deployment workflow

**Files:**
- Create: `.github/workflows/deploy.yml`
- Create: `docs/DEPLOYMENT_SECRETS.md`
- Modify: `deploy/terraform/README.md`

**Interfaces:**
- Consumes: `SUPABASE_ACCESS_TOKEN` and `SUPABASE_DB_PASSWORD` secrets plus the non-secret `SUPABASE_PROJECT_REF` environment variable from the protected environment.
- Produces: ordered `supabase db push` before cloud deployment and a documented repository-secret contract.

- [ ] **Step 1: Define deployment triggers and concurrency**

Trigger only on push to `main` and manual dispatch. Set `concurrency: creditops-deploy-${{ github.ref }}` with cancellation disabled for an in-progress deployment.

- [ ] **Step 2: Add the Supabase migration job**

Install a pinned Supabase CLI release, run `supabase link --project-ref "$SUPABASE_PROJECT_REF"`, then `supabase db push --password "$SUPABASE_DB_PASSWORD"`. Use environment-scoped secrets and do not print command arguments containing passwords.

- [ ] **Step 3: Document required secrets and non-secret variables**

Document exact names, purpose, minimum scope, environment placement, rotation, and the prohibition on putting FPT or database payloads in GitHub Actions.

- [ ] **Step 4: Commit the migration job and documentation**

```bash
git add .github/workflows/deploy.yml docs/DEPLOYMENT_SECRETS.md deploy/terraform/README.md
git commit -m "ci: add Supabase migration deployment"
```

### Task 3: Add Google OIDC, image publication, and Cloud Run deploy

**Files:**
- Modify: `.github/workflows/deploy.yml`
- Create: `.github/workflows/scripts/verify-image-reference.sh`

**Interfaces:**
- Consumes: non-secret `GCP_WORKLOAD_IDENTITY_PROVIDER` and `GCP_DEPLOYER_SERVICE_ACCOUNT` variables, plus Artifact Registry and Cloud Run service/job variables.
- Produces: commit-tagged image and Cloud Run API deployment after successful migrations.

- [ ] **Step 1: Authenticate without a key file**

Use `google-github-actions/auth` with `workload_identity_provider` and `service_account`; grant the deployer only Artifact Registry write and Cloud Run deploy permissions outside the repository.

- [ ] **Step 2: Build and push the immutable image**

Use `docker/setup-buildx-action`, `docker/login-action` for Artifact Registry, and `docker/build-push-action` with tags `${{ github.sha }}` and `${{ github.run_id }}`. Deploy only the SHA tag.

- [ ] **Step 3: Deploy the API service**

Run `gcloud run deploy "$CLOUD_RUN_API_SERVICE" --image "$IMAGE_URI@${DIGEST}" --region "$GCP_REGION" --quiet` after resolving the pushed digest. Do not add `--allow-unauthenticated`.

- [ ] **Step 4: Keep worker deployment gated**

Only invoke `gcloud run jobs deploy` when `WORKER_RUNTIME_READY == true`; otherwise emit a masked informational log and leave the existing worker gate unchanged.

- [ ] **Step 5: Commit the Google deployment changes**

```bash
git add .github/workflows/deploy.yml .github/workflows/scripts/verify-image-reference.sh
git commit -m "ci: deploy immutable Cloud Run images"
```

### Task 4: Add Vercel deployment and final verification

**Files:**
- Modify: `.github/workflows/deploy.yml`
- Create: `docs/DEPLOYMENT_RUNBOOK.md`

**Interfaces:**
- Consumes: the `VERCEL_TOKEN` secret, non-secret `VERCEL_ORG_ID`/`VERCEL_PROJECT_ID` identifiers, and Vercel environment variables.
- Produces: Vercel deployment after Supabase and Cloud Run jobs succeed.

- [ ] **Step 1: Deploy the frontend with the exact project**

Install a pinned Vercel CLI version and run `vercel pull --yes --environment=production --token "$VERCEL_TOKEN"`, `vercel build --prod --token "$VERCEL_TOKEN"`, and `vercel deploy --prebuilt --prod --token "$VERCEL_TOKEN" --scope "$VERCEL_ORG_ID"`.

- [ ] **Step 2: Add post-deploy smoke checks**

Check the configured frontend URL and Cloud Run health endpoint without logging authorization headers or response bodies containing case data.

- [ ] **Step 3: Document setup, permissions, and rollback**

Document environment protection, token rotation, WIF IAM grants, Supabase migration recovery, Cloud Run revision rollback, Vercel redeploy, and the fact that live provider execution is not verified in this workspace.

- [ ] **Step 4: Run the complete local verification suite**

Run backend tests/ruff/mypy, frontend tests/typecheck/lint/build, YAML parsing, `git diff --check`, and Terraform validation if the CLI is installed.

- [ ] **Step 5: Commit the Vercel and runbook changes**

```bash
git add .github/workflows/deploy.yml docs/DEPLOYMENT_RUNBOOK.md
git commit -m "ci: deploy Vercel frontend"
```
