from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsManager, is_manager
from apps.common.throttles import ManagerMutationThrottleMixin
from apps.notifications.models import Notification
from apps.notifications.serializers import (
    ManagerProductNotificationSerializer,
    NotificationSerializer,
)
from apps.notifications.services import create_notification
from apps.orchestrator.listing_state_services import create_listing_state_jobs
from apps.orchestrator.serializers import MarketplaceJobSerializer
from apps.orchestrator.tasks import execute_marketplace_job
from apps.products.filters import filter_products
from apps.products.models import Product
from apps.products.pricing import latest_rate
from apps.products.serializers import ProductSerializer
from apps.products.services import request_product_availability

from .models import ModerationDecision
from .serializers import (
    ApproveProductSerializer,
    ModerationDecisionSerializer,
    RejectProductSerializer,
)
from .services import (
    approve_product,
    reject_product,
)


class ProductModerationHistoryView(generics.ListAPIView):
    serializer_class = ModerationDecisionSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        product_queryset = Product.objects.all()

        if not is_manager(self.request.user):
            product_queryset = product_queryset.filter(owner=self.request.user)

        product = get_object_or_404(
            product_queryset,
            pk=self.kwargs["product_pk"],
        )

        return (
            ModerationDecision.objects.filter(product=product)
            .select_related("manager")
            .order_by("-created_at")
        )


class ManagerProductListView(generics.ListAPIView):
    serializer_class = ProductSerializer
    permission_classes = [IsManager]

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["exchange_rate"] = latest_rate()
        return context

    def get_queryset(self):
        queryset = (
            Product.objects.select_related("owner")
            .prefetch_related(
                "variants",
                "images",
                "images__generated_images",
            )
            .exclude(status=Product.Status.ARCHIVED)
        )

        owner_id = self.request.query_params.get("owner_id")

        if owner_id:
            try:
                queryset = queryset.filter(owner_id=int(owner_id))
            except ValueError as error:
                from rest_framework.exceptions import ValidationError

                raise ValidationError(
                    {"owner_id": "This value must be an integer."}
                ) from error

        return filter_products(
            queryset=queryset,
            query_params=self.request.query_params,
        )


class ManagerSendProductNotificationView(ManagerMutationThrottleMixin, APIView):
    permission_classes = [IsManager]

    @extend_schema(
        request=ManagerProductNotificationSerializer,
        responses={201: NotificationSerializer},
    )
    def post(self, request, product_pk: int):
        product = get_object_or_404(
            Product.objects.select_related("owner").exclude(
                status=Product.Status.ARCHIVED
            ),
            pk=product_pk,
        )

        serializer = ManagerProductNotificationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        notification = create_notification(
            user=product.owner,
            sender=request.user,
            product=product,
            notification_type=Notification.Type.MANAGER_MESSAGE,
            title=serializer.validated_data.get(
                "title",
                "Product update",
            ),
            body=serializer.validated_data["body"],
            data={
                "product_id": product.id,
                "product_title": product.title,
            },
        )

        return Response(
            NotificationSerializer(
                notification,
                context={"request": request},
            ).data,
            status=status.HTTP_201_CREATED,
        )


class ManagerApproveProductView(ManagerMutationThrottleMixin, APIView):
    permission_classes = [IsManager]

    @extend_schema(
        request=ApproveProductSerializer,
        responses={200: ProductSerializer},
    )
    def post(self, request, product_pk: int):
        product = get_object_or_404(Product, pk=product_pk)

        serializer = ApproveProductSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        product = approve_product(
            product=product,
            manager=request.user,
            comment=serializer.validated_data.get("comment", ""),
        )

        return Response(ProductSerializer(product, context={"request": request}).data)


class ManagerRejectProductView(ManagerMutationThrottleMixin, APIView):
    permission_classes = [IsManager]

    @extend_schema(
        request=RejectProductSerializer,
        responses={200: ProductSerializer},
    )
    def post(self, request, product_pk: int):
        product = get_object_or_404(Product, pk=product_pk)

        serializer = RejectProductSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        product = reject_product(
            product=product,
            manager=request.user,
            comment=serializer.validated_data["comment"],
        )

        return Response(ProductSerializer(product, context={"request": request}).data)


class ManagerRequestProductAvailabilityView(ManagerMutationThrottleMixin, APIView):
    permission_classes = [IsManager]

    @extend_schema(request=None, responses={200: ProductSerializer})
    def post(self, request, product_pk: int):
        product = get_object_or_404(
            Product.objects.select_related("owner"),
            pk=product_pk,
        )
        product = request_product_availability(
            product=product,
            manager=request.user,
        )
        return Response(ProductSerializer(product, context={"request": request}).data)


class ManagerDeactivateProductView(ManagerMutationThrottleMixin, APIView):
    """Compatibility endpoint: schedule deactivation for every active listing."""

    permission_classes = [IsManager]

    @extend_schema(
        request=None,
        responses={202: MarketplaceJobSerializer(many=True)},
        description=(
            "Legacy shortcut that deactivates every active listing of a product. "
            "Use the orchestrator listing-state endpoint to choose targets."
        ),
    )
    def post(self, request, product_pk: int):
        product = get_object_or_404(Product, pk=product_pk)
        jobs, unavailable = create_listing_state_jobs(
            product=product,
            requested_by=request.user,
            action="deactivate",
        )
        if not jobs:
            return Response(
                {
                    "detail": "No active marketplace listings were found.",
                    "unavailable_targets": unavailable,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        for job in jobs:
            transaction.on_commit(
                lambda job_id=str(job.id): execute_marketplace_job.delay(job_id)
            )

        return Response(
            {
                "jobs": MarketplaceJobSerializer(jobs, many=True).data,
                "unavailable_targets": unavailable,
            },
            status=status.HTTP_202_ACCEPTED,
        )
