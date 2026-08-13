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


@extend_schema_serializer(component_name="ModerationReject")
class RejectProductSerializer(serializers.Serializer):
    comment = serializers.CharField(
        max_length=2000,
        trim_whitespace=True,
    )

    def validate_comment(self, comment):
        if not comment:
            raise serializers.ValidationError(
                "A rejection reason is required."
            )

        return comment
