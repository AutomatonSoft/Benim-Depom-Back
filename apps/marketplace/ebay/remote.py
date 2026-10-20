from urllib.parse import quote

from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView
from urllib3.util.retry import Retry

from apps.common.marketplace_http import build_marketplace_session
from apps.common.permissions import IsManager

from .client import EbayAPIError, api_base_url, get_seller_resource
from .models import EbayOfferPreparation
from .services import get_access_token


class EbayRemoteSnapshotSerializer(serializers.Serializer):
    """Возвращает прочитанные ресурсы eBay и отдельный результат сверки описания."""

    product = serializers.IntegerField()
    account = serializers.CharField()
    environment = serializers.CharField()
    sku = serializers.CharField()
    offer_id = serializers.CharField()
    checked_at = serializers.DateTimeField()
    inventory_item = serializers.JSONField()
    offer = serializers.JSONField()
    description_matches = serializers.DictField(child=serializers.BooleanField())


class EbayDraftRemoteView(APIView):
    """Читает ресурсы по сохранённым SKU/offerId, не изменяя черновик и состояние подготовки."""

    permission_classes = (IsManager,)
    account = "jv"

    @extend_schema(
        responses={200: EbayRemoteSnapshotSerializer},
        tags=["eBay"],
        description="Reads the saved Inventory Item and Offer directly from eBay. Compares descriptions with the local draft. Does not publish, update, or reconcile preparation state.",
    )
    def get(self, request, product_id):
        """Читает ресурсы выбранного аккаунта; OAuth-токен обновляется штатно."""
        api_base_url()
        preparation = get_object_or_404(
            EbayOfferPreparation.objects.select_related("draft"),
            draft__product_id=product_id,
            draft__environment=settings.EBAY_ENVIRONMENT,
            draft__account=self.account,
            environment=settings.EBAY_ENVIRONMENT,
            account=self.account,
        )
        if not preparation.offer_id:
            raise serializers.ValidationError(
                "Preparation has no saved offerId to read."
            )
        token = get_access_token(account=preparation.account)
        with build_marketplace_session() as session:
            for adapter in session.adapters.values():
                adapter.max_retries = Retry(total=0)
            inventory = get_seller_resource(
                session=session,
                access_token=token,
                path=f"/sell/inventory/v1/inventory_item/{quote(preparation.sku, safe='')}",
            )
            offer = get_seller_resource(
                session=session,
                access_token=token,
                path=f"/sell/inventory/v1/offer/{quote(preparation.offer_id, safe='')}",
            )
        if (
            inventory.get("sku") != preparation.sku
            or offer.get("offerId") != preparation.offer_id
            or offer.get("sku") != preparation.sku
            or offer.get("marketplaceId") != "EBAY_DE"
        ):
            raise EbayAPIError(
                "eBay resources do not match the saved account preparation."
            )
        product = inventory.get("product")
        expected = preparation.draft.description
        result = {
            "product": product_id,
            "account": preparation.account,
            "environment": preparation.environment,
            "sku": preparation.sku,
            "offer_id": preparation.offer_id,
            "checked_at": timezone.now(),
            "inventory_item": inventory,
            "offer": offer,
            "description_matches": {
                "inventory_item": bool(expected)
                and isinstance(product, dict)
                and product.get("description") == expected,
                "offer": bool(expected) and offer.get("listingDescription") == expected,
            },
        }
        response = Response(EbayRemoteSnapshotSerializer(result).data)
        response["Cache-Control"] = "no-store"
        return response
