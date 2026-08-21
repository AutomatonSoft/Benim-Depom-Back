from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import IdempotencyRecord


@shared_task(
    name="apps.idempotency.tasks.purge_expired_idempotency_records",
)
def purge_expired_idempotency_records() -> dict[str, int]:
    """
    Deletes one bounded batch of expired idempotency responses.

    The limit keeps database locks short even if this table grows.
    """
    now = timezone.now()

    record_ids = list(
        IdempotencyRecord.objects.filter(expires_at__lte=now)
        .order_by("id")
        .values_list("id", flat=True)[
            : settings.IDEMPOTENCY_CLEANUP_BATCH_SIZE
        ]
    )

    if not record_ids:
        return {"deleted": 0}

    deleted, _ = IdempotencyRecord.objects.filter(
        id__in=record_ids,
        expires_at__lte=now,
    ).delete()

    return {"deleted": deleted}