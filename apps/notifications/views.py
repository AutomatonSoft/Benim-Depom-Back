from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DeviceToken, Notification
from .serializers import (
    AVAILABILITY_TYPES,
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
    elif category == "other":
        queryset = queryset.exclude(
            notification_type__in=(*REVIEW_TYPES, *AVAILABILITY_TYPES)
        )

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
        )
        if search.isdigit():
            query |= Q(product_id=int(search))
        queryset = queryset.filter(query)

    return queryset


class NotificationListView(generics.ListAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

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
        queryset = (
            Notification.objects.filter(user=self.request.user)
            .select_related("product", "product__owner", "sender")
            .order_by("-created_at")
        )
        return _apply_notification_filters(queryset, validated)


class NotificationSummaryView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={200: NotificationSummarySerializer})
    def get(self, request):
        base = Notification.objects.filter(user=request.user)
        review_q = Q(notification_type__in=REVIEW_TYPES)
        availability_q = Q(notification_type__in=AVAILABILITY_TYPES)
        other_q = ~Q(notification_type__in=(*REVIEW_TYPES, *AVAILABILITY_TYPES))

        counts = base.aggregate(
            all=Count("id"),
            unread_total=Count("id", filter=Q(is_read=False)),
            review=Count("id", filter=review_q),
            availability=Count("id", filter=availability_q),
            other=Count("id", filter=other_q),
            unread_review=Count("id", filter=review_q & Q(is_read=False)),
            unread_availability=Count("id", filter=availability_q & Q(is_read=False)),
            unread_other=Count("id", filter=other_q & Q(is_read=False)),
        )
        return Response(NotificationSummarySerializer(counts).data)


class NotificationReadView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=None, responses={200: NotificationSerializer})
    def post(self, request, notification_pk: int):
        notification = get_object_or_404(
            Notification.objects.select_related("product", "product__owner", "sender"),
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
