#!/bin/sh
# Container entrypoint: migrate, then serve.
#
# Render's free tier has no pre-deploy hook and no one-off jobs, so the
# migration has to run somewhere in the normal startup path. Doing it here
# means a deploy that changes the schema cannot serve traffic against the old
# one — the alternative is remembering to run alembic by hand before every
# deploy, which works right up until the once it doesn't.
#
# Safe on a single instance, which is what the free tier gives you. If this
# ever scales past one, move migrations to a real pre-deploy step: concurrent
# `alembic upgrade head` runs race on the version table.
set -e

if [ "${RUN_MIGRATIONS_ON_START:-true}" = "true" ]; then
  echo "==> alembic upgrade head"
  # Not guarded by `|| true`: booting an app against a schema it does not
  # match produces confusing 500s at runtime. Failing here is louder and the
  # platform's own restart loop makes it visible.
  alembic upgrade head
  echo "==> migrations applied"
else
  echo "==> RUN_MIGRATIONS_ON_START is not 'true', skipping migrations"
fi

# exec so uvicorn becomes PID 1 and receives SIGTERM directly. Without it this
# shell holds PID 1, swallows the signal, and the platform's graceful-shutdown
# window expires into a SIGKILL mid-request.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
