from rest_framework import serializers

EU_COUNTRIES = frozenset(
    "AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE".split()
)


class RegulatoryContactSerializer(serializers.Serializer):
    """Проверяет адрес и электронный контакт без подстановки данных продавца."""

    companyName = serializers.CharField(max_length=100)
    addressLine1 = serializers.CharField(max_length=180)
    addressLine2 = serializers.CharField(max_length=180, required=False)
    city = serializers.CharField(max_length=64)
    postalCode = serializers.CharField(max_length=9)
    country = serializers.RegexField(r"^[A-Z]{2}$")
    stateOrProvince = serializers.CharField(max_length=64, required=False)
    email = serializers.EmailField(max_length=180, required=False)
    contactUrl = serializers.URLField(max_length=250, required=False)
    phone = serializers.CharField(max_length=64, required=False)

    def validate(self, attrs):
        """Требует электронный контакт дополнительно к почтовому адресу."""
        if not (attrs.get("email") or attrs.get("contactUrl")):
            raise serializers.ValidationError("Provide email or contactUrl.")
        return attrs


class ResponsiblePersonSerializer(RegulatoryContactSerializer):
    """Описывает подтверждённую ответственную компанию в ЕС."""

    types = serializers.ListField(
        child=serializers.ChoiceField(choices=["EUResponsiblePerson"]),
        min_length=1,
        max_length=1,
        default=lambda: ["EUResponsiblePerson"],
    )

    def validate_country(self, value):
        if value not in EU_COUNTRIES:
            raise serializers.ValidationError(
                "Responsible person must be based in the EU."
            )
        return value


class EbayRegulatorySerializer(serializers.Serializer):
    """Позволяет отдельно сохранить ответственного, пока производитель неизвестен."""

    manufacturer = RegulatoryContactSerializer(required=False)
    responsiblePersons = ResponsiblePersonSerializer(
        many=True, required=False, min_length=1, max_length=5
    )


def missing_regulatory_fields(regulatory):
    """Перечисляет недостающие GPSR-контакты для проверки перед публикацией."""
    missing = []
    manufacturer = regulatory.get("manufacturer")
    if not manufacturer:
        missing.append("regulatory.manufacturer")
    if (
        not manufacturer or manufacturer.get("country") not in EU_COUNTRIES
    ) and not regulatory.get("responsiblePersons"):
        missing.append("regulatory.responsiblePersons")
    return missing
