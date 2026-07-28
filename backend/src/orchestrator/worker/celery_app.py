"""Celery application.

Broker on Redis /0, results on /1 (documented DB map). Run the worker with
--pool=solo: our tasks wrap an asyncio event loop (asyncio.run inside the
task), and prefork's forked children inherit state that async clients react
badly to. Solo pool = one loop, one process, no forking surprises; scale by
running more worker processes, not prefork children.

    make worker
"""

from celery import Celery

from orchestrator.config import get_settings, redis_url_with_db

settings = get_settings()

celery_app = Celery(
    "orchestrator",
    broker=redis_url_with_db(settings.redis_url, 0),
    backend=redis_url_with_db(settings.redis_url, 1),
    include=["orchestrator.worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # a lost worker should not silently eat a task — requeue it
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # one graph run is minutes, not milliseconds; don't prefetch a queue
    worker_prefetch_multiplier=1,
)
