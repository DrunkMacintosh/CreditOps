from __future__ import annotations

import re
from collections.abc import Mapping
from typing import overload
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "[REDACTED]"

_SENSITIVE_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "cookie",
        "databaseurl",
        "jwt",
        "password",
        "privatekey",
        "refreshtoken",
        "secret",
        "secretkey",
        "session",
        "signedurl",
        "supabaseservicerolekey",
        "token",
    }
)
_BEARER_PATTERN = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]+")
_AUTHORIZATION_PATTERN = re.compile(
    r"(?i)\b(Authorization\s*[:=]\s*)(?!\[REDACTED\])[^\s,;]+(?:\s+[^\s,;]+)?"
)
_URL_PATTERN = re.compile(r"https?://[^\s\]\[\"'<>]+")


def _normalized_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def _is_sensitive_key(key: object) -> bool:
    normalized = _normalized_key(key)
    return (
        normalized in _SENSITIVE_KEYS
        or normalized.endswith(("apikey", "cookie", "token"))
        or any(
            marker in normalized
            for marker in ("authorization", "password", "privatekey", "secret", "signedurl")
        )
    )


def _is_sensitive_query_key(key: str) -> bool:
    normalized = _normalized_key(key)
    return normalized.endswith(("credential", "key", "signature", "token")) or any(
        marker in normalized
        for marker in ("authorization", "cookie", "password", "privatekey", "secret")
    )


def _redact_url(match: re.Match[str]) -> str:
    raw_url = match.group(0)
    try:
        parsed = urlsplit(raw_url)
        hostname = parsed.hostname or ""
        if parsed.port is not None:
            hostname = f"{hostname}:{parsed.port}"
        netloc = f"{REDACTED}@{hostname}" if parsed.username or parsed.password else hostname
        query = urlencode(
            [
                (key, REDACTED if _is_sensitive_query_key(key) else value)
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            ]
        )
        fragment = REDACTED if parsed.fragment else ""
        return urlunsplit((parsed.scheme, netloc, parsed.path, query, fragment))
    except ValueError:
        return REDACTED


def _redact_string(value: str) -> str:
    value = _BEARER_PATTERN.sub(rf"\1{REDACTED}", value)
    value = _AUTHORIZATION_PATTERN.sub(rf"\1{REDACTED}", value)
    return _URL_PATTERN.sub(_redact_url, value)


class LogRedactor:
    """Copy and redact structured log values before serialization."""

    @overload
    def clean(self, value: str) -> str: ...

    @overload
    def clean(self, value: dict[str, object]) -> dict[str, object]: ...

    @overload
    def clean(self, value: list[object]) -> list[object]: ...

    @overload
    def clean(self, value: object) -> object: ...

    def clean(self, value: object) -> object:
        if isinstance(value, Mapping):
            return {
                str(key): REDACTED if _is_sensitive_key(key) else self.clean(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self.clean(item) for item in value]
        if isinstance(value, tuple):
            return [self.clean(item) for item in value]
        if isinstance(value, str):
            return _redact_string(value)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        return _redact_string(str(value))
