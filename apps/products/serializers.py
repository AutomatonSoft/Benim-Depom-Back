import json
import warnings
from decimal import Decimal, InvalidOperation

from django.conf import settings
from drf_spectacular.utils import (
    extend_schema_field,
    extend_schema_serializer,
)
from PIL import Image, UnidentifiedImageError
from rest_framework import serializers
from rest_framework.fields import empty

from apps.catalog.otto_catalog import (
    OttoCatalogError,
    get_otto_catalog,
)
from apps.common.permissions import is_manager

from .models import (
    Product,
    ProductGeneratedImage,
    ProductImage,
    ProductVariant,
)
from .services import create_product, create_product_with_images, update_product


@extend_schema_serializer(component_name="ProductsOwner")
class ProductOwnerSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    username = serializers.CharField(read_only=True)
    first_name = serializers.CharField(read_only=True)
    email = serializers.CharField(read_only=True)


@extend_schema_serializer(component_name="ProductsVariant")
class ProductVariantSerializer(serializers.ModelSerializer):
    color_hex = serializers.RegexField(
        regex=r"^#[0-9A-Fa-f]{6}$",
        max_length=7,
        error_messages={
            "invalid": "Use a hexadecimal color in the #RRGGBB format.",
        },
    )
    materials = serializers.ListField(
        child=serializers.CharField(max_length=100, trim_whitespace=True),
        min_length=1,
        max_length=2,
        help_text=(
            "Materials in priority order. The first item is the primary "
            "material and is sent to Kaufland."
        ),
    )

    class Meta:
        model = ProductVariant
        fields = (
            "id",
            "color_hex",
            "materials",
            "width_cm",
            "height_cm",
            "length_cm",
            "quantity",
        )
        read_only_fields = ("id",)

    def validate_color_hex(self, value):
        return value.upper()

    def validate_materials(self, values):
        normalized = [value.strip() for value in values]

        if any(not value for value in normalized):
            raise serializers.ValidationError("Material names must not be empty.")

        if len({value.casefold() for value in normalized}) != len(normalized):
            raise serializers.ValidationError(
                "Material names must be unique within one variant."
            )

        return normalized


@extend_schema_serializer(component_name="ProductsGeneratedImage")
class ProductGeneratedImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductGeneratedImage
        fields = (
            "id",
            "mode",
            "image",
            "created_at",
        )
        read_only_fields = fields


@extend_schema_serializer(component_name="ProductsImage")
class ProductImageSerializer(serializers.ModelSerializer):
    generated_images = ProductGeneratedImageSerializer(
        many=True,
        read_only=True,
    )

    class Meta:
        model = ProductImage
        fields = (
            "id",
            "image",
            "processed_image",
            "position",
            "is_primary",
            "processing_status",
            "processing_error",
            "processing_result",
            "generated_images",
        )
        read_only_fields = (
            "id",
            "image",
            "processed_image",
            "position",
            "is_primary",
            "processing_status",
            "processing_error",
            "processing_result",
        )

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")

        if request and not is_manager(request.user):
            for field in (
                "processed_image",
                "processing_status",
                "processing_error",
                "processing_result",
                "generated_images",
            ):
                data.pop(field, None)

        return data


@extend_schema_serializer(component_name="ProductsProduct")
class ProductSerializer(serializers.ModelSerializer):
    product_type = serializers.CharField(max_length=255, trim_whitespace=True)
    seller = ProductOwnerSerializer(source="owner", read_only=True)
    unit_price = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=True,
    )
    currency = serializers.ChoiceField(
        choices=Product.Currency.choices,
        required=False,
        default=Product.Currency.TRY,
    )
    warehouse_city = serializers.ChoiceField(
        choices=Product.WarehouseCity.choices,
        required=True,
    )
    listing_price_eur = serializers.SerializerMethodField()
    listing_price_eur_override = serializers.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        required=False,
        allow_null=True,
    )
    pricing_overrides = serializers.JSONField(required=False)
    pricing_formula = serializers.SerializerMethodField()
    otto_category_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
    )
    otto_category_group_id = serializers.IntegerField(
        required=False,
        allow_null=True,
        min_value=1,
    )
    otto_category_name = serializers.CharField(read_only=True)
    otto_category_group_name = serializers.CharField(read_only=True)
    otto_attributes = serializers.DictField(
        required=False,
        default=dict,
    )
    resubmit_for_moderation = serializers.BooleanField(
        write_only=True,
        required=False,
        default=False,
        help_text=(
            "Seller-only flag for a rejected product. Set true after all "
            "corrections are complete to send it back to moderation."
        ),
    )
    variants = ProductVariantSerializer(many=True, required=False)
    images = ProductImageSerializer(many=True, read_only=True)
    total_quantity = serializers.SerializerMethodField()
    total_amount = serializers.SerializerMethodField()
    last_moderation_decision = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = (
            "id",
            "owner",
            "seller",
            "title",
            "product_type",
            "unit_price",
            "currency",
            "warehouse_city",
            "listing_price_eur",
            "listing_price_eur_override",
            "pricing_overrides",
            "pricing_formula",
            "total_amount",
            "otto_category_id",
            "otto_category_group_id",
            "otto_category_name",
            "otto_category_group_name",
            "otto_attributes",
            "resubmit_for_moderation",
            "status",
            "last_moderation_decision",
            "ean_jv",
            "ean_xl",
            "is_available",
            "approved_at",
            "availability_reminder_sent_at",
            "availability_confirmed_at",
            "deactivation_requested_at",
            "deactivated_at",
            "variants",
            "images",
            "total_quantity",
            "created_at",
            "updated_at",
        )
        read_only_fields = (
            "id",
            "owner",
            "seller",
            "status",
            "last_moderation_decision",
            "ean_jv",
            "ean_xl",
            "images",
            "total_quantity",
            "otto_category_name",
            "otto_category_group_name",
            "created_at",
            "updated_at",
            "is_available",
            "approved_at",
            "availability_reminder_sent_at",
            "availability_confirmed_at",
            "deactivation_requested_at",
            "deactivated_at",
            "listing_price_eur",
            "pricing_formula",
        )

    @extend_schema_field(serializers.IntegerField)
    def get_total_quantity(self, product) -> int:
        return sum(variant.quantity for variant in product.variants.all())

    @extend_schema_field(
        serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    )
    def get_listing_price_eur(self, product):
        from .pricing import safe_listing_price_eur

        if "exchange_rate" in self.context:
            price = safe_listing_price_eur(product, rate=self.context["exchange_rate"])
        else:
            price = safe_listing_price_eur(product)
        if price is None:
            return None
        return f"{price:.2f}"

    @extend_schema_field(serializers.DictField)
    def get_pricing_formula(self, product):
        from .pricing import effective_pricing_formula

        if "exchange_rate" in self.context:
            return effective_pricing_formula(
                product, rate=self.context["exchange_rate"]
            )
        return effective_pricing_formula(product)

    @extend_schema_field(serializers.DecimalField(max_digits=14, decimal_places=2))
    def get_total_amount(self, product):
        total = product.unit_price * sum(
            variant.quantity for variant in product.variants.all()
        )
        return f"{total:.2f}"

    @extend_schema_field(
        serializers.ChoiceField(
            choices=["approved", "rejected", "returned_to_review"],
            allow_null=True,
        )
    )
    def get_last_moderation_decision(self, product) -> str | None:
        return getattr(product, "last_moderation_decision", None)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")

        if request and not is_manager(request.user):
            data.pop("ean_jv", None)
            data.pop("ean_xl", None)
            data.pop("pricing_overrides", None)
            data.pop("pricing_formula", None)
            data.pop("listing_price_eur_override", None)
            data.pop("last_moderation_decision", None)

        return data

    def validate_pricing_overrides(self, value):
        if value in (None, {}):
            return {}
        if not isinstance(value, dict):
            raise serializers.ValidationError("Formula overrides must be an object.")
        allowed = {
            "margin",
            "adv_fee",
            "vat",
            "city_tariffs_eur_per_cbm",
            "de_size_tiers",
            "eur_to_try",
            "eur_to_usd",
        }
        unknown = set(value) - allowed
        if unknown:
            raise serializers.ValidationError(
                f"Unknown formula fields: {', '.join(sorted(unknown))}."
            )
        for key in ("margin", "adv_fee", "vat", "eur_to_try", "eur_to_usd"):
            if key not in value:
                continue
            try:
                number = Decimal(str(value[key]))
            except InvalidOperation as error:
                raise serializers.ValidationError({key: "Must be a number."}) from error
            if number <= 0 and key in {"eur_to_try", "eur_to_usd"}:
                raise serializers.ValidationError({key: "Must be greater than zero."})
            if number < 0:
                raise serializers.ValidationError({key: "Must not be negative."})
            value[key] = str(number)
        if "city_tariffs_eur_per_cbm" in value:
            tariffs = value["city_tariffs_eur_per_cbm"]
            if not isinstance(tariffs, dict):
                raise serializers.ValidationError(
                    {"city_tariffs_eur_per_cbm": "Must be an object of city codes."}
                )
            cities = {choice.value for choice in Product.WarehouseCity}
            for city, tariff in tariffs.items():
                if city not in cities:
                    raise serializers.ValidationError(
                        {"city_tariffs_eur_per_cbm": f"Unknown city '{city}'."}
                    )
                try:
                    amount = Decimal(str(tariff))
                except InvalidOperation as error:
                    raise serializers.ValidationError(
                        {"city_tariffs_eur_per_cbm": "Tariffs must be numbers."}
                    ) from error
                if amount < 0:
                    raise serializers.ValidationError(
                        {"city_tariffs_eur_per_cbm": "Tariffs must not be negative."}
                    )
                tariffs[city] = str(amount)
        if "de_size_tiers" in value:
            tiers = value["de_size_tiers"]
            if not isinstance(tiers, list) or not tiers:
                raise serializers.ValidationError(
                    {"de_size_tiers": "Provide at least one size tier."}
                )
            cleaned = []
            for tier in tiers:
                if not isinstance(tier, dict) or not {
                    "code",
                    "min_cbm",
                    "max_cbm",
                    "price_eur",
                }.issubset(tier):
                    raise serializers.ValidationError(
                        {
                            "de_size_tiers": (
                                "Each tier needs code, min_cbm, max_cbm and price_eur."
                            )
                        }
                    )
                cleaned.append(
                    {
                        "code": str(tier["code"]),
                        "min_cbm": str(tier["min_cbm"]),
                        "max_cbm": str(tier["max_cbm"]),
                        "price_eur": str(tier["price_eur"]),
                    }
                )
            value["de_size_tiers"] = cleaned
        return value

    def validate(self, attrs):
        """Validate draft data without requiring a complete OTTO form."""
        request = self.context.get("request")
        manager_only = {
            field
            for field in ("pricing_overrides", "listing_price_eur_override")
            if field in attrs
        }
        if manager_only and (not request or not is_manager(request.user)):
            raise serializers.ValidationError(
                {
                    field: "Only managers can change the listing formula or euro price."
                    for field in manager_only
                }
            )

        protected_ean_fields = {
            field for field in ("ean_jv", "ean_xl") if field in self.initial_data
        }

        if protected_ean_fields:
            raise serializers.ValidationError(
                {
                    field: (
                        "EANs are assigned by the approved EAN-pool service "
                        "and cannot be set through the product API."
                    )
                    for field in protected_ean_fields
                }
            )

        product_type = attrs.get("product_type")
        if product_type is not None and not product_type.strip():
            raise serializers.ValidationError(
                {"product_type": "Product type must not be empty."}
            )

        variants = attrs.get("variants")

        if self.instance is None and not variants:
            raise serializers.ValidationError(
                {"variants": "A product must contain at least one variant."}
            )

        if variants is not None and not variants:
            raise serializers.ValidationError(
                {"variants": "At least one variant is required."}
            )

        self._validate_otto_catalog_data(attrs)

        if attrs.get("resubmit_for_moderation"):
            request = self.context.get("request")

            if self.instance is None:
                raise serializers.ValidationError(
                    {
                        "resubmit_for_moderation": (
                            "A newly created product is submitted automatically."
                        )
                    }
                )

            if request and is_manager(request.user):
                raise serializers.ValidationError(
                    {
                        "resubmit_for_moderation": (
                            "Only the product seller can resubmit a rejected product."
                        )
                    }
                )

            if self.instance.status != Product.Status.REJECTED:
                raise serializers.ValidationError(
                    {
                        "resubmit_for_moderation": (
                            "Only a rejected product can be resubmitted."
                        )
                    }
                )

        return attrs

    def _validate_otto_catalog_data(self, attrs):
        """Validate supplied OTTO category data against the local catalog."""
        category_id = attrs.get(
            "otto_category_id",
            getattr(self.instance, "otto_category_id", None),
        )
        group_id = attrs.get(
            "otto_category_group_id",
            getattr(self.instance, "otto_category_group_id", None),
        )
        attributes = attrs.get(
            "otto_attributes",
            getattr(self.instance, "otto_attributes", {}),
        )
        attributes_were_sent = "otto_attributes" in getattr(self, "initial_data", {})
        has_attribute_values = bool(attributes)

        if category_id is None and group_id is None:
            if attributes_were_sent and has_attribute_values:
                raise serializers.ValidationError(
                    {
                        "otto_category_id": (
                            "otto_category_id and otto_category_group_id "
                            "must be supplied together."
                        )
                    }
                )
            attrs.pop("otto_category_id", None)
            attrs.pop("otto_category_group_id", None)
            if attributes_were_sent and not has_attribute_values:
                attrs.pop("otto_attributes", None)
            return

        if category_id is None or group_id is None:
            raise serializers.ValidationError(
                {
                    "otto_category_id": (
                        "otto_category_id and otto_category_group_id "
                        "must be supplied together."
                    )
                }
            )

        if not isinstance(attributes, dict):
            raise serializers.ValidationError(
                {"otto_attributes": "Expected an object with attribute values."}
            )

        try:
            catalog = get_otto_catalog()
        except OttoCatalogError as exc:
            raise serializers.ValidationError(
                {"detail": "OTTO catalog is temporarily unavailable."}
            ) from exc

        category = catalog["categories_by_id"].get(category_id)
        if category is None:
            raise serializers.ValidationError(
                {"otto_category_id": "Unknown OTTO category ID."}
            )

        actual_group_id = int(category["category_group_id"])
        if actual_group_id != group_id:
            raise serializers.ValidationError(
                {
                    "otto_category_group_id": (
                        "The selected category does not belong to this "
                        "OTTO category group."
                    )
                }
            )

        definitions = catalog["attributes_by_id_by_group_id"].get(
            group_id,
            {},
        )
        normalized_attributes = {}

        for raw_attribute_id, value in attributes.items():
            try:
                attribute_id = int(raw_attribute_id)
            except (TypeError, ValueError) as exc:
                raise serializers.ValidationError(
                    {
                        "otto_attributes": (
                            "Attribute keys must contain numeric OTTO IDs."
                        )
                    }
                ) from exc

            definition = definitions.get(attribute_id)
            if definition is None:
                raise serializers.ValidationError(
                    {
                        "otto_attributes": (
                            f"Attribute {attribute_id} does not belong to "
                            "the selected category group."
                        )
                    }
                )

            normalized_attributes[str(attribute_id)] = (
                self._validate_otto_attribute_value(
                    definition=definition,
                    value=value,
                )
            )

        attrs["otto_attributes"] = normalized_attributes
        attrs["otto_category_name"] = category["name"]
        attrs["otto_category_group_name"] = category["category_group"]

    @staticmethod
    def _validate_otto_attribute_value(*, definition, value):
        """Validate one single- or multi-value OTTO catalog attribute."""
        if definition["multiValue"] and not isinstance(value, list):
            raise serializers.ValidationError(
                {
                    str(definition["attributeId"]): (
                        "This attribute accepts multiple values and must "
                        "be sent as an array."
                    )
                }
            )

        if not definition["multiValue"] and isinstance(value, list):
            raise serializers.ValidationError(
                {
                    str(definition["attributeId"]): (
                        "This attribute accepts only one value."
                    )
                }
            )

        values = value if definition["multiValue"] else [value]
        if not values:
            raise serializers.ValidationError(
                {str(definition["attributeId"]): "Attribute value must not be empty."}
            )

        validated_values = [
            ProductSerializer._validate_single_otto_value(
                definition=definition,
                value=item,
            )
            for item in values
        ]

        allowed_values = definition.get("allowedValues") or []
        if allowed_values:
            invalid_values = [
                item for item in validated_values if item not in allowed_values
            ]
            if invalid_values:
                raise serializers.ValidationError(
                    {
                        str(definition["attributeId"]): (
                            "Value is not allowed for this OTTO attribute."
                        )
                    }
                )

        return validated_values if definition["multiValue"] else validated_values[0]

    @staticmethod
    def _validate_single_otto_value(*, definition, value):
        """Validate one primitive value from the current OTTO schema."""
        attribute_type = definition["type"]

        if attribute_type == "STRING":
            if not isinstance(value, str) or not value.strip():
                raise serializers.ValidationError(
                    {str(definition["attributeId"]): "Expected a non-empty string."}
                )
            return value.strip()

        if attribute_type == "INTEGER":
            if isinstance(value, bool) or not isinstance(value, int):
                raise serializers.ValidationError(
                    {str(definition["attributeId"]): "Expected an integer."}
                )
            return value

        if attribute_type == "FLOAT":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise serializers.ValidationError(
                    {str(definition["attributeId"]): "Expected a number."}
                )
            return value

        raise serializers.ValidationError(
            {
                str(definition["attributeId"]): (
                    f"Unsupported OTTO attribute type: {attribute_type}."
                )
            }
        )

    def create(self, validated_data):
        variants_data = validated_data.pop("variants")
        validated_data.pop("resubmit_for_moderation", None)

        return create_product(
            owner=self.context["request"].user,
            data=validated_data,
            variants_data=variants_data,
        )

    def update(self, instance, validated_data):
        variants_data = validated_data.pop("variants", None)
        resubmit_for_moderation = validated_data.pop(
            "resubmit_for_moderation",
            False,
        )
        if (
            "pricing_overrides" in validated_data
            and "listing_price_eur_override" not in validated_data
        ):
            validated_data["listing_price_eur_override"] = None

        product = update_product(
            product=instance,
            data=validated_data,
            variants_data=variants_data,
        )

        if resubmit_for_moderation:
            # Lazy import avoids a products <-> moderation import cycle.
            from apps.moderation.services import submit_product_for_moderation

            return submit_product_for_moderation(product=product)

        return product


@extend_schema_serializer(component_name="ProductsAvailability")
class ProductAvailabilitySerializer(serializers.Serializer):
    is_available = serializers.BooleanField()


@extend_schema_serializer(component_name="ProductsImageUpload")
class ProductImageUploadSerializer(serializers.Serializer):
    image = serializers.ImageField()
    is_primary = serializers.BooleanField(default=False)

    def validate_image(self, image):
        allowed_formats = {
            "JPEG": "image/jpeg",
            "PNG": "image/png",
            "WEBP": "image/webp",
        }

        if image.size > settings.PRODUCT_IMAGE_MAX_UPLOAD_BYTES:
            raise serializers.ValidationError("Image size must not exceed 10 MB.")

        declared_content_type = getattr(image, "content_type", None)

        if (
            declared_content_type
            and declared_content_type not in allowed_formats.values()
        ):
            raise serializers.ValidationError("Allowed image formats: JPEG, PNG, WEBP.")

        try:
            image.seek(0)

            with warnings.catch_warnings():
                warnings.simplefilter(
                    "error",
                    Image.DecompressionBombWarning,
                )

                with Image.open(image) as parsed_image:
                    detected_format = (parsed_image.format or "").upper()
                    expected_content_type = allowed_formats.get(detected_format)

                    if expected_content_type is None:
                        raise serializers.ValidationError(
                            "Allowed image formats: JPEG, PNG, WEBP."
                        )

                    if (
                        declared_content_type
                        and declared_content_type != expected_content_type
                    ):
                        raise serializers.ValidationError(
                            "Image content type does not match its actual format."
                        )

                    pixel_count = parsed_image.width * parsed_image.height

                    if pixel_count > settings.PRODUCT_IMAGE_MAX_PIXELS:
                        raise serializers.ValidationError(
                            "Image dimensions are too large."
                        )

                    # Checks that the file has a valid image structure
                    # without fully decoding it into memory.
                    parsed_image.verify()

        except serializers.ValidationError:
            raise
        except (
            UnidentifiedImageError,
            OSError,
            Image.DecompressionBombError,
            Image.DecompressionBombWarning,
        ) as error:
            raise serializers.ValidationError(
                "Uploaded file is not a valid safe image."
            ) from error
        finally:
            image.seek(0)

        return image


class MultipartJSONListField(serializers.ListField):
    """Accept a JSON-encoded list from a multipart form field."""

    def get_value(self, dictionary):
        if hasattr(dictionary, "get"):
            return dictionary.get(self.field_name, empty)

        return super().get_value(dictionary)

    def to_internal_value(self, data):
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as error:
                raise serializers.ValidationError("Expected a JSON array.") from error

        return super().to_internal_value(data)


class MultipartJSONDictField(serializers.DictField):
    """Accept a JSON-encoded object from a multipart form field."""

    def get_value(self, dictionary):
        if hasattr(dictionary, "get"):
            return dictionary.get(self.field_name, empty)

        return super().get_value(dictionary)

    def to_internal_value(self, data):
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError as error:
                raise serializers.ValidationError("Expected a JSON object.") from error

        return super().to_internal_value(data)


class MultipartImageListField(serializers.ListField):
    """Read repeated ``images`` fields from Django's multipart QueryDict."""

    def get_value(self, dictionary):
        if hasattr(dictionary, "getlist"):
            values = dictionary.getlist(self.field_name) or dictionary.getlist(
                f"{self.field_name}[]"
            )
            if values:
                return values

        return super().get_value(dictionary)


@extend_schema_serializer(component_name="ProductsMultipartCreate")
class ProductMultipartCreateSerializer(ProductSerializer):
    """Product creation payload for ``multipart/form-data`` clients."""

    variants = MultipartJSONListField(
        child=ProductVariantSerializer(),
        allow_empty=False,
        help_text="JSON array of product variants.",
    )
    otto_attributes = MultipartJSONDictField(
        required=False,
        default=dict,
        help_text="Optional JSON object with OTTO attribute values.",
    )
    images = MultipartImageListField(
        child=serializers.ImageField(),
        min_length=1,
        max_length=10,
        write_only=True,
        help_text="One to ten image files. The first image becomes primary.",
    )

    def validate_images(self, images):
        image_validator = ProductImageUploadSerializer()
        errors = {}
        validated_images = []

        for index, image in enumerate(images):
            try:
                validated_images.append(image_validator.validate_image(image))
            except serializers.ValidationError as error:
                errors[str(index)] = error.detail

        if errors:
            raise serializers.ValidationError(errors)

        return validated_images

    def create(self, validated_data):
        variants_data = validated_data.pop("variants")
        image_files = validated_data.pop("images")
        validated_data.pop("resubmit_for_moderation", None)

        return create_product_with_images(
            owner=self.context["request"].user,
            data=validated_data,
            variants_data=variants_data,
            image_files=image_files,
        )

    def to_representation(self, instance):
        return ProductSerializer(instance, context=self.context).data


@extend_schema_serializer(component_name="ProductsImageReorder")
class ProductImageReorderSerializer(serializers.Serializer):
    image_ids = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        allow_empty=False,
    )

    def validate_image_ids(self, image_ids):
        if len(image_ids) != len(set(image_ids)):
            raise serializers.ValidationError("Image identifiers must be unique")

        return image_ids
