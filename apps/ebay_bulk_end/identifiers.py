from datetime import timedelta

from django.db.models import Exists, OuterRef
from django.utils import timezone

from .domain import child, item_identifiers, listing_status, text
from .ebay import get_item
from .errors import BulkEndError
from .models import EbayListingIdentifiers
from .storage import save_job

IDENTIFIERS_PER_TASK = 10
IDENTIFIER_TTL = timedelta(hours=24)


def identifier_index(job):
    return EbayListingIdentifiers.objects.filter(
        environment=job["environment"], seller_id=job["seller_id"]
    )


def index_snapshot_identifiers(snapshot, job, token, seller_username):
    cutoff = job.setdefault(
        "identifier_cutoff", (timezone.now() - IDENTIFIER_TTL).isoformat()
    )
    index = identifier_index(job)
    fresh = index.filter(item_id=OuterRef("item_id"), checked_at__gte=cutoff)
    pending = (
        snapshot.listings.annotate(identifiers_ready=Exists(fresh))
        .filter(identifiers_ready=False)
        .order_by("item_id")
        .values_list("item_id", flat=True)
        .distinct()
    )
    batch = list(pending[:IDENTIFIERS_PER_TASK])
    if not batch:
        return True
    for item_id in batch:
        item = get_item(token, item_id)
        values = []
        if item is not None:
            if text(item, "ItemID") != item_id:
                raise BulkEndError(
                    "eBay returned another ItemID during identifier indexing."
                )
            actual_seller = text(child(item, "Seller"), "UserID")
            if (
                not actual_seller
                or actual_seller.casefold() != seller_username.casefold()
            ):
                raise BulkEndError(
                    "The listing owner could not be verified during identifier indexing."
                )
            if listing_status(item).casefold() == "active":
                values = item_identifiers(item)
        index.update_or_create(
            item_id=item_id,
            defaults={
                "environment": job["environment"],
                "seller_id": job["seller_id"],
                "identifiers": values,
                "checked_at": timezone.now(),
            },
        )
    total = snapshot.listings.order_by().values("item_id").distinct().count()
    remaining = pending.count()
    job["progress"] = {
        "stage": "indexing_identifiers",
        "current": total - remaining,
        "total": total,
    }
    job["message"] = "Проверяем EAN и Herstellernummer активных объявлений eBay."
    save_job(job)
    # Candidate verification gets a separate task to stay below Celery's time limit.
    return False
