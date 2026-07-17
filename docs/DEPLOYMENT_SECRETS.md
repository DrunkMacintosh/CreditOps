# GitHub Actions deployment configuration

This repository uses GitHub Actions to deploy the synthetic development environment. It does not authorize production banking data, official SHB policy, or production approval workflows.

## Required GitHub Environment

Create a protected GitHub Environment named `staging`. Put all secrets below in that environment, not in workflow YAML. Require reviewers before creating a separate `production` environment. The deploy workflow only runs from `main` after the `CI` workflow succeeds.

## Environment secrets

| Secret | Used by | Purpose and minimum scope |
| --- | --- | --- |
| `VERCEL_TOKEN` | Vercel job | A dedicated Vercel token limited to the target team/project. Rotate it regularly. |
| `SUPABASE_ACCESS_TOKEN` | Supabase job | Supabase CLI access for the one target project. Do not use a personal token when a dedicated automation identity is available. |
| `SUPABASE_DB_PASSWORD` | Supabase job | Database password required by `supabase link`/`db push`; never print it. Alternatively replace this with one `SUPABASE_DB_URL` secret and use `supabase db push --db-url`. |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | Cloud Run job | Full Google provider resource name for GitHub OIDC. This is an identifier, but keep it environment-scoped with the deploy identity. |
| `GCP_DEPLOYER_SERVICE_ACCOUNT` | Cloud Run job | Dedicated deployer service account email. It must not be a user account or a JSON key. |

`VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`, and `SUPABASE_PROJECT_REF` are identifiers and are configured as environment variables in the workflow. They are not credentials.

## Environment variables

Configure these as `staging` Environment variables:

| Variable | Example shape |
| --- | --- |
| `GCP_PROJECT_ID` | `synthetic-creditops-dev` |
| `GCP_REGION` | `asia-southeast1` |
| `GAR_LOCATION` | `asia-southeast1` |
| `GAR_REPOSITORY` | `creditops` |
| `CLOUD_RUN_API_SERVICE` | `creditops-api` |
| `CLOUD_RUN_WORKER_JOB` | `creditops-worker` |
| `SUPABASE_PROJECT_REF` | Supabase project ref |
| `VERCEL_ORG_ID` | Vercel team/org ID |
| `VERCEL_PROJECT_ID` | Vercel project ID |
| `VERCEL_CLI_VERSION` | An explicitly approved Vercel CLI version |
| `VERCEL_PRODUCTION_URL` | Optional HTTPS URL for a smoke check |
| `WORKER_RUNTIME_READY` | `false` until the live worker processor and recovery path are verified |

## Google IAM prerequisites

The GitHub OIDC provider must allow only this repository and the `main` ref/environment subject. The deployer service account needs only:

- Artifact Registry writer on the target repository;
- Cloud Run deploy permissions for the API service and worker Job;
- Service Account User on the Cloud Run runtime identities;
- permission to resolve the pushed Artifact Registry digest.

It does not need a service-account key. The Cloud Run runtime service accounts separately access pinned Secret Manager versions. FPT API keys, database URLs, Supabase service-role keys, and OIDC configuration stay in Google Secret Manager and are not exposed to GitHub Actions.

## Supabase migration safety

Migrations are applied before Cloud Run or Vercel deployment. The CLI uses the ordered files in `supabase/migrations/`; a failed migration stops the workflow. Review migration SQL and take the approved backup before enabling a non-synthetic environment.

## Rotation and incident response

1. Disable the affected GitHub Environment or revoke the provider/token.
2. Rotate `VERCEL_TOKEN`, Supabase access/database credentials, or the Google trust binding as applicable.
3. Review GitHub Actions logs for accidental exposure; GitHub masks configured secrets but does not redact values written to files or transformed values automatically.
4. Roll Cloud Run back to the previous immutable revision and redeploy the last known-good Vercel deployment.
5. Never add a secret to `.env`, workflow YAML, artifacts, cache keys, commit messages, or logs.
