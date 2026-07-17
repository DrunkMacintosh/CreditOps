"""Idempotently provision the private Supabase Storage buckets for CreditOps."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from typing import Literal
from urllib.parse import quote, urlparse

import httpx

BUCKET_NAMES: tuple[str, ...] = (
    "creditops-incoming",
    "creditops-originals",
    "creditops-derived",
)

ProvisionResult = Literal["created", "updated", "unchanged"]


class StorageProvisionError(RuntimeError):
    """Raised when the Storage API cannot establish the private-bucket contract."""


def _storage_base_url(supabase_url: str) -> str:
    normalized = supabase_url.rstrip("/")
    parsed = urlparse(normalized)
    local_hosts = {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise StorageProvisionError("SUPABASE_URL must be an absolute HTTP(S) URL")
    if parsed.scheme != "https" and parsed.hostname not in local_hosts:
        raise StorageProvisionError("non-local SUPABASE_URL must use HTTPS")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise StorageProvisionError("SUPABASE_URL must not contain credentials, query, or fragment")
    return f"{normalized}/storage/v1/"


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:300]
    return str(payload)[:300]


def _request(
    client: httpx.Client,
    method: str,
    path: str,
    *,
    payload: dict[str, object] | None = None,
) -> httpx.Response:
    try:
        response = client.request(method, path, json=payload)
    except httpx.HTTPError as exc:
        raise StorageProvisionError(f"Storage request failed: {exc}") from exc
    return response


def _get_bucket(client: httpx.Client, bucket_name: str) -> dict[str, object] | None:
    response = _request(client, "GET", f"bucket/{quote(bucket_name, safe='')}")
    if response.status_code == httpx.codes.NOT_FOUND:
        return None
    if response.is_error:
        raise StorageProvisionError(
            f"failed to inspect bucket {bucket_name!r}: "
            f"HTTP {response.status_code} {_error_detail(response)}"
        )
    payload = response.json()
    if not isinstance(payload, dict):
        raise StorageProvisionError(f"unexpected response while inspecting {bucket_name!r}")
    return payload


def _private_bucket_options(existing: dict[str, object] | None = None) -> dict[str, object]:
    options: dict[str, object] = {"public": False}
    if existing is None:
        return options
    for key in ("file_size_limit", "allowed_mime_types"):
        value = existing.get(key)
        if value is not None:
            options[key] = value
    return options


def ensure_private_bucket(client: httpx.Client, bucket_name: str) -> ProvisionResult:
    existing = _get_bucket(client, bucket_name)
    if existing is None:
        response = _request(
            client,
            "POST",
            "bucket",
            payload={"id": bucket_name, "name": bucket_name, "public": False},
        )
        if response.status_code == httpx.codes.CONFLICT:
            existing = _get_bucket(client, bucket_name)
            if existing is None:
                raise StorageProvisionError(
                    f"bucket {bucket_name!r} conflicted but is not readable"
                )
        elif response.is_error:
            raise StorageProvisionError(
                f"failed to create bucket {bucket_name!r}: "
                f"HTTP {response.status_code} {_error_detail(response)}"
            )
        else:
            return "created"

    if existing.get("public") is False:
        return "unchanged"

    response = _request(
        client,
        "PUT",
        f"bucket/{quote(bucket_name, safe='')}",
        payload=_private_bucket_options(existing),
    )
    if response.is_error:
        raise StorageProvisionError(
            f"failed to make bucket {bucket_name!r} private: "
            f"HTTP {response.status_code} {_error_detail(response)}"
        )
    return "updated"


def provision(supabase_url: str, service_role_key: str) -> tuple[ProvisionResult, ...]:
    if not service_role_key.strip():
        raise StorageProvisionError("SUPABASE_SERVICE_ROLE_KEY is required")

    headers = {
        "Authorization": f"Bearer {service_role_key}",
        "apikey": service_role_key,
        "Content-Type": "application/json",
    }
    with httpx.Client(
        base_url=_storage_base_url(supabase_url),
        headers=headers,
        timeout=30.0,
        follow_redirects=False,
    ) as client:
        return tuple(ensure_private_bucket(client, name) for name in BUCKET_NAMES)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.environ.get("SUPABASE_URL"),
        help="Supabase project URL (defaults to SUPABASE_URL)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.url:
        raise StorageProvisionError("SUPABASE_URL or --url is required")
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    results = provision(args.url, service_role_key)
    for bucket_name, result in zip(BUCKET_NAMES, results, strict=True):
        print(f"{bucket_name}: {result} (private)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
