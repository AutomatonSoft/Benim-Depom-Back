from rest_framework import serializers

from .models import DeviceToken, Notification


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


class DeviceTokenDeactivateSerializer(serializers.Serializer):
    token = serializers.CharField()


class NotificationSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(
        source="product.id",
        read_only=True,
    )

    class Meta:
        model = Notification
        fields = (
            "id",
            "notification_type",
            "product_id",
            "data",
            "is_read",
            "created_at",
            "read_at",
        )
        read_only_fields = fields