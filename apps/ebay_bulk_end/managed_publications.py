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
    return set(records) & requested
