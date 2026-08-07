"""Translate a libpq-style connection string into asyncpg connect arguments.

Hosted Postgres providers hand out libpq URLs. Neon's, copied from its
dashboard, looks like:

    postgresql://user:pw@ep-x-123.region.aws.neon.tech/neondb?sslmode=require&channel_binding=require

`sslmode` and `channel_binding` are libpq parameters. asyncpg accepts neither —
its `connect()` has no `**kwargs`, so SQLAlchemy passing them through raises
`TypeError: connect() got an unexpected keyword argument 'sslmode'` on the first
connection. Pasting a provider URL in unmodified therefore fails, and the error
names the keyword rather than the cause.

This module strips those parameters and expresses the same intent in asyncpg's
own vocabulary, so `DATABASE_URL` can hold exactly what the provider printed.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.engine.url import URL, make_url

# libpq spellings asyncpg does not accept, mapped to their asyncpg equivalent.
# A value of None means "drop it": the behaviour is already asyncpg's default.
_LIBPQ_TRANSLATIONS: dict[str, str | None] = {
    "sslmode": "ssl",
    # asyncpg negotiates SCRAM channel binding automatically when the server
    # asks for it, so the explicit request carries no extra information.
    "channel_binding": None,
    "target_session_attrs": None,
    "connect_timeout": "timeout",
    "application_name": None,
}

# asyncpg's ssl= accepts the same vocabulary as libpq's sslmode.
_SSL_MODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")

# Neon, Supabase and RDS Proxy all put a marker in the pooled hostname.
_POOLER_MARKERS = ("-pooler.", "pgbouncer")


def is_pooled_host(host: str | None) -> bool:
    """Whether the host looks like a transaction-mode connection pooler."""
    return bool(host) and any(marker in host.lower() for marker in _POOLER_MARKERS)


def normalize(database_url: str) -> tuple[str, dict[str, Any]]:
    """Return `(url_without_libpq_params, connect_args)`.

    The URL keeps its driver prefix; only the query string is rewritten.
    """
    url: URL = make_url(database_url)

    # Anything other than asyncpg (psycopg, plain postgresql://) speaks libpq
    # natively and must be left alone.
    if url.drivername != "postgresql+asyncpg":
        return database_url, {}

    connect_args: dict[str, Any] = {}
    remaining: dict[str, Any] = {}

    for key, value in url.query.items():
        if key not in _LIBPQ_TRANSLATIONS:
            remaining[key] = value
            continue

        target = _LIBPQ_TRANSLATIONS[key]
        if target is None:
            continue

        if target == "ssl":
            mode = str(value).lower()
            # Deliberately written back into the URL query rather than into
            # connect_args. Alembic builds its own engine from `sqlalchemy.url`
            # alone via async_engine_from_config and never sees connect_args, so
            # an ssl= living there would silently produce an unencrypted
            # migration connection — which Neon rejects outright with
            # "connection is insecure". In the URL it survives both paths.
            # An unrecognised mode is left for asyncpg to reject with its own
            # message rather than silently reinterpreted here.
            remaining["ssl"] = mode if mode in _SSL_MODES else value
        elif target == "timeout":
            connect_args["timeout"] = float(value)
        else:
            connect_args[target] = value

    if is_pooled_host(url.host):
        # Transaction-mode poolers hand each transaction a different backend, so
        # a prepared statement cached against one connection is not valid on the
        # next. Both caches have to go: asyncpg's own (a connect argument) and
        # SQLAlchemy's on top of it (a dialect argument, which travels in the
        # URL query — it is not accepted by create_async_engine).
        # Without this, queries fail intermittently with
        # "prepared statement ... does not exist".
        connect_args["statement_cache_size"] = 0
        remaining["prepared_statement_cache_size"] = "0"

    # render_as_string(hide_password=False), never str(url): SQLAlchemy's
    # __str__ masks the password as "***", which produces a URL that looks
    # correct in logs and fails authentication at connect time.
    return url.set(query=remaining).render_as_string(hide_password=False), connect_args
