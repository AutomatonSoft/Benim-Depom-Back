from celery import shared_task

from .media_uploads import cleanup_local_photos, recover_photo_uploads, transfer_photos


@shared_task(
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=540,
    time_limit=600,
    ignore_result=True,
)
def transfer_product_photos(product_id):
    """Transfer photos independently of moderation; beat recovers failures."""
    transfer_photos(product_id)


@shared_task(ignore_result=True)
def recover_product_photo_uploads():
    recover_photo_uploads()


@shared_task(ignore_result=True)
def cleanup_product_local_photos():
    return cleanup_local_photos()
