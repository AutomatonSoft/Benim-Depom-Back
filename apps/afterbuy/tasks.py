from celery import shared_task

from .reconcile import reconcile_afterbuy_stock
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


@shared_task(
    name="apps.afterbuy.tasks.reconcile_afterbuy_stock",
    autoretry_for=(OSError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=2,
)
def reconcile_afterbuy_stock_task() -> dict:
    return reconcile_afterbuy_stock()
