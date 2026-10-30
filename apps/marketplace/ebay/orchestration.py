"""Use the existing eBay lifecycle from the common marketplace queue."""

from django.conf import settings
from django.utils import timezone
from rest_framework.exceptions import APIException, ValidationError

from apps.orchestrator.models import MarketplacePublication

from .models import EbayDraft, EbayOfferPreparation


def sync_publication(preparation):
    if preparation.account != "depotum" or not preparation.offer_id:
        return
    statuses = {"published": "active", "withdrawn": "deactivated", "failed": "failed"}
    state = statuses.get(preparation.status)
    if not state:
        return
    publication, _ = MarketplacePublication.objects.get_or_create(
        product_id=preparation.draft.product_id,
        marketplace="ebay",
        account="depotum",
        environment=preparation.environment,
        defaults={"ean": preparation.sku},
    )
    publication.status = state
    publication.external_id = preparation.listing_id
    publication.external_reference = preparation.offer_id
    publication.last_error = (
        {"detail": preparation.last_error} if preparation.last_error else {}
    )
    if state == "active":
        publication.published_at = publication.published_at or timezone.now()
    if state == "deactivated":
        publication.deactivated_at = timezone.now()
    publication.save(
        update_fields=(
            "status",
            "external_id",
            "external_reference",
            "last_error",
            "published_at",
            "deactivated_at",
            "updated_at",
        )
    )


def execute(*, product, operation):
    from .drafts import EbayDraftSerializer
    from .listing import change_listing, get_listing_snapshot
    from .preparation import PreparationConflict, PreparationSerializer, prepare_offer
    from .publishing import _publication_checks, publish_offer
    from .updating import update_unpublished_offer

    try:
        draft = EbayDraft.objects.get(
            product=product,
            account="depotum",
            environment=settings.EBAY_ENVIRONMENT,
        )
        preparation = EbayOfferPreparation.objects.filter(draft=draft).first()
        if preparation and preparation.status in {"processing", "uncertain"}:
            raise PreparationConflict("Previous eBay request requires reconciliation.")
        if operation == "deactivate":
            preparation = change_listing(product.pk, withdraw=True)
        else:
            snapshot = (
                get_listing_snapshot(product.pk)
                if preparation and preparation.offer_id
                else None
            )
            active = bool(
                snapshot
                and snapshot["remote_status"] == "PUBLISHED"
                and snapshot["listing_status"] in (None, "ACTIVE")
                and snapshot["remote_listing_id"]
            )
            if operation == "search":
                return {
                    "ok": True,
                    "status_code": 200,
                    "details": {
                        "listingId": snapshot["remote_listing_id"] if snapshot else "",
                        "status": "active" if active else "deactivated",
                    },
                }
            if operation == "update" and not active:
                raise PreparationConflict("eBay has no active listing to update.")
            if active and operation != "update":
                from .preparation import _save_state

                _save_state(
                    preparation,
                    status="published",
                    listing_id=snapshot["remote_listing_id"],
                    last_error="",
                )
            if operation == "update" or not active:
                # Refresh card facts at dispatch time, including stock changed since form save.
                serializer = EbayDraftSerializer(
                    draft,
                    data={"sync_product": True},
                    partial=True,
                    context={"product": product, "account": "depotum"},
                )
                serializer.is_valid(raise_exception=True)
                serializer.save()
                if operation == "update":
                    preparation = change_listing(product.pk)
                else:
                    prepare = (
                        update_unpublished_offer
                        if preparation and preparation.offer_id
                        else prepare_offer
                    )
                    preparation = prepare(product.pk, account="depotum")
                    checks = _publication_checks(preparation)
                    if not checks["ready"]:
                        raise ValidationError(
                            {"blocking_reasons": checks["blocking_reasons"]}
                        )
                    preparation = publish_offer(
                        product.pk, checks["preview_hash"], account="depotum"
                    )
        sync_publication(preparation)
        return {
            "ok": True,
            "status_code": 200,
            "details": {
                **PreparationSerializer(preparation).data,
                "listingId": preparation.listing_id,
            },
        }
    except EbayDraft.DoesNotExist:
        return {
            "ok": False,
            "status_code": 400,
            "details": {"detail": "Save the eBay draft first."},
        }
    except APIException as error:
        return {
            "ok": False,
            "status_code": error.status_code,
            "details": {"detail": error.detail},
        }
