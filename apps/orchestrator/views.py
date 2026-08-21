from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import status, generics
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.external_json import compact_external_json
from apps.common.permissions import IsManager, is_manager
from apps.common.throttles import (
    AiGenerationRateThrottle,
    ManagerMutationThrottleMixin,
)
from apps.products.models import Product

from .models import (
    MarketplaceContentGeneration,
    MarketplaceJob,
    MarketplaceListingConfiguration,
    MarketplacePublication,
)
from .serializers import (
    MarketplaceJobRequestSerializer,
    MarketplaceListingStateRequestSerializer,
    MarketplaceJobSerializer,
    MarketplacePublicationSerializer,
    MarketplacePublicationFilterSerializer,
    OttoListingConfigurationResponseSerializer,
    OttoListingConfigurationSerializer,
    HoodListingConfigurationResponseSerializer,
    HoodListingConfigurationSerializer,
    KauflandListingConfigurationResponseSerializer,
    KauflandListingConfigurationSerializer,
    MarketplaceContentGenerationApplyRequestSerializer,
    MarketplaceContentGenerationRequestSerializer,
    MarketplaceContentGenerationSerializer,
)
from .listing_state_services import create_listing_state_jobs
from .tasks import (
    execute_marketplace_job,
    generate_marketplace_content,
)
from apps.marketplace.otto.payload_builder import (
    OttoPayloadValidationError,
    build_otto_payload,
)
from apps.marketplace.hood.payload_builder import (
    HoodPayloadValidationError,
    build_hood_payload,
)
from apps.marketplace.kaufland.payload_builder import (
    KauflandPayloadValidationError,
    build_kaufland_create_payload,
    build_kaufland_update_payload,
)
from .ai_content import (
    build_product_snapshot,
    universal_content_to_marketplace_configuration,
)
from apps.idempotency.services import (
    IdempotencyKeyReuseError,
    IdempotencyRequestInProgressError,
    abandon_idempotency_claim,
    claim_idempotency_key,
    complete_idempotency_claim,
)
from apps.products.views import IDEMPOTENCY_KEY_HEADER


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


class ProductMarketplaceJobCreateView(ManagerMutationThrottleMixin, APIView):
    permission_classes = (IsAuthenticated, IsManager)

    @extend_schema(
        request=MarketplaceJobRequestSerializer,
        responses={202: MarketplaceJobSerializer},
        parameters=[IDEMPOTENCY_KEY_HEADER],
        description=(
            "Creates an asynchronous marketplace job. "
            "Use Idempotency-Key to prevent duplicate marketplace operations."
        ),
    )
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
                marketplace = target["marketplace"]
                account = target["account"]

                if marketplace not in {"otto", "hood", "kaufland"}:
                    continue

                configuration = MarketplaceListingConfiguration.objects.filter(
                    product=product,
                    marketplace=marketplace,
                    account=account,
                ).first()

                configuration_data = (
                    configuration.configuration
                    if configuration is not None
                    else {}
                )

                try:
                    if marketplace == "otto":
                        payload = build_otto_payload(
                            product=product,
                            account=account,
                            configuration=configuration_data,
                        )

                    elif marketplace == "hood":
                        payload = build_hood_payload(
                            product=product,
                            account=account,
                            configuration=configuration_data,
                        )

                    elif operation == MarketplaceJob.Operation.PUBLISH:
                        payload = build_kaufland_create_payload(
                            product=product,
                            account=account,
                            configuration=configuration_data,
                        )

                    else:
                        payload = build_kaufland_update_payload(
                            product=product,
                            account=account,
                            configuration=configuration_data,
                        )

                    target_payloads[f"{marketplace}:{account}"] = payload

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

                except HoodPayloadValidationError as exc:
                    return Response(
                        {
                            "detail": (
                                "Hood listing is not ready for publication. "
                                "Fix the manager configuration first."
                            ),
                            "target": target,
                            "errors": exc.errors,
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                except KauflandPayloadValidationError as exc:
                    action = (
                        "publication"
                        if operation == MarketplaceJob.Operation.PUBLISH
                        else "update"
                    )

                    return Response(
                        {
                            "detail": (
                                f"Kaufland listing is not ready for {action}. "
                                "Fix the manager configuration first."
                            ),
                            "target": target,
                            "errors": exc.errors,
                        },
                        status=status.HTTP_400_BAD_REQUEST,
                    )
        try:
            claim = claim_idempotency_key(
                request=request,
                endpoint=f"marketplace-job:{operation}:{product_pk}",
            )
        except IdempotencyKeyReuseError:
            return Response(
                {
                    "detail": (
                        "This Idempotency-Key was already used with "
                        "different request data."
                    )
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        except IdempotencyRequestInProgressError:
            return Response(
                {
                    "detail": (
                        "A marketplace request with this Idempotency-Key "
                        "is still being processed."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        if claim.is_replay:
            return Response(
                claim.replay_body,
                status=claim.replay_status,
            )

        try:
            with transaction.atomic():
                job = MarketplaceJob.objects.create(
                    product=product,
                    requested_by=request.user,
                    operation=operation,
                    requested_channels=data["channels"],
                    request_payload=compact_external_json({
                        "payloads": payloads,
                        "target_payloads": target_payloads,
                        "accounts": data.get("accounts", {}),
                        "targets": data.get("targets", []),
                    }),
                )
                transaction.on_commit(
                    lambda: execute_marketplace_job.delay(str(job.id))
                )
        except Exception:
            abandon_idempotency_claim(claim=claim)
            raise

        response_data = MarketplaceJobSerializer(job).data
        complete_idempotency_claim(
            claim=claim,
            response_status=status.HTTP_202_ACCEPTED,
            response_body=response_data,
        )

        return Response(response_data, status=status.HTTP_202_ACCEPTED)


class ProductMarketplaceListingStateView(ManagerMutationThrottleMixin, APIView):
    """Change listing state for selected targets, or all applicable targets."""

    permission_classes = (IsAuthenticated, IsManager)

    @extend_schema(
        request=MarketplaceListingStateRequestSerializer,
        responses={202: MarketplaceJobSerializer(many=True)},
        parameters=[IDEMPOTENCY_KEY_HEADER],
        description=(
            "Deactivates selected marketplace listings. Without targets it "
            "uses every active listing of the product. Hood/Kaufland are "
            "deleted because their APIs have no reversible deactivation; "
            "OTTO is deactivated and can later be activated again."
        ),
    )
    def post(self, request, product_pk: int):
        product = get_object_or_404(Product, pk=product_pk)
        serializer = MarketplaceListingStateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            claim = claim_idempotency_key(
                request=request,
                endpoint=f"marketplace-listing-state:{product_pk}",
            )
        except IdempotencyKeyReuseError:
            return Response(
                {
                    "detail": (
                        "This Idempotency-Key was already used with "
                        "different request data."
                    )
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        except IdempotencyRequestInProgressError:
            return Response(
                {
                    "detail": (
                        "A listing-state request with this Idempotency-Key "
                        "is still being processed."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        if claim.is_replay:
            return Response(
                claim.replay_body,
                status=claim.replay_status,
            )

        try:
            jobs, unavailable = create_listing_state_jobs(
                product=product,
                requested_by=request.user,
                action=serializer.validated_data["action"],
                requested_targets=serializer.validated_data.get("targets"),
            )
        except Exception:
            abandon_idempotency_claim(claim=claim)
            raise

        if not jobs:
            abandon_idempotency_claim(claim=claim)
            return Response(
                {
                    "detail": "No eligible marketplace listings were found.",
                    "unavailable_targets": unavailable,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        for job in jobs:
            transaction.on_commit(
                lambda job_id=str(job.id): execute_marketplace_job.delay(job_id)
            )

        response_data = {
            "action": serializer.validated_data["action"],
            "jobs": MarketplaceJobSerializer(jobs, many=True).data,
            "unavailable_targets": unavailable,
        }
        complete_idempotency_claim(
            claim=claim,
            response_status=status.HTTP_202_ACCEPTED,
            response_body=response_data,
        )

        return Response(response_data, status=status.HTTP_202_ACCEPTED)


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


class ProductOttoListingConfigurationView(ManagerMutationThrottleMixin, APIView):
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
            context={"account": account},
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


class ProductHoodListingConfigurationView(ManagerMutationThrottleMixin, APIView):
    """Manager configuration for one future Hood listing/account pair."""

    permission_classes = (IsAuthenticated, IsManager)

    def _get_configuration(self, product_pk: int, account: str):
        if account not in MarketplacePublication.Account.values:
            return None

        product = get_object_or_404(Product, pk=product_pk)

        configuration, _ = MarketplaceListingConfiguration.objects.get_or_create(
            product=product,
            marketplace=MarketplacePublication.Marketplace.HOOD,
            account=account,
            defaults={"configuration": {}},
        )
        return configuration

    @extend_schema(
        responses={200: HoodListingConfigurationResponseSerializer},
        description=(
            "Returns manager-editable Hood content for one account. "
            "It can be prepared before publication."
        ),
    )
    def get(self, request, product_pk: int, account: str):
        configuration = self._get_configuration(product_pk, account)

        if configuration is None:
            return Response(
                {"detail": "Account must be 'jv' or 'xl'."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            HoodListingConfigurationResponseSerializer(configuration).data
        )

    @extend_schema(
        request=HoodListingConfigurationSerializer,
        responses={200: HoodListingConfigurationResponseSerializer},
        description=(
            "Partially updates manager-owned Hood content. "
            "Use the payload preview endpoint to validate the final request."
        ),
    )
    def patch(self, request, product_pk: int, account: str):
        configuration = self._get_configuration(product_pk, account)

        if configuration is None:
            return Response(
                {"detail": "Account must be 'jv' or 'xl'."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = HoodListingConfigurationSerializer(
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)

        updated_configuration = dict(configuration.configuration or {})
        updated_configuration.update(
            serializer.to_storage(serializer.validated_data)
        )

        configuration.configuration = updated_configuration
        configuration.save(update_fields=("configuration", "updated_at"))

        return Response(
            HoodListingConfigurationResponseSerializer(configuration).data
        )


class ProductHoodPayloadPreviewView(APIView):
    """Builds Hood payload but never sends it to Hood."""

    permission_classes = (IsAuthenticated, IsManager)

    @extend_schema(
        responses={200: dict, 400: dict},
        description=(
            "Builds and validates the final Hood create/update payload "
            "without making an external request."
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
            marketplace=MarketplacePublication.Marketplace.HOOD,
            account=account,
        ).first()

        try:
            payload = build_hood_payload(
                product=product,
                account=account,
                configuration=(
                    configuration.configuration if configuration else {}
                ),
            )
        except HoodPayloadValidationError as exc:
            return Response(
                {
                    "detail": "Hood listing is not ready for publication.",
                    "errors": exc.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"payload": payload})


class ProductKauflandListingConfigurationView(ManagerMutationThrottleMixin, APIView):
    """Manager configuration for one future Kaufland listing/account pair."""

    permission_classes = (IsAuthenticated, IsManager)

    def _get_configuration(self, product_pk: int, account: str):
        if account not in MarketplacePublication.Account.values:
            return None

        product = get_object_or_404(Product, pk=product_pk)

        configuration, _ = MarketplaceListingConfiguration.objects.get_or_create(
            product=product,
            marketplace=MarketplacePublication.Marketplace.KAUFLAND,
            account=account,
            defaults={"configuration": {}},
        )
        return configuration

    @extend_schema(
        responses={200: KauflandListingConfigurationResponseSerializer},
        description=(
            "Returns manager-editable Kaufland content for one account."
        ),
    )
    def get(self, request, product_pk: int, account: str):
        configuration = self._get_configuration(product_pk, account)

        if configuration is None:
            return Response(
                {"detail": "Account must be 'jv' or 'xl'."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            KauflandListingConfigurationResponseSerializer(
                configuration
            ).data
        )

    @extend_schema(
        request=KauflandListingConfigurationSerializer,
        responses={200: KauflandListingConfigurationResponseSerializer},
        description=(
            "Partially updates manager-owned Kaufland listing content."
        ),
    )
    def patch(self, request, product_pk: int, account: str):
        configuration = self._get_configuration(product_pk, account)

        if configuration is None:
            return Response(
                {"detail": "Account must be 'jv' or 'xl'."},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = KauflandListingConfigurationSerializer(
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)

        updated_configuration = dict(configuration.configuration or {})
        updated_configuration.update(
            serializer.to_storage(serializer.validated_data)
        )

        configuration.configuration = updated_configuration
        configuration.save(update_fields=("configuration", "updated_at"))

        return Response(
            KauflandListingConfigurationResponseSerializer(
                configuration
            ).data
        )


class ProductKauflandCreatePayloadPreviewView(APIView):
    """Builds Kaufland create payload but never sends it."""

    permission_classes = (IsAuthenticated, IsManager)

    @extend_schema(
        responses={200: dict, 400: dict},
        description=(
            "Builds and validates PUT /api/products/upload/ payload "
            "without making an external request."
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
            marketplace=MarketplacePublication.Marketplace.KAUFLAND,
            account=account,
        ).first()

        try:
            payload = build_kaufland_create_payload(
                product=product,
                account=account,
                configuration=(
                    configuration.configuration if configuration else {}
                ),
            )
        except KauflandPayloadValidationError as exc:
            return Response(
                {
                    "detail": "Kaufland listing is not ready for publication.",
                    "errors": exc.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"payload": payload})


class ProductKauflandUpdatePayloadPreviewView(APIView):
    """Builds Kaufland update payload but never sends it."""

    permission_classes = (IsAuthenticated, IsManager)

    @extend_schema(
        responses={200: dict, 400: dict},
        description=(
            "Builds and validates PATCH /api/products/{ean}/change/ payload "
            "without making an external request."
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
            marketplace=MarketplacePublication.Marketplace.KAUFLAND,
            account=account,
        ).first()

        try:
            payload = build_kaufland_update_payload(
                product=product,
                account=account,
                configuration=(
                    configuration.configuration if configuration else {}
                ),
            )
        except KauflandPayloadValidationError as exc:
            return Response(
                {
                    "detail": "Kaufland update is not ready.",
                    "errors": exc.errors,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"payload": payload})



class ProductMarketplaceContentGenerationCreateView(
    ManagerMutationThrottleMixin,
    APIView,
):
    """Creates one asynchronous universal German AI-content draft."""

    permission_classes = (IsAuthenticated, IsManager)
    throttle_classes = [AiGenerationRateThrottle]
    allowed_statuses = {
        Product.Status.SUBMITTED,
        Product.Status.UNDER_REVIEW,
        Product.Status.APPROVED,
        Product.Status.DEACTIVATED,
    }

    @extend_schema(
        request=MarketplaceContentGenerationRequestSerializer,
        responses={202: MarketplaceContentGenerationSerializer},
        parameters=[IDEMPOTENCY_KEY_HEADER],
        description=(
            "Starts one AI generation request for a product. "
            "The result is a draft only and is not applied automatically."
        ),
    )
    def post(self, request, product_pk: int):
        product = get_object_or_404(
            Product.objects.prefetch_related(
                "variants"
            ),
            pk=product_pk,
        )

        if product.status not in self.allowed_statuses:
            return Response(
                {
                    "detail": (
                        "AI content can be generated only for submitted, "
                        "under-review, or approved products."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = MarketplaceContentGenerationRequestSerializer(
            data=request.data
        )
        serializer.is_valid(raise_exception=True)

        try:
            claim = claim_idempotency_key(
                request=request,
                endpoint=f"marketplace-ai-content:{product_pk}",
            )
        except IdempotencyKeyReuseError:
            return Response(
                {
                    "detail": (
                        "This Idempotency-Key was already used with "
                        "different request data."
                    )
                },
                status=status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        except IdempotencyRequestInProgressError:
            return Response(
                {
                    "detail": (
                        "An AI request with this Idempotency-Key is still "
                        "being processed."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        if claim.is_replay:
            return Response(
                claim.replay_body,
                status=claim.replay_status,
            )

        try:
            with transaction.atomic():
                generation = MarketplaceContentGeneration.objects.create(
                    product=product,
                    requested_by=request.user,
                    targets=serializer.validated_data["targets"],
                    language="de",
                    input_snapshot=build_product_snapshot(product),
                )

                generation_id = str(generation.id)
                transaction.on_commit(
                    lambda: generate_marketplace_content.delay(generation_id)
                )
        except Exception:
            abandon_idempotency_claim(claim=claim)
            raise

        response_data = MarketplaceContentGenerationSerializer(generation).data
        complete_idempotency_claim(
            claim=claim,
            response_status=status.HTTP_202_ACCEPTED,
            response_body=response_data,
        )

        return Response(
            response_data,
            status=status.HTTP_202_ACCEPTED,
        )


class MarketplaceContentGenerationDetailView(APIView):
    """Returns the current state and generated draft."""

    permission_classes = (IsAuthenticated, IsManager)

    @extend_schema(
        responses={200: MarketplaceContentGenerationSerializer},
        description="Returns the status and result of an AI content generation.",
    )
    def get(self, request, generation_id):
        generation = get_object_or_404(
            MarketplaceContentGeneration.objects.select_related(
                "product",
                "requested_by",
            ),
            pk=generation_id,
        )

        return Response(
            MarketplaceContentGenerationSerializer(generation).data
        )


class ProductMarketplaceContentGenerationApplyView(
    ManagerMutationThrottleMixin,
    APIView,
):
    """
    Explicitly copies generated text into marketplace configurations.

    A manager may safely review the result before this endpoint is called.
    """

    permission_classes = (IsAuthenticated, IsManager)

    @extend_schema(
        request=MarketplaceContentGenerationApplyRequestSerializer,
        responses={200: dict},
        description=(
            "Applies a completed AI draft to selected marketplace "
            "configurations. Existing values remain unchanged unless "
            "overwrite=true."
        ),
    )
    def post(self, request, product_pk: int, generation_id):
        serializer = MarketplaceContentGenerationApplyRequestSerializer(
            data=request.data
        )
        serializer.is_valid(raise_exception=True)

        targets = serializer.validated_data["targets"]
        overwrite = serializer.validated_data["overwrite"]

        with transaction.atomic():
            product = get_object_or_404(Product, pk=product_pk)

            generation = get_object_or_404(
                MarketplaceContentGeneration.objects.select_for_update(),
                pk=generation_id,
                product=product,
            )

            if generation.status != MarketplaceContentGeneration.Status.SUCCEEDED:
                return Response(
                    {
                        "detail": (
                            "Only a successfully completed AI generation "
                            "can be applied."
                        )
                    },
                    status=status.HTTP_409_CONFLICT,
                )

            content = (
                generation.result.get("universal", {})
                .get("content", {})
            )

            if not content:
                return Response(
                    {"detail": "The AI generation has no usable result."},
                    status=status.HTTP_409_CONFLICT,
                )

            allowed_targets = {
                (target["marketplace"], target["account"])
                for target in generation.targets
            }

            requested_targets = {
                (target["marketplace"], target["account"])
                for target in targets
            }

            if not requested_targets.issubset(allowed_targets):
                return Response(
                    {
                        "targets": (
                            "You may apply content only to targets selected "
                            "when the generation was started."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            updated_targets = []
            skipped_fields = {}

            for target in targets:
                marketplace = target["marketplace"]
                account = target["account"]
                target_key = f"{marketplace}:{account}"

                patch = universal_content_to_marketplace_configuration(
                    marketplace=marketplace,
                    content=content,
                )

                configuration, _ = (
                    MarketplaceListingConfiguration.objects.get_or_create(
                        product=product,
                        marketplace=marketplace,
                        account=account,
                        defaults={"configuration": {}},
                    )
                )

                values = dict(configuration.configuration or {})
                skipped = []
                changed = False

                for field_name, value in patch.items():
                    if not overwrite and values.get(field_name):
                        skipped.append(field_name)
                        continue

                    if values.get(field_name) != value:
                        values[field_name] = value
                        changed = True

                if changed:
                    configuration.configuration = values
                    configuration.save(
                        update_fields=("configuration", "updated_at")
                    )
                    updated_targets.append(target)

                if skipped:
                    skipped_fields[target_key] = skipped

        return Response(
            {
                "generation_id": str(generation.id),
                "updated_targets": updated_targets,
                "skipped_fields": skipped_fields,
                "overwrite": overwrite,
            }
        )
