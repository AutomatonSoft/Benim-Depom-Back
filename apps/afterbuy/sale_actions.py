from __future__ import annotations

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.notifications.copy import render_notification_copy

from apps.notifications.models import Notification
from apps.notifications.services import (
    create_notification,
    notification_language,
    product_display_name,
)
from apps.orchestrator.job_services import (
    MarketplacePayloadBuildError,
    create_marketplace_job,
)
from apps.orchestrator.models import MarketplaceJob, MarketplacePublication

from .channel_qty import fetch_selling_channel_quantity, product_listing_ean
from .models import AfterbuyOrderItem
from .notify import _format_sold_at
from .parser import TRACKED_MARKETPLACES


def apply_canonical_qty_to_product(product, quantity: int) -> None:
    quantity = max(0, int(quantity))
    variants = list(product.variants.all())
    if not variants:
        return
    if len(variants) == 1:
        variant = variants[0]
        if variant.quantity != quantity:
            variant.quantity = quantity
            variant.save(update_fields=["quantity"])
        return
    total = sum(variant.quantity for variant in variants)
    assigned = 0
    for index, variant in enumerate(variants):
        if index == len(variants) - 1:
            new_qty = quantity - assigned
        elif total == 0:
            new_qty = quantity if index == 0 else 0
        else:
            new_qty = round(quantity * variant.quantity / total)
            assigned += new_qty
        new_qty = max(0, new_qty)
        if variant.quantity != new_qty:
            variant.quantity = new_qty
            variant.save(update_fields=["quantity"])


def _order_item_from_notification(notification: Notification) -> AfterbuyOrderItem:
    order_item = notification.afterbuy_order_item
    if order_item is None:
        raise ValidationError(
            {"detail": "This message is not linked to an Afterbuy order."}
        )
    if order_item.matched_product_id is None:
        raise ValidationError({"detail": "Afterbuy line is not matched to a product."})
    if order_item.marketplace not in TRACKED_MARKETPLACES:
        raise ValidationError(
            {"detail": "Stock sync is only available for OTTO, Hood and Kaufland."}
        )
    return order_item


def sibling_update_targets(*, product, sold_marketplace: str, sold_account: str):
    targets = []
    for publication in MarketplacePublication.objects.filter(
        product=product,
        status=MarketplacePublication.Status.ACTIVE,
    ).order_by("marketplace", "account"):
        if (
            publication.marketplace == sold_marketplace
            and publication.account == sold_account
        ):
            continue
        targets.append(
            {
                "marketplace": publication.marketplace,
                "account": publication.account,
            }
        )
    return targets


@transaction.atomic
def sync_stock_from_afterbuy_sale(*, notification: Notification, requested_by):
    order_item = _order_item_from_notification(notification)
    product = order_item.matched_product
    account = order_item.order.account
    ean = product_listing_ean(product=product, account=account)
    if not ean:
        raise ValidationError(
            {"detail": "Product has no EAN for the selling Afterbuy account."}
        )

    live = fetch_selling_channel_quantity(
        marketplace=order_item.marketplace,
        ean=ean,
        account=account,
        request_id=f"afterbuy-manager-sync-{notification.pk}",
    )
    if live is None:
        raise ValidationError(
            {
                "detail": (
                    "Could not read quantity from the selling marketplace. "
                    "Try again after the listing API is available."
                )
            }
        )

    apply_canonical_qty_to_product(product, live)
    targets = sibling_update_targets(
        product=product,
        sold_marketplace=order_item.marketplace,
        sold_account=account,
    )
    job = None
    if targets:
        try:
            job = create_marketplace_job(
                product=product,
                requested_by=requested_by,
                operation=MarketplaceJob.Operation.UPDATE,
                targets=targets,
            )
        except MarketplacePayloadBuildError as exc:
            raise ValidationError(exc.data) from exc

    sale = getattr(order_item, "sale_notification", None)
    if sale is not None:
        sale.stock_synced_at = timezone.now()
        sale.save(update_fields=["stock_synced_at", "updated_at"])

    return job, live, targets


def notify_seller_of_afterbuy_sale(*, notification: Notification) -> Notification:
    order_item = _order_item_from_notification(notification)
    product = order_item.matched_product
    sale = getattr(order_item, "sale_notification", None)
    if sale is not None and sale.seller_notified_at is not None:
        existing = (
            Notification.objects.filter(
                user=product.owner,
                product=product,
                notification_type=Notification.Type.PRODUCT_SOLD,
                afterbuy_order_item=order_item,
            )
            .exclude(pk=notification.pk)
            .order_by("-id")
            .first()
        )
        if existing is not None:
            return existing

    seller = product.owner
    language = notification_language(seller)
    title, body = render_notification_copy(
        key="product_sold_seller",
        language=language,
        name=product_display_name(product, language=language),
        sold_at=_format_sold_at(order_item),
        qty_sold=str(order_item.quantity),
    )
    seller_note = create_notification(
        user=seller,
        sender=notification.user,
        product=product,
        notification_type=Notification.Type.PRODUCT_SOLD,
        title=title,
        body=body,
        afterbuy_order_item=order_item,
    )
    if sale is not None:
        sale.seller_notified_at = timezone.now()
        sale.save(update_fields=["seller_notified_at", "updated_at"])
    return seller_note
