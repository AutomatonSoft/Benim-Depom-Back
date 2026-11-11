from apps.marketplace.ebay.models import EbayOfferPreparation
from apps.orchestrator.models import MarketplacePublication


def managed_depotum_item_ids(item_ids, environment):
    if environment not in {"sandbox", "production"}:
        raise ValueError("A valid eBay environment is required.")

    requested = {str(item_id) for item_id in item_ids}
    if not requested:
        return set()
    if any(not item_id.isdigit() for item_id in requested):
        raise ValueError("Valid eBay ItemIDs are required.")

    # Blank legacy environments are ambiguous, so matching records stay protected.
    records = MarketplacePublication.objects.filter(
        marketplace="ebay",
        account="depotum",
        environment__in=(environment, ""),
        external_id__in=requested,
    ).values_list("external_id", flat=True)
    managed = set(records) & requested
    preparations = EbayOfferPreparation.objects.filter(
        account="depotum",
        environment__in=(environment, ""),
    ).values_list("listing_id", "listing_id_history")
    for listing_id, history in preparations.iterator(chunk_size=500):
        if listing_id in requested:
            managed.add(listing_id)
        if isinstance(history, list):
            managed.update(
                str(item_id) for item_id in history if str(item_id) in requested
            )
    return managed
