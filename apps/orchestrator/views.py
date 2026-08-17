from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status, generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsManager, is_manager
from apps.products.models import Product

from .models import (
    MarketplaceJob,
    MarketplaceListingConfiguration,
    MarketplacePublication,
)
from .serializers import (
    MarketplaceJobRequestSerializer,
    MarketplaceJobSerializer,
    MarketplacePublicationSerializer,
    MarketplacePublicationFilterSerializer,
    OttoListingConfigurationResponseSerializer,
    OttoListingConfigurationSerializer,
)
from .tasks import execute_marketplace_job
from apps.marketplace.otto.payload_builder import (
    OttoPayloadValidationError,
    build_otto_payload,
)


def resolve_requested_targets(data) -> list[dict[str, str]]:
    """
    Supports both the new target format and the legacy channels/accounts format.
    """
    targets = data.get("targets", [])

    if targets:
        return targets

    accounts = data.get("accounts", {})
    resolved_targets = []

    for marketplace in data["channels"]:
        account = accounts.get(marketplace)

        if account:
            resolved_targets.append(
                {
                    "marketplace": marketplace,
                    "account": account,
                }
            )
            continue

        resolved_targets.extend(
            [
                {"marketplace": marketplace, "account": "jv"},
                {"marketplace": marketplace, "account": "xl"},
            ]
        )

    return resolved_targets

class ProductMarketplaceJobCreateView(APIView):
    permission_classes = (IsAuthenticated, IsManager)

    @extend_schema(request=MarketplaceJobRequestSerializer, responses={202: MarketplaceJobSerializer})
    def post(self, request, product_pk: int, operation: str):
        if operation not in MarketplaceJob.Operation.values:
            return Response(
                {
                    "detail": (
                        "Operation must be search, publish, update, activate, "
                        "delete, or deactivate."
                    )
                },
                status=status.HTTP_404_NOT_FOUND,
            )
        
        product = get_object_or_404(
            Product.objects.prefetch_related("variants"),
            pk=product_pk,
        )
        if not is_manager(request.user) and product.owner_id != request.user.id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        
        serializer = MarketplaceJobRequestSerializer(data=request.data, context={"operation": operation})
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        payloads = dict(data.get("payloads", {}))
        target_payloads = {}

        if operation in {
            MarketplaceJob.Operation.PUBLISH,
            MarketplaceJob.Operation.UPDATE,
        }:
            for target in resolve_requested_targets(data):
                if target["marketplace"] != "otto":
                    continue

                configuration = MarketplaceListingConfiguration.objects.filter(
                    product=product,
                    marketplace=MarketplacePublication.Marketplace.OTTO,
                    account=target["account"],
                ).first()

                try:
                    target_payloads[
                        f"{target['marketplace']}:{target['account']}"
                    ] = build_otto_payload(
                        product=product,
                        account=target["account"],
                        configuration=(
                            configuration.configuration
                            if configuration is not None
                            else {}
                        ),
                    )
                except OttoPayloadValidationError as exc:
                    return Response(
                        {
                            "detail": (
                                "OTTO listing is not ready for publication. "
                                "Fix the manager configuration first."
                            ),
                            "target": target,
                            "errors": exc.errors,
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
        with transaction.atomic():
            job = MarketplaceJob.objects.create(
                product=product,
                requested_by=request.user,
                operation=operation,
                requested_channels=data["channels"],
                request_payload={
                    "payloads": payloads,
                    "target_payloads": target_payloads,
                    "accounts": data.get("accounts", {}),
                    "targets": data.get("targets", []),
                },
            )
            transaction.on_commit(lambda: execute_marketplace_job.delay(str(job.id)))

        return Response(MarketplaceJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class MarketplaceJobDetailView(APIView):
    permission_classes = (IsAuthenticated,)

    @extend_schema(responses={200: MarketplaceJobSerializer})
    def get(self, request, job_id):
        job = get_object_or_404(MarketplaceJob.objects.select_related("product"), pk=job_id)
        if not is_manager(request.user) and job.product.owner_id != request.user.id:
            return Response(status=status.HTTP_403_FORBIDDEN)
        
        return Response(MarketplaceJobSerializer(job).data)


class ProductMarketplacePublicationListView(APIView):
    """
    Статусы всех публикаций товара для менеджерской web-панели.
    """

    permission_classes = (IsAuthenticated, IsManager)

    @extend_schema(
        responses={200: MarketplacePublicationSerializer(many=True)},
        description=(
            "Returns publication states for every marketplace/account "
            "pair of a product."
        ),
    )
    def get(self, request, product_pk: int):
        product = get_object_or_404(Product, pk=product_pk)

        publications = (
            MarketplacePublication.objects
            .filter(product=product)
            .select_related("last_job")
            .order_by("marketplace", "account")
        )

        return Response(
            MarketplacePublicationSerializer(
                publications,
                many=True,
            ).data
        )


class MarketplacePublicationListView(generics.ListAPIView):
    """
    Общий пагинируемый список публикаций для менеджерской панели.
    """

    permission_classes = (IsAuthenticated, IsManager)
    serializer_class = MarketplacePublicationSerializer

    @extend_schema(
        parameters=[MarketplacePublicationFilterSerializer],
        responses={200: MarketplacePublicationSerializer(many=True)},
        description=(
            "Manager list of marketplace publications. "
            "Supports filtering by marketplace, account, status and product."
        ),
    )
    def get(self, request, *args, **kwargs):
        self.filters = MarketplacePublicationFilterSerializer(
            data=request.query_params,
        )
        self.filters.is_valid(raise_exception=True)
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        filters = self.filters.validated_data

        queryset = MarketplacePublication.objects.select_related(
            "product",
            "product__owner",
            "last_job",
        ).order_by("-updated_at")

        for field in ("marketplace", "account", "status", "product_id"):
            value = filters.get(field)

            if value is not None:
                queryset = queryset.filter(**{field: value})

        return queryset


class ProductOttoListingConfigurationView(APIView):
    """Manager configuration for one future OTTO listing/account pair."""

    permission_classes = (IsAuthenticated, IsManager)

    def _get_configuration(self, product_pk: int, account: str):
        if account not in MarketplacePublication.Account.values:
            return None

        product = get_object_or_404(Product, pk=product_pk)
        configuration, _ = MarketplaceListingConfiguration.objects.get_or_create(
            product=product,
            marketplace=MarketplacePublication.Marketplace.OTTO,
            account=account,
            defaults={"configuration": {}},
        )
        return configuration

    @extend_schema(
        responses={200: OttoListingConfigurationResponseSerializer},
        description=(
            "Returns manager-editable OTTO content for one account. This can "
            "be prepared before the product is approved or assigned an EAN."
        ),
    )
    def get(self, request, product_pk: int, account: str):
        configuration = self._get_configuration(product_pk, account)
        if configuration is None:
            return Response(
                {"detail": "Account must be 'jv' or 'xl'."},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(OttoListingConfigurationResponseSerializer(configuration).data)

    @extend_schema(
        request=OttoListingConfigurationSerializer,
        responses={200: OttoListingConfigurationResponseSerializer},
        description=(
            "Partially updates manager-owned OTTO content. The final payload "
            "is checked separately by the OTTO preview endpoint."
        ),
    )
    def patch(self, request, product_pk: int, account: str):
        configuration = self._get_configuration(product_pk, account)
        if configuration is None:
            return Response(
                {"detail": "Account must be 'jv' or 'xl'."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = OttoListingConfigurationSerializer(
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)

        updated_configuration = dict(configuration.configuration or {})
        updated_configuration.update(serializer.to_storage(serializer.validated_data))
        configuration.configuration = updated_configuration
        configuration.save(update_fields=("configuration", "updated_at"))

        return Response(OttoListingConfigurationResponseSerializer(configuration).data)


class ProductOttoPayloadPreviewView(APIView):
    """Builds but never sends the final OTTO request payload."""

    permission_classes = (IsAuthenticated, IsManager)

    @extend_schema(
        responses={
            200: dict,
            400: dict,
        },
        description=(
            "Validates and returns the OTTO create/update payload without "
            "making an external marketplace request. MSRP/UVP is calculated "
            "from the configured selling price."
        ),
    )
    def get(self, request, product_pk: int, account: str):
        if account not in MarketplacePublication.Account.values:
            return Response(
                {"detail": "Account must be 'jv' or 'xl'."},
                status=status.HTTP_404_NOT_FOUND,
            )

        product = get_object_or_404(
            Product.objects.prefetch_related("variants"),
            pk=product_pk,
        )
        configuration = MarketplaceListingConfiguration.objects.filter(
            product=product,
            marketplace=MarketplacePublication.Marketplace.OTTO,
            account=account,
        ).first()

        try:
            payload = build_otto_payload(
                product=product,
                account=account,
                configuration=(configuration.configuration if configuration else {}),
            )
        except OttoPayloadValidationError as exc:
            return Response(
                {
                    "detail": "OTTO listing is not ready for publication.",
                    "errors": exc.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"payload": payload})
