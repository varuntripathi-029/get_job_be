"""Celery application and Beat schedule.

Phase 1 left this package empty, so the app is defined here. Crawl and scoring
tasks are registered in `include` ahead of their modules existing — Celery only
imports what is present, and adding those files later needs no change here.
"""

import ssl

from celery import Celery
from celery.schedules import crontab

from app.config import settings

_IS_TLS = settings.redis_url.startswith("rediss://")

# Upstash terminates TLS with a public CA certificate, so normal verification
# works. kombu still needs to be told explicitly — without ssl_cert_reqs it
# raises "A rediss:// URL must have parameter ssl_cert_reqs" and refuses to
# start.
_SSL = {"ssl_cert_reqs": ssl.CERT_REQUIRED} if _IS_TLS else None

celery_app = Celery(
    "hiresignal",
    broker=settings.redis_url,
    # No result backend. Upstash bills per command and storing a result costs a
    # SET plus the reads that follow; nothing here awaits a return value.
    include=[
        "workers.crawl",
        "workers.jobs_sync",
        "workers.score",
        "workers.cleanup",
        "workers.embeddings",
        "workers.newsletter",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Tasks here make network calls with their own retries; a worker crash
    # should re-run the task rather than silently drop it.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Guard against a hung provider call wedging a worker slot forever.
    task_soft_time_limit=600,
    task_time_limit=900,
    # --- Upstash command budget (free tier: 10,000/day) ---------------------
    # Celery is by far the largest consumer of Redis commands here, and every
    # one of these settings removes a recurring source of chatter.
    task_ignore_result=True,  # no result writes or reads
    worker_send_task_events=False,  # no task-event stream
    task_send_sent_event=False,
    worker_enable_remote_control=False,  # no control/reply pubsub channels
    broker_heartbeat=0,  # Upstash does not drop idle connections
    broker_pool_limit=1,
    beat_max_loop_interval=300,
    broker_connection_retry_on_startup=True,
    broker_transport_options={
        "visibility_timeout": 3600,
        # How long BRPOP blocks per poll. This is the single biggest lever on
        # command spend: an idle worker issues one BRPOP per interval per
        # queue, so 1s costs ~86k commands/day and 20s costs ~4.3k.
        "socket_timeout": 30,
        "socket_keepalive": True,
    },
    broker_use_ssl=_SSL,
    redis_backend_use_ssl=_SSL,
)

celery_app.conf.beat_schedule = {
    "tick-scheduler": {
        "task": "workers.crawl.tick_scheduler",
        # Every 5 minutes, not every 60s. Crawl frequencies run 360-4320
        # minutes, so 5-minute resolution costs nothing in freshness and cuts
        # Beat's share of the Upstash budget from ~1440 to ~288 commands/day.
        "schedule": 300.0,
    },
    "sync-all-ats-jobs": {
        "task": "workers.jobs_sync.sync_all_jobs",
        "schedule": crontab(minute=0, hour="*/12"),
    },
    "cleanup-crawl-logs": {
        "task": "workers.cleanup.cleanup_old_crawl_logs",
        "schedule": crontab(hour=3, minute=0),
    },
    "generate-job-embeddings": {
        "task": "workers.embeddings.generate_job_embeddings",
        "schedule": crontab(minute=0, hour="*/6"),
    },
    "send-weekly-newsletter": {
        "task": "workers.newsletter.generate_and_send_newsletter",
        # Monday 09:00 UTC.
        "schedule": crontab(day_of_week=1, hour=9, minute=0),
    },
}
