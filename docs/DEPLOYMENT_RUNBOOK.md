# Sổ tay triển khai CreditOps (Provisioning Runbook)

> Toàn bộ dữ liệu khách hàng, chính sách, tài liệu và phản hồi hệ thống ngân hàng trong dự án này là **dữ liệu tổng hợp (synthetic)**, chỉ dùng để trình diễn. Sổ tay này không chứng minh production readiness, tuân thủ quy định, hay phê duyệt của SHB — xem `AGENTS.md` và `deploy/terraform/README.md`.

Tài liệu này là **checklist thực thi lần-đầu-triển-khai** (provisioning) cho người **đã có sẵn**: một Supabase project, một GCP project đã bật billing, và một FPT API key + endpoint. Nó bổ sung (không lặp lại) `docs/DEPLOYMENT_SECRETS.md` (bản đồ secret/variable cho riêng pipeline GitHub Actions) và `deploy/terraform/README.md` (hợp đồng hạ tầng GCP). Khi có mâu thuẫn, `deploy/terraform/README.md` và mã nguồn là nguồn sự thật.

## Cách đọc tài liệu này

Mỗi pha chia hai phần:

- **Cần từ bạn** — giá trị/quyết định bạn phải tự cung cấp hoặc xác nhận trước.
- **Lệnh chạy sẵn** — lệnh copy-paste được, thay các `<PLACEHOLDER>`.

Quy tắc bất biến: **không dán secret vào chat, commit, log, hay artifact CI**. Giá trị nhạy cảm chỉ được phép tồn tại ở một trong ba nơi: shell cục bộ của bạn (không lưu lịch sử), GitHub Environment secrets, hoặc Google Secret Manager. `.env` và `.env.*` đã nằm trong `.gitignore` (ngoại trừ `.env.example`), áp dụng cho mọi thư mục con kể cả `apps/web/`.

## Trước khi bắt đầu — công cụ cần cài & đăng nhập cục bộ

| Công cụ | Dùng cho | Xác minh |
|---|---|---|
| `gh` (GitHub CLI) | Environment secrets/variables | `gh auth status` |
| `gcloud` | mọi thao tác GCP | `gcloud auth login && gcloud auth application-default login` |
| `supabase` CLI (>=2.54) | link, migrations, pgTAP | `supabase --version` |
| `vercel` CLI | project envs | `vercel login` |
| `terraform` (>=1.10, <2.0) | Cloud Run/IAM/Scheduler | `terraform version` |
| `docker` (buildx) | build ảnh bootstrap | `docker buildx version` |
| `python3`, `pnpm@11.13.1`, Node 24 | scripts, `apps/web` | đã pin trong `package.json`/`.nvmrc` |

Repo GitHub hiện tại: `DrunkMacintosh/CreditOps` (thay bằng repo thật của bạn nếu khác — lệnh dưới dùng `gh repo view --json nameWithOwner -q .nameWithOwner` để tự lấy).

## Lệch có chủ đích so với một giả định ban đầu — đọc trước Pha A

> **Cập nhật (commit `1f64999`) — fast-path cho demo:** `deploy.yml` hiện có bước `Apply runtime environment to API service` tự SUY RA và bơm runtime env cho API service từ GitHub secrets/vars: `DATABASE_URL` (dựng từ `SUPABASE_PROJECT_REF` + `SUPABASE_DB_PASSWORD` URL-encoded), `SUPABASE_URL`, tọa độ GCP dispatcher, và — khi được set — `SUPABASE_SERVICE_ROLE_KEY`, `OIDC_*`, `FPT_*`. Nghĩa là **Pha B (Secret Manager) không còn là điều kiện tiên quyết để deploy demo**; nó vẫn là đường hardening khuyến nghị (giá trị bơm qua env hiển thị với project viewer trong console GCP). Đoạn dưới đây mô tả thiết kế Secret-Manager-only gốc và vẫn đúng cho vận hành ngoài demo.

`docs/DEPLOYMENT_SECRETS.md` (đã có, được review) quy định rõ: **credential runtime không bao giờ chạm vào GitHub Actions** — `DATABASE_URL` và `SUPABASE_SERVICE_ROLE_KEY` chỉ sống trong Google Secret Manager (đọc bởi Cloud Run qua Terraform `api_secret_refs`/`worker_secret_refs`), không phải GitHub secrets. Xác minh: không workflow nào trong `.github/workflows/` (`deploy.yml`, `db-migrate.yml`) đọc hai tên đó. Sổ tay này giữ nguyên quy tắc đã-review đó thay vì đặt `DATABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` làm GitHub secret — hai giá trị này xuất hiện ở **Pha B (Google Secret Manager)**, không phải Pha A. `SUPABASE_URL` cũng không được workflow nào đọc nên không cần làm GitHub variable; nó chỉ cần cục bộ (script provisioning bucket) và trong Secret Manager (Pha B). `SUPABASE_PROJECT_REF` thì đúng là GitHub variable — cả `deploy.yml` và `db-migrate.yml` đều đọc nó.

---

## Pha A — Supabase

### Cần từ bạn

| Giá trị | Lấy ở đâu |
|---|---|
| `SUPABASE_PROJECT_REF` | Supabase Dashboard → Project Settings → General |
| Mật khẩu DB dự án (hoặc chuỗi kết nối đầy đủ) | Dashboard → Project Settings → Database |
| Supabase access token (cá nhân hoặc service) | Dashboard → Account → Access Tokens |
| `SUPABASE_URL` | Dashboard → Project Settings → API → Project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Dashboard → Project Settings → API → `service_role` (secret) |

### Lệnh chạy sẵn

```bash
export SUPABASE_PROJECT_REF="<your-project-ref>"

# 1) Đăng nhập + link (cục bộ, một lần)
supabase login
supabase link --project-ref "$SUPABASE_PROJECT_REF"
```

**A.1 — Áp dụng 37 migration đã có trong `supabase/migrations/`:**

```bash
supabase db push --linked --yes
supabase migration list
```

(CI cũng làm việc này tự động ở mỗi lần deploy — bước trên chỉ để bootstrap lần đầu và để bạn xác nhận thủ công trước khi bật CI.)

**A.2 — Chạy bộ test pgTAP một lần** (`supabase/tests/`, 33 file):

```bash
supabase test db
```

Lệnh này khởi động một shadow Postgres qua Docker và áp toàn bộ migration vào đó — cần **Docker Desktop đang chạy**. (Ghi chú nội bộ: lệnh này không chạy được trong sandbox của agent hỗ trợ dự án vì không có Docker ở đó; trên máy của bạn với Docker sẵn sàng thì lệnh sẽ chạy bình thường.)

**A.3 — Tạo 3 storage bucket riêng tư mà code kỳ vọng** (lấy từ `services/api/src/creditops/infrastructure/supabase/storage.py`, hằng số `_PRIVATE_BUCKETS`): `creditops-incoming`, `creditops-originals`, `creditops-derived`. Đã có script idempotent `scripts/provision_supabase_storage.py`:

```bash
export SUPABASE_URL="https://${SUPABASE_PROJECT_REF}.supabase.co"
export SUPABASE_SERVICE_ROLE_KEY="<paste-only-in-your-own-shell-never-in-chat>"
python3 scripts/provision_supabase_storage.py --url "$SUPABASE_URL"
```

In ra `created`/`updated`/`unchanged` cho mỗi bucket, tất cả `public=false`.

**A.4 — Tạo GitHub Environment `staging` (nếu chưa có) + set secrets/variables mà `deploy.yml`/`db-migrate.yml` thực sự đọc:**

```bash
REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
gh api -X PUT "repos/${REPO}/environments/staging"

gh secret set SUPABASE_ACCESS_TOKEN --env staging   # từ Dashboard → Access Tokens
gh secret set SUPABASE_DB_PASSWORD  --env staging   # hoặc thay bằng SUPABASE_DB_URL và sửa --db-url trong workflow (không nằm trong phạm vi sổ tay này)
gh variable set SUPABASE_PROJECT_REF --env staging --body "$SUPABASE_PROJECT_REF"
```

---

## Pha B — GCP (Cloud Run, Artifact Registry, IAM, Scheduler)

**Phát hiện quan trọng:** `deploy/terraform/` đã có sẵn một hợp đồng Terraform review-được cho phần lớn hạ tầng này (service account, Cloud Run API service + worker Job, Vercel↔GCP Workload Identity Federation, Cloud Scheduler có cổng chặn). Sổ tay này **dùng `terraform apply`** cho những phần đó thay vì phát minh lệnh `gcloud` thô song song — làm vậy sẽ tạo tài nguyên trùng/lệch với state Terraform đã cam kết. Lệnh `gcloud` thô chỉ dùng cho phần Terraform **chưa** phủ: Artifact Registry repository, các Secret Manager container/version (phải tồn tại *trước* `terraform apply` theo `deploy/terraform/README.md` bước 0), và GitHub Actions OIDC cho riêng `deploy.yml` (tách biệt với Vercel WIF pool mà Terraform tạo).

### Cần từ bạn

| Giá trị | Ghi chú |
|---|---|
| `GCP_PROJECT_ID` | project đã bật billing |
| `GCP_REGION` | region duyệt (region/data residency vẫn là OPEN QUESTION theo `deploy/terraform/README.md`; ví dụ `asia-southeast1`) |
| Tên Artifact Registry repo | ví dụ `creditops` |
| Vercel team slug, project, environment | dùng để suy ra `web_oidc_subject` chính xác — phải khớp với Pha C |
| `<GITHUB_OWNER>/<GITHUB_REPO>` | ràng buộc OIDC provider của GitHub Actions |

### B.1 — Bật API còn thiếu + tạo Artifact Registry repo (KHÔNG có trong Terraform)

`deploy/terraform/providers.tf` chỉ bật `cloudresourcemanager, cloudscheduler, iam, iamcredentials, logging, monitoring, run, secretmanager, sts` — thiếu `artifactregistry.googleapis.com`, và không có `google_artifact_registry_repository` nào trong toàn bộ `deploy/terraform/`. Đây là khoảng trống thật, không phải giản lược của sổ tay.

```bash
export GCP_PROJECT_ID="<your-gcp-project-id>"
export GCP_REGION="asia-southeast1"
export GAR_LOCATION="$GCP_REGION"
export GAR_REPOSITORY="creditops"

gcloud config set project "$GCP_PROJECT_ID"
gcloud services enable artifactregistry.googleapis.com --project="$GCP_PROJECT_ID"

gcloud artifacts repositories create "$GAR_REPOSITORY" \
  --project="$GCP_PROJECT_ID" \
  --repository-format=docker \
  --location="$GAR_LOCATION" \
  --description="CreditOps synthetic-dev images"
```

### B.2 — Tạo container Secret Manager + phiên bản đầu tiên (bắt buộc trước `terraform apply`)

`deploy/terraform/modules/secrets/main.tf` chỉ **đọc** metadata secret đã tồn tại (`data "google_secret_manager_secret"`) — nó không bao giờ tạo secret hay ghi payload. `deploy/terraform/variables.tf` (`api_secret_refs`/`worker_secret_refs`) nhận vào map `{tên biến môi trường -> {secret_id, version}}`; đây là **cơ chế duy nhất** mà module `cloud_run` dùng để bơm biến môi trường ngoài 3 hằng số cứng (`APP_ENV`, `DATA_CLASS`, `SERVICE_NAME`) — kể cả với giá trị không nhạy cảm như `GCP_PROJECT_ID`. Đây là ràng buộc thật của module hiện tại, không phải lựa chọn của sổ tay; xem mục "Câu hỏi còn mở" bên dưới.

| Biến môi trường (Cloud Run) | Secret Manager `secret_id` gợi ý | Runtime | Bắt buộc? |
|---|---|---|---|
| `DATABASE_URL` | `creditops-database-url` | API + worker | Có — không có thì không kết nối được Postgres |
| `SUPABASE_URL` | `creditops-supabase-url` | API + worker | Có |
| `SUPABASE_SERVICE_ROLE_KEY` | `creditops-supabase-service-role-key` | API + worker | Có |
| `SUPABASE_STORAGE_TUS_URL` | `creditops-supabase-tus-url` | API + worker | Không — mặc định suy từ `SUPABASE_URL` |
| `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL` | `creditops-oidc-issuer/-audience/-jwks-url` | API | **Chưa có giá trị thật** — xem "Câu hỏi còn mở": chưa chọn nhà cung cấp danh tính phát hành JWT `roles` claim mà `services/api/src/creditops/api/auth.py` yêu cầu |
| `GCP_PROJECT_ID`, `GCP_LOCATION`, `GCP_WORKER_JOB_NAME` | `creditops-gcp-project-id/-location/-worker-job` | API | Không — cả ba cùng có hoặc cùng không (`config.py` comment); thiếu thì dispatcher tắt, chỉ còn Scheduler sweep mỗi phút làm trigger |
| `FPT_API_KEY` | `creditops-fpt-api-key` | worker | Chỉ khi Pha D đã benchmark PASS |
| `FPT_REASONING_ENDPOINT_URL` / `_ENDPOINT_ID` | `creditops-fpt-reasoning-url/-id` | worker | Như trên |
| `FPT_VISION_ENDPOINT_URL` / `_ENDPOINT_ID` | `creditops-fpt-vision-url/-id` | worker | Như trên |
| `FPT_EMBEDDING_ENDPOINT_URL` / `_ENDPOINT_ID` | `creditops-fpt-embedding-url/-id` | worker | Như trên |

**Không** tạo `FPT_KIE_*`/`FPT_TABLE_*` — `model_catalog.py` chưa pin model cho hai capability này; nếu endpoint được cấu hình mà không có model pin, `FPTCatalog._capabilities_from_environment` ném `ValueError` và **toàn bộ** gateway (mọi capability) bị tắt, không chỉ hai capability đó.

```bash
create_secret() {
  local name="$1"
  gcloud secrets create "$name" --project="$GCP_PROJECT_ID" --replication-policy=automatic
}
add_version() {
  local name="$1"
  printf '%s' "<value-you-hold-locally>" | gcloud secrets versions add "$name" --project="$GCP_PROJECT_ID" --data-file=-
}

for s in creditops-database-url creditops-supabase-url creditops-supabase-service-role-key; do
  create_secret "$s"
  add_version "$s"   # nhập giá trị thật, KHÔNG hardcode trong script
done
# Lặp lại cho các secret FPT_* sau khi Pha D cho kết quả PASS.
```

Ghi lại số phiên bản in ra (`version: "1"`, ...) — đó là giá trị đưa vào `api_secret_refs`/`worker_secret_refs` của Terraform ở B.3.

### B.3 — Build ảnh bootstrap, rồi `terraform apply` (tạo service account, Cloud Run API+Job, Vercel WIF, Scheduler có cổng chặn)

**Thứ tự bắt buộc:** `terraform apply` phải chạy **trước** lần `deploy.yml` đầu tiên. `variables.tf` buộc `container_image` phải ghim theo digest (`@sha256:...`), và `deploy.yml` chỉ *cập nhật* image trên service/Job đã tồn tại (`gcloud run jobs update` không tự tạo Job). Nếu để `deploy.yml` chạy trước, worker Job sẽ không tồn tại và bước "Roll worker Jobs" sẽ thất bại; ngược lại nếu Cloud Run API được tạo lần đầu bởi `gcloud run deploy` thay vì Terraform, nó sẽ dùng service account mặc định thay vì `creditops-api` — sai với thiết kế IAM/least-privilege đã cam kết.

```bash
# 1) build + push một ảnh bootstrap
docker buildx build --platform linux/amd64 \
  -f services/api/Dockerfile \
  -t "${GAR_LOCATION}-docker.pkg.dev/${GCP_PROJECT_ID}/${GAR_REPOSITORY}/creditops-api:bootstrap" \
  --push .

DIGEST="$(gcloud artifacts docker images describe \
  "${GAR_LOCATION}-docker.pkg.dev/${GCP_PROJECT_ID}/${GAR_REPOSITORY}/creditops-api:bootstrap" \
  --project="$GCP_PROJECT_ID" --format='value(image_summary.digest)')"
export CONTAINER_IMAGE="${GAR_LOCATION}-docker.pkg.dev/${GCP_PROJECT_ID}/${GAR_REPOSITORY}/creditops-api@${DIGEST}"
```

Tạo file tfvars **cục bộ, không commit** (không có payload secret bên trong — chỉ `secret_id`/`version` — nhưng vẫn để ngoài git vì đây là repo public và file này lộ topology):

```bash
cat > /tmp/creditops-dev.auto.tfvars <<EOF
project_id              = "$GCP_PROJECT_ID"
region                  = "$GCP_REGION"
container_image         = "$CONTAINER_IMAGE"
api_cpu                 = "1"
api_memory              = "512Mi"
api_timeout_seconds     = 30
api_concurrency         = 40
api_min_instances       = 0
api_max_instances       = 2
worker_cpu              = "1"
worker_memory           = "512Mi"
worker_timeout_seconds  = 300
web_identity_pool_id    = "creditops-web-pool"
web_identity_provider_id = "creditops-web-oidc"
vercel_team_slug        = "<your-vercel-team-slug>"
web_oidc_subject        = "owner:<your-vercel-team-slug>:project:<your-vercel-project>:environment:production"
api_secret_refs = {
  DATABASE_URL              = { secret_id = "creditops-database-url",             version = "1" }
  SUPABASE_URL              = { secret_id = "creditops-supabase-url",             version = "1" }
  SUPABASE_SERVICE_ROLE_KEY = { secret_id = "creditops-supabase-service-role-key", version = "1" }
}
worker_secret_refs = {
  DATABASE_URL              = { secret_id = "creditops-database-url",             version = "1" }
  SUPABASE_URL              = { secret_id = "creditops-supabase-url",             version = "1" }
  SUPABASE_SERVICE_ROLE_KEY = { secret_id = "creditops-supabase-service-role-key", version = "1" }
}
EOF
```

(Bảng B.2 liệt kê thêm `OIDC_*`/`GCP_*`/`FPT_*` — thêm vào hai map trên khi có giá trị thật; đừng thêm `OIDC_*` giả để "cho đủ", vì hiện chưa có giá trị thật — xem "Câu hỏi còn mở".)

```bash
terraform -chdir=deploy/terraform fmt -check -recursive
terraform -chdir=deploy/terraform init -backend=false
terraform -chdir=deploy/terraform validate
terraform -chdir=deploy/terraform/envs/dev init
terraform -chdir=deploy/terraform/envs/dev plan  -var-file=/tmp/creditops-dev.auto.tfvars
terraform -chdir=deploy/terraform/envs/dev apply -var-file=/tmp/creditops-dev.auto.tfvars

terraform -chdir=deploy/terraform/envs/dev output -json
```

Ghi lại `api_url`, `worker_job_name`, `service_account_emails.web_invoker`, `web_identity_provider_name` — dùng ở Pha C.

### B.4 — GitHub Actions OIDC riêng cho `deploy.yml` (không nằm trong Terraform ở trên)

`deploy/terraform/modules/iam/main.tf` chỉ tạo Workload Identity Pool cho **Vercel**; nó không tạo pool/provider/service-account nào cho GitHub Actions. `deploy.yml`'s `google-github-actions/auth@v3` cần một pool/provider riêng trỏ tới `token.actions.githubusercontent.com`, và một deployer service account.

```bash
export GITHUB_OWNER_REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
export GCP_PROJECT_NUMBER="$(gcloud projects describe "$GCP_PROJECT_ID" --format='value(projectNumber)')"

gcloud iam workload-identity-pools create "github-actions-pool" \
  --project="$GCP_PROJECT_ID" --location="global" \
  --display-name="GitHub Actions deploy"

gcloud iam workload-identity-pools providers create-oidc "github-actions-provider" \
  --project="$GCP_PROJECT_ID" --location="global" \
  --workload-identity-pool="github-actions-pool" \
  --display-name="GitHub Actions OIDC" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository == '${GITHUB_OWNER_REPO}' && assertion.ref == 'refs/heads/main'" \
  --issuer-uri="https://token.actions.githubusercontent.com"

gcloud iam service-accounts create creditops-deployer \
  --project="$GCP_PROJECT_ID" \
  --display-name="CreditOps GitHub Actions deployer"

gcloud iam service-accounts add-iam-policy-binding \
  "creditops-deployer@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
  --project="$GCP_PROJECT_ID" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${GCP_PROJECT_NUMBER}/locations/global/workloadIdentityPools/github-actions-pool/attribute.repository/${GITHUB_OWNER_REPO}"

# Quyền tối thiểu theo deploy.yml + docs/DEPLOYMENT_SECRETS.md "Google IAM prerequisites":
gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
  --member="serviceAccount:creditops-deployer@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/artifactregistry.writer"
gcloud projects add-iam-policy-binding "$GCP_PROJECT_ID" \
  --member="serviceAccount:creditops-deployer@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/run.admin"
gcloud iam service-accounts add-iam-policy-binding \
  "creditops-api@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
  --project="$GCP_PROJECT_ID" --role="roles/iam.serviceAccountUser" \
  --member="serviceAccount:creditops-deployer@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
gcloud iam service-accounts add-iam-policy-binding \
  "creditops-worker@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
  --project="$GCP_PROJECT_ID" --role="roles/iam.serviceAccountUser" \
  --member="serviceAccount:creditops-deployer@${GCP_PROJECT_ID}.iam.gserviceaccount.com"
```

GitHub secrets/variables cho `staging` Environment (khớp đúng tên mà `deploy.yml` đọc):

```bash
GCP_WIP_PROVIDER="projects/${GCP_PROJECT_NUMBER}/locations/global/workloadIdentityPools/github-actions-pool/providers/github-actions-provider"

gh secret set GCP_WORKLOAD_IDENTITY_PROVIDER --env staging --body "$GCP_WIP_PROVIDER"
gh secret set GCP_DEPLOYER_SERVICE_ACCOUNT   --env staging --body "creditops-deployer@${GCP_PROJECT_ID}.iam.gserviceaccount.com"

gh variable set GCP_PROJECT_ID          --env staging --body "$GCP_PROJECT_ID"
gh variable set GCP_REGION              --env staging --body "$GCP_REGION"
gh variable set GAR_LOCATION            --env staging --body "$GAR_LOCATION"
gh variable set GAR_REPOSITORY          --env staging --body "$GAR_REPOSITORY"
gh variable set CLOUD_RUN_API_SERVICE   --env staging --body "creditops-api"     # tên cố định trong deploy/terraform/modules/cloud_run/main.tf — không tự đặt tên khác
gh variable set CLOUD_RUN_WORKER_JOB    --env staging --body "creditops-worker"  # như trên
gh variable set WORKER_RUNTIME_READY    --env staging --body "false"            # giữ false — xem B.5
# CLOUD_RUN_WORKER_JOB_AGENT: để trống — xem B.6 (Job thứ hai chưa tồn tại)
```

### B.5 — Cloud Scheduler sweep mỗi phút — trạng thái hiện tại (đừng tự bật sớm)

`deploy/terraform/modules/scheduler/main.tf` chỉ tạo `google_cloud_scheduler_job.worker_recovery` khi `worker_runtime_ready = true`; mặc định `false` trong `deploy/terraform/envs/dev/main.tf` (hardcode `worker_runtime_ready = false`, không đọc từ biến). Đây là fail-closed **có chủ đích** (`deploy/terraform/README.md`: "Set this gate to true only after the Task 6 worker claims a real Supabase queue message... and has passing recovery tests"). Đừng bật `WORKER_RUNTIME_READY`/`worker_runtime_ready` chỉ để "cho đủ checklist". Khi worker đã được verify:

1. sửa `worker_runtime_ready = true` trong `deploy/terraform/envs/dev/main.tf` (thay đổi mã — ngoài phạm vi sổ tay này) rồi `terraform apply` lại;
2. `gh variable set WORKER_RUNTIME_READY --env staging --body "true"` để `deploy.yml` bắt đầu roll worker Job ở mỗi lần deploy.

### B.6 — Câu hỏi còn mở: Job thứ hai cho `WORKER_MODE=agent`

`deploy.yml` đã có logic roll một Job `agent` tùy chọn (`CLOUD_RUN_WORKER_JOB_AGENT`), nhưng `deploy/terraform/modules/cloud_run/main.tf` chỉ định nghĩa **một** `google_cloud_run_v2_job "worker"`. Tạo Job thứ hai + Scheduler riêng cho nó là thay đổi mã Terraform — nằm ngoài phạm vi sổ tay này (xem "Câu hỏi còn mở" ở cuối tài liệu).

---

## Pha C — Vercel

### Cần từ bạn

- Project Vercel đã liên kết với repo này (`vercel login`, `vercel link` nếu chưa).
- Team slug / project / environment **giống hệt** giá trị đã dùng cho `web_oidc_subject` ở B.3 — sai lệch một ký tự khiến `attribute_condition` của Terraform từ chối token (fail-closed).

### C.1 — Set 6 biến môi trường server-side mà `apps/web/lib/server/cloud-run-auth.ts` + `creditops-bff.ts` đọc

Lưu ý: `deploy/terraform/envs/dev/main.tf` chỉ re-export hai output `api_url`/`worker_job_name` (xem dòng 130-136) — **không** re-export `service_account_emails`/`web_identity_provider_name` dù root module `deploy/terraform/outputs.tf` có khai báo chúng. `terraform -chdir=deploy/terraform/envs/dev output service_account_emails` sẽ báo lỗi "output not found". Với `GCP_SERVICE_ACCOUNT_EMAIL`, dùng lại đúng `account_id = "creditops-web-invoker"` đã hardcode trong `deploy/terraform/modules/iam/main.tf` để tự ráp email (deterministic, không cần đọc state); `GCP_WORKLOAD_IDENTITY_POOL_ID`/`_PROVIDER_ID` chính là hai giá trị bạn đã tự chọn và đặt vào tfvars ở B.3 — không cần đọc lại từ Terraform.

```bash
API_URL="$(terraform -chdir=deploy/terraform/envs/dev output -raw api_url)"   # gốc https, không path/query
WEB_INVOKER_EMAIL="creditops-web-invoker@${GCP_PROJECT_ID}.iam.gserviceaccount.com"

printf '%s' "$API_URL"        | vercel env add CREDITOPS_API_URL production
printf '%s' "$API_URL"        | vercel env add CREDITOPS_API_AUDIENCE production
printf '%s' "$GCP_PROJECT_NUMBER" | vercel env add GCP_PROJECT_NUMBER production
printf '%s' "creditops-web-pool"   | vercel env add GCP_WORKLOAD_IDENTITY_POOL_ID production      # phải khớp web_identity_pool_id trong tfvars (B.3)
printf '%s' "creditops-web-oidc"   | vercel env add GCP_WORKLOAD_IDENTITY_POOL_PROVIDER_ID production  # phải khớp web_identity_provider_id trong tfvars (B.3)
printf '%s' "$WEB_INVOKER_EMAIL"   | vercel env add GCP_SERVICE_ACCOUNT_EMAIL production
```

Muốn xác nhận `WEB_INVOKER_EMAIL` khớp với những gì Terraform thực sự tạo (thay vì chỉ tự ráp chuỗi), có thể thêm một khối `output` chuyển tiếp vào `deploy/terraform/envs/dev/main.tf` (thay đổi mã, ngoài phạm vi sổ tay này) hoặc đọc trực tiếp: `gcloud iam service-accounts describe "$WEB_INVOKER_EMAIL" --project="$GCP_PROJECT_ID"`.

`CREDITOPS_API_AUDIENCE` phải là **chính xác** gốc HTTPS không path/query/fragment (`cloud-run-auth.ts`'s `isHttpsUrl` từ chối bất kỳ path nào) — dùng đúng `api_url` output của Terraform.

### C.2 — Bật Vercel OIDC Federation (thao tác UI, không có lệnh CLI)

Vercel Project → Settings → Security/Environment Variables → **OIDC Federation** → bật cho môi trường Production. Khi bật, Vercel tự inject `VERCEL_OIDC_TOKEN`/header `x-vercel-oidc-token` cho mỗi request lúc chạy — không cần set thủ công (`cloud-run-auth.ts` chỉ đọc `process.env.VERCEL_OIDC_TOKEN` trực tiếp khi `process.env.VERCEL !== "1"`, tức là đường dùng để test cục bộ).

### C.3 — GitHub secrets/variables cho job `vercel` trong `deploy.yml`

```bash
gh secret set VERCEL_TOKEN      --env staging
gh variable set VERCEL_ORG_ID     --env staging --body "<vercel-team-id>"
gh variable set VERCEL_PROJECT_ID --env staging --body "<vercel-project-id>"
gh variable set VERCEL_CLI_VERSION --env staging --body "<pinned-version>"
```

---

## Pha D — FPT

### Cần từ bạn

- `FPT_API_KEY`.
- Với ít nhất capability `reasoning` (model đã pin: `DeepSeek-V4-Flash` trong `services/api/src/creditops/infrastructure/fpt/model_catalog.py`): `FPT_REASONING_ENDPOINT_URL` (HTTPS, không query/fragment) và `FPT_REASONING_ENDPOINT_ID`. Tương tự cho `vision` (`Qwen2.5-VL-72B-Instruct`) và `embedding` (`multilingual-e5-large`) nếu có endpoint. **Không** cấu hình `kie`/`table` — chưa pin model, xem B.2.

### D.1 — `.env` cục bộ, không commit, không dán vào chat

```bash
cat >> .env <<'EOF'
FPT_API_KEY=<value-you-hold-locally>
FPT_REASONING_ENDPOINT_URL=https://<fpt-endpoint>
FPT_REASONING_ENDPOINT_ID=<endpoint-id>
EOF
```

`scripts/smoke_fpt.py` và harness đánh giá đọc `os.environ` **trực tiếp** (không tự nạp file `.env` như `Settings` của FastAPI làm) — nạp file vào shell trước khi chạy:

```bash
set -a; source .env; set +a
```

### D.2 — Smoke test trực tiếp lên endpoint sống

```bash
python3 scripts/smoke_fpt.py
```

In `SKIP` nếu catalog chưa cấu hình đủ, `PASS`/`FAIL` cho kết quả gọi thật — không bao giờ giả lập.

### D.3 — Benchmark harness

```bash
python3 scripts/run_fpt_benchmark.py   # đường dẫn tham chiếu — có thể đang được một agent khác viết song song trong nhánh này (evaluation/runner.py + evaluation/manifests/*.yaml đã tồn tại làm nền)
```

### D.4 — Chỉ khi benchmark PASS: commit bằng chứng vào mã

1. Thêm một dòng vào `FPT_BENCHMARK_RECORDS` trong `services/api/src/creditops/infrastructure/fpt/benchmark_records.py`, khớp chính xác `capability`, `model_id` (từ `model_catalog.py`), `endpoint_id`, và `route_version`/`prompt_version`/`schema_version` hiện tại của `catalog.py` (`fpt-route-v1`/`intake-prompt-v1`/`intake-schema-v1`).
2. Thêm dòng vào `docs/DECISION_LOG.md` theo đúng khuôn bảng đã dùng (`| Date | Decision | Reason | Alternatives considered | Status | Conditions... |`), ví dụ:

   ```
   | <YYYY-MM-DD> | Commit a PASSED FPT benchmark record for capability=reasoning, model_id=DeepSeek-V4-Flash, endpoint_id=<endpoint-id>, binding route_version=fpt-route-v1/prompt_version=intake-prompt-v1/schema_version=intake-schema-v1. | Benchmark evidence recorded per the fail-closed FPT activation gate (2026-07-18 entry above). | Leaving the route DISABLED; activating without a committed record (not permitted by catalog.py). | CONFIRMED | Review if route_version/prompt_version/schema_version changes, or if the benchmark evidence artefact is later found invalid. |
   ```

3. Chỉ sau bước 1-2, route mới chuyển ACTIVE (`FPTCatalog.from_configuration` kiểm tra `_has_benchmark_pass`).

### D.5 — Đưa `FPT_*` thật vào Cloud Run worker

Quay lại **Pha B.2**: tạo secret Manager cho `FPT_API_KEY`/`FPT_REASONING_ENDPOINT_URL`/`FPT_REASONING_ENDPOINT_ID` (và vision/embedding nếu có), thêm vào `worker_secret_refs` trong tfvars, `terraform apply` lại. Worker (`WORKER_MODE=agent`/`document`) sẽ có `FPTCatalog.from_configuration` gọi được vào lần chạy tiếp theo.

---

## Pha E — Kiểm tra sau triển khai

### E.1/E.2 — Health, ready, và khả năng thấy Job worker

`scripts/smoke_cloud.sh` đã có sẵn — làm đúng việc này:

```bash
scripts/smoke_cloud.sh "$API_URL" "$GCP_PROJECT_ID" "$GCP_REGION" creditops-worker
# thêm --execute-worker để chủ động chạy thử một lượt Job (chỉ khi WORKER_RUNTIME_READY=true)
```

Kiểm tra `/api/v1/health` trả `{"status": "ok"}` và `/api/v1/ready` trả `{"status": "configuration-valid"}` qua Google identity token có audience đúng — script làm việc này tự động.

### E.3 — Một lượt đi qua case tổng hợp (synthetic case walkthrough)

**Bị chặn hiện tại:** `require_actor` (`services/api/src/creditops/api/auth.py`) xác thực JWT RS256 có claim `roles` qua `OIDC_ISSUER`/`OIDC_AUDIENCE`/`OIDC_JWKS_URL` — nhưng **chưa có nhà cung cấp danh tính nào được chọn** để phát hành JWT đó (xem Pha B.2 và "Câu hỏi còn mở"; `creditops-bff.ts` comment: "The future identity callback must issue this..."). Do đó một lượt đi qua có xác thực đầy đủ (tạo case → upload → intake → underwriting → risk review → credit ops) **chưa thể thực hiện được từ ngoài** cho tới khi có identity provider thật. Khi có:

1. Lấy một JWT hợp lệ (subject + `roles` claim) từ identity provider đã chọn.
2. `POST {API_URL}/api/v1/cases` với `Authorization: Bearer <jwt>` + `X-Serverless-Authorization: Bearer <google-id-token-audience=API_URL>` (BFF làm bước thứ hai tự động; gọi thẳng Cloud Run cần tự tạo cả hai header).
3. Theo dõi qua các endpoint `GET /api/v1/cases/{id}`, `/orchestration/advance`, v.v. — danh sách đầy đủ route nằm trong `apps/web/lib/server/creditops-bff.ts`.

### E.4 — Checklist chấp nhận (acceptance)

- [ ] `supabase migration list` khớp 37 file trong `supabase/migrations/`.
- [ ] `supabase test db` PASS cục bộ.
- [ ] 3 bucket private tồn tại (`provision_supabase_storage.py` in `unchanged` ở lần chạy thứ hai).
- [ ] `terraform -chdir=deploy/terraform/envs/dev plan` không có diff ngoài dự kiến.
- [ ] `scripts/smoke_cloud.sh` PASS.
- [ ] `deploy.yml` chạy xanh từ `main` (workflow_dispatch một lần trước khi dựa vào trigger tự động).
- [ ] Vercel production deploy trả 200 ở `VERCEL_PRODUCTION_URL` (nếu đã set).
- [ ] `scripts/smoke_fpt.py` in `PASS` (không bắt buộc để deploy, bắt buộc để bật route FPT).
- [ ] Mọi trang/API vẫn hiển thị banner "dữ liệu tổng hợp" bắt buộc (`shared/synthetic-notice.json`).

---

## Bảng tổng hợp biến/secret (đầy đủ)

| Tên | Loại | Nơi lưu | Đọc bởi |
|---|---|---|---|
| `SUPABASE_ACCESS_TOKEN` | secret | GitHub Env `staging` | `deploy.yml`, `db-migrate.yml` |
| `SUPABASE_DB_PASSWORD` | secret | GitHub Env `staging` | `deploy.yml`, `db-migrate.yml` |
| `SUPABASE_PROJECT_REF` | variable | GitHub Env `staging` | `deploy.yml`, `db-migrate.yml` |
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | secret | GitHub Env `staging` | `deploy.yml` (`google-github-actions/auth`) |
| `GCP_DEPLOYER_SERVICE_ACCOUNT` | secret | GitHub Env `staging` | `deploy.yml` |
| `GCP_PROJECT_ID`, `GCP_REGION`, `GAR_LOCATION`, `GAR_REPOSITORY`, `CLOUD_RUN_API_SERVICE`, `CLOUD_RUN_WORKER_JOB`, `CLOUD_RUN_WORKER_JOB_AGENT` (mở), `WORKER_RUNTIME_READY` | variable | GitHub Env `staging` | `deploy.yml` |
| `VERCEL_TOKEN` | secret | GitHub Env `staging` | `deploy.yml` |
| `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`, `VERCEL_CLI_VERSION`, `VERCEL_PRODUCTION_URL` | variable | GitHub Env `staging` | `deploy.yml` |
| `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL`, `GCP_PROJECT_ID`, `GCP_LOCATION`, `GCP_WORKER_JOB_NAME` | Secret Manager (payload) | GCP Secret Manager | Cloud Run API via `api_secret_refs` |
| `DATABASE_URL`, `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `FPT_API_KEY`, `FPT_{REASONING,VISION,EMBEDDING}_ENDPOINT_{URL,ID}` | Secret Manager (payload) | GCP Secret Manager | Cloud Run worker via `worker_secret_refs` |
| `WORKER_MODE` | plain env, non-secret | pinned by `deploy.yml`'s `--update-env-vars` per Job | worker |
| `FPT_{CAP}_MODEL_ID` | committed code | `model_catalog.py` | catalog (env cannot override) |
| `CREDITOPS_API_URL`, `CREDITOPS_API_AUDIENCE`, `GCP_PROJECT_NUMBER`, `GCP_WORKLOAD_IDENTITY_POOL_ID`, `GCP_WORKLOAD_IDENTITY_POOL_PROVIDER_ID`, `GCP_SERVICE_ACCOUNT_EMAIL` | project env | Vercel (Production) | `apps/web/lib/server/{cloud-run-auth.ts,creditops-bff.ts}` |
| `VERCEL_OIDC_TOKEN` | platform-injected | Vercel runtime (per-request header when deployed) | `cloud-run-auth.ts` |
| `FPT_API_KEY`, `FPT_{REASONING,VISION,EMBEDDING}_ENDPOINT_{URL,ID}` | local-only `.env` | máy của bạn | `scripts/smoke_fpt.py`, `scripts/run_fpt_benchmark.py` (evaluation harness) |

## Câu hỏi còn mở để lại trong sổ tay này

- **Nhà cung cấp danh tính (identity provider) chưa được chọn.** `OIDC_ISSUER`/`OIDC_AUDIENCE`/`OIDC_JWKS_URL` cần một bên phát hành JWT RS256 có claim `roles` cho `require_actor`; chưa có lựa chọn nào được xác nhận (không phải Supabase Auth mặc định, vốn không tự có claim `roles` hay JWKS RS256 theo cấu hình cơ bản). Chặn E.3 hoàn toàn.
- **`GCP_PROJECT_ID`/`GCP_LOCATION`/`GCP_WORKER_JOB_NAME` phải đi qua Secret Manager** dù không nhạy cảm, vì `cloud_run` module Terraform không có cơ chế bơm plain env ngoài 3 hằng số cứng. Cân nhắc mở rộng module (thêm biến `api_extra_env`) hoặc set qua `--update-env-vars` trong `deploy.yml` như đã làm với `WORKER_MODE` — cả hai đều là thay đổi mã, ngoài phạm vi sổ tay này.
- **Job Cloud Run thứ hai cho `WORKER_MODE=agent`** (B.6) và Cloud Scheduler riêng cho nó chưa có trong Terraform; `deploy.yml` đã chờ sẵn `CLOUD_RUN_WORKER_JOB_AGENT`.
- **Region/data residency** chưa được duyệt chính thức (`deploy/terraform/README.md`, "Unresolved gates").
- **`scripts/run_fpt_benchmark.py`** được tham chiếu theo yêu cầu nhiệm vụ nhưng chưa tồn tại trong repo tại thời điểm viết sổ tay này — có thể đang được viết song song; `evaluation/runner.py` + `evaluation/manifests/*.yaml` đã có làm nền.
- **`kie`/`table`** vẫn chưa pin model trong `model_catalog.py` — hai capability này ở lại fail-closed cho tới khi có quyết định benchmark.
- **`worker_runtime_ready`/`WORKER_RUNTIME_READY`** phải ở lại `false` cho tới khi worker có bằng chứng sweep/checkpoint/recovery thật — đừng bật chỉ vì sổ tay này đã "xong".

---

## Quy trình vận hành ổn định (release thông thường)

Phần dưới đây mô tả những gì đã xảy ra *sau khi* các pha A–E ở trên hoàn tất một lần; nó đã tồn tại trong lịch sử commit của tài liệu này và được giữ nguyên ý nghĩa.

### Release thông thường

1. Mở pull request. `CI` phải pass test/Ruff/mypy backend và test/typecheck/lint/build frontend.
2. Merge vào `main` chỉ sau khi được review.
3. Workflow `Deploy synthetic development` tự chạy sau khi `CI` thành công trên `main`.
4. Nó áp migration Supabase, publish ảnh theo commit, deploy Cloud Run API riêng tư, và deploy frontend Vercel đã build sẵn.
5. Worker Job không bị đổi khi `WORKER_RUNTIME_READY=false`.

### Release thủ công

Chỉ dùng **Run workflow** cho một commit đã có trên `main`. Workflow vẫn áp migration trước và dùng Environment `staging` đã được bảo vệ.

### Rollback

- **Supabase:** dừng workflow khi migration lỗi; dùng forward migration đã duyệt hoặc quy trình khôi phục database đã tài liệu hóa. Đừng sửa lịch sử migration tùy tiện.
- **Cloud Run:** trỏ service về revision/image digest bất biến trước đó qua Cloud Run console hoặc `gcloud run services update-traffic`.
- **Vercel:** promote deployment tốt gần nhất từ dashboard Vercel hoặc deploy lại cùng commit qua workflow.
- **Worker:** giữ `WORKER_RUNTIME_READY=false` trừ khi queue, checkpoint, và processor FPT thật đã được live-verify.

### Điều mà workflow này KHÔNG chứng minh

CI xanh không chứng minh Supabase, Cloud Run, Vercel, FPT, data residency, khôi phục dữ liệu, tính đúng của policy, tuân thủ quy định, hay phê duyệt của SHB. Chạy thật đòi hỏi môi trường synthetic đã duyệt và credential tương ứng.
