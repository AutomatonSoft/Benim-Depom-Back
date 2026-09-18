from django.db import transaction
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.common.permissions import IsManager
from apps.common.throttles import ManagerMutationThrottleMixin
from apps.notifications.models import Notification
from apps.notifications.serializers import NotificationSerializer
from apps.orchestrator.serializers import MarketplaceJobSerializer
from apps.orchestrator.tasks import execute_marketplace_job
from apps.products.serializers import ManagerSalesStatsSerializer

from .sale_actions import notify_seller_of_afterbuy_sale, sync_stock_from_afterbuy_sale
from .stats import manager_sales_stats_from_query


def _manager_sale_notification(request, notification_pk: int) -> Notification:
    return get_object_or_404(
        Notification.objects.select_related(
            "afterbuy_order_item",
            "afterbuy_order_item__order",
            "afterbuy_order_item__matched_product",
            "afterbuy_order_item__sale_notification",
            "product",
            "product__owner",
        ).prefetch_related("product__variants"),
        pk=notification_pk,
        user=request.user,
        notification_type=Notification.Type.PRODUCT_SOLD,
    )


class AfterbuySyncStockView(ManagerMutationThrottleMixin, APIView):
    permission_classes = [IsManager]

    @extend_schema(
        request=None,
        responses={202: MarketplaceJobSerializer},
        description=(
            "Read quantity from the selling Afterbuy channel, write it to "
            "the product warehouse, and enqueue UPDATE jobs for other "
            "active marketplace accounts."
        ),
    )
    def post(self, request, notification_pk: int):
        notification = _manager_sale_notification(request, notification_pk)
        job, live, targets = sync_stock_from_afterbuy_sale(
            notification=notification,
            requested_by=request.user,
        )
        sale = getattr(notification.afterbuy_order_item, "sale_notification", None)
        if sale is not None:
            sale.refresh_from_db()
        if job is not None:
            job_id = str(job.id)

            def enqueue_job(job_id=job_id):
                try:
                    execute_marketplace_job.delay(job_id)
                except Exception:
                    pass

            transaction.on_commit(enqueue_job)

        payload = {
            "qty_after": live,
            "targets": targets,
            "job": MarketplaceJobSerializer(job).data if job is not None else None,
            "notification": NotificationSerializer(
                notification,
                context={"request": request},
            ).data,
        }
        return Response(payload, status=status.HTTP_202_ACCEPTED)


class AfterbuyNotifySellerView(ManagerMutationThrottleMixin, APIView):
    permission_classes = [IsManager]

    @extend_schema(
        request=None,
        responses={201: NotificationSerializer},
        description="Send the seller a product_sold notification for this Afterbuy sale.",
    )
    def post(self, request, notification_pk: int):
        notification = _manager_sale_notification(request, notification_pk)
        sale = getattr(notification.afterbuy_order_item, "sale_notification", None)
        already = sale is not None and sale.seller_notified_at is not None
        seller_note = notify_seller_of_afterbuy_sale(notification=notification)
        return Response(
            NotificationSerializer(
                seller_note,
                context={"request": request},
            ).data,
            status=status.HTTP_200_OK if already else status.HTTP_201_CREATED,
        )


class ManagerSalesStatsView(APIView):
    permission_classes = [IsManager]

    @extend_schema(
        parameters=[
            OpenApiParameter(
                name="from",
                type=OpenApiTypes.DATE,
                required=False,
                description="Inclusive start date (YYYY-MM-DD, Europe/Berlin).",
            ),
            OpenApiParameter(
                name="to",
                type=OpenApiTypes.DATE,
                required=False,
                description="Inclusive end date (YYYY-MM-DD, Europe/Berlin).",
            ),
            OpenApiParameter(
                name="ean",
                type=OpenApiTypes.STR,
                required=False,
                description="Product EAN (JV or XL).",
            ),
            OpenApiParameter(
                name="seller_email",
                type=OpenApiTypes.STR,
                required=False,
                description="Seller account email.",
            ),
        ],
        responses={200: ManagerSalesStatsSerializer},
        description=(
            "Company-wide Afterbuy sales totals using frozen card prices. "
            "Filter by period, seller email, or product EAN."
        ),
    )
    def get(self, request):
        payload = manager_sales_stats_from_query(query_params=request.query_params)
        return Response(ManagerSalesStatsSerializer(payload).data)
