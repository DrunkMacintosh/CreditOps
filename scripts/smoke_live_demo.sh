#!/usr/bin/env bash
# Repeatable live smoke check for the deployed CreditOps hackathon demo.
#
# Scope: this script can only observe what is reachable from the public
# internet as an anonymous client. It does NOT have gcloud/vercel/supabase
# credentials (see AGENTS.md / docs/HACKATHON_DEMO_ACTIVATION.md — those
# steps require an operator with cloud access) and it never authenticates,
# uploads a document, or drives the browser flow itself. It:
#   1. Confirms the public web app is up and still serves the demo CTA.
#   2. Confirms the private API's current anonymous-access posture and
#      documents *why* a 403 there is the expected, correct outcome (not
#      a smoke-test failure) — see [API] below.
#   3. Prints the exact manual/browser steps and evidence list an operator
#      needs to run the real E2E (activated-demo) smoke by hand.
#
# No secrets are read, required, or printed. Only the public URLs below are
# contacted.
#
# Exit status:
#   0  current observed state matches the documented expected state
#      (web root = 200 with the demo CTA present; API is unreachable
#      anonymously, i.e. 403/401 — this is by design, see [API]).
#   1  the public web app is down or no longer serves the demo entry
#      point (a real regression this script exists to catch).
#   2  required tooling (curl) is missing.
#
# Usage:
#   scripts/smoke_live_demo.sh
#
# Optional overrides (for pointing at a different environment):
#   WEB_URL=https://example.vercel.app API_URL=https://example.run.app \
#     scripts/smoke_live_demo.sh

set -euo pipefail

readonly WEB_URL="${WEB_URL:-https://credit-ops-web.vercel.app}"
readonly API_URL="${API_URL:-https://creditops-api-375194342640.asia-southeast1.run.app}"
readonly DEMO_CTA="Trải nghiệm demo"
readonly CURL_MAX_TIME=15

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  sed -n '2,33p' "$0"
  exit 0
fi

command -v curl >/dev/null 2>&1 || {
  echo "FAIL: required command is unavailable: curl" >&2
  exit 2
}

section() {
  printf '\n== %s ==\n' "$1"
}

# fetch URL, return "HTTP_CODE" on stdout and body in the given file.
# Never uses curl --fail: a 4xx/5xx here is data to report, not a curl error.
fetch() {
  local url="$1" body_file="$2"
  curl --silent --show-error --max-time "$CURL_MAX_TIME" \
    --output "$body_file" --write-out '%{http_code}' \
    "$url" 2>/dev/null || echo "000"
}

overall_status=0
web_body_file="$(mktemp)"
api_root_body_file="$(mktemp)"
api_health_body_file="$(mktemp)"
trap 'rm -f "$web_body_file" "$api_root_body_file" "$api_health_body_file"' EXIT

# ---------------------------------------------------------------------------
section "WEB  ${WEB_URL}"
# ---------------------------------------------------------------------------
echo "This is the public Vercel-hosted frontend. Expected: 200, and the"
echo "landing page still offers the anonymous demo CTA."

web_code="$(fetch "$WEB_URL/" "$web_body_file")"
echo "GET ${WEB_URL}/ -> HTTP ${web_code}"

if [[ "$web_code" == "200" ]]; then
  echo "PASS: web root returned 200."
else
  echo "FAIL: web root returned ${web_code}, expected 200." >&2
  overall_status=1
fi

if grep -qF "$DEMO_CTA" "$web_body_file"; then
  echo "PASS: demo CTA string \"${DEMO_CTA}\" is present on the landing page."
else
  echo "FAIL: demo CTA string \"${DEMO_CTA}\" was not found on the landing page." >&2
  overall_status=1
fi

# ---------------------------------------------------------------------------
section "API  ${API_URL}"
# ---------------------------------------------------------------------------
echo "This is the private Cloud Run FastAPI service, deployed with"
echo "--no-allow-unauthenticated. Anonymous requests are rejected by the"
echo "Cloud Run IAM invoker check before they ever reach the application —"
echo "so an anonymous 403 here is the EXPECTED, correct current state, not"
echo "a smoke-test failure. This script does not hold and will not acquire"
echo "credentials to call the API as an authenticated caller; verifying"
echo "actual application health requires an operator with gcloud access"
echo "(see scripts/smoke_cloud.sh, which authenticates and checks"
echo "/api/v1/health and /api/v1/ready)."

api_root_code="$(fetch "$API_URL/" "$api_root_body_file")"
echo "GET ${API_URL}/ -> HTTP ${api_root_code}"
api_health_code="$(fetch "$API_URL/api/v1/health" "$api_health_body_file")"
echo "GET ${API_URL}/api/v1/health -> HTTP ${api_health_code}"

is_anon_blocked() {
  [[ "$1" == "403" || "$1" == "401" ]]
}

if is_anon_blocked "$api_root_code" && is_anon_blocked "$api_health_code"; then
  echo "EXPECTED: both endpoints reject anonymous access (403/401)."
  echo "  This confirms the API is private, as intended for the current"
  echo "  build stage; it is not evidence the API service is unhealthy."
else
  echo "NOTE: anonymous response differs from the documented expected"
  echo "  state (403/401 on both endpoints). This is not treated as a"
  echo "  smoke-test failure by itself — it may mean demo mode was"
  echo "  enabled (see docs/HACKATHON_DEMO_ACTIVATION.md step 2) or the"
  echo "  service is mid-deploy — but it should be checked by an operator"
  echo "  with cloud access before assuming the private posture is intact."
fi

# ---------------------------------------------------------------------------
section "MANUAL E2E — activated demo (requires a browser, cannot be scripted here)"
# ---------------------------------------------------------------------------
cat <<'EOF'
Prerequisite: demo mode has been enabled on the Cloud Run API by an
operator (DEMO_SESSION_ENABLED=true + DEMO_JWT_PRIVATE_KEY set — see
docs/HACKATHON_DEMO_ACTIVATION.md). Anonymous 403 on the API, as observed
above, means these steps cannot be exercised yet.

  1. Open the web app fresh, with no existing session cookie.
  2. Click "Trải nghiệm demo" on the landing page.
     -> confirm the app bootstraps a session (POST /api/v1/demo-sessions
        succeeds; __Host-creditops-workforce + CSRF cookies are set) and
        lands you in the working app, not an error state.
  3. Upload a synthetic PDF document.
     -> confirm the upload status transitions QUEUED -> RUNNING ->
        COMPLETED (poll, do not just check the first response), and that
        the completed case shows evidence/provenance for extracted facts
        (not a fabricated/empty result).
  4. Force a failure case (e.g. an unsupported/corrupt synthetic file, or
     whatever the demo's documented failure trigger is).
     -> confirm the UI honestly shows FAILED with no fallback success and
        no fabricated data.
  5. Open a second, independent browser session (new incognito window, no
     shared cookies) and repeat step 2 to mint a second demo session.
     -> confirm the second session cannot see or access the first
        session's case (cross-session isolation, enforced by Postgres RLS).

Evidence to capture at each step (no secrets, all public/operational
identifiers):
  - Public URL exercised (web and/or API).
  - Deployed git SHA (the commit actually live behind the URL).
  - Cloud Run revision id (API and worker, e.g. from `gcloud run
    services describe` / `gcloud run jobs describe`, or the response
    headers if exposed).
  - Worker execution id and task id for the processed upload.
  - Correlation id for the request chain (upload -> orchestration -> facts).
  - FPT route/model id actually used for inference on that document.
  - Timestamps for each state transition (QUEUED/RUNNING/COMPLETED/FAILED).
  - The final observed result (evidence shown, or the FAILED state shown).
EOF

# ---------------------------------------------------------------------------
section "SUMMARY"
# ---------------------------------------------------------------------------
if [[ "$overall_status" -eq 0 ]]; then
  echo "PASS: web is up and serves the demo CTA (current expected state)."
else
  echo "FAIL: see FAIL lines above." >&2
fi

exit "$overall_status"
