from drf_spectacular.utils import extend_schema_field, extend_schema_serializer
from rest_framework import serializers

from .models import DeviceToken, Notification


@extend_schema_serializer(component_name="NotificationsDeviceToken")
class DeviceTokenSerializer(serializers.ModelSerializer):
    token = serializers.CharField(write_only=True)

    class Meta:
        model = DeviceToken
        fields = (
            "id",
            "token",
            "platform",
            "is_active",
            "last_seen_at",
        )
        read_only_fields = (
            "id",
            "is_active",
            "last_seen_at",
        )

    def create(self, validated_data):
        user = self.context["request"].user

        device_token, _ = DeviceToken.objects.update_or_create(
            token=validated_data["token"],
            defaults={
                "user": user,
                "platform": validated_data["platform"],
                "is_active": True,
            },
        )

        return device_token


@extend_schema_serializer(component_name="NotificationsDeviceDeactivate")
class DeviceTokenDeactivateSerializer(serializers.Serializer):
    token = serializers.CharField()


@extend_schema_serializer(component_name="NotificationsNotification")
class NotificationSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(
        source="product.id",
        read_only=True,
        allow_null=True,
    )
    product_title = serializers.SerializerMethodField()

    sender_id = serializers.IntegerField(
        source="sender.id",
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = Notification
        fields = (
            "id",
            "notification_type",
            "product_id",
            "product_title",
            "is_read",
            "created_at",
            "read_at",
            "responded_at",
            "sender_id",
            "title",
            "body",
        )
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_product_title(self, notification) -> str | None:
        if notification.product_id is None:
            return None
        return notification.product.title


@extend_schema_serializer(component_name="WebManagerProductNotification")
class ManagerProductNotificationSerializer(serializers.Serializer):
    title = serializers.CharField(
        max_length=150,
        required=False,
        allow_blank=True,
    )
    body = serializers.CharField(max_length=1500)

    def validate_body(self, value):
        if not value.strip():
            raise serializers.ValidationError("Message text cannot be empty.")
        return value
