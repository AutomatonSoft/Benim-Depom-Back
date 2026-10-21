import logging

from celery import shared_task

logger = logging.getLogger(__name__)


def enqueue_email_task(task, **kwargs):
    try:
        # Bound Redis publishing too: SMTP runs only in the notifications worker.
        with task.app.connection_for_write(
            connect_timeout=1,
            transport_options={
                "max_retries": 0,
                "socket_connect_timeout": 1,
                "socket_timeout": 1,
                "retry_on_timeout": False,
            },
        ) as connection:
            task.apply_async(kwargs=kwargs, connection=connection, retry=False)
    except Exception:
        # shortcut: failed enqueue is logged only; use an outbox if delivery must be guaranteed.
        logger.exception("Could not enqueue email task %s", task.name)


@shared_task
def healthcheck_task():
    return {"status": "ok"}
