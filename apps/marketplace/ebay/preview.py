from .client import api_base_url
from .description import render_listing_description
from .drafts import EbayDraftSerializer
from .regulatory import EbayRegulatorySerializer, missing_regulatory_fields


def draft_ean(draft):
    """Читает существующий JV EAN; новые номера не выделяет и товар не изменяет."""
    value = draft.product.ean_jv
    if (
        draft.account not in {"jv", "depotum"}
        or not value.isascii()
        or not value.isdigit()
    ):
        return ""
    if len(value) not in (8, 13):
        return ""
    checksum = sum(
        int(digit) * (3 if index % 2 == 0 else 1)
        for index, digit in enumerate(reversed(value[:-1]))
    )
    return value if (checksum + int(value[-1])) % 10 == 0 else ""


def build_draft_preview(draft):
    """Собирает два JSON из сохранённого черновика, не вызывая API и не сохраняя данные."""
    base_url = api_base_url()
    missing = EbayDraftSerializer(draft).data["missing_requirements"]
    sku = draft_ean(draft)
    if not sku:
        missing["fields"].append("ean_jv")
    inventory_item = {
        "condition": "NEW",
        "product": {
            key: value
            for key, value in {
                "title": draft.title,
                "description": draft.description,
                "imageUrls": draft.image_urls,
                "aspects": draft.aspects,
                "ean": [sku] if sku else [],
            }.items()
            if value
        },
    }
    offer = {
        "sku": sku,
        "marketplaceId": "EBAY_DE",
        "format": "FIXED_PRICE",
        "listingDuration": "GTC",
        "includeCatalogProductDetails": False,
        "tax": {"vatPercentage": 19.0},
        "listingPolicies": {
            key: value
            for key, value in {
                "fulfillmentPolicyId": draft.fulfillment_policy_id,
                "paymentPolicyId": draft.payment_policy_id,
                "returnPolicyId": draft.return_policy_id,
            }.items()
            if value
        },
    }
    if draft.regulatory:
        regulatory = EbayRegulatorySerializer(data=draft.regulatory)
        regulatory.is_valid(raise_exception=True)
        offer["regulatory"] = regulatory.validated_data
    for key, value in {
        "categoryId": draft.category_id,
        "merchantLocationKey": draft.merchant_location_key,
        "listingDescription": render_listing_description(draft, ean=sku)
        if draft.description
        else "",
    }.items():
        if value:
            offer[key] = value
    if draft.quantity is not None:
        inventory_item["availability"] = {
            "shipToLocationAvailability": {"quantity": draft.quantity}
        }
        offer["availableQuantity"] = draft.quantity
    if draft.price_eur is not None:
        offer["pricingSummary"] = {
            "price": {"value": format(draft.price_eur, ".2f"), "currency": "EUR"}
        }
    headers = {"Content-Type": "application/json", "Content-Language": "de-DE"}
    return {
        "preview_only": True,
        "product": draft.product_id,
        "account": draft.account,
        "environment": draft.environment,
        "marketplace_id": "EBAY_DE",
        "sku": sku,
        "ean": sku,
        "missing_regulatory_fields": missing_regulatory_fields(draft.regulatory),
        "draft_complete": not any(missing.values()),
        "missing_requirements": missing,
        "checks_not_performed": [
            "current_category_and_condition_rules",
            "current_seller_policies_and_stock_location",
            "image_availability",
            "gpsr_and_other_regulatory_requirements",
        ],
        "inventory_item": {
            "method": "PUT",
            "url": f"{base_url}/sell/inventory/v1/inventory_item/{sku}"
            if sku
            else None,
            "headers": headers,
            "body": inventory_item,
        },
        "offer": {
            "method": "POST",
            "url": f"{base_url}/sell/inventory/v1/offer",
            "headers": headers,
            "body": offer,
        },
    }
