from django.db import transaction
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.common.permissions import IsManager, is_manager
from apps.common.throttles import ManagerMutationThrottleMixin
from apps.ean.models import EanCode
from apps.notifications.models import Notification
from apps.notifications.serializers import (
    ManagerProductNotificationSerializer,
    NotificationSerializer,
)
from apps.notifications.services import create_notification
from apps.orchestrator.listing_state_services import create_listing_state_jobs
from apps.orchestrator.models import MarketplacePublication
from apps.orchestrator.serializers import MarketplaceJobSerializer
from apps.orchestrator.tasks import execute_marketplace_job
from apps.products.filters import filter_products
from apps.products.models import Product
from apps.products.pricing import latest_rate
from apps.products.serializers import (
    PriceNegotiationCreateSerializer,
    ProductSerializer,
)
from apps.products.services import (
    create_price_negotiation,
    request_product_availability,
)

from .models import ModerationDecision
from .serializers import (
    ApproveProductSerializer,
    ApproveSellerChangesSerializer,
    ChangeApprovedProductStatusSerializer,
    ManagerDashboardSerializer,
    ModerationDecisionSerializer,
    RejectProductSerializer,
    RejectSellerChangesSerializer,
)
from .services import (
    approve_product,
    approve_seller_changes,
    change_approved_product_status,
    reject_product,
    reject_seller_changes,
)

QUEUE_LIMIT = 8
HISTORY_PAGE_SIZE = 5
ACTIVE_LISTING_CHANNELS = (
    (MarketplacePublication.Marketplace.OTTO, MarketplacePublication.Account.JV),
    (MarketplacePublication.Marketplace.KAUFLAND, MarketplacePublication.Account.JV),
    (MarketplacePublication.Marketplace.HOOD, MarketplacePublication.Account.JV),
    (MarketplacePublication.Marketplace.OTTO, MarketplacePublication.Account.XL),
    (MarketplacePublication.Marketplace.KAUFLAND, MarketplacePublication.Account.XL),
    (MarketplacePublication.Marketplace.HOOD, MarketplacePublication.Account.XL),
)


def _absolute_media_url(request, file_field) -> str:
    name = getattr(file_field, "name", None)
    if not file_field or not name:
        return ""
    try:
        url = file_field.url
    except ValueError:
        return ""
    if request:
        return request.build_absolute_uri(url)
    return url


class ManagerDashboardView(APIView):
    permission_classes = [IsManager]

    @extend_schema(
        responses={200: ManagerDashboardSerializer},
        description=(
            "Live overview counts for the manager home page: "
            "moderation queue, active marketplace listings, free EANs, "
            "and sellers."
        ),
    )
    def get(self, request):
        now = timezone.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = today_start.replace(day=1)

        awaiting = Product.objects.filter(status=Product.Status.SUBMITTED).aggregate(
            total=Count("id"),
            today=Count("id", filter=Q(created_at__gte=today_start)),
        )
        published = MarketplacePublication.objects.filter(
            published_at__gte=today_start,
        ).aggregate(
            total=Count("id"),
            marketplaces=Count("marketplace", distinct=True),
        )
        sellers = User.objects.filter(
            role=User.Role.SELLER,
            is_active=True,
            is_email_verified=True,
        ).aggregate(
            total=Count("id"),
            this_month=Count("id", filter=Q(date_joined__gte=month_start)),
        )
        listing_rows = {
            (row["marketplace"], row["account"]): row["total"]
            for row in MarketplacePublication.objects.filter(
                status=MarketplacePublication.Status.ACTIVE,
            )
            .values("marketplace", "account")
            .annotate(total=Count("id"))
        }
        active_listings = [
            {
                "marketplace": marketplace,
                "account": account,
                "count": listing_rows.get((marketplace, account), 0),
            }
            for marketplace, account in ACTIVE_LISTING_CHANNELS
        ]
        free_eans = EanCode.objects.filter(state=EanCode.State.AVAILABLE).aggregate(
            jv=Count("id", filter=Q(account=EanCode.Account.JV)),
            xl=Count("id", filter=Q(account=EanCode.Account.XL)),
            total=Count("id"),
        )

        queue_products = list(
            Product.objects.filter(status=Product.Status.SUBMITTED)
            .select_related("owner")
            .prefetch_related("images")
            .order_by("-created_at")[:QUEUE_LIMIT]
        )
        queue = []
        for product in queue_products:
            images = list(product.images.all())
            primary = next(
                (image for image in images if image.is_primary),
                images[0] if images else None,
            )
            owner = product.owner
            queue.append(
                {
                    "id": product.id,
                    "title": product.title,
                    "product_type": product.product_type,
                    "created_at": product.created_at,
                    "image": _absolute_media_url(
                        request, primary.image if primary else None
                    ),
                    "seller_name": (owner.first_name or "").strip() or owner.username,
                }
            )

        payload = {
            "awaiting_review": awaiting["total"],
            "awaiting_review_today": awaiting["today"],
            "published_today": published["total"],
            "published_today_marketplaces": published["marketplaces"],
            "active_sellers": sellers["total"],
            "sellers_joined_this_month": sellers["this_month"],
            "active_listings": active_listings,
            "free_eans": free_eans,
            "queue": queue,
        }
        serializer = ManagerDashboardSerializer(payload)
        return Response(serializer.data)


class ModerationHistoryPagination(PageNumberPagination):
    page_size = HISTORY_PAGE_SIZE


class ManagerProductPagination(PageNumberPagination):
    page_size = 10
    page_size_query_param = "page_size"
    max_page_size = 100


class ProductModerationHistoryView(generics.ListAPIView):
    serializer_class = ModerationDecisionSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = ModerationHistoryPagination

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
    pagination_class = ManagerProductPagination

    def get_serializer_context(self):
        context = super().get_serializer_context()
        context["exchange_rate"] = latest_rate()
        return context

    def get_queryset(self):
        queryset = (
            Product.objects.select_related("owner")
            .prefetch_related(
                "variants",
                "set_parts",
                "images",
                "images__generated_images",
                "price_negotiations",
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
            expected_catalog_revision=serializer.validated_data.get(
                "expected_catalog_revision"
            ),
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
            expected_catalog_revision=serializer.validated_data.get(
                "expected_catalog_revision"
            ),
        )

        return Response(ProductSerializer(product, context={"request": request}).data)


class ManagerChangeProductStatusView(ManagerMutationThrottleMixin, APIView):
    permission_classes = [IsManager]

    @extend_schema(
        request=ChangeApprovedProductStatusSerializer,
        responses={200: ProductSerializer},
        description=(
            "Move an approved product back to review or reject it. "
            "Blocked while marketplace listings are still live or in progress."
        ),
    )
    def post(self, request, product_pk: int):
        product = get_object_or_404(Product, pk=product_pk)
        serializer = ChangeApprovedProductStatusSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product = change_approved_product_status(
            product=product,
            manager=request.user,
            status=serializer.validated_data["status"],
            comment=serializer.validated_data.get("comment", ""),
            expected_catalog_revision=serializer.validated_data.get(
                "expected_catalog_revision"
            ),
        )
        return Response(ProductSerializer(product, context={"request": request}).data)


class ManagerApproveSellerChangesView(ManagerMutationThrottleMixin, APIView):
    permission_classes = [IsManager]

    @extend_schema(
        request=ApproveSellerChangesSerializer,
        responses={200: ProductSerializer},
        description=(
            "Apply pending seller catalog changes and queue marketplace "
            "updates for active listings."
        ),
    )
    def post(self, request, product_pk: int):
        product = get_object_or_404(Product, pk=product_pk)
        serializer = ApproveSellerChangesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product, job = approve_seller_changes(
            product=product,
            manager=request.user,
            expected_catalog_revision=serializer.validated_data[
                "expected_catalog_revision"
            ],
        )
        if job is not None:
            job_id = str(job.id)

            def enqueue_job(job_id=job_id):
                try:
                    execute_marketplace_job.delay(job_id)
                except Exception:
                    pass

            transaction.on_commit(enqueue_job)
        product = (
            Product.objects.select_related("owner")
            .prefetch_related(
                "variants",
                "set_parts",
                "images",
                "images__generated_images",
            )
            .get(pk=product.pk)
        )
        try:
            payload = dict(
                ProductSerializer(product, context={"request": request}).data
            )
        except Exception:
            payload = {
                "id": product.pk,
                "status": product.status,
                "pending_changes": product.pending_changes,
                "catalog_revision": product.catalog_revision,
                "variants": [],
            }
        try:
            payload["marketplace_job"] = (
                MarketplaceJobSerializer(job).data if job is not None else None
            )
        except Exception:
            payload["marketplace_job"] = None
        return Response(payload)


class ManagerRejectSellerChangesView(ManagerMutationThrottleMixin, APIView):
    permission_classes = [IsManager]

    @extend_schema(
        request=RejectSellerChangesSerializer,
        responses={200: ProductSerializer},
        description=(
            "Discard pending seller catalog changes. The product stays approved. "
            "The seller is notified with the rejection reason."
        ),
    )
    def post(self, request, product_pk: int):
        product = get_object_or_404(Product, pk=product_pk)
        serializer = RejectSellerChangesSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product = reject_seller_changes(
            product=product,
            manager=request.user,
            comment=serializer.validated_data["comment"],
            expected_catalog_revision=serializer.validated_data[
                "expected_catalog_revision"
            ],
        )
        product = (
            Product.objects.select_related("owner")
            .prefetch_related(
                "variants",
                "set_parts",
                "images",
                "images__generated_images",
            )
            .get(pk=product.pk)
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


class ManagerCreatePriceNegotiationView(ManagerMutationThrottleMixin, APIView):
    permission_classes = [IsManager]

    @extend_schema(
        request=PriceNegotiationCreateSerializer,
        responses={201: ProductSerializer},
        description=(
            "Send a price offer to the seller in the product currency. "
            "A new pending offer supersedes any previous pending offer. "
            "Accepting later updates catalog unit_price only and does not "
            "sync marketplace listings."
        ),
    )
    def post(self, request, product_pk: int):
        product = get_object_or_404(
            Product.objects.select_related("owner").prefetch_related(
                "variants",
                "set_parts",
                "images",
                "images__generated_images",
                "price_negotiations",
            ),
            pk=product_pk,
        )
        serializer = PriceNegotiationCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        create_price_negotiation(
            product=product,
            manager=request.user,
            proposed_unit_price=serializer.validated_data["proposed_unit_price"],
            message=serializer.validated_data["message"],
        )
        product = (
            Product.objects.select_related("owner")
            .prefetch_related(
                "variants",
                "set_parts",
                "images",
                "images__generated_images",
                "price_negotiations",
            )
            .get(pk=product.pk)
        )
        return Response(
            ProductSerializer(product, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


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
