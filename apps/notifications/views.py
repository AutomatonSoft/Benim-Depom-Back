from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DeviceToken, Notification
from .serializers import (
    AFTERBUY_TYPES,
    AVAILABILITY_TYPES,
    MANAGER_SENT_TYPES,
    PRICE_TYPES,
    REVIEW_TYPES,
    DeviceTokenDeactivateSerializer,
    DeviceTokenSerializer,
    NotificationFilterSerializer,
    NotificationSerializer,
    NotificationSummarySerializer,
)


class DeviceTokenRegisterView(generics.CreateAPIView):
    serializer_class = DeviceTokenSerializer
    permission_classes = [IsAuthenticated]


class DeviceTokenDeactivateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        request=DeviceTokenDeactivateSerializer,
        responses={204: None},
    )
    def post(self, request):
        serializer = DeviceTokenDeactivateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        DeviceToken.objects.filter(
            user=request.user,
            token=serializer.validated_data["token"],
        ).update(is_active=False)

        return Response(status=status.HTTP_204_NO_CONTENT)


def _apply_notification_filters(queryset, filters: dict):
    category = filters.get("category")
    if category == "review":
        queryset = queryset.filter(notification_type__in=REVIEW_TYPES)
    elif category == "availability":
        queryset = queryset.filter(notification_type__in=AVAILABILITY_TYPES)
    elif category == "price":
        queryset = queryset.filter(notification_type__in=PRICE_TYPES)
    elif category == "afterbuy":
        queryset = queryset.filter(notification_type__in=AFTERBUY_TYPES)

    notification_type = filters.get("notification_type")
    if notification_type:
        queryset = queryset.filter(notification_type=notification_type)

    is_read = filters.get("is_read")
    if is_read is True:
        queryset = queryset.filter(is_read=True)
    elif is_read is False:
        queryset = queryset.filter(is_read=False)

    search = (filters.get("search") or "").strip()
    if search:
        query = (
            Q(title__icontains=search)
            | Q(body__icontains=search)
            | Q(product__title__icontains=search)
            | Q(product__owner__username__icontains=search)
            | Q(product__owner__email__icontains=search)
            | Q(product__owner__first_name__icontains=search)
            | Q(product__owner__last_name__icontains=search)
            | Q(sender__username__icontains=search)
            | Q(sender__email__icontains=search)
            | Q(user__username__icontains=search)
            | Q(user__email__icontains=search)
            | Q(user__first_name__icontains=search)
            | Q(user__last_name__icontains=search)
        )
        if search.isdigit():
            query |= Q(product_id=int(search))
        queryset = queryset.filter(query)

    return queryset


class NotificationPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100


class NotificationListView(generics.ListAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
    pagination_class = NotificationPagination

    @extend_schema(
        parameters=[NotificationFilterSerializer],
        responses={200: NotificationSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        self.filters = NotificationFilterSerializer(data=request.query_params)
        self.filters.is_valid(raise_exception=True)
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        filters = getattr(self, "filters", None)
        validated = filters.validated_data if filters is not None else {}
        related = (
            "product",
            "product__owner",
            "sender",
            "user",
            "price_negotiation",
            "price_negotiation__manager",
            "afterbuy_order_item",
            "afterbuy_order_item__order",
            "afterbuy_order_item__sale_notification",
        )
        prefetch = (
            "product__price_negotiations",
            Prefetch(
                "product__notifications",
                queryset=(
                    Notification.objects.filter(
                        notification_type__in=MANAGER_SENT_TYPES,
                    )
                    .select_related("sender")
                    .order_by("-created_at")
                ),
                to_attr="manager_sent_notifications",
            ),
        )
        if validated.get("category") == "outgoing":
            queryset = (
                Notification.objects.filter(sender=self.request.user)
                .select_related(*related)
                .prefetch_related(*prefetch)
                .order_by("-created_at")
            )
        else:
            queryset = (
                Notification.objects.filter(user=self.request.user)
                .select_related(*related)
                .prefetch_related(*prefetch)
                .order_by("-created_at")
            )
        return _apply_notification_filters(queryset, validated)


class NotificationSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: NotificationSummarySerializer})
    def get(self, request):
        inbox = Notification.objects.filter(user=request.user)
        outgoing = Notification.objects.filter(sender=request.user)
        review_q = Q(notification_type__in=REVIEW_TYPES)
        availability_q = Q(notification_type__in=AVAILABILITY_TYPES)
        price_q = Q(notification_type__in=PRICE_TYPES)
        afterbuy_q = Q(notification_type__in=AFTERBUY_TYPES)

        inbox_counts = inbox.aggregate(
            all=Count("id"),
            unread_total=Count("id", filter=Q(is_read=False)),
            review=Count("id", filter=review_q),
            availability=Count("id", filter=availability_q),
            price=Count("id", filter=price_q),
            afterbuy=Count("id", filter=afterbuy_q),
            unread_review=Count("id", filter=review_q & Q(is_read=False)),
            unread_availability=Count("id", filter=availability_q & Q(is_read=False)),
            unread_price=Count("id", filter=price_q & Q(is_read=False)),
            unread_afterbuy=Count("id", filter=afterbuy_q & Q(is_read=False)),
        )
        outgoing_counts = outgoing.aggregate(
            outgoing=Count("id"),
            unread_outgoing=Count("id", filter=Q(is_read=False)),
        )
        return Response(
            NotificationSummarySerializer({**inbox_counts, **outgoing_counts}).data
        )


class NotificationReadView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={200: NotificationSerializer})
    def post(self, request, notification_pk: int):
        notification = get_object_or_404(
            Notification.objects.select_related(
                "product",
                "product__owner",
                "sender",
                "price_negotiation",
                "price_negotiation__manager",
            ).prefetch_related("product__price_negotiations"),
            pk=notification_pk,
            user=request.user,
        )

        if not notification.is_read:
            notification.is_read = True
            notification.read_at = timezone.now()
            notification.save(update_fields=("is_read", "read_at"))

        return Response(
            NotificationSerializer(notification, context={"request": request}).data
        )


class NotificationReadAllView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={204: None})
    def post(self, request):
        Notification.objects.filter(
            user=request.user,
            is_read=False,
        ).update(is_read=True, read_at=timezone.now())

        return Response(status=status.HTTP_204_NO_CONTENT)
