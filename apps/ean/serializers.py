from drf_spectacular.utils import extend_schema_serializer
from rest_framework import serializers

from .models import EanCode


def is_valid_gtin(value: str) -> bool:
    if len(value) not in {8, 12, 13, 14} or not value.isdigit():
        return False

    total = 0
    for index, digit in enumerate(reversed(value[:-1])):
        total += int(digit) * (3 if index % 2 == 0 else 1)

    return (10 - total % 10) % 10 == int(value[-1])


@extend_schema_serializer(component_name="EanImport")
class EanImportSerializer(serializers.Serializer):
    account = serializers.ChoiceField(choices=EanCode.Account.choices)
    codes = serializers.CharField(trim_whitespace=True)

    def validate_codes(self, value: str) -> str:
        if not value.strip():
            raise serializers.ValidationError("At least one EAN is required.")
        return value


@extend_schema_serializer(component_name="EanCode")
class EanCodeSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = EanCode
        fields = (
            "id",
            "code",
            "account",
            "product_id",
            "imported_at",
            "assigned_at",
        )
        read_only_fields = fields
