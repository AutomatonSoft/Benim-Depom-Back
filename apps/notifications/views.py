from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DeviceToken, Notification
from .serializers import (
    DeviceTokenDeactivateSerializer,
    DeviceTokenSerializer,
    NotificationSerializer,
)


class DeviceTokenRegisterView(generics.CreateAPIView):
    serializer_class = DeviceTokenSerializer
    permission_classes = [IsAuthenticated]


class DeviceTokenDeactivateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = DeviceTokenDeactivateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        DeviceToken.objects.filter(
            user=request.user,
            token=serializer.validated_data["token"],
        ).update(is_active=False)

        return Response(status=status.HTTP_204_NO_CONTENT)


class NotificationListView(generics.ListAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(
            user=self.request.user
        ).select_related("product")



class NotificationReadView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, notification_pk: int):
        notification = get_object_or_404(
            Notification,
            pk=notification_pk,
            user=request.user
        )

        if not notification.is_read:
            notification.is_read = True
            notification.read_at = timezone.now()
            notification.save(update_fields=("is_read", "read_at"))

        return Response(
            NotificationSerializer(
                notification,
                context={"request": request}
            ).data
        )



class NotificationReadAllView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        Notification.objects.filter(
            user=request.user,
            is_read=False,
        ).update(
            is_read=True,
            read_at=timezone.now()
        )

        return Response(status=status.HTTP_204_NO_CONTENT)

    