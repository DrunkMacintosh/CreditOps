#!/usr/bin/env bash
set -euo pipefail

tagged_image="${1:-}"
digest="${2:-}"

if [[ "$tagged_image" != *:* || "$tagged_image" == *'@'* ]]; then
  echo "expected a tagged Artifact Registry image" >&2
  exit 64
fi

if [[ "$digest" != sha256:* || "${#digest}" -ne 71 ]]; then
  echo "expected a 64-character sha256 image digest" >&2
  exit 64
fi

printf '%s@%s\n' "${tagged_image%:*}" "$digest"
