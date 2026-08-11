from rest_framework import serializers

from .models import ModerationDecision


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


class ApproveProductSerializer(serializers.Serializer):
    comment = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=2000,
    )


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