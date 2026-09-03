from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers

from .models import ModerationDecision


@extend_schema_serializer(component_name="ModerationDecision")
class ModerationDecisionSerializer(serializers.ModelSerializer):
    manager_username = serializers.CharField(
        source="manager.username",
        read_only=True,
    )

    class Meta:
        model = ModerationDecision
        fields = (
            "id",
            "decision",
            "comment",
            "manager",
            "manager_username",
            "created_at",
        )
        read_only_fields = fields


@extend_schema_serializer(component_name="ModerationApprove")
class ApproveProductSerializer(serializers.Serializer):
    comment = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=2000,
    )


@extend_schema_serializer(component_name="ManagerDashboardQueueItem")
class ManagerDashboardQueueItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    title = serializers.CharField()
    product_type = serializers.CharField()
    created_at = serializers.DateTimeField()
    image = serializers.CharField(allow_blank=True)
    seller_name = serializers.CharField()


@extend_schema_serializer(component_name="ManagerDashboardChannelCount")
class ManagerDashboardChannelCountSerializer(serializers.Serializer):
    marketplace = serializers.CharField()
    account = serializers.CharField()
    count = serializers.IntegerField()


@extend_schema_serializer(component_name="ManagerDashboardFreeEans")
class ManagerDashboardFreeEansSerializer(serializers.Serializer):
    jv = serializers.IntegerField()
    xl = serializers.IntegerField()
    total = serializers.IntegerField()


@extend_schema_serializer(component_name="ManagerDashboard")
class ManagerDashboardSerializer(serializers.Serializer):
    awaiting_review = serializers.IntegerField()
    awaiting_review_today = serializers.IntegerField()
    published_today = serializers.IntegerField()
    published_today_marketplaces = serializers.IntegerField()
    active_sellers = serializers.IntegerField()
    sellers_joined_this_month = serializers.IntegerField()
    active_listings = ManagerDashboardChannelCountSerializer(many=True)
    free_eans = ManagerDashboardFreeEansSerializer()
    queue = ManagerDashboardQueueItemSerializer(many=True)


@extend_schema_serializer(component_name="ModerationReject")
class RejectProductSerializer(serializers.Serializer):
    comment = serializers.CharField(
        max_length=2000,
        trim_whitespace=True,
    )

    def validate_comment(self, comment):
        if not comment:
            raise serializers.ValidationError("A rejection reason is required.")

        return comment


@extend_schema_serializer(component_name="ManagerProductStatusChange")
class ChangeApprovedProductStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=(
            ("submitted", "Submitted"),
            ("rejected", "Rejected"),
        )
    )
    comment = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=2000,
        trim_whitespace=True,
    )

    def validate(self, attrs):
        if attrs["status"] == "rejected" and not attrs.get("comment"):
            raise serializers.ValidationError(
                {"comment": "A rejection reason is required."}
            )
        return attrs
