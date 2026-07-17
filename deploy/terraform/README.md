# CreditOps managed-cloud deployment contract

This Terraform is a **synthetic-development contract only**. It does not prove that a cloud environment is operational, production-ready, compliant, or approved by SHB.

> All customer data, policies, documents, and banking-system responses in this project are synthetic and created solely for demonstration.

## Fail-closed rollout order

### 0. Provision secrets outside Terraform

Create every Secret Manager container and at least one numeric version through an approved secret-administration process before `terraform plan`. The `api_secret_refs` and `worker_secret_refs` inputs contain only secret IDs and numeric version numbers. Terraform reads container metadata to verify that each named container already exists; it never reads a payload and never creates a secret version.

This ordering avoids both secret material in Terraform state and a first-apply cycle in which Cloud Run requires a version that does not yet exist. The Cloud Run module explicitly depends on completion of all runtime service-account and secret-access IAM grants.

Secret-map keys cannot replace the reserved `APP_ENV`, `DATA_CLASS`, or `SERVICE_NAME` variables.

### 1. Configure one exact Vercel workload identity

Set:

- `vercel_team_slug` to the approved Vercel team slug;
- `web_oidc_subject` to one exact `owner:<team>:project:<project>:environment:<environment>` subject;
- unique `web_identity_pool_id` and `web_identity_provider_id` values.

Terraform derives the issuer `https://oidc.vercel.com/<team>` and audience `https://vercel.com/<team>` rather than accepting arbitrary URLs. The provider maps only `google.subject = assertion.sub` and applies the same exact subject as its CEL condition. That subject may impersonate only `creditops-web-invoker`; that service account alone receives `roles/run.invoker` on the private API. No public principal is accepted.

The server-side Vercel BFF integration is a separate application step:

1. obtain a fresh Vercel OIDC token inside each request;
2. exchange it through the emitted Workload Identity Provider;
3. impersonate the emitted web-invoker service account and generate a Google-signed ID token whose audience is the exact Cloud Run `api_url`;
4. send that Google token as `X-Serverless-Authorization`;
5. preserve the workforce JWT separately as `Authorization: Bearer ...` for FastAPI authentication.

Do not create or upload a long-lived Google service-account key. Do not fetch the Vercel token at module scope. Until the BFF performs this exchange, the Cloud Run API is intentionally unreachable from Vercel.

`additional_api_invoker_members` is limited to explicit Google `serviceAccount:`, `user:`, or `group:` principals for controlled smoke/operations access. It cannot contain `allUsers` or `allAuthenticatedUsers`.

### 2. Keep inactive runtimes disabled

`worker_runtime_ready` defaults to `false`. In this state:

- the Scheduler recovery trigger is absent;
- API and Scheduler service accounts do not receive worker Job invocation rights; and
- a manual container worker execution exits with code 78 instead of falsely reporting a successful sweep.

Set this gate to `true` only after the Task 6 worker claims a real Supabase queue message, honors the durable worker slot, persists checkpoints, and has passing recovery tests.

`operational_metrics_ready` also defaults to `false`. Alert policies remain absent until the application emits all documented, redacted events. The retained metric definitions are scoped by the formatter-controlled `creditops-api` or `creditops-worker` service field. The provider alert is an event-rate alert, not a request-failure percentage.

## Verification gates

Run before any apply:

```bash
terraform -chdir=deploy/terraform fmt -check -recursive
terraform -chdir=deploy/terraform init -backend=false
terraform -chdir=deploy/terraform validate
uv run pytest services/api/tests/security -q
```

Build the container with Docker or Cloud Build and deploy by immutable digest. The base-image patch tag in the Dockerfile is not a registry digest and must be resolved under the approved image-supply-chain process before a hardened release.

`scripts/smoke_cloud.sh` checks authenticated `/health`, configuration-only `/ready`, and worker Job visibility. `/ready` does not currently prove database, queue, Storage, FPT, restore, or end-to-end workflow readiness. Do not interpret a smoke pass as production readiness.

## Unresolved gates

Approved regions, data residency and cross-border flows, workload identities, resource sizes, notification channels, secret administration, restore testing, provider endpoints, and production-data authorization remain open questions. Real banking data is prohibited.

Reference contracts: [Vercel OIDC](https://vercel.com/docs/oidc), [Vercel OIDC with Google Cloud](https://vercel.com/docs/oidc/gcp), [Cloud Run service-to-service authentication](https://cloud.google.com/run/docs/authenticating/service-to-service), and [Google IAM Credentials ID tokens](https://cloud.google.com/docs/authentication/get-id-token).
