from rest_framework import serializers

from .auth import ALLOWED_ACCOUNTS
from .domain import MAX_EANS, normalize_eans
from .errors import BulkEndError


class ScanSerializer(serializers.Serializer):
    account = serializers.ChoiceField(choices=ALLOWED_ACCOUNTS)
    eans = serializers.ListField(
        child=serializers.CharField(trim_whitespace=True, min_length=8, max_length=13),
        min_length=1,
        max_length=MAX_EANS,
    )

    def validate_eans(self, values):
        try:
            return normalize_eans(values)
        except BulkEndError as error:
            raise serializers.ValidationError(str(error)) from error


class ConfirmSerializer(serializers.Serializer):
    item_ids = serializers.ListField(
        child=serializers.RegexField(regex=r"^[0-9]{1,20}$"),
        min_length=1,
        max_length=2000,
    )
    typed_account = serializers.ChoiceField(
        choices=tuple(account.upper() for account in ALLOWED_ACCOUNTS)
    )
    include_variations = serializers.BooleanField(default=False)
    include_auction_risk = serializers.BooleanField(default=False)
    confirm_unavailable_reason = serializers.BooleanField()

    def validate_item_ids(self, values):
        if len(set(values)) != len(values):
            raise serializers.ValidationError(
                "Каждое объявление можно выбрать только один раз."
            )
        return values
