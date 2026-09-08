from drf_spectacular.utils import extend_schema_field, extend_schema_serializer
from rest_framework import serializers

from .models import DeviceToken, Notification


NOTIFICATION_CATEGORY_CHOICES = (
    ("review", "Review"),
    ("availability", "Availability"),
    ("other", "Other"),
)

REVIEW_TYPES = (
    Notification.Type.PRODUCT_SUBMITTED_FOR_REVIEW,
    Notification.Type.PRODUCT_CHANGE_REQUESTED,
)

AVAILABILITY_TYPES = (
    Notification.Type.PRODUCT_AVAILABILITY_REMINDER,
    Notification.Type.PRODUCT_DEACTIVATION_REQUESTED,
)


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


@extend_schema_serializer(component_name="NotificationsNotificationFilter")
class NotificationFilterSerializer(serializers.Serializer):
    search = serializers.CharField(required=False, allow_blank=True, max_length=200)
    category = serializers.ChoiceField(
        choices=NOTIFICATION_CATEGORY_CHOICES,
        required=False,
    )
    is_read = serializers.BooleanField(required=False, allow_null=True, default=None)
    notification_type = serializers.ChoiceField(
        choices=Notification.Type.choices,
        required=False,
    )


@extend_schema_serializer(component_name="NotificationsNotification")
class NotificationSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(
        source="product.id",
        read_only=True,
        allow_null=True,
    )
    product_title = serializers.SerializerMethodField()
    seller_username = serializers.SerializerMethodField()
    seller_email = serializers.SerializerMethodField()
    seller_name = serializers.SerializerMethodField()

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
            "seller_username",
            "seller_email",
            "seller_name",
            "is_read",
            "created_at",
            "read_at",
            "responded_at",
            "sender_id",
            "title",
            "body",
        )
        read_only_fields = fields

    def _seller(self, notification: Notification):
        product = notification.product
        if product is None:
            return None
        return getattr(product, "owner", None)

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_product_title(self, notification) -> str | None:
        if notification.product_id is None:
            return None
        return notification.product.title

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_seller_username(self, notification) -> str | None:
        seller = self._seller(notification)
        return seller.username if seller is not None else None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_seller_email(self, notification) -> str | None:
        seller = self._seller(notification)
        return seller.email if seller is not None else None

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_seller_name(self, notification) -> str | None:
        seller = self._seller(notification)
        if seller is None:
            return None
        full = f"{seller.first_name or ''} {seller.last_name or ''}".strip()
        return full or None


@extend_schema_serializer(component_name="NotificationsSummary")
class NotificationSummarySerializer(serializers.Serializer):
    unread_total = serializers.IntegerField()
    all = serializers.IntegerField()
    review = serializers.IntegerField()
    availability = serializers.IntegerField()
    other = serializers.IntegerField()
    unread_review = serializers.IntegerField()
    unread_availability = serializers.IntegerField()
    unread_other = serializers.IntegerField()


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
