from celery import shared_task

from .sync import sync_afterbuy_sales


@shared_task(
    name="apps.afterbuy.tasks.sync_afterbuy_sales",
    autoretry_for=(OSError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=2,
)
def sync_afterbuy_sales_task() -> dict:
    return sync_afterbuy_sales()
