"""Database / Neon security helpers — redaction, TLS policy, target safety.

Never print or return full DATABASE_URL with credentials.
"""
from __future__ import annotations

import os
import re
import sys
from typing import Optional
from urllib.parse import parse_qs, urlparse, urlunparse


# Common secret-bearing substrings (passwords, tokens) found in exception text.
_SECRET_PATTERNS = (
    re.compile(r"(postgresql(?:\+\w+)?://[^:\s/]+:)([^@\s]+)(@)", re.I),
    re.compile(r"(mysql(?:\+\w+)?://[^:\s/]+:)([^@\s]+)(@)", re.I),
    re.compile(r"(mongodb(?:\+\w+)?://[^:\s/]+:)([^@\s]+)(@)", re.I),
    re.compile(r"(?i)(password|passwd|pwd|secret|api[_-]?key|token)\s*[=:]\s*([^\s,;]+)"),
    re.compile(r"(?i)(Bearer\s+)([A-Za-z0-9\-._~+/]+=*)"),
)


def redact_database_url(url: str | None) -> str:
    """Return a log-safe database URL: scheme://user:***@host/db(?sslmode=…)."""
    if not url:
        return "(empty)"
    try:
        parsed = urlparse(url.strip())
        if not parsed.scheme:
            return "(unparseable)"
        user = parsed.username or ""
        host = parsed.hostname or ("file" if parsed.scheme.startswith("sqlite") else "unknown")
        port = f":{parsed.port}" if parsed.port else ""
        path = parsed.path or ""
        # Keep only non-secret query keys (sslmode, etc.)
        qs = parse_qs(parsed.query, keep_blank_values=True)
        safe_q = []
        for key in ("sslmode", "channel_binding", "connect_timeout"):
            if key in qs and qs[key]:
                safe_q.append(f"{key}={qs[key][0]}")
        query = "&".join(safe_q)
        netloc = f"{user}:***@{host}{port}" if user else f"{host}{port}"
        return urlunparse((parsed.scheme, netloc, path, "", query, ""))
    except Exception:
        return "(unparseable)"


def redact_secrets(text: str | None) -> str:
    """Strip credentials / tokens from arbitrary error or log text."""
    if text is None:
        return ""
    out = str(text)
    for pat in _SECRET_PATTERNS:
        if pat.groups == 3 and "://" in pat.pattern:
            out = pat.sub(r"\1***\3", out)
        elif "Bearer" in pat.pattern:
            out = pat.sub(r"\1***", out)
        else:
            out = pat.sub(r"\1=***", out)
    # Also collapse any remaining user:pass@ patterns
    out = re.sub(r"(://[^:\s/]+:)([^@\s]+)(@)", r"\1***\3", out)
    return out


def postgres_sslmode(url: str | None) -> Optional[str]:
    if not url:
        return None
    try:
        qs = parse_qs(urlparse(url).query)
        vals = qs.get("sslmode") or []
        return (vals[0] or "").strip().lower() or None
    except Exception:
        return None


def is_postgres_url(url: str | None) -> bool:
    if not url:
        return False
    scheme = (urlparse(url).scheme or "").lower()
    return scheme.startswith("postgresql") or scheme.startswith("postgres")


def looks_like_managed_cloud_db(url: str | None) -> bool:
    """Heuristic: Neon / cloud Postgres hosts (not local)."""
    if not url:
        return False
    host = (urlparse(url).hostname or "").lower()
    return any(
        marker in host
        for marker in (
            "neon.tech",
            "amazonaws.com",
            "azure.com",
            "googleapis.com",
            "digitalocean.com",
            "supabase.co",  # not used by this project; still treat as managed
        )
    )


_SAFE_SSLMODES = frozenset({"require", "verify-ca", "verify-full"})


def assert_postgres_tls_policy(url: str | None, *, environment: str) -> None:
    """Production/staging Postgres must request TLS via sslmode."""
    if not is_postgres_url(url):
        return
    env = (environment or "").strip().lower()
    if env not in {"production", "prod", "staging"}:
        return
    mode = postgres_sslmode(url)
    if mode not in _SAFE_SSLMODES:
        raise RuntimeError(
            "Refusing to start: PostgreSQL DATABASE_URL in staging/production must include "
            "sslmode=require (or verify-ca / verify-full). "
            f"Observed sslmode={mode!r} target={redact_database_url(url)}"
        )


def assert_no_auto_create_in_managed(environment: str, auto_create: bool) -> None:
    """Staging/production must never silently create_all / ALTER."""
    env = (environment or "").strip().lower()
    if env in {"production", "prod", "staging"} and auto_create:
        raise RuntimeError(
            "Refusing to start: AUTO_CREATE_SCHEMA must be false in staging/production. "
            "Use Alembic migrations (`alembic upgrade head`) instead."
        )


def assert_safe_database_target(
    url: str | None,
    *,
    purpose: str,
    allow_managed: bool = False,
    environment: str | None = None,
) -> None:
    """Block accidental destructive/test work against managed cloud DBs.

    allow_managed=True for intentional Neon verify/diagnose scripts.
    """
    env = (environment or os.environ.get("ENVIRONMENT") or os.environ.get("ENV") or "").strip().lower()
    force = (os.environ.get("ALLOW_NEON_TESTS") or "").strip().lower() in {"1", "true", "yes"}
    if allow_managed:
        return
    # Pytest / explicit test env must not hit Neon unless ALLOW_NEON_TESTS=1.
    under_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST")) or any(
        "pytest" in (a or "") for a in sys.argv
    )
    if (under_pytest or env in {"test", "testing"}) and looks_like_managed_cloud_db(url) and not force:
        raise RuntimeError(
            f"Refusing {purpose}: test/pytest process would use managed cloud database "
            f"{redact_database_url(url)}. Use SQLite/test DB, or set ALLOW_NEON_TESTS=1 "
            "only for intentional Neon verification scripts."
        )


def public_exception_detail(exc: BaseException, *, is_production: bool) -> str:
    """Client-facing 500 detail — never include secrets or raw DB diagnostics in production."""
    if is_production:
        return "An internal error occurred."
    return f"An internal error occurred: {redact_secrets(str(exc))}"
