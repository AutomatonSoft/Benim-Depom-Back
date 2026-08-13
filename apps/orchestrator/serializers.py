from rest_framework import serializers

from .models import MarketplaceJob


class MarketplaceJobRequestSerializer(serializers.Serializer):
    channels = serializers.ListField(
        child=serializers.ChoiceField(choices=("hood", "otto", "kaufland")),
        required=False,
        allow_empty=False,
    )
    payloads = serializers.DictField(child=serializers.DictField(), required=False)
    accounts = serializers.DictField(child=serializers.CharField(), required=False)

    def validate(self, attrs):
        operation = self.context["operation"]
        channels = list(dict.fromkeys(attrs.get("channels", ["hood", "otto", "kaufland"])))
        attrs["channels"] = channels
        payloads = attrs.get("payloads", {})
        if operation in {MarketplaceJob.Operation.PUBLISH, MarketplaceJob.Operation.UPDATE}:
            missing = [channel for channel in channels if channel not in payloads]
            if missing:
                raise serializers.ValidationError(
                    {"payloads": f"Payload is required for: {', '.join(missing)}."}
                )
        unknown = set(payloads) - set(channels)
        if unknown:
            raise serializers.ValidationError(
                {"payloads": f"Channels were not requested: {', '.join(sorted(unknown))}."}
            )
        return attrs


class MarketplaceJobSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = MarketplaceJob
        fields = (
            "id", "product_id", "operation", "status", "request_id",
            "requested_channels", "results", "error", "created_at",
            "started_at", "finished_at",
        )
