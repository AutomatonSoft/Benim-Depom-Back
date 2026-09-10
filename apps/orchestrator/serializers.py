from decimal import Decimal

from django.utils.dateparse import parse_datetime
from drf_spectacular.utils import extend_schema_field, extend_schema_serializer
from rest_framework import serializers

from apps.catalog.otto_shipping_profiles import (
    OttoShippingProfilesError,
    get_otto_shipping_profile,
)
from apps.marketplace.kaufland.payload_builder import (
    KAUFLAND_STOREFRONTS,
)
from apps.marketplace.materials import (
    clean_material_names,
    contains_source_language,
)
from apps.marketplace.otto.payload_builder import OTTO_VAT_VALUES
from apps.products.pricing import fill_marketplace_price

from .capabilities import supports_operation
from .models import (
    MarketplaceContentGeneration,
    MarketplaceJob,
    MarketplaceListingConfiguration,
    MarketplacePublication,
)


class GermanListingMaterialsMixin(serializers.Serializer):
    materials = serializers.ListField(
        child=serializers.CharField(max_length=80, allow_blank=False),
        max_length=2,
        required=False,
        allow_empty=True,
        help_text="German material names for the marketplace listing (max 2).",
    )

    def validate_materials(self, values):
        names = clean_material_names(values)
        if any(contains_source_language(name) for name in names):
            raise serializers.ValidationError("Materials must be in German.")
        return names


class MarketplaceTargetSerializer(serializers.Serializer):
    marketplace = serializers.ChoiceField(choices=("hood", "otto", "kaufland"))
    account = serializers.ChoiceField(choices=("jv", "xl"))


class MarketplaceListingStateRequestSerializer(serializers.Serializer):
    """Manager request to change state of selected marketplace listings."""

    action = serializers.ChoiceField(choices=("deactivate", "activate"))
    targets = MarketplaceTargetSerializer(many=True, required=False)

    def validate_targets(self, targets):
        pairs = {(target["marketplace"], target["account"]) for target in targets}
        if len(pairs) != len(targets):
            raise serializers.ValidationError(
                "Each marketplace/account pair must be unique."
            )
        return targets


class MarketplaceJobRequestSerializer(serializers.Serializer):
    channels = serializers.ListField(
        child=serializers.ChoiceField(choices=("hood", "otto", "kaufland")),
        required=False,
        allow_empty=False,
    )
    payloads = serializers.DictField(child=serializers.DictField(), required=False)
    accounts = serializers.DictField(child=serializers.CharField(), required=False)
    targets = serializers.ListField(
        child=MarketplaceTargetSerializer(),
        required=False,
        allow_empty=False,
    )

    def validate(self, attrs):
        operation = self.context["operation"]
        targets = attrs.get("targets", [])

        if targets:
            unique_pairs = set()

            for target in targets:
                pair = (target["marketplace"], target["account"])

                if pair in unique_pairs:
                    raise serializers.ValidationError(
                        {
                            "targets": (
                                "Each marketplace and account pair must be unique."
                            )
                        }
                    )

                unique_pairs.add(pair)

            channels = list(dict.fromkeys(target["marketplace"] for target in targets))
        else:
            channels = list(
                dict.fromkeys(
                    attrs.get(
                        "channels",
                        ["hood", "otto", "kaufland"],
                    )
                )
            )

        attrs["channels"] = channels
        unsupported_marketplaces = [
            marketplace
            for marketplace in channels
            if not supports_operation(
                marketplace=marketplace,
                operation=operation,
            )
        ]

        if unsupported_marketplaces:
            field_name = "targets" if targets else "channels"

            raise serializers.ValidationError(
                {
                    field_name: (
                        f"Operation '{operation}' is not supported by: "
                        f"{', '.join(sorted(unsupported_marketplaces))}."
                    )
                }
            )
        payloads = attrs.get("payloads", {})

        if operation in {
            MarketplaceJob.Operation.PUBLISH,
            MarketplaceJob.Operation.UPDATE,
        }:
            missing = [
                channel
                for channel in channels
                if channel not in {"otto", "hood", "kaufland"}
                and channel not in payloads
            ]

            if missing:
                raise serializers.ValidationError(
                    {"payloads": (f"Payload is required for: {', '.join(missing)}.")}
                )

        unknown_payload_channels = set(payloads) - set(channels)

        if unknown_payload_channels:
            raise serializers.ValidationError(
                {
                    "payloads": (
                        "Channels were not requested: "
                        f"{', '.join(sorted(unknown_payload_channels))}."
                    )
                }
            )

        accounts = attrs.get("accounts", {})

        unknown_account_channels = set(accounts) - set(channels)

        if unknown_account_channels:
            raise serializers.ValidationError(
                {
                    "accounts": (
                        "Channels were not requested: "
                        f"{', '.join(sorted(unknown_account_channels))}."
                    )
                }
            )

        invalid_accounts = {
            channel: account
            for channel, account in accounts.items()
            if account not in {"jv", "xl"}
        }

        if invalid_accounts:
            raise serializers.ValidationError(
                {"accounts": ("Account values must be either 'jv' or 'xl'.")}
            )

        return attrs


JOB_IN_PROGRESS_STATUSES = frozenset(
    {
        MarketplaceJob.Status.QUEUED,
        MarketplaceJob.Status.RUNNING,
        MarketplaceJob.Status.PENDING_CONFIRMATION,
    }
)


class MarketplaceJobFilterSerializer(serializers.Serializer):
    in_progress = serializers.BooleanField(required=False)
    marketplace = serializers.ChoiceField(
        choices=MarketplacePublication.Marketplace.choices,
        required=False,
    )
    status = serializers.ChoiceField(
        choices=MarketplaceJob.Status.choices,
        required=False,
    )
    operation = serializers.ChoiceField(
        choices=MarketplaceJob.Operation.choices,
        required=False,
    )
    product_id = serializers.IntegerField(min_value=1, required=False)
    search = serializers.CharField(required=False, allow_blank=True, max_length=200)


class MarketplaceJobSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(read_only=True)
    product_title = serializers.CharField(source="product.title", read_only=True)
    requested_targets = serializers.SerializerMethodField()
    in_progress = serializers.SerializerMethodField()

    @extend_schema_field(MarketplaceTargetSerializer(many=True))
    def get_requested_targets(self, job):
        return job.request_payload.get("targets", [])

    @extend_schema_field(serializers.BooleanField())
    def get_in_progress(self, job):
        return job.status in JOB_IN_PROGRESS_STATUSES

    class Meta:
        model = MarketplaceJob
        fields = (
            "id",
            "product_id",
            "product_title",
            "operation",
            "status",
            "in_progress",
            "request_id",
            "requested_channels",
            "requested_targets",
            "results",
            "error",
            "created_at",
            "started_at",
            "finished_at",
        )


@extend_schema_serializer(component_name="MarketplaceContentGenerationRequest")
class MarketplaceContentGenerationRequestSerializer(serializers.Serializer):
    targets = MarketplaceTargetSerializer(
        many=True,
        allow_empty=False,
        help_text="Marketplace/account pairs that will receive the generated draft.",
    )

    def validate_targets(self, targets):
        unique_pairs = {
            (target["marketplace"], target["account"]) for target in targets
        }

        if len(unique_pairs) != len(targets):
            raise serializers.ValidationError(
                "Each marketplace/account pair must be unique."
            )

        return targets


@extend_schema_serializer(component_name="MarketplaceContentGenerationApplyRequest")
class MarketplaceContentGenerationApplyRequestSerializer(
    MarketplaceContentGenerationRequestSerializer
):
    overwrite = serializers.BooleanField(
        default=False,
        help_text=(
            "If false, AI fills only empty fields. "
            "If true, it replaces existing manager text."
        ),
    )


@extend_schema_serializer(component_name="MarketplaceContentGenerationEditRequest")
class MarketplaceContentGenerationEditRequestSerializer(serializers.Serializer):
    """Manager edits of a completed AI draft before it is applied."""

    title = serializers.CharField(
        max_length=70,
        help_text="German marketplace title, maximum 70 characters.",
    )
    description = serializers.CharField(
        help_text=(
            "German description consisting of two or three plain-text "
            "paragraphs separated by an empty line."
        ),
    )
    bullet_points = serializers.ListField(
        child=serializers.CharField(),
        allow_empty=False,
        help_text="Three to five concise German product highlights.",
    )
    materials = serializers.ListField(
        child=serializers.CharField(max_length=80, allow_blank=False),
        max_length=2,
        required=False,
        allow_empty=True,
        help_text="German material names copied into listing configurations.",
    )


@extend_schema_serializer(component_name="MarketplaceContentGeneration")
class MarketplaceContentGenerationSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(read_only=True)
    requested_by_id = serializers.IntegerField(read_only=True)

    class Meta:
        model = MarketplaceContentGeneration
        fields = (
            "id",
            "product_id",
            "requested_by_id",
            "targets",
            "language",
            "status",
            "input_snapshot",
            "result",
            "error",
            "model",
            "celery_task_id",
            "created_at",
            "started_at",
            "finished_at",
        )
        read_only_fields = fields


class MarketplacePublicationFilterSerializer(serializers.Serializer):
    marketplace = serializers.ChoiceField(
        choices=MarketplacePublication.Marketplace.choices,
        required=False,
    )
    account = serializers.ChoiceField(
        choices=MarketplacePublication.Account.choices,
        required=False,
    )
    status = serializers.ChoiceField(
        choices=MarketplacePublication.Status.choices,
        required=False,
    )
    product_id = serializers.IntegerField(
        min_value=1,
        required=False,
    )
    search = serializers.CharField(required=False, allow_blank=True, max_length=200)


@extend_schema_serializer(component_name="MarketplacePublication")
class MarketplacePublicationSerializer(serializers.ModelSerializer):
    product_id = serializers.IntegerField(read_only=True)
    product_title = serializers.CharField(
        source="product.title",
        read_only=True,
    )
    owner_id = serializers.IntegerField(
        source="product.owner_id",
        read_only=True,
    )
    last_job_id = serializers.UUIDField(
        read_only=True,
        allow_null=True,
    )

    class Meta:
        model = MarketplacePublication
        fields = (
            "id",
            "product_id",
            "product_title",
            "owner_id",
            "marketplace",
            "account",
            "ean",
            "status",
            "external_id",
            "external_reference",
            "last_job_id",
            "attempt_count",
            "last_response",
            "last_error",
            "published_at",
            "deactivated_at",
            "deleted_at",
            "last_attempt_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields


@extend_schema_serializer(component_name="OttoListingConfiguration")
class OttoListingConfigurationSerializer(
    GermanListingMaterialsMixin, serializers.Serializer
):
    """Editable OTTO content shown in the manager web panel.

    These names are API-stable; the web frontend should render human labels,
    such as "OTTO selling price (€)" rather than the JSON key itself.
    """

    product_line = serializers.CharField(
        max_length=70,
        required=False,
        allow_blank=True,
        help_text="German product name / product line (max 70 characters).",
    )
    standard_price = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=False,
        help_text="OTTO selling price in EUR.",
    )
    vat = serializers.ChoiceField(
        choices=OTTO_VAT_VALUES,
        required=False,
        help_text="VAT class for OTTO: FULL, REDUCED, or FREE.",
    )
    shipping_profile_id = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        help_text="OTTO shipping profile ID for the selected account.",
    )
    media_urls = serializers.ListField(
        child=serializers.URLField(),
        required=False,
        allow_empty=True,
        help_text=("Ignored. Marketplace listings use AI images of the cover photo."),
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="German product description. It can later be suggested by AI and edited by a manager.",
    )
    bullet_points = serializers.ListField(
        child=serializers.CharField(max_length=1000, allow_blank=False),
        max_length=5,
        required=False,
        allow_empty=True,
        help_text="Up to five German product highlights.",
    )
    brand_id = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        help_text="Brand ID registered in the selected OTTO account.",
    )
    manufacturer = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
    )
    isbn = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        help_text="Optional: ISBN, used for books.",
    )
    upc = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        help_text="Optional: UPC barcode.",
    )
    pzn = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        help_text="Optional: German pharmaceutical product number.",
    )
    mpn = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        help_text="Optional: manufacturer part number.",
    )
    moin = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        help_text="Optional: OTTO/internal product identifier.",
    )
    offering_start_date = serializers.DateTimeField(
        required=False,
        allow_null=True,
        help_text="Optional: date and time when the offer becomes available.",
    )
    release_date = serializers.DateTimeField(
        required=False,
        allow_null=True,
        help_text="Optional: product release date, for example for pre-orders.",
    )
    order_max_quantity = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
        help_text="Optional: maximum number of units one customer may order.",
    )
    order_period_in_days = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
        help_text="Optional: period in days for the purchase limit.",
    )
    product_url = serializers.URLField(required=False, allow_blank=True)
    bundle = serializers.BooleanField(required=False, allow_null=True)
    multi_pack = serializers.BooleanField(required=False, allow_null=True)
    fsc_certified = serializers.BooleanField(required=False, allow_null=True)
    disposal = serializers.BooleanField(required=False, allow_null=True)

    def validate_shipping_profile_id(self, value):
        account = self.context.get("account")

        if account not in {"jv", "xl"}:
            return value

        try:
            profile = get_otto_shipping_profile(
                account=account,
                shipping_profile_id=value,
            )
        except OttoShippingProfilesError as exc:
            raise serializers.ValidationError(
                "OTTO shipping profiles are temporarily unavailable."
            ) from exc

        if profile is None:
            raise serializers.ValidationError(
                "This shipping profile does not belong to the selected account."
            )

        return value

    @staticmethod
    def to_storage(validated_data):
        """Convert non-JSON values before storing configuration as JSON."""
        result = dict(validated_data)

        if "standard_price" in result:
            result["standard_price"] = format(result["standard_price"], "f")

        for field in ("offering_start_date", "release_date"):
            value = result.get(field)
            if value is not None:
                result[field] = value.isoformat()

        return result

    def to_representation(self, instance):
        data = dict(instance)

        for field in ("offering_start_date", "release_date"):
            value = data.get(field)
            if isinstance(value, str):
                parsed_value = parse_datetime(value)
                if parsed_value is not None:
                    data[field] = parsed_value

        return super().to_representation(data)


@extend_schema_serializer(component_name="OttoListingConfigurationResponse")
class OttoListingConfigurationResponseSerializer(serializers.ModelSerializer):
    configuration = OttoListingConfigurationSerializer(read_only=True)

    class Meta:
        model = MarketplaceListingConfiguration
        fields = (
            "id",
            "product",
            "marketplace",
            "account",
            "configuration",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["configuration"] = fill_marketplace_price(
            data.get("configuration") or {},
            instance.product,
            field="standard_price",
        )
        return data


@extend_schema_serializer(component_name="HoodListingConfiguration")
class HoodListingConfigurationSerializer(
    GermanListingMaterialsMixin, serializers.Serializer
):
    """
    Настройки менеджера для одного Hood-объявления.
    Финальный JSON для Hood строится автоматически из товара и этих полей.
    """

    title = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        help_text="Название товара для Hood.",
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="HTML-описание товара для Hood.",
    )
    price = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=False,
        help_text="Цена одного товара для Hood.",
    )
    category_id = serializers.CharField(
        max_length=100,
        required=False,
        allow_blank=True,
        help_text="Ignored. Hood listings always use category 2412.",
    )
    image_urls = serializers.ListField(
        child=serializers.URLField(),
        required=False,
        allow_empty=True,
        help_text="Ignored. Marketplace listings use AI images of the cover photo.",
    )
    property_overrides = serializers.DictField(
        child=serializers.CharField(
            max_length=1000,
            allow_blank=False,
        ),
        required=False,
        help_text=(
            "Дополнительные или исправленные свойства Hood. "
            'Формат: {"Farbe": "Braun", "Stil": "Modern"}.'
        ),
    )

    @staticmethod
    def to_storage(validated_data):
        result = dict(validated_data)

        if "price" in result:
            result["price"] = format(result["price"], "f")

        return result


@extend_schema_serializer(component_name="HoodListingConfigurationResponse")
class HoodListingConfigurationResponseSerializer(serializers.ModelSerializer):
    configuration = HoodListingConfigurationSerializer(read_only=True)

    class Meta:
        model = MarketplaceListingConfiguration
        fields = (
            "id",
            "product",
            "marketplace",
            "account",
            "configuration",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["configuration"] = fill_marketplace_price(
            data.get("configuration") or {},
            instance.product,
            field="price",
        )
        return data


@extend_schema_serializer(component_name="KauflandListingConfiguration")
class KauflandListingConfigurationSerializer(
    GermanListingMaterialsMixin, serializers.Serializer
):
    """Настройки менеджера для одного Kaufland-объявления."""

    title = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        help_text="Название товара для Kaufland.",
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Описание товара для Kaufland.",
    )
    price = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=False,
        help_text="Цена одного товара.",
    )
    image_urls = serializers.ListField(
        child=serializers.URLField(),
        required=False,
        allow_empty=False,
        help_text="Ignored. Marketplace listings use AI images of the cover photo.",
    )
    delivery = serializers.IntegerField(
        min_value=0,
        required=False,
        help_text="Ignored. Kaufland listings use 32 delivery days by default.",
    )
    storefronts = serializers.ListField(
        child=serializers.ChoiceField(choices=KAUFLAND_STOREFRONTS),
        required=False,
        allow_empty=False,
        help_text=(
            "Страны публикации. По умолчанию — ['de']. "
            "Допустимые: de, cz, sk, pl, at, fr, it."
        ),
    )
    storefront = serializers.ChoiceField(
        choices=KAUFLAND_STOREFRONTS,
        required=False,
        allow_blank=False,
        help_text=(
            "Одна страна для update. Если не указана, "
            "используется первая из storefronts или de."
        ),
    )
    id_offer = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        help_text="Необязательный внутренний ID предложения.",
    )
    unit_id = serializers.CharField(
        max_length=255,
        required=False,
        allow_blank=True,
        help_text="Необязательный unit ID для Kaufland update.",
    )

    @staticmethod
    def to_storage(validated_data):
        result = dict(validated_data)

        if "price" in result:
            result["price"] = format(result["price"], "f")

        return result


@extend_schema_serializer(component_name="KauflandListingConfigurationResponse")
class KauflandListingConfigurationResponseSerializer(serializers.ModelSerializer):
    configuration = KauflandListingConfigurationSerializer(read_only=True)

    class Meta:
        model = MarketplaceListingConfiguration
        fields = (
            "id",
            "product",
            "marketplace",
            "account",
            "configuration",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["configuration"] = fill_marketplace_price(
            data.get("configuration") or {},
            instance.product,
            field="price",
        )
        return data
